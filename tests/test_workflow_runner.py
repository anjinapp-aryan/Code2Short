import uuid

from code2shorts.artifacts import Artifact, ArtifactType, InMemoryArtifactStore
from code2shorts.core.models import SupportedLanguage
from code2shorts.workflow import (
    Code2ShortsState,
    FailureKind,
    InMemoryCheckpointStore,
    NodeExecutionError,
    NodeResult,
    RetryPolicy,
    Workflow,
    WorkflowContext,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowMetadata,
    WorkflowNode,
    WorkflowRequest,
    WorkflowRunner,
)


def _initial_state() -> Code2ShortsState:
    return Code2ShortsState(
        request=WorkflowRequest(
            topic="test",
            language=SupportedLanguage.JAVA,
            source_files={},
            entry_point="",
        ),
        metadata=WorkflowMetadata(workflow_id="wf-1", execution_id=uuid.uuid4().hex),
    )


class RecordingNode(WorkflowNode):
    """Always succeeds; saves one artifact tagged with its own name."""

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, state, context: WorkflowContext) -> NodeResult:
        artifact = Artifact.create(ArtifactType.SOURCE, self.name, content={"node": self.name})
        context.artifact_store.save(artifact)
        state.artifact_ids[self.name] = artifact.id
        return NodeResult(state=state, artifact_ids_created=[artifact.id])


class AlwaysFailNode(WorkflowNode):
    def __init__(self, name: str, kind: FailureKind) -> None:
        self.name = name
        self._kind = kind

    def run(self, state, context: WorkflowContext) -> NodeResult:
        raise NodeExecutionError(self._kind, f"{self.name} always fails")


class FailNTimesNode(WorkflowNode):
    """Fails with a TRANSIENT error the first `fail_count` attempts, then
    succeeds — used to prove retry actually re-invokes the node."""

    def __init__(self, name: str, fail_count: int) -> None:
        self.name = name
        self._fail_count = fail_count
        self.calls = 0

    def run(self, state, context: WorkflowContext) -> NodeResult:
        self.calls += 1
        if context.attempt <= self._fail_count:
            raise NodeExecutionError(FailureKind.TRANSIENT, "flaky")
        return NodeResult(state=state, artifact_ids_created=[])


def test_successful_sequential_workflow_completes_and_emits_events() -> None:
    events: list[WorkflowEvent] = []
    runner = WorkflowRunner(InMemoryArtifactStore(), event_sink=events.append)
    workflow = Workflow("test", [RecordingNode("a"), RecordingNode("b")])

    result = runner.run(workflow, _initial_state())

    assert result.succeeded
    assert result.state.artifact_ids.keys() == {"a", "b"}
    assert result.state.metadata.completed_node_names == ["a", "b"]

    event_types = [e.type for e in events]
    assert event_types == [
        WorkflowEventType.WORKFLOW_STARTED,
        WorkflowEventType.NODE_STARTED,
        WorkflowEventType.ARTIFACT_CREATED,
        WorkflowEventType.NODE_COMPLETED,
        WorkflowEventType.NODE_STARTED,
        WorkflowEventType.ARTIFACT_CREATED,
        WorkflowEventType.NODE_COMPLETED,
        WorkflowEventType.WORKFLOW_COMPLETED,
    ]


def test_node_failure_stops_workflow_and_records_error() -> None:
    runner = WorkflowRunner(InMemoryArtifactStore())
    workflow = Workflow(
        "test", [RecordingNode("a"), AlwaysFailNode("boom", FailureKind.DETERMINISTIC)]
    )

    result = runner.run(workflow, _initial_state())

    assert not result.succeeded
    assert result.failed_node == "boom"
    assert len(result.state.errors) == 1
    assert result.state.errors[0].kind == FailureKind.DETERMINISTIC
    assert result.state.metadata.completed_node_names == ["a"]  # "boom" never completed


def test_deterministic_failure_is_never_retried() -> None:
    runner = WorkflowRunner(InMemoryArtifactStore())
    node = AlwaysFailNode("boom", FailureKind.DETERMINISTIC)
    workflow = Workflow("test", [node])
    policy = RetryPolicy(max_attempts=5, retryable_kinds={FailureKind.TRANSIENT})

    result = runner.run(workflow, _initial_state(), retry_policy=policy)

    assert not result.succeeded
    assert len(result.state.errors) == 1  # only tried once


def test_transient_failure_is_retried_until_success() -> None:
    runner = WorkflowRunner(InMemoryArtifactStore())
    node = FailNTimesNode("flaky", fail_count=2)
    workflow = Workflow("test", [node])
    policy = RetryPolicy(max_attempts=5, retryable_kinds={FailureKind.TRANSIENT})

    result = runner.run(workflow, _initial_state(), retry_policy=policy)

    assert result.succeeded
    assert node.calls == 3  # failed twice, succeeded on the 3rd attempt
    assert len(result.state.errors) == 2  # both failed attempts recorded


def test_transient_failure_exhausts_retries_and_fails() -> None:
    runner = WorkflowRunner(InMemoryArtifactStore())
    node = FailNTimesNode("flaky", fail_count=10)
    workflow = Workflow("test", [node])
    policy = RetryPolicy(max_attempts=3, retryable_kinds={FailureKind.TRANSIENT})

    result = runner.run(workflow, _initial_state(), retry_policy=policy)

    assert not result.succeeded
    assert node.calls == 3


def test_checkpoint_and_resume() -> None:
    artifact_store = InMemoryArtifactStore()
    checkpoint_store = InMemoryCheckpointStore()
    runner = WorkflowRunner(artifact_store, checkpoint_store=checkpoint_store)

    # A workflow whose second node fails, simulating a crash after node "a".
    workflow_that_fails = Workflow(
        "test", [RecordingNode("a"), AlwaysFailNode("b", FailureKind.PERMANENT)]
    )
    state = _initial_state()
    first_result = runner.run(workflow_that_fails, state)
    assert not first_result.succeeded
    assert first_result.state.metadata.completed_node_names == ["a"]

    checkpoint = checkpoint_store.load_latest("wf-1", state.metadata.execution_id)
    assert checkpoint is not None
    assert checkpoint.last_completed_node == "a"

    # Now resume with a FIXED version of node "b" via a workflow using the
    # same node names — resume() picks up right after the checkpoint.
    workflow_fixed = Workflow("test", [RecordingNode("a"), RecordingNode("b")])
    resumed_result = runner.resume(workflow_fixed, "wf-1", state.metadata.execution_id)

    assert resumed_result.succeeded
    assert resumed_result.state.metadata.completed_node_names == ["a", "b"]
    assert "a" in resumed_result.state.artifact_ids
    assert "b" in resumed_result.state.artifact_ids


def test_retry_policy_should_retry() -> None:
    policy = RetryPolicy(max_attempts=3, retryable_kinds={FailureKind.TRANSIENT})
    assert policy.should_retry(FailureKind.TRANSIENT, attempt=1)
    assert policy.should_retry(FailureKind.TRANSIENT, attempt=2)
    assert not policy.should_retry(FailureKind.TRANSIENT, attempt=3)  # max_attempts reached
    assert not policy.should_retry(FailureKind.DETERMINISTIC, attempt=1)
    assert not policy.should_retry(FailureKind.PERMANENT, attempt=1)
    assert not policy.should_retry(FailureKind.VALIDATION, attempt=1)

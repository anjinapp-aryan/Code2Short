"""Local, sequential WorkflowRunner. No distributed scheduling, no queues,
no worker clusters — a plain Python loop over an ordered node list, with
retry, checkpointing, and structured event emission as first-class
concerns. This is deliberately the whole engine; see
ARCHITECTURE_DECISIONS.md, "Local workflow runner instead of a distributed
workflow engine".
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel

from code2shorts.workflow.checkpoint import Checkpoint, CheckpointStore
from code2shorts.workflow.context import WorkflowContext
from code2shorts.workflow.errors import NodeExecutionError, WorkflowError
from code2shorts.workflow.events import WorkflowEvent, WorkflowEventType
from code2shorts.workflow.node import WorkflowNode
from code2shorts.workflow.retry import NO_RETRY, RetryPolicy
from code2shorts.workflow.state import Code2ShortsState

from code2shorts.artifacts.store import ArtifactStore


class Workflow:
    """An ordered list of nodes. Deliberately not a graph — Phase 3 only
    needs sequential flow plus (future) simple pass/fail branching (see
    ARCHITECTURE_DECISIONS.md "conditional flow"); a DAG engine is not
    justified yet.
    """

    def __init__(self, name: str, nodes: list[WorkflowNode]) -> None:
        self.name = name
        self.nodes = nodes


class WorkflowResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    state: Code2ShortsState
    succeeded: bool
    failed_node: str | None = None


class WorkflowRunner:
    def __init__(
        self,
        artifact_store: ArtifactStore,
        checkpoint_store: CheckpointStore | None = None,
        event_sink: Callable[[WorkflowEvent], None] | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._checkpoint_store = checkpoint_store
        self._event_sink = event_sink or (lambda event: None)

    def run(
        self,
        workflow: Workflow,
        initial_state: Code2ShortsState,
        retry_policy: RetryPolicy | None = None,
        start_index: int = 0,
    ) -> WorkflowResult:
        retry_policy = retry_policy or NO_RETRY
        state = initial_state

        self._emit(state, WorkflowEventType.WORKFLOW_STARTED)

        for node in workflow.nodes[start_index:]:
            attempt = 1
            while True:
                context = WorkflowContext(
                    artifact_store=self._artifact_store,
                    workflow_id=state.metadata.workflow_id,
                    execution_id=state.metadata.execution_id,
                    emit=self._event_sink,
                    attempt=attempt,
                )
                self._emit(state, WorkflowEventType.NODE_STARTED, node_name=node.name)
                try:
                    result = node.run(state, context)
                except NodeExecutionError as error:
                    state.errors.append(
                        WorkflowError(
                            node_name=node.name,
                            kind=error.kind,
                            message=error.message,
                            detail=error.detail,
                            attempt=attempt,
                        )
                    )
                    self._emit(
                        state,
                        WorkflowEventType.NODE_FAILED,
                        node_name=node.name,
                        data={"kind": error.kind.value, "message": error.message},
                    )
                    if retry_policy.should_retry(error.kind, attempt):
                        self._emit(state, WorkflowEventType.RETRY_STARTED, node_name=node.name)
                        delay = retry_policy.delay_for_attempt(attempt)
                        if delay > 0:
                            time.sleep(delay)
                        attempt += 1
                        self._emit(state, WorkflowEventType.RETRY_COMPLETED, node_name=node.name)
                        continue
                    self._emit(state, WorkflowEventType.WORKFLOW_FAILED, node_name=node.name)
                    return WorkflowResult(state=state, succeeded=False, failed_node=node.name)
                else:
                    state = result.state
                    for artifact_id in result.artifact_ids_created:
                        self._emit(
                            state,
                            WorkflowEventType.ARTIFACT_CREATED,
                            node_name=node.name,
                            artifact_id=artifact_id,
                        )
                    state.metadata.completed_node_names.append(node.name)
                    self._emit(state, WorkflowEventType.NODE_COMPLETED, node_name=node.name)
                    if self._checkpoint_store is not None:
                        self._checkpoint_store.save(
                            Checkpoint(
                                workflow_id=state.metadata.workflow_id,
                                execution_id=state.metadata.execution_id,
                                last_completed_node=node.name,
                                state=state,
                            )
                        )
                    break

        self._emit(state, WorkflowEventType.WORKFLOW_COMPLETED)
        return WorkflowResult(state=state, succeeded=True)

    def resume(
        self,
        workflow: Workflow,
        workflow_id: str,
        execution_id: str,
        retry_policy: RetryPolicy | None = None,
    ) -> WorkflowResult:
        if self._checkpoint_store is None:
            raise ValueError("resume() requires a checkpoint_store")
        checkpoint = self._checkpoint_store.load_latest(workflow_id, execution_id)
        if checkpoint is None:
            raise ValueError(f"no checkpoint found for {workflow_id}/{execution_id}")
        node_names = [node.name for node in workflow.nodes]
        start_index = node_names.index(checkpoint.last_completed_node) + 1
        return self.run(
            workflow, checkpoint.state, retry_policy=retry_policy, start_index=start_index
        )

    def _emit(
        self,
        state: Code2ShortsState,
        event_type: WorkflowEventType,
        node_name: str | None = None,
        artifact_id: str | None = None,
        data: dict | None = None,
    ) -> None:
        self._event_sink(
            WorkflowEvent(
                type=event_type,
                workflow_id=state.metadata.workflow_id,
                execution_id=state.metadata.execution_id,
                node_name=node_name,
                artifact_id=artifact_id,
                data=data or {},
            )
        )

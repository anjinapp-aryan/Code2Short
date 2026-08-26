"""Phase 3 end-to-end: real Maven/JDK compile+trace (Phase 1/2, unmodified)
composed with the new workflow/artifact/AI layers. No mocked Java — only
the LLM call is mocked (MockLLMProvider), which is exactly the seam Phase 3
is designed to keep swappable.
"""

from __future__ import annotations

import uuid

import pytest

from code2shorts.ai.contracts import ExplanationResponse, ExplanationStep
from code2shorts.ai.providers import MockLLMProvider
from code2shorts.artifacts import ArtifactType, InMemoryArtifactStore
from code2shorts.core.models import SupportedLanguage
from code2shorts.workflow import (
    Code2ShortsState,
    CompileNode,
    ExplainNode,
    TraceNode,
    Workflow,
    WorkflowMetadata,
    WorkflowRequest,
    WorkflowRunner,
)
from tests.java_fixtures import load_java_fixture

pytestmark = pytest.mark.integration


def _state_from_fixture(variant: str, input_value: str) -> Code2ShortsState:
    code = load_java_fixture(variant)
    return Code2ShortsState(
        request=WorkflowRequest(
            topic="Reverse String",
            language=SupportedLanguage.JAVA,
            source_files=code.source_files,
            entry_point=code.entry_point,
            input_value=input_value,
        ),
        metadata=WorkflowMetadata(workflow_id="reverse-string", execution_id=uuid.uuid4().hex),
    )


def _valid_explanation_json() -> str:
    return ExplanationResponse(
        summary="Two pointers walk toward the center, swapping characters.",
        learning_objectives=["two-pointer technique"],
        steps=[ExplanationStep(order=0, description="program starts", referenced_trace_event_indices=[0])],
        referenced_trace_event_indices=[0],
    ).model_dump_json()


def test_full_pipeline_compile_trace_explain() -> None:
    artifact_store = InMemoryArtifactStore()
    provider = MockLLMProvider(canned_response=_valid_explanation_json())
    workflow = Workflow(
        "reverse-string",
        [CompileNode(), TraceNode(), ExplainNode(provider, provider_name="mock", model="mock")],
    )
    runner = WorkflowRunner(artifact_store)

    result = runner.run(workflow, _state_from_fixture("correct", "HELLO"))

    assert result.succeeded, result.state.errors
    assert result.state.artifact_ids.keys() == {"source", "compilation", "trace", "explanation"}
    assert result.state.validation.all_passed

    # every artifact really exists in the store
    for artifact_id in result.state.artifact_ids.values():
        assert artifact_store.exists(artifact_id)

    trace_artifact = artifact_store.get(result.state.artifact_ids["trace"])
    assert trace_artifact.type == ArtifactType.TRACE
    assert trace_artifact.content["output"] == "OLLEH"

    # lineage: source -> compilation -> trace -> explanation
    source_id = result.state.artifact_ids["source"]
    compilation_id = result.state.artifact_ids["compilation"]
    trace_id = result.state.artifact_ids["trace"]
    explanation_id = result.state.artifact_ids["explanation"]

    assert [c.id for c in artifact_store.list_children(source_id)] == [compilation_id]
    assert [c.id for c in artifact_store.list_children(compilation_id)] == [trace_id]
    assert [c.id for c in artifact_store.list_children(trace_id)] == [explanation_id]


def test_compile_failure_is_deterministic_and_stops_before_trace() -> None:
    artifact_store = InMemoryArtifactStore()
    workflow = Workflow("reverse-string", [CompileNode(), TraceNode()])
    runner = WorkflowRunner(artifact_store)

    result = runner.run(workflow, _state_from_fixture("compile_error", ""))

    assert not result.succeeded
    assert result.failed_node == "compile"
    assert result.state.errors[0].kind.value == "deterministic"
    assert "trace" not in result.state.artifact_ids  # never reached


def test_ai_hallucinated_trace_event_fails_validation_not_silently_accepted() -> None:
    artifact_store = InMemoryArtifactStore()
    hallucinated_response = ExplanationResponse(
        summary="s",
        steps=[
            ExplanationStep(
                order=0, description="d", referenced_trace_event_indices=[999_999]
            )
        ],
    ).model_dump_json()
    provider = MockLLMProvider(canned_response=hallucinated_response)
    workflow = Workflow(
        "reverse-string",
        [CompileNode(), TraceNode(), ExplainNode(provider)],
    )
    runner = WorkflowRunner(artifact_store)

    result = runner.run(workflow, _state_from_fixture("correct", "HELLO"))

    assert not result.succeeded
    assert result.failed_node == "explain"
    assert not result.state.validation.all_passed
    assert "999999" in result.state.validation.results[-1].errors[0]
    assert "explanation" not in result.state.artifact_ids  # never accepted as an artifact


def test_ai_schema_validation_rejects_malformed_output() -> None:
    artifact_store = InMemoryArtifactStore()
    provider = MockLLMProvider(canned_response="this is not JSON")
    workflow = Workflow("reverse-string", [CompileNode(), TraceNode(), ExplainNode(provider)])
    runner = WorkflowRunner(artifact_store)

    result = runner.run(workflow, _state_from_fixture("correct", "HELLO"))

    assert not result.succeeded
    assert result.failed_node == "explain"
    assert result.state.errors[-1].kind.value == "validation"


def test_artifact_checksum_deduplicates_identical_traces() -> None:
    # Running the same source+input twice produces two distinct trace
    # artifacts (by id) but with identical checksums — proving determinism
    # is visible at the artifact layer, not just informally "the JSON
    # matches" (this is the same guarantee Phase 2's
    # test_deterministic_repeated_execution proves at the trace level;
    # here we prove the artifact layer preserves it).
    artifact_store = InMemoryArtifactStore()
    workflow = Workflow("reverse-string", [CompileNode(), TraceNode()])
    runner = WorkflowRunner(artifact_store)

    result_a = runner.run(workflow, _state_from_fixture("correct", "HELLO"))
    result_b = runner.run(workflow, _state_from_fixture("correct", "HELLO"))

    trace_a = artifact_store.get(result_a.state.artifact_ids["trace"])
    trace_b = artifact_store.get(result_b.state.artifact_ids["trace"])

    assert trace_a.id != trace_b.id
    assert trace_a.checksum == trace_b.checksum
    assert artifact_store.find_by_checksum(trace_a.checksum).id == trace_a.id

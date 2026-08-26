"""Phase 4 end-to-end: real Java compile+trace (Phase 1/2, unmodified),
mock LLM (explanation + visualization plan + narration), mock TTS, a
trusted-renderer TEST DOUBLE (FakeVideoRenderer — no Manim required), and
MediaComposer with an injected fake ffmpeg call (no ffmpeg required).

No external API credentials required anywhere in this file.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from code2shorts.ai.contracts import (
    ExplanationResponse,
    ExplanationStep,
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.ai.providers import MockLLMProvider
from code2shorts.artifacts import ArtifactType, InMemoryArtifactStore
from code2shorts.core.models import SupportedLanguage
from code2shorts.execution.sandbox import ProcessResult
from code2shorts.media import MediaComposer
from code2shorts.narration import MockTTSProvider
from code2shorts.visualization import FakeVideoRenderer
from code2shorts.workflow import (
    Code2ShortsState,
    CompileNode,
    ComposeMediaNode,
    ExplainNode,
    FinalValidationNode,
    NarrationNode,
    RenderVideoNode,
    TraceNode,
    VisualizationPlanNode,
    Workflow,
    WorkflowMetadata,
    WorkflowRequest,
    WorkflowRunner,
)
from tests.java_fixtures import load_java_fixture

pytestmark = pytest.mark.integration


def _first_indices(prompt: str, count: int) -> list[int]:
    """Trace events appear in every generated prompt as '[idx] ...' lines,
    in trace order — parse real indices out rather than hardcoding
    fixture-specific numbers, so this test doesn't silently drift from
    whatever the instrumenter actually emits.
    """
    indices = [int(m) for m in re.findall(r"^\s*\[(\d+)\]", prompt, re.MULTILINE)]
    return indices[:count]


def _explanation_provider() -> MockLLMProvider:
    def respond(prompt: str) -> str:
        idx = _first_indices(prompt, 1)[0]
        return ExplanationResponse(
            summary="Two pointers walk toward the center, swapping characters.",
            learning_objectives=["two-pointer technique"],
            steps=[
                ExplanationStep(order=0, description="program starts", referenced_trace_event_indices=[idx])
            ],
            referenced_trace_event_indices=[idx],
        ).model_dump_json()

    return MockLLMProvider(canned_response=respond)


def _visualization_provider() -> MockLLMProvider:
    def respond(prompt: str) -> str:
        idx0, idx1 = _first_indices(prompt, 2)
        return VisualizationPlanResponse(
            lesson_title="Reverse String",
            steps=[
                VisualizationStepPlan(
                    order=0, visual_action=VisualAction.INTRO, trace_event_index=idx0,
                    narration_text="We begin reversing the string.", duration_seconds=1.5,
                ),
                VisualizationStepPlan(
                    order=1, visual_action=VisualAction.COMPLETION, trace_event_index=idx1,
                    narration_text="The string is now reversed.", duration_seconds=1.5,
                ),
            ],
        ).model_dump_json()

    return MockLLMProvider(canned_response=respond)


def _narration_provider() -> MockLLMProvider:
    return MockLLMProvider(
        canned_response=NarrationResponse(
            segments=[
                NarrationSegment(order=0, text="We begin reversing the string.", visualization_step_order=0),
                NarrationSegment(order=1, text="The string is now reversed.", visualization_step_order=1),
            ]
        ).model_dump_json()
    )


def _fake_ffmpeg(command, cwd, timeout_seconds):
    # output path is always the last positional argument in MediaComposer's
    # fixed command list
    Path(command[-1]).write_bytes(b"final-composed-video")
    return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.05)


def test_full_phase4_pipeline_no_external_credentials(tmp_path: Path) -> None:
    code = load_java_fixture("correct")
    artifact_store = InMemoryArtifactStore()
    workflow = Workflow(
        "reverse-string-video",
        [
            CompileNode(),
            TraceNode(),
            ExplainNode(_explanation_provider(), provider_name="mock", model="mock"),
            VisualizationPlanNode(_visualization_provider(), provider_name="mock", model="mock"),
            NarrationNode(_narration_provider(), provider_name="mock", model="mock"),
            RenderVideoNode(FakeVideoRenderer(), output_dir=tmp_path),
            ComposeMediaNode(
                tts_provider=MockTTSProvider(),
                composer=MediaComposer(run_subprocess_fn=_fake_ffmpeg),
                output_dir=tmp_path,
            ),
            FinalValidationNode(),
        ],
    )
    runner = WorkflowRunner(artifact_store)
    state = Code2ShortsState(
        request=WorkflowRequest(
            topic="Reverse String",
            language=SupportedLanguage.JAVA,
            source_files=code.source_files,
            entry_point=code.entry_point,
            input_value="HELLO",
        ),
        metadata=WorkflowMetadata(workflow_id="reverse-string-video", execution_id=uuid.uuid4().hex),
    )

    result = runner.run(workflow, state)

    assert result.succeeded, result.state.errors
    assert result.state.validation.all_passed

    expected_stages = {
        "source", "compilation", "trace", "explanation",
        "visualization_plan", "narration", "rendered_video", "audio", "final_video",
    }
    assert result.state.artifact_ids.keys() == expected_stages

    # every artifact really exists, and the final one really has a checksum
    for artifact_id in result.state.artifact_ids.values():
        assert artifact_store.exists(artifact_id)

    final_artifact = artifact_store.get(result.state.artifact_ids["final_video"])
    assert final_artifact.type == ArtifactType.FINAL_VIDEO
    assert final_artifact.content["checksum"]
    assert Path(final_artifact.content["output_path"]).exists()

    # full lineage chain, source -> ... -> final_video
    lineage_order = [
        "source", "compilation", "trace", "explanation",
        "visualization_plan", "narration",
    ]
    for parent_key, child_key in zip(lineage_order, lineage_order[1:]):
        parent_id = result.state.artifact_ids[parent_key]
        child_id = result.state.artifact_ids[child_key]
        children = {c.id for c in artifact_store.list_children(parent_id)}
        assert child_id in children, f"{child_key} should be a child of {parent_key}"

    # rendered_video and narration both feed compose_media's two outputs
    rendered_video_id = result.state.artifact_ids["rendered_video"]
    narration_id = result.state.artifact_ids["narration"]
    audio_id = result.state.artifact_ids["audio"]
    final_id = result.state.artifact_ids["final_video"]
    assert audio_id in {c.id for c in artifact_store.list_children(narration_id)}
    assert final_id in {c.id for c in artifact_store.list_children(rendered_video_id)}
    assert final_id in {c.id for c in artifact_store.list_children(audio_id)}


def test_pipeline_stops_before_visualization_if_explanation_hallucinates() -> None:
    code = load_java_fixture("correct")
    artifact_store = InMemoryArtifactStore()
    hallucinating_provider = MockLLMProvider(
        canned_response=ExplanationResponse(
            summary="s", steps=[ExplanationStep(order=0, description="d", referenced_trace_event_indices=[999_999])]
        ).model_dump_json()
    )
    workflow = Workflow(
        "reverse-string-video",
        [CompileNode(), TraceNode(), ExplainNode(hallucinating_provider), VisualizationPlanNode(_visualization_provider())],
    )
    runner = WorkflowRunner(artifact_store)
    state = Code2ShortsState(
        request=WorkflowRequest(
            topic="t", language=SupportedLanguage.JAVA,
            source_files=code.source_files, entry_point=code.entry_point, input_value="HELLO",
        ),
        metadata=WorkflowMetadata(workflow_id="wf", execution_id=uuid.uuid4().hex),
    )

    result = runner.run(workflow, state)

    assert not result.succeeded
    assert result.failed_node == "explain"
    assert "visualization_plan" not in result.state.artifact_ids

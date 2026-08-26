"""PHASE 4.1 GOLDEN PATH — the only test in this repository that invokes
REAL Manim and REAL FFmpeg and inspects the resulting MP4.

    Java source -> Maven compile -> JUnit -> real execution -> ExecutionTrace
      -> VisualizationPlan (deterministic fixture, no live LLM)
      -> trusted ManimVideoRenderer -> REAL Manim -> real MP4
      -> real FFmpeg composition -> final playable MP4

Nothing about rendering or composition is mocked here. The LLM and speech
synthesis are the only substituted parts, exactly as Phase 4.1 permits:
the visualization plan is built deterministically from the REAL trace
(so it still cannot reference an event that did not happen), and audio is
real-but-silent (SyntheticTTSProvider) so that composition itself is
genuinely exercised.

Auto-skips when Manim/FFmpeg are absent so the normal developer workflow
(`pytest`, `pytest -m "not integration"`) never depends on them.
"""

from __future__ import annotations

import shutil
import subprocess
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
from code2shorts.core.models import ExecutionTrace, SupportedLanguage, TraceEventType, TraceStatus
from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java import JavaAdapter
from code2shorts.media import MediaComposer, probe_video
from code2shorts.narration import SyntheticTTSProvider
from code2shorts.visualization import ManimVideoRenderer, validate_visualization_plan
from code2shorts.workflow import (
    Code2ShortsState,
    CompileNode,
    ComposeMediaNode,
    ExplainNode,
    FinalValidationNode,
    NarrationNode,
    RenderVideoNode,
    TraceNode,
    Workflow,
    WorkflowMetadata,
    WorkflowRequest,
    WorkflowRunner,
)
from tests.java_fixtures import load_java_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.real_render,
    pytest.mark.skipif(
        shutil.which("manim") is None or shutil.which("ffmpeg") is None,
        reason="real render requires both manim and ffmpeg on PATH",
    ),
]

GOLDEN_INPUT = "HELLO"
GOLDEN_OUTPUT = "OLLEH"
EXPECTED_WIDTH = 1080
EXPECTED_HEIGHT = 1920


# ---------------------------------------------------------------------------
# Deterministic AI substitutes. Each is built FROM the real trace/plan it is
# given, so it can never reference an event that did not actually occur —
# the same invariant the real validators enforce against a live model.
# ---------------------------------------------------------------------------


def _explanation_for(trace: ExecutionTrace) -> str:
    indices = [event.step_index for event in trace.events]
    return ExplanationResponse(
        summary="Two pointers move toward each other, swapping characters until they meet.",
        learning_objectives=["two-pointer technique", "in-place array mutation"],
        steps=[
            ExplanationStep(
                order=0,
                description="The pointers start at both ends of the character array.",
                referenced_trace_event_indices=indices[:1],
            )
        ],
        referenced_trace_event_indices=indices[:1],
    ).model_dump_json()


def _plan_for(trace: ExecutionTrace) -> VisualizationPlanResponse:
    """Pick real, chronologically-ordered trace events and build a plan
    that references only those. Chooses genuinely interesting moments
    (the swaps) rather than the first N events, so the rendered video
    actually depicts the algorithm."""
    swaps = [e for e in trace.events if e.event_type == TraceEventType.ARRAY_WRITE.value]
    assert swaps, "reverse-string trace must contain ARRAY_WRITE events"

    chosen = [trace.events[0], swaps[0], swaps[-1], trace.events[-1]]
    actions = [
        VisualAction.INTRO,
        VisualAction.SWAP,
        VisualAction.SWAP,
        VisualAction.COMPLETION,
    ]
    narrations = [
        f"Reversing {GOLDEN_INPUT} with two pointers.",
        f"Swap: {swaps[0].description}",
        f"Swap: {swaps[-1].description}",
        f"Result: {GOLDEN_OUTPUT}",
    ]
    return VisualizationPlanResponse(
        lesson_title="Reverse a String in Java",
        steps=[
            VisualizationStepPlan(
                order=order,
                visual_action=action,
                trace_event_index=event.step_index,
                narration_text=text,
                duration_seconds=1.5,
            )
            for order, (event, action, text) in enumerate(zip(chosen, actions, narrations))
        ],
    )


def _narration_for(plan: VisualizationPlanResponse) -> str:
    return NarrationResponse(
        segments=[
            NarrationSegment(
                order=step.order, text=step.narration_text, visualization_step_order=step.order
            )
            for step in plan.steps
        ]
    ).model_dump_json()


def _extract_frames(video_path: Path, out_dir: Path, count: int = 4) -> list[Path]:
    """Pull representative frames out of the REAL rendered video with
    FFmpeg, so the video can be inspected rather than merely assumed
    correct because Manim exited 0."""
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata = probe_video(video_path)
    frames: list[Path] = []
    for index in range(count):
        # sample evenly across the video, avoiding the very first/last frame
        timestamp = metadata.duration_seconds * (index + 0.5) / count
        frame_path = out_dir / f"frame_{index}.png"
        completed = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [
                "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path),
                "-frames:v", "1", str(frame_path),
            ],
            capture_output=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-800:]
        frames.append(frame_path)
    return frames


def _frame_is_not_blank(frame_path: Path) -> bool:
    """A frame that is a single flat colour (all-black/all-white) means the
    render produced nothing visible at that timestamp."""
    from PIL import Image

    with Image.open(frame_path) as image:
        greyscale = image.convert("L")
        extrema = greyscale.getextrema()
    return extrema[1] - extrema[0] > 10  # some real contrast exists


@pytest.fixture(scope="module")
def golden_output_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("phase41_golden")


def test_golden_path_produces_a_real_playable_1080x1920_video(golden_output_dir: Path) -> None:
    code = load_java_fixture("correct")
    adapter = JavaAdapter()
    artifact_store = InMemoryArtifactStore()

    # ---- 1. Real Java validation: compile + JUnit + execute + trace -----
    with Workspace() as workspace:
        compile_result = adapter.compile(code, workspace)
        assert compile_result.succeeded, compile_result.stderr
    with Workspace() as workspace:
        test_result = adapter.test(code, workspace)
        assert test_result.succeeded, test_result.stdout + test_result.stderr
        assert test_result.tests_run > 0
        assert test_result.tests_passed == test_result.tests_run
    with Workspace() as workspace:
        trace = adapter.trace(code, workspace, GOLDEN_INPUT)

    assert trace.status == TraceStatus.COMPLETED
    assert trace.succeeded
    assert trace.output == GOLDEN_OUTPUT
    assert [e.step_index for e in trace.events] == list(range(len(trace.events)))

    # the trace really describes two-pointer reversal, not just "some events"
    event_types = {e.event_type for e in trace.events}
    assert TraceEventType.ARRAY_READ.value in event_types
    assert TraceEventType.ARRAY_WRITE.value in event_types
    assert TraceEventType.LOOP_ITERATION.value in event_types
    assert TraceEventType.CONDITION_EVALUATED.value in event_types
    assert any(e.variable_name == "left" for e in trace.events)
    assert any(e.variable_name == "right" for e in trace.events)

    # ---- 2. Visualization plan, validated against the REAL trace -------
    plan = _plan_for(trace)
    validation = validate_visualization_plan(plan, trace)
    assert validation.passed, validation.errors

    # every referenced index is a real trace event (belt and braces: this is
    # the trace/plan/video consistency invariant Phase 4.1 requires)
    real_indices = {e.step_index for e in trace.events}
    assert all(step.trace_event_index in real_indices for step in plan.steps)

    # ---- 3. REAL Manim render -------------------------------------------
    renderer = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=600.0)
    render_result = renderer.render(plan, golden_output_dir / "render")

    video_path = Path(render_result.output_path)
    assert video_path.is_file()
    assert "partial_movie_files" not in video_path.parts

    # ---- 4. Inspect the ACTUAL file, don't trust the exit code ----------
    metadata = probe_video(video_path)
    assert metadata.width == EXPECTED_WIDTH
    assert metadata.height == EXPECTED_HEIGHT
    assert metadata.duration_seconds > 0
    assert metadata.frame_count > 0
    assert metadata.codec == "h264"

    # renderer metadata must agree with reality (the Phase 4 estimate did not)
    assert render_result.resolution == f"{EXPECTED_WIDTH}x{EXPECTED_HEIGHT}"
    assert render_result.duration_seconds == pytest.approx(metadata.duration_seconds, abs=0.2)
    assert render_result.metadata["probed"] is True

    # ---- 5. Representative frames are real, non-blank images ------------
    frames = _extract_frames(video_path, golden_output_dir / "frames", count=4)
    assert len(frames) == 4
    for frame in frames:
        assert frame.is_file() and frame.stat().st_size > 0
        assert _frame_is_not_blank(frame), f"{frame.name} is blank/flat — nothing rendered"

    # ---- 6. REAL FFmpeg composition -------------------------------------
    tts = SyntheticTTSProvider()
    audio_result = tts.synthesize("Reversing HELLO with two pointers.", golden_output_dir / "narration")
    composer = MediaComposer(timeout_seconds=180.0)
    final_path = golden_output_dir / "final" / "final.mp4"
    compose_result = composer.compose(
        video_path, Path(audio_result.audio_path), final_path, metadata.duration_seconds
    )

    # ---- 7. Final MP4 is real, playable, still 1080x1920 ----------------
    final_metadata = probe_video(Path(compose_result.output_path))
    assert final_metadata.width == EXPECTED_WIDTH
    assert final_metadata.height == EXPECTED_HEIGHT
    assert final_metadata.duration_seconds > 0
    assert final_metadata.frame_count > 0
    assert compose_result.checksum


def test_golden_path_through_the_real_workflow_with_full_lineage(
    golden_output_dir: Path,
) -> None:
    """Same real render, but driven end-to-end by the actual Phase 3/4
    WorkflowRunner, so artifact lineage is verified on the real pipeline
    rather than on a hand-assembled sequence."""
    code = load_java_fixture("correct")
    artifact_store = InMemoryArtifactStore()

    # The plan/narration providers need the real trace, which only exists
    # once TraceNode has run — so they read it back out of the prompt the
    # nodes build, exactly like a real model would.
    def explanation_response(prompt: str) -> str:
        indices = [int(line.split("]")[0].strip(" [")) for line in prompt.splitlines() if line.strip().startswith("[")]
        return ExplanationResponse(
            summary="Two pointers swap characters until they meet.",
            steps=[
                ExplanationStep(
                    order=0, description="pointers start at both ends",
                    referenced_trace_event_indices=indices[:1],
                )
            ],
            referenced_trace_event_indices=indices[:1],
        ).model_dump_json()

    captured_plan: dict[str, VisualizationPlanResponse] = {}

    def plan_response(prompt: str) -> str:
        indices = [int(line.split("]")[0].strip(" [")) for line in prompt.splitlines() if line.strip().startswith("[")]
        plan = VisualizationPlanResponse(
            lesson_title="Reverse a String in Java",
            steps=[
                VisualizationStepPlan(
                    order=0, visual_action=VisualAction.INTRO, trace_event_index=indices[0],
                    narration_text="Reversing HELLO with two pointers.", duration_seconds=1.5,
                ),
                VisualizationStepPlan(
                    order=1, visual_action=VisualAction.COMPLETION, trace_event_index=indices[-1],
                    narration_text="Result: OLLEH", duration_seconds=1.5,
                ),
            ],
        )
        captured_plan["plan"] = plan
        return plan.model_dump_json()

    def narration_response(prompt: str) -> str:
        return _narration_for(captured_plan["plan"])

    workflow = Workflow(
        "reverse-string-real-render",
        [
            CompileNode(),
            TraceNode(),
            ExplainNode(MockLLMProvider(canned_response=explanation_response)),
            NarrationNode(MockLLMProvider(canned_response=narration_response)),
            RenderVideoNode(
                ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=600.0),
                output_dir=golden_output_dir / "workflow",
            ),
            ComposeMediaNode(
                tts_provider=SyntheticTTSProvider(),
                composer=MediaComposer(timeout_seconds=180.0),
                output_dir=golden_output_dir / "workflow",
            ),
            FinalValidationNode(),
        ],
    )
    # VisualizationPlanNode must sit between explain and narration
    from code2shorts.workflow import VisualizationPlanNode

    workflow.nodes.insert(3, VisualizationPlanNode(MockLLMProvider(canned_response=plan_response)))

    runner = WorkflowRunner(artifact_store)
    state = Code2ShortsState(
        request=WorkflowRequest(
            topic="Reverse a String in Java",
            language=SupportedLanguage.JAVA,
            source_files=code.source_files,
            entry_point=code.entry_point,
            input_value=GOLDEN_INPUT,
        ),
        metadata=WorkflowMetadata(
            workflow_id="reverse-string-real-render", execution_id=uuid.uuid4().hex
        ),
    )

    result = runner.run(workflow, state)
    assert result.succeeded, result.state.errors
    assert result.state.validation.all_passed

    # ---- full artifact lineage, verified by parent/child relationships --
    ids = result.state.artifact_ids
    expected_stages = {
        "source", "compilation", "trace", "explanation",
        "visualization_plan", "narration", "rendered_video", "audio", "final_video",
    }
    assert ids.keys() == expected_stages

    chain = ["source", "compilation", "trace", "explanation", "visualization_plan", "narration"]
    for parent_key, child_key in zip(chain, chain[1:]):
        children = {c.id for c in artifact_store.list_children(ids[parent_key])}
        assert ids[child_key] in children, f"{child_key} must descend from {parent_key}"

    assert ids["audio"] in {c.id for c in artifact_store.list_children(ids["narration"])}
    assert ids["final_video"] in {c.id for c in artifact_store.list_children(ids["rendered_video"])}
    assert ids["final_video"] in {c.id for c in artifact_store.list_children(ids["audio"])}

    # ---- the final artifact is a real, playable 1080x1920 video ---------
    final_artifact = artifact_store.get(ids["final_video"])
    assert final_artifact.type == ArtifactType.FINAL_VIDEO
    final_metadata = probe_video(Path(final_artifact.content["output_path"]))
    assert (final_metadata.width, final_metadata.height) == (EXPECTED_WIDTH, EXPECTED_HEIGHT)
    assert final_metadata.duration_seconds > 0
    assert final_metadata.frame_count > 0


def test_golden_path_is_repeatable(golden_output_dir: Path) -> None:
    """Run the render twice into separate workspaces: the second run must
    not corrupt or depend on the first. Byte-identical MP4s are NOT
    required (encoders embed timestamps); identical LOGICAL output is."""
    code = load_java_fixture("correct")
    adapter = JavaAdapter()

    with Workspace() as workspace:
        trace_a = adapter.trace(code, workspace, GOLDEN_INPUT)
    with Workspace() as workspace:
        trace_b = adapter.trace(code, workspace, GOLDEN_INPUT)

    # same input -> same logical trace (Phase 2's determinism guarantee)
    assert [e.model_dump(exclude={"step_index"}) for e in trace_a.events] == [
        e.model_dump(exclude={"step_index"}) for e in trace_b.events
    ]

    plan_a = _plan_for(trace_a)
    plan_b = _plan_for(trace_b)
    assert plan_a == plan_b  # same trace -> same logical visualization

    renderer = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=600.0)
    result_a = renderer.render(plan_a, golden_output_dir / "repeat_a")
    result_b = renderer.render(plan_b, golden_output_dir / "repeat_b")

    meta_a = probe_video(Path(result_a.output_path))
    meta_b = probe_video(Path(result_b.output_path))

    assert (meta_a.width, meta_a.height) == (meta_b.width, meta_b.height) == (EXPECTED_WIDTH, EXPECTED_HEIGHT)
    assert meta_a.frame_count == meta_b.frame_count
    assert meta_a.duration_seconds == pytest.approx(meta_b.duration_seconds, abs=0.05)
    # first run's output still intact after the second run
    assert Path(result_a.output_path).is_file()

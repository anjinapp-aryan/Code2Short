"""PHASE 4.5 PRODUCTION E2E GATE.

Real Java -> real compile -> real JUnit -> real execution -> real trace ->
real visualization plan -> REAL Manim -> real TTS -> real SRT -> real
FFmpeg -> validated MP4, for all five golden-path algorithms.

No fake renderer. No mocked media. The only substitution is the LLM, which
Phase 4.5 deliberately excludes (deterministic fixtures instead) — Gemini
belongs to Phase 5.

Auto-skips only when a required external tool is genuinely absent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from code2shorts.ai.contracts import (
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import ExecutionTrace, TraceEventType, TraceStatus
from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java import JavaAdapter
from code2shorts.media import MediaComposer, probe_video
from code2shorts.media.validation import (
    MediaPolicy,
    validate_final_video,
    validate_timeline,
)
from code2shorts.narration import (
    SapiTTSProvider,
    SyntheticTTSProvider,
    align_narration,
    validate_alignment,
    validate_srt,
)
from code2shorts.narration.subtitles import build_srt, write_srt
from code2shorts.visualization import (
    ManimVideoRenderer,
    RenderContext,
    reconstruct_frames,
    validate_visualization_plan,
)
from code2shorts.visualization.code_state import (
    build_code_state,
    resolve_source_locations,
)
from tests.java_fixtures import load_algorithm_fixture, load_java_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.real_render,
    pytest.mark.skipif(
        shutil.which("manim") is None
        or shutil.which("ffmpeg") is None
        or shutil.which("mvn") is None
        or shutil.which("java") is None,
        reason="E2E gate needs manim, ffmpeg, mvn and java on PATH",
    ),
]

# (loader kind, variant, program arg, expected stdout)
ALGORITHMS = [
    ("reverse_string", "correct", "HELLO", "OLLEH"),
    ("algorithm", "palindrome", "RACECAR", "true"),
    ("algorithm", "two_sum", "9", "0,1"),
    ("algorithm", "move_zeroes", "", "1,3,12,0,0"),
    ("algorithm", "remove_duplicates", "", "3"),
]

STEP_DURATION = 4.0  # sized to fit real narration; see PHASE_4_4 notes


def _load(kind: str, variant: str):
    return load_java_fixture(variant) if kind == "reverse_string" else load_algorithm_fixture(variant)


def _plan_from_trace(trace: ExecutionTrace, title: str) -> VisualizationPlanResponse:
    """Deterministic planner: pick real, chronologically ordered events.
    Algorithm-agnostic — no branching on which algorithm this is."""
    touched = [
        e
        for e in trace.events
        if e.event_type in (TraceEventType.ARRAY_READ.value, TraceEventType.ARRAY_WRITE.value)
    ]
    chosen = [trace.events[0]]
    chosen += touched[:2] if len(touched) >= 2 else touched
    chosen.append(trace.events[-1])
    # keep chronological, drop duplicates
    seen, ordered = set(), []
    for event in sorted(chosen, key=lambda e: e.step_index):
        if event.step_index not in seen:
            seen.add(event.step_index)
            ordered.append(event)

    actions = [VisualAction.INTRO, VisualAction.ARRAY_ACCESS, VisualAction.SWAP, VisualAction.COMPLETION]
    return VisualizationPlanResponse(
        lesson_title=title,
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=actions[min(i, len(actions) - 1)],
                trace_event_index=event.step_index,
                narration_text=f"Step {i + 1}. {event.description}"[:90],
                duration_seconds=STEP_DURATION,
            )
            for i, event in enumerate(ordered)
        ],
    )


@pytest.fixture(scope="module")
def e2e_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("phase45_e2e")


@pytest.mark.parametrize(("kind", "variant", "arg", "expected"), ALGORITHMS)
def test_full_production_pipeline_per_algorithm(
    kind: str, variant: str, arg: str, expected: str, e2e_dir: Path
) -> None:
    out = e2e_dir / variant
    out.mkdir(parents=True, exist_ok=True)
    code = _load(kind, variant)
    adapter = JavaAdapter()

    # ---- 1. real compile -------------------------------------------------
    with Workspace() as ws:
        compiled = adapter.compile(code, ws)
    assert compiled.succeeded, compiled.stderr

    # ---- 2. real JUnit (only the reverse_string fixture ships tests) -----
    if kind == "reverse_string":
        with Workspace() as ws:
            tested = adapter.test(code, ws)
        assert tested.succeeded, tested.stdout + tested.stderr
        assert tested.tests_run > 0 and tested.tests_passed == tested.tests_run

    # ---- 3/4. real execution + trace -------------------------------------
    with Workspace() as ws:
        trace = adapter.trace(code, ws, arg)
    assert trace.status is TraceStatus.COMPLETED
    assert trace.output == expected, f"{variant}: got {trace.output!r}"
    assert [e.step_index for e in trace.events] == list(range(len(trace.events)))
    assert trace.trace_schema_version >= 2

    # ---- 5. FrameState reconstruction ------------------------------------
    frames = reconstruct_frames(trace)
    assert len(frames) == len(trace.events)
    with_array = [f for f in frames if f.primary_array and f.primary_array.cells]
    assert with_array, f"{variant}: no array state reconstructed"
    assert any(f.pointers for f in frames), f"{variant}: no pointers derived"

    # ---- 6. visualization plan validated against the REAL trace ----------
    plan = _plan_from_trace(trace, variant.replace("_", " ").title())
    plan_validation = validate_visualization_plan(plan, trace)
    assert plan_validation.passed, plan_validation.errors
    real_indices = {e.step_index for e in trace.events}
    assert all(s.trace_event_index in real_indices for s in plan.steps)

    # ---- 7. code synchronization points at the real file/line ------------
    locations = resolve_source_locations(trace, code.source_files, code.entry_point)
    checked = 0
    for event in trace.events:
        location = locations[event.step_index]
        if location.line is None or location.file is None:
            continue
        state = build_code_state(location, code.source_files, window_radius=6)
        assert state.highlight_offset is not None
        shown = state.lines[state.highlight_offset]
        real = code.source_files[location.file].splitlines()[location.line - 1]
        assert shown == real, f"{variant}: highlight != real source at {location.file}:{location.line}"
        checked += 1
    assert checked > 0

    # ---- 8. narration mapped to valid visualization steps ----------------
    narration = NarrationResponse(
        segments=[
            NarrationSegment(order=s.order, text=s.narration_text, visualization_step_order=s.order)
            for s in plan.steps
        ]
    )

    tts = SapiTTSProvider() if SapiTTSProvider.is_available() else SyntheticTTSProvider()
    audio_by_segment: dict[int, tuple[str, float]] = {}
    for segment in narration.segments:
        result = tts.synthesize(segment.text, out / f"seg_{segment.order}")
        assert Path(result.audio_path).is_file()
        assert result.duration_seconds > 0
        audio_by_segment[segment.order] = (result.audio_path, result.duration_seconds)

    alignment = align_narration(narration, plan, audio_by_segment=audio_by_segment)
    assert validate_alignment(alignment, plan).passed, validate_alignment(alignment, plan).errors

    # ---- 9. subtitles ----------------------------------------------------
    srt_path = write_srt(alignment, out / "subs.srt")
    srt_validation = validate_srt(srt_path.read_text(encoding="utf-8"))
    assert srt_validation.passed, srt_validation.errors

    # ---- 10. REAL Manim render -------------------------------------------
    renderer = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=600.0)
    context = RenderContext(
        trace=trace, source_files=dict(code.source_files), entry_point=code.entry_point
    )
    render = renderer.render(plan, out / "render", context)
    rendered_path = Path(render.output_path)
    assert rendered_path.is_file()
    assert "partial_movie_files" not in rendered_path.parts

    video_meta = probe_video(rendered_path)
    assert (video_meta.width, video_meta.height) == (1080, 1920)
    assert video_meta.codec == "h264"
    assert video_meta.frame_count > 0

    # ---- 11. REAL FFmpeg composition -------------------------------------
    combined = _concat_audio(alignment, out / "narration.wav")
    final_path = out / "final.mp4"
    MediaComposer(timeout_seconds=300.0).compose(
        rendered_path, combined, final_path, video_meta.duration_seconds
    )

    # ---- 12. media policy validation on the REAL final artifact ----------
    report = validate_final_video(
        final_path,
        policy=MediaPolicy(),
        expected_duration_seconds=video_meta.duration_seconds,
    )
    assert report.passed, f"{variant}: {report.errors}"

    timeline = validate_timeline(alignment, probe_video(final_path).duration_seconds)
    assert timeline.passed, timeline.errors


def _concat_audio(alignment, output_path: Path) -> Path:
    """Place each segment at its aligned start. Fixed argument list."""
    from code2shorts.execution.sandbox import run_subprocess

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths = [Path(s.audio_path).resolve() for s in alignment.segments]

    command = ["ffmpeg", "-y"]
    for path in paths:
        command += ["-i", str(path)]
    filters = [
        f"[{i}:a]adelay={int(s.start_seconds * 1000)}|{int(s.start_seconds * 1000)}[a{i}]"
        for i, s in enumerate(alignment.segments)
    ]
    mix = "".join(f"[a{i}]" for i in range(len(paths)))
    filters.append(f"{mix}amix=inputs={len(paths)}:normalize=0[out]")
    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-t", f"{alignment.total_duration_seconds:.3f}",
        str(output_path),
    ]
    result = run_subprocess(command, cwd=output_path.parent, timeout_seconds=180.0)
    assert not result.timed_out and result.returncode == 0, result.stderr[-1200:]
    return output_path


def test_no_algorithm_specific_branching_in_the_visualization_engine() -> None:
    """The renderer must stay generic — this fails if anyone special-cases
    an algorithm inside the visualization package."""
    package = Path(__file__).resolve().parents[1] / "src" / "code2shorts" / "visualization"
    forbidden = [
        "reverse_string", "reverseString", "two_sum", "twoSum", "palindrome",
        "move_zeroes", "moveZeroes", "remove_duplicates", "removeDuplicates",
    ]
    offenders = [
        f"{path.name}: {name}"
        for path in package.rglob("*.py")
        for name in forbidden
        if name in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"algorithm-specific logic leaked into the renderer: {offenders}"

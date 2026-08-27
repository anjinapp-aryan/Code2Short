"""Phase 6 golden path: real Java collections -> real trace -> real MP4.

    python scripts/run_phase6_golden_path.py --algorithm two_sum_map

Deterministic end to end: no LLM is involved. The visualization plan is
built directly from the OBSERVED trace, so every frame is grounded by
construction — which is exactly what Phase 6 needs to prove. Phase 5.3
already established the LLM planning layer; repeating it here would only
add nondeterminism to a structural verification.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from code2shorts.ai.contracts import (  # noqa: E402
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import SupportedLanguage, TraceEventType  # noqa: E402
from code2shorts.execution.sandbox import Workspace  # noqa: E402
from code2shorts.langadapter.java import JavaAdapter  # noqa: E402
from code2shorts.media.probe import probe_video  # noqa: E402
from code2shorts.media.validation import MediaPolicy, validate_final_video  # noqa: E402
from code2shorts.visualization import ManimVideoRenderer, RenderContext  # noqa: E402
from code2shorts.visualization.state import reconstruct_frames  # noqa: E402
from tests.java_fixtures import load_algorithm_fixture  # noqa: E402

ALGORITHMS = {
    "binary_search": ("9", "4"),
    "two_sum_map": ("26", "2,3"),
    "balanced_parens": ("(())", "true"),
    "task_queue": ("3", "6"),
}
STEP_SECONDS = 2.5

#: Events worth a frame. Structural, not algorithmic: a collection
#: mutation, an array write, or a scalar change is a state transition the
#: viewer should see.
INTERESTING = {
    TraceEventType.COLLECTION_MUTATION.value,
    TraceEventType.ARRAY_WRITE.value,
    TraceEventType.ARRAY_READ.value,
    TraceEventType.VARIABLE_ASSIGN.value,
    TraceEventType.PROGRAM_START.value,
    TraceEventType.PROGRAM_END.value,
}


def build_plan_from_trace(trace, title: str) -> VisualizationPlanResponse:
    """One step per observed state transition. No LLM, no invention."""
    steps: list[VisualizationStepPlan] = []
    for event in trace.events:
        if event.event_type not in INTERESTING:
            continue
        if event.event_type == TraceEventType.COLLECTION_MUTATION.value:
            action = VisualAction.DATA_STRUCTURE_UPDATE
        elif event.event_type == TraceEventType.ARRAY_WRITE.value:
            action = VisualAction.SWAP
        elif event.event_type == TraceEventType.ARRAY_READ.value:
            action = VisualAction.ARRAY_ACCESS
        elif event.event_type == TraceEventType.PROGRAM_START.value:
            action = VisualAction.INTRO
        elif event.event_type == TraceEventType.PROGRAM_END.value:
            action = VisualAction.COMPLETION
        else:
            action = VisualAction.VARIABLE_UPDATE
        steps.append(
            VisualizationStepPlan(
                order=len(steps),
                visual_action=action,
                trace_event_index=event.step_index,
                narration_text=event.description,
                duration_seconds=STEP_SECONDS,
            )
        )
    return VisualizationPlanResponse(lesson_title=title, steps=steps)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--algorithm", default="two_sum_map", choices=sorted(ALGORITHMS))
    parser.add_argument("--output-dir", type=Path, default=Path("output/phase6"))
    args = parser.parse_args()

    for tool in ("mvn", "java", "manim", "ffmpeg"):
        if shutil.which(tool) is None:
            print(f"ERROR: {tool!r} not on PATH", file=sys.stderr)
            return 2

    out = args.output_dir / args.algorithm
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    argument, expected = ALGORITHMS[args.algorithm]
    code = load_algorithm_fixture(args.algorithm)
    timings: dict[str, float] = {}

    started = time.perf_counter()
    workspace = Workspace()
    trace = JavaAdapter().trace(code, workspace, input_value=argument)
    timings["compile+execute+trace"] = time.perf_counter() - started

    if not trace.succeeded:
        print(f"TRACE FAILED exit={trace.exit_code}\n{trace.stderr[:600]}", file=sys.stderr)
        workspace.cleanup()
        return 1

    print(f"trace: {len(trace.events)} events, {argument!r} -> {trace.output!r} "
          f"(expected {expected!r})")
    if trace.output.strip() != expected:
        print("ERROR: execution output mismatch", file=sys.stderr)
        workspace.cleanup()
        return 1

    started = time.perf_counter()
    frames = reconstruct_frames(trace)
    timings["snapshot_reconstruction"] = time.perf_counter() - started

    mutations = [e for e in trace.events
                 if e.event_type == TraceEventType.COLLECTION_MUTATION.value]
    print(f"collection mutations observed: {len(mutations)}")
    for event in mutations:
        frame = frames[event.step_index]
        snapshot = frame.primary_map or frame.primary_sequence
        if snapshot is None:
            print(f"  step {event.step_index}: NOT REPRESENTED "
                  f"(kind={event.collection_kind}) — validation should fail")
            continue
        if frame.primary_map is not None:
            print(f"  step {event.step_index:>3} line {event.line_number:>3} "
                  f"{snapshot.last_operation:<8} map ordered={snapshot.ordered} "
                  f"{snapshot.entries}")
        else:
            print(f"  step {event.step_index:>3} line {event.line_number:>3} "
                  f"{snapshot.last_operation:<8} seq end={snapshot.active_end} "
                  f"{snapshot.elements}")

    started = time.perf_counter()
    plan = build_plan_from_trace(trace, args.algorithm.replace("_", " ").title())
    timings["planning"] = time.perf_counter() - started
    print(f"plan: {len(plan.steps)} steps (one per observed state transition)")

    started = time.perf_counter()
    render = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=900.0).render(
        plan,
        out / "render",
        RenderContext(
            trace=trace,
            source_files=dict(code.source_files),
            entry_point=code.entry_point,
        ),
    )
    timings["manim"] = time.perf_counter() - started

    video = probe_video(Path(render.output_path))
    # Visual-only render: narration/audio composition is Phase 4.4 and is
    # not what Phase 6 verifies, so audio is not required here.
    report = validate_final_video(
        Path(render.output_path),
        policy=MediaPolicy(require_audio=False, audio_codec=None),
        expected_duration_seconds=video.duration_seconds,
    )
    print(f"video: {video.width}x{video.height} {video.codec} "
          f"{video.duration_seconds:.2f}s {video.frame_count} frames")
    print(f"media validation: {'PASS' if report.passed else 'FAIL ' + str(report.errors)}")
    print("timings:", {k: round(v, 2) for k, v in timings.items()})
    print(f"\nfinal artifact: {Path(render.output_path).resolve()}")

    workspace.cleanup()
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

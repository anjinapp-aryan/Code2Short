"""Phase 4.1 golden path: produce ONE real, playable 1080x1920 MP4 from
the real pipeline, and print a performance breakdown.

    python scripts/run_golden_path.py [--output-dir output/golden]

Requires Manim and FFmpeg on PATH (and Maven + a JDK, as every phase since
Phase 1 has). This is a developer/verification entry point, not part of
the library: the same code paths are exercised by
tests/test_real_render.py, which is the authoritative gate.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code2shorts.ai.contracts import (  # noqa: E402
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import ExecutionTrace, TraceEventType, TraceStatus  # noqa: E402
from code2shorts.execution.sandbox import Workspace  # noqa: E402
from code2shorts.langadapter.java import JavaAdapter  # noqa: E402
from code2shorts.media import MediaComposer, probe_video  # noqa: E402
from code2shorts.narration import SyntheticTTSProvider  # noqa: E402
from code2shorts.visualization import (  # noqa: E402
    ManimVideoRenderer,
    RenderContext,
    validate_visualization_plan,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.java_fixtures import load_java_fixture  # noqa: E402

GOLDEN_INPUT = "HELLO"
GOLDEN_OUTPUT = "OLLEH"


def _build_plan(trace: ExecutionTrace) -> VisualizationPlanResponse:
    """Deterministic stand-in for the AI planner, built FROM the real
    trace — it cannot reference an event that did not happen."""
    swaps = [e for e in trace.events if e.event_type == TraceEventType.ARRAY_WRITE.value]
    chosen = [trace.events[0], swaps[0], swaps[-1], trace.events[-1]]
    actions = [VisualAction.INTRO, VisualAction.SWAP, VisualAction.SWAP, VisualAction.COMPLETION]
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/golden", type=Path)
    parser.add_argument("--frames", type=int, default=4, help="frames to extract for inspection")
    args = parser.parse_args()

    for tool in ("manim", "ffmpeg", "mvn", "java"):
        if shutil.which(tool) is None:
            print(f"ERROR: {tool!r} not found on PATH", file=sys.stderr)
            return 2

    output_dir: Path = args.output_dir
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    total_start = time.perf_counter()
    code = load_java_fixture("correct")
    adapter = JavaAdapter()

    start = time.perf_counter()
    with Workspace() as workspace:
        compile_result = adapter.compile(code, workspace)
    timings["java_compile"] = time.perf_counter() - start
    if not compile_result.succeeded:
        print("ERROR: Java compilation failed", file=sys.stderr)
        return 1

    start = time.perf_counter()
    with Workspace() as workspace:
        test_result = adapter.test(code, workspace)
    timings["junit_tests"] = time.perf_counter() - start
    if not test_result.succeeded:
        print("ERROR: JUnit tests failed", file=sys.stderr)
        return 1
    print(f"JUnit: {test_result.tests_passed}/{test_result.tests_run} passed")

    start = time.perf_counter()
    with Workspace() as workspace:
        trace = adapter.trace(code, workspace, GOLDEN_INPUT)
    timings["execute_and_trace"] = time.perf_counter() - start
    if trace.status is not TraceStatus.COMPLETED or trace.output != GOLDEN_OUTPUT:
        print(f"ERROR: trace failed: status={trace.status} output={trace.output!r}", file=sys.stderr)
        return 1
    print(f"Trace: {len(trace.events)} events, {GOLDEN_INPUT} -> {trace.output}")

    plan = _build_plan(trace)
    validation = validate_visualization_plan(plan, trace)
    if not validation.passed:
        print(f"ERROR: visualization plan rejected: {validation.errors}", file=sys.stderr)
        return 1
    print(f"VisualizationPlan: {len(plan.steps)} steps, validated against real trace")

    start = time.perf_counter()
    renderer = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=600.0)
    render_context = RenderContext(
        trace=trace,
        source_files=dict(code.source_files),
        entry_point=code.entry_point,
        lesson_title=plan.lesson_title,
    )
    render_result = renderer.render(plan, output_dir / "render", render_context)
    timings["manim_render"] = time.perf_counter() - start
    rendered = Path(render_result.output_path)
    rendered_meta = probe_video(rendered)
    print(
        f"Manim render: {rendered_meta.width}x{rendered_meta.height} "
        f"{rendered_meta.codec} {rendered_meta.duration_seconds:.2f}s "
        f"{rendered_meta.frame_count} frames"
    )

    start = time.perf_counter()
    audio = SyntheticTTSProvider().synthesize(
        " ".join(step.narration_text for step in plan.steps), output_dir / "narration"
    )
    timings["tts_synthetic_audio"] = time.perf_counter() - start

    start = time.perf_counter()
    final_path = output_dir / "final.mp4"
    compose_result = MediaComposer(timeout_seconds=180.0).compose(
        rendered, Path(audio.audio_path), final_path, rendered_meta.duration_seconds
    )
    timings["ffmpeg_compose"] = time.perf_counter() - start

    final_meta = probe_video(Path(compose_result.output_path))
    print(
        f"Final MP4: {final_meta.width}x{final_meta.height} {final_meta.codec} "
        f"{final_meta.duration_seconds:.2f}s {final_meta.frame_count} frames"
    )

    frames_dir = output_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    for index in range(args.frames):
        timestamp = final_meta.duration_seconds * (index + 0.5) / args.frames
        subprocess.run(  # noqa: S603 - fixed argument list, no shell
            ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(final_path),
             "-frames:v", "1", str(frames_dir / f"frame_{index}.png")],
            capture_output=True, timeout=60, check=True,
        )
    print(f"Frames for inspection: {frames_dir}")

    timings["total"] = time.perf_counter() - total_start
    print("\nPerformance (seconds):")
    for name, seconds in timings.items():
        print(f"  {name:24s} {seconds:8.2f}")

    print(f"\nFinal artifact: {final_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 4.4 golden path: real Java -> trace -> visuals -> REAL speech ->
subtitles -> real FFmpeg mux -> final MP4, with every media property
probed rather than assumed.

    python scripts/run_narrated_golden_path.py [--output-dir output/narrated]

Requires Manim, FFmpeg, Maven, a JDK, and (for real speech) Windows.
Falls back to SyntheticTTSProvider when SAPI is unavailable, and says so.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from code2shorts.ai.contracts import (  # noqa: E402
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import ExecutionTrace, TraceEventType, TraceStatus  # noqa: E402
from code2shorts.execution.sandbox import Workspace  # noqa: E402
from code2shorts.langadapter.java import JavaAdapter  # noqa: E402
from code2shorts.media import MediaComposer, probe_audio, probe_video  # noqa: E402
from code2shorts.narration import (  # noqa: E402
    SapiTTSProvider,
    SyntheticTTSProvider,
    align_narration,
    validate_alignment,
    validate_srt,
    write_srt,
)
from code2shorts.narration.subtitles import build_srt  # noqa: E402
from code2shorts.visualization import (  # noqa: E402
    ManimVideoRenderer,
    RenderContext,
    validate_visualization_plan,
)
from tests.java_fixtures import load_java_fixture  # noqa: E402

GOLDEN_INPUT = "HELLO"
GOLDEN_OUTPUT = "OLLEH"


def _build_plan(trace: ExecutionTrace) -> VisualizationPlanResponse:
    swaps = [e for e in trace.events if e.event_type == TraceEventType.ARRAY_WRITE.value]
    chosen = [trace.events[0], swaps[0], swaps[-1], trace.events[-1]]
    actions = [VisualAction.INTRO, VisualAction.SWAP, VisualAction.SWAP, VisualAction.COMPLETION]
    narrations = [
        f"We reverse the string {GOLDEN_INPUT} using two pointers.",
        "The left and right pointers swap their characters.",
        "The pointers move inward and swap again.",
        f"The pointers meet, and the result is {GOLDEN_OUTPUT}.",
    ]
    return VisualizationPlanResponse(
        lesson_title="Reverse a String in Java",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=action,
                trace_event_index=event.step_index,
                narration_text=text,
                # Sized from MEASURED speech, not guessed: SAPI produced
                # 3.07-3.41s for these lines, so 4.0s leaves headroom. The
                # visual timeline stays authoritative - narration is made to
                # fit the plan, never the plan stretched to fit narration.
                duration_seconds=4.0,
            )
            for i, (event, action, text) in enumerate(zip(chosen, actions, narrations))
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output/narrated", type=Path)
    args = parser.parse_args()

    for tool in ("manim", "ffmpeg", "mvn", "java"):
        if shutil.which(tool) is None:
            print(f"ERROR: {tool!r} not on PATH", file=sys.stderr)
            return 2

    out = args.output_dir
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    total_start = time.perf_counter()
    code = load_java_fixture("correct")
    adapter = JavaAdapter()

    start = time.perf_counter()
    with Workspace() as ws:
        compiled = adapter.compile(code, ws)
    timings["java_compile"] = time.perf_counter() - start
    if not compiled.succeeded:
        print("ERROR: compile failed", file=sys.stderr)
        return 1

    start = time.perf_counter()
    with Workspace() as ws:
        tested = adapter.test(code, ws)
    timings["junit"] = time.perf_counter() - start
    if not tested.succeeded:
        print("ERROR: JUnit failed", file=sys.stderr)
        return 1
    print(f"JUnit: {tested.tests_passed}/{tested.tests_run} passed")

    start = time.perf_counter()
    with Workspace() as ws:
        trace = adapter.trace(code, ws, GOLDEN_INPUT)
    timings["execute_trace"] = time.perf_counter() - start
    if trace.status is not TraceStatus.COMPLETED or trace.output != GOLDEN_OUTPUT:
        print(f"ERROR: trace {trace.status} output={trace.output!r}", file=sys.stderr)
        return 1
    print(f"Trace: {len(trace.events)} events, schema v{trace.trace_schema_version}")

    plan = _build_plan(trace)
    if not validate_visualization_plan(plan, trace).passed:
        print("ERROR: plan rejected", file=sys.stderr)
        return 1

    narration = NarrationResponse(
        segments=[
            NarrationSegment(order=s.order, text=s.narration_text, visualization_step_order=s.order)
            for s in plan.steps
        ]
    )

    # ---- REAL speech -----------------------------------------------------
    if SapiTTSProvider.is_available():
        tts = SapiTTSProvider()
        print("TTS: Windows SAPI (real speech)")
    else:
        tts = SyntheticTTSProvider()
        print("TTS: synthetic silence (SAPI unavailable on this platform)")

    start = time.perf_counter()
    audio_dir = out / "audio"
    audio_by_segment: dict[int, tuple[str, float]] = {}
    for segment in narration.segments:
        result = tts.synthesize(segment.text, audio_dir / f"seg_{segment.order}")
        audio_by_segment[segment.order] = (result.audio_path, result.duration_seconds)
        meta = probe_audio(Path(result.audio_path))
        print(
            f"  seg {segment.order}: {meta.duration_seconds:.2f}s "
            f"{meta.codec} {meta.sample_rate}Hz ch={meta.channels}"
        )
    timings["tts"] = time.perf_counter() - start

    # ---- alignment + subtitles -------------------------------------------
    alignment = align_narration(narration, plan, audio_by_segment=audio_by_segment)
    alignment_validation = validate_alignment(alignment, plan)
    print(
        f"Alignment: {len(alignment.segments)} segments, "
        f"timeline {alignment.total_duration_seconds:.2f}s, "
        f"overflow={alignment.overflow_count} "
        f"({'OK' if alignment_validation.passed else 'WARN: ' + '; '.join(alignment_validation.errors)})"
    )

    srt_path = write_srt(alignment, out / "subtitles.srt")
    srt_validation = validate_srt(srt_path.read_text(encoding="utf-8"))
    print(f"Subtitles: {srt_path.name} valid={srt_validation.passed}")

    # ---- render ----------------------------------------------------------
    start = time.perf_counter()
    renderer = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=600.0)
    context = RenderContext(
        trace=trace, source_files=dict(code.source_files), entry_point=code.entry_point
    )
    render = renderer.render(plan, out / "render", context)
    timings["manim"] = time.perf_counter() - start
    video_meta = probe_video(Path(render.output_path))
    print(
        f"Video: {video_meta.width}x{video_meta.height} {video_meta.codec} "
        f"{video_meta.duration_seconds:.2f}s {video_meta.frame_count} frames"
    )

    # ---- concatenate narration, then mux ---------------------------------
    start = time.perf_counter()
    combined = _concat_audio(
        [Path(audio_by_segment[s.segment_id][0]) for s in alignment.segments],
        alignment,
        out / "narration_full.wav",
    )
    final = out / "final.mp4"
    compose = MediaComposer(timeout_seconds=180.0).compose(
        Path(render.output_path), combined, final, video_meta.duration_seconds
    )
    timings["ffmpeg"] = time.perf_counter() - start

    final_video = probe_video(Path(compose.output_path))
    final_audio = probe_audio(Path(compose.output_path))
    print(
        f"Final: {final_video.width}x{final_video.height} {final_video.codec} "
        f"{final_video.duration_seconds:.2f}s {final_video.frame_count} frames | "
        f"audio {final_audio.codec} {final_audio.sample_rate}Hz "
        f"ch={final_audio.channels} {final_audio.duration_seconds:.2f}s"
    )

    timings["total"] = time.perf_counter() - total_start
    print("\nPerformance (seconds):")
    for name, seconds in timings.items():
        print(f"  {name:16s} {seconds:8.2f}")
    print(f"\nFinal artifact: {final.resolve()}")
    print(f"Subtitles     : {srt_path.resolve()}")
    return 0


def _concat_audio(paths: list[Path], alignment, output_path: Path) -> Path:
    """Place each segment's audio at its aligned start time using FFmpeg's
    adelay + amix. Fixed argument list; the only dynamic values are numeric
    delays and file paths this script itself produced."""
    from code2shorts.execution.sandbox import run_subprocess

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    command = ["ffmpeg", "-y"]
    for path in paths:
        command += ["-i", str(Path(path).resolve())]

    filters = []
    for index, segment in enumerate(alignment.segments):
        delay_ms = int(segment.start_seconds * 1000)
        filters.append(f"[{index}:a]adelay={delay_ms}|{delay_ms}[a{index}]")
    mix_inputs = "".join(f"[a{i}]" for i in range(len(paths)))
    filters.append(f"{mix_inputs}amix=inputs={len(paths)}:normalize=0[out]")

    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-t", f"{alignment.total_duration_seconds:.3f}",
        str(output_path),
    ]
    result = run_subprocess(command, cwd=output_path.parent, timeout_seconds=180.0)
    if result.timed_out or result.returncode != 0:
        raise RuntimeError(f"audio concat failed: {result.stderr[-1500:]}")
    return output_path


if __name__ == "__main__":
    raise SystemExit(main())

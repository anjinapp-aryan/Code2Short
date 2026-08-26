#!/usr/bin/env python
"""Code2Shorts driver - the programmatic handle on the pipeline.

Code2Shorts has no GUI and its packaged CLI is a stub, so "running the
app" means driving the pipeline and *looking at the frames it emits*.
This script is that handle.

    python .claude/skills/run-code2shorts/driver.py <command> [args]

Commands (fast -> slow):
    doctor                    check every external tool + report what works
    trace     <algo> [input]  real Java compile+execute+trace, dump events
    frames    <algo> [input]  reconstruct array/pointer/scalar state per event
    sync      <algo> [input]  verify highlighted line == real source line
    scene     <algo> [input]  generate Manim source WITHOUT rendering (instant)
    render    <algo> [input]  REAL Manim render -> MP4  (~25s)
    pipeline  <algo> [input]  render + speech + subtitles + mux + validate
    shots     <mp4> [n]       extract PNG frames so you can actually look
    validate  <mp4> [expect]  probe + media policy check

Algorithms: reverse_string palindrome two_sum move_zeroes remove_duplicates

Everything writes under output/driver/<algo>/ (gitignored).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
VENV_SCRIPTS = REPO / ".venv" / "Scripts"
VENV_BIN = REPO / ".venv" / "bin"

# The venv's Scripts/bin dir holds manim and (after the setup step in
# SKILL.md) ffmpeg. Prepending it here means the driver works from any
# shell, including ones whose PATH lacks them - a real problem on this
# machine, where the default Bash PATH has neither ffmpeg nor Maven.
for candidate in (VENV_SCRIPTS, VENV_BIN):
    if candidate.is_dir():
        os.environ["PATH"] = str(candidate) + os.pathsep + os.environ.get("PATH", "")

# Maven is the one tool we cannot ship. Honour the standard MAVEN_HOME /
# M2_HOME so it can be supplied without editing this file.
#
# Why this exists: under Git-Bash, `export PATH="$PATH:/i/Software/.../bin"`
# LOOKS right (bash `which mvn` finds it) but a native Windows Python still
# cannot see it - the POSIX /i/... entry is not translated on the way into
# the child process and arrives as an empty PATH element. Setting
# MAVEN_HOME to a WINDOWS-style path sidesteps the translation entirely.
for var in ("MAVEN_HOME", "M2_HOME"):
    home = os.environ.get(var)
    if home:
        maven_bin = Path(home) / "bin"
        if maven_bin.is_dir():
            os.environ["PATH"] = str(maven_bin) + os.pathsep + os.environ["PATH"]
            break

sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

ALGORITHMS = {
    "reverse_string": ("fixture", "correct", "HELLO", "OLLEH"),
    "palindrome": ("algorithm", "palindrome", "RACECAR", "true"),
    "two_sum": ("algorithm", "two_sum", "9", "0,1"),
    "move_zeroes": ("algorithm", "move_zeroes", "", "1,3,12,0,0"),
    "remove_duplicates": ("algorithm", "remove_duplicates", "", "3"),
}


def _die(message: str, code: int = 1):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def _resolve(algo: str):
    if algo not in ALGORITHMS:
        _die(f"unknown algorithm {algo!r}. Known: {', '.join(ALGORITHMS)}")
    return ALGORITHMS[algo]


def _load(algo: str):
    from tests.java_fixtures import load_algorithm_fixture, load_java_fixture

    kind, variant, default_input, expected = _resolve(algo)
    code = load_java_fixture(variant) if kind == "fixture" else load_algorithm_fixture(variant)
    return code, default_input, expected


def _out_dir(algo: str) -> Path:
    path = REPO / "output" / "driver" / algo
    path.mkdir(parents=True, exist_ok=True)
    return path


def _trace(algo: str, program_input: str | None):
    from code2shorts.execution.sandbox import Workspace
    from code2shorts.langadapter.java import JavaAdapter

    code, default_input, expected = _load(algo)
    program_input = default_input if program_input is None else program_input
    with Workspace() as workspace:
        trace = JavaAdapter().trace(code, workspace, program_input)
    return code, trace, program_input, expected


# ---------------------------------------------------------------- doctor


def cmd_doctor(_args):
    print("=== external tools ===")
    ok = True
    hints = {
        "java": "install a JDK 17+ and put it on PATH",
        "mvn": "install Maven and add its bin/ to PATH. NOTE: on this machine "
               "PowerShell has Maven on PATH but Git-Bash does not - run the "
               "driver from PowerShell, or export PATH=$PATH:/c/path/to/maven/bin",
        "manim": 'pip install -e ".[render]"',
        "ffmpeg": "copy .venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-*.exe "
                  "to .venv/Scripts/ffmpeg.exe",
    }
    for tool, needed_for in [
        ("java", "compile/execute/trace"),
        ("mvn", "build + instrumenter"),
        ("manim", "render"),
        ("ffmpeg", "composition, frame extraction, synthetic audio"),
    ]:
        found = shutil.which(tool)
        status = f"OK  {found}" if found else "MISSING"
        print(f"  {tool:8s} {status:.<72} ({needed_for})")
        if not found:
            print(f"           -> {hints[tool]}")
        ok = ok and bool(found)

    print("\n=== python packages ===")
    for module in ("pydantic", "manim", "av", "srt"):
        try:
            __import__(module)
            print(f"  {module:10s} OK")
        except ImportError:
            print(f"  {module:10s} MISSING")
            ok = False

    print("\n=== instrumenter jar (required for trace()) ===")
    jar = REPO / "tools" / "java-instrumenter" / "target" / "java-instrumenter-1.0.0.jar"
    if jar.is_file():
        print(f"  OK  {jar.relative_to(REPO)}")
    else:
        print("  MISSING -> cd tools/java-instrumenter && mvn -q clean package")
        ok = False

    print("\n=== real speech (optional) ===")
    try:
        from code2shorts.narration import SapiTTSProvider

        available = SapiTTSProvider.is_available()
        print(f"  Windows SAPI: {'available' if available else 'unavailable -> synthetic silence fallback'}")
    except Exception as error:  # noqa: BLE001
        print(f"  could not check: {error}")

    print("\nRESULT:", "ready" if ok else "NOT ready - fix the MISSING items above")
    return 0 if ok else 1


# ----------------------------------------------------------------- trace


def cmd_trace(args):
    algo = args[0]
    program_input = args[1] if len(args) > 1 else None
    _, trace, program_input, expected = _trace(algo, program_input)

    print(f"algorithm : {algo}")
    print(f"input     : {program_input!r}")
    print(f"status    : {trace.status.value}")
    print(f"output    : {trace.output!r}  (expected {expected!r})")
    print(f"schema    : v{trace.trace_schema_version}")
    print(f"events    : {len(trace.events)}\n")
    for event in trace.events:
        bits = [f"[{event.step_index:3d}]", f"{event.event_type:20s}"]
        if event.line_number is not None:
            bits.append(f"line={event.line_number:<4d}")
        if event.variable_name:
            bits.append(f"{event.variable_name}={event.new_value!r}")
        if event.condition_result is not None:
            bits.append(f"-> {event.condition_result}")
        if event.return_value is not None:
            bits.append(f"return {event.return_value!r}")
        print("  " + " ".join(bits))

    if trace.output != expected:
        _die(f"output {trace.output!r} != expected {expected!r}")
    return 0


# ---------------------------------------------------------------- frames


def cmd_frames(args):
    from code2shorts.visualization import reconstruct_frames

    algo = args[0]
    _, trace, _, _ = _trace(algo, args[1] if len(args) > 1 else None)
    frames = reconstruct_frames(trace)

    print(f"{len(frames)} frames reconstructed\n")
    for frame in frames:
        array = frame.primary_array
        cells = "[" + " ".join(array.cells) + "]" if array and array.cells else "-"
        pointers = ", ".join(f"{p.name}@{p.index}" for p in frame.pointers) or "-"
        scalars = " ".join(f"{k}={v}" for k, v in sorted(frame.scalars.items())) or "-"
        print(f"  [{frame.step_index:3d}] {frame.event_type:20s} {cells}")
        print(f"        pointers: {pointers}")
        print(f"        scalars : {scalars}")
    return 0


# ------------------------------------------------------------------ sync


def cmd_sync(args):
    """Prove the highlighted line is the REAL source line — the core
    Phase 4.3 invariant, and the fastest way to catch a code-sync bug."""
    from code2shorts.visualization.code_state import (
        build_code_state,
        resolve_source_locations,
    )

    algo = args[0]
    code, trace, _, _ = _trace(algo, args[1] if len(args) > 1 else None)
    locations = resolve_source_locations(trace, code.source_files, code.entry_point)

    checked = mismatches = 0
    for event in trace.events:
        location = locations[event.step_index]
        if location.line is None or location.file is None:
            continue
        state = build_code_state(location, code.source_files, window_radius=6)
        if state.highlight_offset is None:
            print(f"  [{event.step_index:3d}] NO VISIBLE HIGHLIGHT")
            mismatches += 1
            continue
        shown = state.lines[state.highlight_offset]
        real = code.source_files[location.file].splitlines()[location.line - 1]
        status = "ok " if shown == real else "BAD"
        if shown != real:
            mismatches += 1
        checked += 1
        print(
            f"  [{event.step_index:3d}] {status} {Path(location.file).name}:{location.line:<4d}"
            f" {shown.strip()[:60]!r}"
        )

    print(f"\nchecked={checked} mismatches={mismatches}")
    files = {loc.file for loc in locations.values() if loc.file}
    print(f"files referenced: {len(files)}")
    return 1 if mismatches else 0


# ----------------------------------------------------------------- scene


def _plan_for(trace, title: str, duration: float = 4.0):
    from code2shorts.ai.contracts import (
        VisualAction,
        VisualizationPlanResponse,
        VisualizationStepPlan,
    )
    from code2shorts.core.models import TraceEventType

    touched = [
        e
        for e in trace.events
        if e.event_type in (TraceEventType.ARRAY_READ.value, TraceEventType.ARRAY_WRITE.value)
    ]
    picked = [trace.events[0], *touched[:2], trace.events[-1]]
    ordered, seen = [], set()
    for event in sorted(picked, key=lambda e: e.step_index):
        if event.step_index not in seen:
            seen.add(event.step_index)
            ordered.append(event)

    actions = [
        VisualAction.INTRO,
        VisualAction.ARRAY_ACCESS,
        VisualAction.SWAP,
        VisualAction.COMPLETION,
    ]
    return VisualizationPlanResponse(
        lesson_title=title,
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=actions[min(i, len(actions) - 1)],
                trace_event_index=event.step_index,
                narration_text=f"Step {i + 1}. {event.description}"[:90],
                duration_seconds=duration,
            )
            for i, event in enumerate(ordered)
        ],
    )


def cmd_scene(args):
    """Generate the Manim source WITHOUT rendering. Instant — this is the
    loop to use when iterating on visualization/primitives.py."""
    from code2shorts.visualization import RenderContext, validate_visualization_plan
    from code2shorts.visualization.manim_renderer import build_scene_source

    algo = args[0]
    code, trace, _, _ = _trace(algo, args[1] if len(args) > 1 else None)
    plan = _plan_for(trace, algo.replace("_", " ").title())

    validation = validate_visualization_plan(plan, trace)
    print(f"plan validation: {'PASS' if validation.passed else 'FAIL ' + str(validation.errors)}")
    if not validation.passed:
        return 1

    context = RenderContext(
        trace=trace, source_files=dict(code.source_files), entry_point=code.entry_point
    )
    source = build_scene_source(plan, "GeneratedScene", context)

    path = _out_dir(algo) / "scene.py"
    path.write_text(source, encoding="utf-8")
    compile(source, str(path), "exec")  # must always be valid Python
    print(f"scene source: {path.relative_to(REPO)}  ({len(source.splitlines())} lines, compiles OK)")
    return 0


# ---------------------------------------------------------------- render


def cmd_render(args):
    from code2shorts.media import probe_video
    from code2shorts.visualization import ManimVideoRenderer, RenderContext

    algo = args[0]
    code, trace, _, _ = _trace(algo, args[1] if len(args) > 1 else None)
    plan = _plan_for(trace, algo.replace("_", " ").title())
    out = _out_dir(algo)

    started = time.perf_counter()
    renderer = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=900.0)
    result = renderer.render(
        plan,
        out / "render",
        RenderContext(
            trace=trace, source_files=dict(code.source_files), entry_point=code.entry_point
        ),
    )
    elapsed = time.perf_counter() - started

    meta = probe_video(Path(result.output_path))
    print(f"rendered in {elapsed:.1f}s")
    print(f"  path   : {Path(result.output_path).relative_to(REPO)}")
    print(f"  video  : {meta.width}x{meta.height} {meta.codec} {meta.duration_seconds:.2f}s "
          f"{meta.frame_count} frames @{meta.fps:g}fps")
    print(f"\nnext: driver.py shots {Path(result.output_path).relative_to(REPO)}")
    return 0


# -------------------------------------------------------------- pipeline


def cmd_pipeline(args):
    """Full production path: render + real speech + subtitles + mux +
    media validation. Mirrors tests/test_e2e_golden_path.py."""
    from code2shorts.ai.contracts import NarrationResponse, NarrationSegment
    from code2shorts.media import MediaComposer, probe_audio, probe_video
    from code2shorts.media.validation import validate_final_video, validate_timeline
    from code2shorts.narration import (
        SapiTTSProvider,
        SyntheticTTSProvider,
        align_narration,
        validate_alignment,
        validate_srt,
    )
    from code2shorts.narration.subtitles import write_srt
    from code2shorts.visualization import ManimVideoRenderer, RenderContext

    algo = args[0]
    code, trace, _, expected = _trace(algo, args[1] if len(args) > 1 else None)
    plan = _plan_for(trace, algo.replace("_", " ").title())
    out = _out_dir(algo)
    timings = {}

    narration = NarrationResponse(
        segments=[
            NarrationSegment(
                order=s.order, text=s.narration_text, visualization_step_order=s.order
            )
            for s in plan.steps
        ]
    )

    tts = SapiTTSProvider() if SapiTTSProvider.is_available() else SyntheticTTSProvider()
    print(f"TTS: {type(tts).__name__}")
    started = time.perf_counter()
    audio_by_segment = {}
    for segment in narration.segments:
        result = tts.synthesize(segment.text, out / f"seg_{segment.order}")
        audio_by_segment[segment.order] = (result.audio_path, result.duration_seconds)
        print(f"  seg {segment.order}: {result.duration_seconds:.2f}s")
    timings["tts"] = time.perf_counter() - started

    alignment = align_narration(narration, plan, audio_by_segment=audio_by_segment)
    alignment_check = validate_alignment(alignment, plan)
    print(
        f"alignment: {len(alignment.segments)} segments, {alignment.total_duration_seconds:.2f}s, "
        f"overflow={alignment.overflow_count} "
        f"{'OK' if alignment_check.passed else 'WARN ' + str(alignment_check.errors)}"
    )

    srt_path = write_srt(alignment, out / "subtitles.srt")
    print(f"subtitles: {srt_path.relative_to(REPO)} valid="
          f"{validate_srt(srt_path.read_text(encoding='utf-8')).passed}")

    started = time.perf_counter()
    render = ManimVideoRenderer(fps=30, resolution="1080x1920", timeout_seconds=900.0).render(
        plan,
        out / "render",
        RenderContext(
            trace=trace, source_files=dict(code.source_files), entry_point=code.entry_point
        ),
    )
    timings["manim"] = time.perf_counter() - started
    video_meta = probe_video(Path(render.output_path))
    print(f"video: {video_meta.width}x{video_meta.height} {video_meta.codec} "
          f"{video_meta.duration_seconds:.2f}s {video_meta.frame_count} frames")

    combined = _concat_audio(alignment, out / "narration.wav")
    final_path = out / "final.mp4"
    started = time.perf_counter()
    MediaComposer(timeout_seconds=300.0).compose(
        Path(render.output_path), combined, final_path, video_meta.duration_seconds
    )
    timings["ffmpeg"] = time.perf_counter() - started

    final_video = probe_video(final_path)
    final_audio = probe_audio(final_path)
    print(f"final: {final_video.width}x{final_video.height} {final_video.codec} "
          f"{final_video.duration_seconds:.2f}s | audio {final_audio.codec} "
          f"{final_audio.sample_rate}Hz ch={final_audio.channels} "
          f"{final_audio.duration_seconds:.2f}s")

    report = validate_final_video(
        final_path, expected_duration_seconds=video_meta.duration_seconds
    )
    timeline = validate_timeline(alignment, final_video.duration_seconds)
    print(f"media validation : {'PASS' if report.passed else 'FAIL ' + str(report.errors)}")
    print(f"timeline validate: {'PASS' if timeline.passed else 'FAIL ' + str(timeline.errors)}")

    print("\ntimings:", {k: round(v, 2) for k, v in timings.items()})
    print(f"\nfinal artifact: {final_path.relative_to(REPO)}")
    print(f"next: driver.py shots {final_path.relative_to(REPO)}")
    return 0 if (report.passed and timeline.passed) else 1


def _concat_audio(alignment, output_path: Path) -> Path:
    """Place each segment at its aligned start. Fixed argument list, no shell."""
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
    filters.append(
        "".join(f"[a{i}]" for i in range(len(paths)))
        + f"amix=inputs={len(paths)}:normalize=0[out]"
    )
    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-t", f"{alignment.total_duration_seconds:.3f}",
        str(output_path),
    ]
    result = run_subprocess(command, cwd=output_path.parent, timeout_seconds=300.0)
    if result.timed_out or result.returncode != 0:
        _die(f"audio concat failed: {result.stderr[-1200:]}")
    return output_path


# ----------------------------------------------------------------- shots


def cmd_shots(args):
    """Extract PNG frames. THIS is how you 'look at' a Code2Shorts run —
    read the PNGs afterwards; a green exit code proves nothing visual."""
    from code2shorts.media import probe_video

    video_path = Path(args[0])
    if not video_path.is_absolute():
        video_path = REPO / video_path
    if not video_path.is_file():
        _die(f"no such video: {video_path}")
    count = int(args[1]) if len(args) > 1 else 4

    meta = probe_video(video_path)
    frames_dir = video_path.parent / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for index in range(count):
        # sample mid-step to avoid landing on a fade, which yields a dim
        # frame that looks like a rendering bug but is just bad sampling
        timestamp = meta.duration_seconds * (index + 0.5) / count
        frame_path = frames_dir / f"frame_{index}.png"
        completed = subprocess.run(  # noqa: S603 — fixed arg list, no shell
            ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path),
             "-frames:v", "1", str(frame_path)],
            capture_output=True, timeout=120,
        )
        if completed.returncode != 0:
            _die(completed.stderr.decode(errors="replace")[-800:])
        written.append(frame_path)

    print(f"video: {meta.width}x{meta.height} {meta.duration_seconds:.2f}s")
    for path in written:
        print(f"  {path.relative_to(REPO)}  ({path.stat().st_size} bytes)")
    print("\nNOW READ THOSE PNGs. A passing exit code says nothing about what is on screen.")
    return 0


# -------------------------------------------------------------- validate


def cmd_validate(args):
    from code2shorts.media import probe_audio, probe_video
    from code2shorts.media.probe import VideoProbeError
    from code2shorts.media.validation import validate_final_video

    video_path = Path(args[0])
    if not video_path.is_absolute():
        video_path = REPO / video_path
    expected = float(args[1]) if len(args) > 1 else None

    meta = probe_video(video_path)
    print(f"video: {meta.width}x{meta.height} {meta.codec} {meta.duration_seconds:.3f}s "
          f"{meta.frame_count} frames @{meta.fps:g}fps")
    try:
        audio = probe_audio(video_path)
        print(f"audio: {audio.codec} {audio.sample_rate}Hz ch={audio.channels} "
              f"{audio.duration_seconds:.3f}s")
    except VideoProbeError:
        print("audio: none")

    report = validate_final_video(video_path, expected_duration_seconds=expected)
    print(f"\npolicy: {'PASS' if report.passed else 'FAIL'}")
    for error in report.errors:
        print(f"  - {error}")
    return 0 if report.passed else 1


COMMANDS = {
    "doctor": (cmd_doctor, 0),
    "trace": (cmd_trace, 1),
    "frames": (cmd_frames, 1),
    "sync": (cmd_sync, 1),
    "scene": (cmd_scene, 1),
    "render": (cmd_render, 1),
    "pipeline": (cmd_pipeline, 1),
    "shots": (cmd_shots, 1),
    "validate": (cmd_validate, 1),
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    name, *args = argv
    if name not in COMMANDS:
        _die(f"unknown command {name!r}. Known: {', '.join(COMMANDS)}")
    handler, required = COMMANDS[name]
    if len(args) < required:
        _die(f"{name} needs {required} argument(s). See --help.")
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

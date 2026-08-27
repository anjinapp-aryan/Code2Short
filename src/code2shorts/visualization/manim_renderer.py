"""ManimVideoRenderer: the one place VisualizationPlanResponse DATA is
turned into Manim Python SOURCE — code WE write from validated fields
(narration text goes through `repr()`, which safely escapes it as a
string literal; it is never spliced in as executable syntax), then run
via the same trusted-subprocess pattern as Phase 1's JavaCompiler
(`execution.sandbox.run_subprocess` — fixed argument list, no shell=True,
timeout-bounded, whole-process-tree kill on timeout).

Not covered by an end-to-end integration test in Phase 4 because Manim
itself isn't installed in this environment (it's an optional
`pip install code2shorts[render]` extra, per pyproject.toml — unchanged
from Phase 0). The command-construction and safety properties ARE unit
tested via a dependency-injected fake subprocess runner (no real Manim
needed for that). Real end-to-end rendering is Known Limitations.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from code2shorts.ai.contracts import VisualizationPlanResponse
from code2shorts.execution.sandbox import ProcessResult, run_subprocess
from code2shorts.core.models import SourceLocation
from code2shorts.media.probe import VideoProbeError, probe_video
from code2shorts.visualization.code_state import (
    DEFAULT_WINDOW_RADIUS,
    build_code_state,
    resolve_source_locations,
)
from code2shorts.visualization.primitives import (
    array_row,
    caption_text,
    code_panel,
    map_panel,
    pointer_arrows,
    sequence_panel,
    scalar_panel,
    title_text,
)
from code2shorts.visualization.renderer import (
    RenderContext,
    RenderingFailure,
    RenderResult,
    VideoRenderer,
)
from code2shorts.visualization.state import FrameState, reconstruct_frames

RENDERER_VERSION = "code2shorts-manim-renderer-1.1"
OUTPUT_FILE_NAME = "output.mp4"


def _escape_for_python_literal(text: str) -> str:
    return repr(text)


def build_scene_source(
    plan: VisualizationPlanResponse,
    class_name: str,
    context: RenderContext | None = None,
    window_radius: int = DEFAULT_WINDOW_RADIUS,
) -> str:
    """Deterministic Manim scene source generation.

    Every dynamic value is DATA — from the validated plan or the canonical
    ExecutionTrace — passed through repr() as a Python string literal, never
    spliced in as syntax. This function cannot emit anything the schema and
    semantic validators did not already accept.

    With a RenderContext carrying the trace, each step renders the real
    reconstructed algorithm state (array tiles, pointers, variables, and
    the executing source line). Without one it degrades to captions, so
    every pre-Phase-4.2 caller keeps working unchanged.

    Fully algorithm-agnostic: there is no branch anywhere on algorithm
    name. The visuals are driven entirely by what the trace contains.
    """
    frames_by_step: dict[int, FrameState] = {}
    locations: dict[int, SourceLocation] = {}
    source_files: dict[str, str] = {}

    if context is not None and context.trace is not None:
        frames_by_step = {
            frame.step_index: frame for frame in reconstruct_frames(context.trace)
        }
        source_files = context.source_files or {}
        if source_files:
            # The executing line comes from the TRACE, never from the plan.
            locations = resolve_source_locations(
                context.trace, source_files, context.entry_point
            )

    body: list[str] = []
    body += title_text(plan.lesson_title)
    body += ["self.play(FadeIn(title))"]
    body += ["self.wait(0.4)"]

    previous_groups: list[str] = []
    for step in plan.steps:
        frame = frames_by_step.get(step.trace_event_index)
        step_body: list[str] = []
        groups: list[str] = []

        # Dispatch on WHAT STATE IS PRESENT, never on the algorithm. A
        # frame that observed a collection renders that collection; a frame
        # with an array renders tiles and pointers. Binary search needs no
        # special case here — its low/mid/high are ordinary scalars that
        # index an array, so the existing pointer rule already draws them.
        if frame is not None and frame.primary_map is not None:
            step_body += map_panel(frame, var="map_group")
            step_body += scalar_panel(frame, var="vars_group")
            groups = ["map_group", "vars_group"]

        elif frame is not None and frame.primary_sequence is not None:
            step_body += sequence_panel(frame, var="seq_group")
            step_body += scalar_panel(frame, var="vars_group")
            groups = ["seq_group", "vars_group"]

        elif frame is not None and frame.primary_array is not None:
            step_body += array_row(frame, var="arr_group")
            step_body += pointer_arrows(frame, array_var="arr_group", var="ptr_group")
            step_body += scalar_panel(frame, var="vars_group")
            groups = ["arr_group", "ptr_group", "vars_group"]

        # Code window synchronized to the real source location of THIS
        # trace event — recomputed per step so the window scrolls and the
        # highlight follows actual execution.
        location = locations.get(step.trace_event_index)
        if location is not None and source_files:
            code_state = build_code_state(location, source_files, window_radius)
            if code_state.lines:
                step_body += code_panel(code_state, var="code_group")
                groups.append("code_group")

        step_body += caption_text(step.narration_text, var="caption")
        groups.append("caption")

        # Replace the previous step's visuals rather than stacking them —
        # this is what stops elements overlapping as the video progresses.
        if previous_groups:
            fade_out = ", ".join(f"FadeOut({name})" for name in previous_groups)
            step_body.insert(0, f"self.play({fade_out}, run_time=0.25)")

        fade_in = ", ".join(f"FadeIn({name})" for name in groups)
        step_body.append(f"self.play({fade_in}, run_time=0.4)")
        step_body.append(f"self.wait({float(step.duration_seconds)})")

        body += step_body
        previous_groups = groups

    if previous_groups:
        fade_out = ", ".join(f"FadeOut({name})" for name in previous_groups)
        body.append(f"self.play({fade_out}, run_time=0.3)")

    indented = "\n".join(f"        {line}" for line in body)
    return "\n".join(
        [
            "from manim import *",
            "",
            # Phase 6.1 root-cause fix. Manim keeps `frame_width` at its
            # 16:9 default (14.22 units) even when rendered at 1080x1920,
            # so the scene's coordinate system was 16:9 while the output
            # was 9:16 — Manim letterboxed the whole scene into a band
            # occupying only ~31% of the frame height (measured), and the
            # remaining 69% was structurally unreachable black. Content was
            # never "too small"; the canvas was being thrown away.
            #
            # Deriving frame_width from the real pixel aspect makes the
            # full 1080x1920 addressable and square (240 px per unit on
            # both axes), which is what the layout constants in
            # primitives.py now assume.
            "config.frame_height = 8.0",
            "config.frame_width = config.frame_height * "
            "(config.pixel_width / config.pixel_height)",
            "",
            f"class {class_name}(Scene):",
            "    def construct(self):",
            indented,
        ]
    )


class ManimVideoRenderer(VideoRenderer):
    def __init__(
        self,
        fps: int = 30,
        resolution: str = "1080x1920",
        timeout_seconds: float = 300.0,
        run_subprocess_fn: Callable[..., ProcessResult] | None = None,
    ) -> None:
        self._fps = fps
        self._resolution = resolution
        self._timeout_seconds = timeout_seconds
        self._run_subprocess_fn = run_subprocess_fn or run_subprocess

    def render(
        self,
        plan: VisualizationPlanResponse,
        output_dir: Path,
        context: RenderContext | None = None,
    ) -> RenderResult:
        # Resolve to an absolute path before doing anything else. The
        # subprocess runs with cwd=output_dir, so a RELATIVE scene path
        # would be resolved a second time against that cwd — e.g.
        # "output/golden/render/scene.py" became
        # "output/golden/render/output/golden/render/scene.py" and Manim
        # reported FileNotFoundError. Absolute paths are immune to this
        # regardless of the caller's working directory.
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        class_name = "GeneratedScene"
        scene_path = output_dir / "scene.py"
        scene_path.write_text(
            build_scene_source(plan, class_name, context), encoding="utf-8"
        )

        width, height = self._resolution.split("x")
        command = [
            "manim",
            "render",
            "-qh",
            "--fps",
            str(self._fps),
            "--resolution",
            f"{width},{height}",
            str(scene_path),
            class_name,
            "-o",
            OUTPUT_FILE_NAME,
        ]
        result = self._run_subprocess_fn(
            command, cwd=output_dir, timeout_seconds=self._timeout_seconds
        )
        if result.timed_out or result.returncode != 0:
            raise RenderingFailure(f"manim render failed: {result.stderr[-2000:]}")

        output_path = self._find_output_file(output_dir)
        if output_path is None:
            raise RenderingFailure("manim reported success but produced no .mp4 file")

        checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()

        # Report what the file ACTUALLY is, never what we predicted. The
        # previous arithmetic estimate (2.0 + sum of step durations) was
        # measurably wrong on the first real render — see media/probe.py.
        # A probe failure is not fatal: the render itself succeeded, so we
        # fall back to the declared configuration rather than discarding a
        # good video, and record that the numbers are unverified.
        try:
            probed = probe_video(output_path)
            duration_seconds = probed.duration_seconds
            resolution = probed.resolution
            fps = int(round(probed.fps)) or self._fps
            probe_metadata: dict[str, object] = {
                "probed": True,
                "frame_count": probed.frame_count,
                "codec": probed.codec,
            }
        except VideoProbeError as error:
            duration_seconds = 0.0
            resolution = self._resolution
            fps = self._fps
            probe_metadata = {"probed": False, "probe_error": str(error)}

        return RenderResult(
            output_path=str(output_path),
            checksum=checksum,
            duration_seconds=duration_seconds,
            resolution=resolution,
            fps=fps,
            renderer_version=RENDERER_VERSION,
            metadata={
                "step_count": len(plan.steps),
                "lesson_title": plan.lesson_title,
                "requested_resolution": self._resolution,
                "requested_fps": self._fps,
                **probe_metadata,
            },
        )

    @staticmethod
    def _find_output_file(output_dir: Path) -> Path | None:
        """Manim writes the finished video alongside a `partial_movie_files/`
        directory of per-animation fragments — all `.mp4`. Excluding that
        directory explicitly matters: the previous implementation took the
        alphabetically-first match, which avoided the fragments only by the
        accident that "output.mp4" sorts before "partial_movie_files/", and
        would have silently returned a one-animation fragment as the final
        video for any differently-named output.
        """
        candidates = [
            path
            for path in output_dir.rglob("*.mp4")
            if "partial_movie_files" not in path.parts
        ]
        if not candidates:
            return None
        # Prefer the name we asked Manim for; fall back to the largest
        # remaining candidate (the assembled video is never smaller than a
        # fragment of itself).
        for path in candidates:
            if path.name == OUTPUT_FILE_NAME:
                return path
        return max(candidates, key=lambda path: path.stat().st_size)

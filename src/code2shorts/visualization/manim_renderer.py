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
from code2shorts.visualization.timing import (
    FINAL_FADE_OUT_SECONDS,
    STEP_FADE_IN_SECONDS,
    STEP_FADE_OUT_SECONDS,
    TITLE_FADE_IN_SECONDS,
    TITLE_HOLD_SECONDS,
)
from code2shorts.visualization.code_state import (
    DEFAULT_WINDOW_RADIUS,
    build_code_state,
    resolve_source_locations,
)
from code2shorts.visualization.layout import CompositionLayout
from code2shorts.visualization.primitives import (
    PORTRAIT,
    array_row,
    caption_text,
    code_panel,
    compose_columns,
    compose_vertical,
    fit_window_radius,
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

# Which function arranges one step, by `CompositionLayout.composer`.
COMPOSERS = {"vertical": compose_vertical, "columns": compose_columns}
OUTPUT_FILE_NAME = "output.mp4"


def _escape_for_python_literal(text: str) -> str:
    return repr(text)


def build_scene_source(
    plan: VisualizationPlanResponse,
    class_name: str,
    context: RenderContext | None = None,
    window_radius: int = DEFAULT_WINDOW_RADIUS,
    layout: CompositionLayout | None = None,
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

    `layout` decides geometry only (default: the portrait composition).
    A layout whose composition is not implemented is refused here, so it
    can never be drawn with the portrait arrangement instead.
    """
    layout = layout or PORTRAIT
    if not layout.composition_implemented:
        raise RenderingFailure(
            f"the {layout.name} composition is not implemented yet; refusing to "
            "render it with another layout's arrangement"
        )
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
    body += title_text(plan.lesson_title, layout=layout)
    body += [f"self.play(FadeIn(title), run_time={TITLE_FADE_IN_SECONDS})"]
    body += [f"self.wait({TITLE_HOLD_SECONDS})"]

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
            step_body += map_panel(frame, var="map_group", layout=layout)
            step_body += scalar_panel(frame, var="vars_group", layout=layout)
            groups = ["map_group", "vars_group"]

        elif frame is not None and frame.primary_sequence is not None:
            step_body += sequence_panel(frame, var="seq_group", layout=layout)
            step_body += scalar_panel(frame, var="vars_group", layout=layout)
            groups = ["seq_group", "vars_group"]

        elif frame is not None and frame.primary_array is not None:
            step_body += array_row(frame, var="arr_group", layout=layout)
            step_body += pointer_arrows(
                frame, array_var="arr_group", var="ptr_group", layout=layout
            )
            step_body += scalar_panel(frame, var="vars_group", layout=layout)
            groups = ["arr_group", "ptr_group", "vars_group"]

        # Code window synchronized to the real source location of THIS
        # trace event — recomputed per step so the window scrolls and the
        # highlight follows actual execution.
        location = locations.get(step.trace_event_index)
        if location is not None and source_files:
            # Visible line count follows the CANVAS, not a constant. A
            # window of long lines is width-bound, so its text is small
            # and the spare height is better spent on more context than
            # left black; a window of short lines is height-bound and
            # keeps the minimum. See primitives.fit_window_radius.
            radius = fit_window_radius(
                location, source_files, minimum=window_radius, layout=layout
            )
            code_state = build_code_state(location, source_files, radius)
            if code_state.lines:
                step_body += code_panel(code_state, var="code_group", layout=layout)
                groups.append("code_group")

        step_body += caption_text(step.narration_text, var="caption", layout=layout)
        groups.append("caption")

        # Content-aware composition of the LOWER region. The bands above
        # stay fixed so the array never jumps between steps; below them the
        # caption is pulled up under whatever was actually drawn and the
        # code panel takes every remaining unit of height. Without this the
        # code band was a constant sized for the worst-case caption, so a
        # short caption left ~255 px of unreachable black on the canvas.
        structure_vars = [name for name in groups if name.endswith("_group")
                          and name != "code_group"]
        compose = COMPOSERS[layout.composer]
        step_body += compose(
            structure_vars,
            caption_var="caption",
            code_var="code_group" if "code_group" in groups else None,
            layout=layout,
        )

        # Replace the previous step's visuals rather than stacking them —
        # this is what stops elements overlapping as the video progresses.
        if previous_groups:
            fade_out = ", ".join(f"FadeOut({name})" for name in previous_groups)
            step_body.insert(0, f"self.play({fade_out}, run_time={STEP_FADE_OUT_SECONDS})")

        fade_in = ", ".join(f"FadeIn({name})" for name in groups)
        step_body.append(f"self.play({fade_in}, run_time={STEP_FADE_IN_SECONDS})")
        step_body.append(f"self.wait({float(step.duration_seconds)})")

        body += step_body
        previous_groups = groups

    if previous_groups:
        fade_out = ", ".join(f"FadeOut({name})" for name in previous_groups)
        body.append(f"self.play({fade_out}, run_time={FINAL_FADE_OUT_SECONDS})")

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
            f"config.frame_height = {layout.frame_height}",
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
        layout: CompositionLayout | None = None,
    ) -> None:
        self._fps = fps
        self._resolution = resolution
        self._layout = layout or PORTRAIT
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
        width, height = self._resolution.split("x")
        # The layout and the pixels must describe the same shape. A portrait
        # arrangement asked to fill a landscape frame is refused here, before
        # anything is written or run, rather than rendered as a narrow column.
        pixel_aspect = int(width) / int(height)
        if abs(pixel_aspect - self._layout.aspect) > 1e-3:
            raise RenderingFailure(
                f"the {self._layout.name} layout ({self._layout.aspect:.4f}) does not "
                f"match the requested {self._resolution} ({pixel_aspect:.4f})"
            )

        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        class_name = "GeneratedScene"
        scene_path = output_dir / "scene.py"
        scene_path.write_text(
            build_scene_source(plan, class_name, context, layout=self._layout),
            encoding="utf-8",
        )

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

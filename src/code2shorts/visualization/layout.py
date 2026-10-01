"""CompositionLayout: where things go on the frame, per orientation.

The single seam between an output format and the renderer's geometry.
A `VideoProfile` says how many pixels; a layout says how the scene is
arranged on them. Everything above this - the trace, the teaching plan,
the visualization plan, narration and the timeline - never sees it.

    VideoProfile.orientation -> layout_for(profile) -> CompositionLayout
                                                            |
                              ManimVideoRenderer(layout) -> build_scene_source
                                                            -> primitives(layout=...)

Only GEOMETRY lives here: frame size, safe area, band positions, region
widths and budgets. Typography, colours, padding, cell size and every
readability floor stay module constants in `primitives.py`. Both layouts
keep 240 px per scene unit, so those constants mean the same pixels in
either orientation - that is what lets one set of primitives serve both.

PORTRAIT is built in `primitives.py` directly from the existing module
constants, so it cannot drift from them. LANDSCAPE is the 16:9 arrangement
approved in docs/PHASE_8_2A_LANDSCAPE_DESIGN.md and implemented in Phase
8.2C by `primitives.compose_columns`.

The fields after `code_height_budget` are used only by the column
composer. They default to values that leave the portrait composition - and
therefore every portrait scene - exactly as it was.
"""

from __future__ import annotations

from dataclasses import dataclass

from code2shorts.core.video_profile import Orientation, VideoProfile


@dataclass(frozen=True)
class CompositionLayout:
    name: str
    orientation: Orientation
    composition_implemented: bool
    """False until a composer draws this arrangement. The renderer refuses
    an unimplemented layout before Manim runs."""

    # --- frame, in scene units -------------------------------------------
    frame_width: float
    frame_height: float
    """Written into the scene header as `config.frame_height`; Manim then
    derives the width from the pixel aspect."""

    # --- safe area --------------------------------------------------------
    safe_width: float
    safe_top: float
    safe_bottom: float

    # --- bands and regions --------------------------------------------------
    title_x: float
    title_y: float
    scalars_x: float
    scalars_y: float
    structure_x: float
    """Horizontal centre of the array / map / sequence region."""
    structure_width: float
    structure_top: float
    structure_max_height: float
    array_y: float
    caption_x: float
    caption_y: float
    caption_width: float
    caption_chars_per_line: int
    caption_max_lines: int
    caption_max_height: float
    band_gap: float

    # --- code viewport ------------------------------------------------------
    code_y: float
    code_max_width: float
    code_viewport_width: float
    code_viewport_center_x: float
    code_height_budget: float

    # --- column composition (Phase 8.2C) -------------------------------------
    composer: str = "vertical"
    """`vertical` = `compose_vertical` (one centred column, portrait);
    `columns` = `compose_columns` (code left, state right)."""
    content_top: float = 0.0
    content_bottom: float = 0.0
    """The region between the title band and the explanation band that the
    code column and the state column share."""
    array_top: float = 0.0
    """Where the top edge of the array's cells sits. Anchored by the cells,
    not by the whole group, so pointer labels stacking higher never move
    the array."""
    title_max_height: float | None = None
    """Cap on the title's height after it is fitted to the safe width.
    None emits nothing, which is what the portrait layout needs."""
    separate_pointer_labels: bool = False
    """Lift a pointer label that would overlap another cell's label to the
    next free level, and keep labels inside the state column. False keeps
    the original pointer drawing exactly."""

    @property
    def aspect(self) -> float:
        return self.frame_width / self.frame_height


# 16:9 at the same 240 px/unit as portrait: 1920 x 1080 px = 8.0 x 4.5 units,
# x -4..4, y -2.25..2.25. Safe area 5% on every edge (96 px / 54 px):
# x -3.60..3.60, y -2.025..2.025.
#
#   title band      y  2.025 .. 1.625   full safe width, <= 0.18 u tall
#   content         y  1.485 .. -1.085  (a BAND_GAP of 0.14 either side)
#     code column     x -3.60 .. 0.54   4.14 u = 994 px (the portrait viewport)
#     state column    x  0.74 .. 3.60   2.86 u = 686 px
#   explanation     y -1.225 .. -2.025  full safe width, <= 3 lines
#
# Measured, not assumed (docs/PHASE_8_2C_LANDSCAPE_IMPLEMENTATION.md):
#   * scalars fitted to the column are 0.17-0.46 u tall, so they are
#     top-aligned at content_top and never reach below y = 1.025;
#   * two stacked pointer labels rise 1.05 u above their cells, so the
#     cells' top edge sits at 1.025 - 0.10 - 1.05 = -0.125;
#   * an unscaled array's index row then ends at -1.07, inside the region;
#   * map / sequence panels start under the scalars (0.885) and may use the
#     rest of the column (1.97 u).
LANDSCAPE = CompositionLayout(
    name="landscape",
    orientation=Orientation.LANDSCAPE,
    composition_implemented=True,
    frame_width=8.0,
    frame_height=4.5,
    safe_width=7.2,
    safe_top=2.025,
    safe_bottom=-2.025,
    title_x=0.0,
    title_y=1.825,
    scalars_x=2.17,
    scalars_y=1.255,
    structure_x=2.17,
    structure_width=2.86,
    structure_top=0.885,
    structure_max_height=1.97,
    array_y=-0.45,
    caption_x=0.0,
    caption_y=-1.625,
    caption_width=7.2,
    caption_chars_per_line=80,
    caption_max_lines=3,
    caption_max_height=0.80,
    band_gap=0.14,
    code_y=0.20,
    code_max_width=4.14,
    code_viewport_width=4.14,
    code_viewport_center_x=-1.53,
    code_height_budget=2.09,
    composer="columns",
    content_top=1.485,
    content_bottom=-1.085,
    array_top=-0.125,
    title_max_height=0.18,
    separate_pointer_labels=True,
)


def layout_for(profile: VideoProfile) -> CompositionLayout:
    """The layout a profile's orientation calls for.

    By orientation, not by resolution: a 4K portrait profile keeps the
    portrait arrangement (same aspect, same units) at a higher pixel
    density. Whether a profile may be rendered at all is the profile's
    decision (`VideoProfile.require_renderable`), not this one's.
    """
    if profile.orientation is Orientation.PORTRAIT:
        from code2shorts.visualization.primitives import PORTRAIT

        return PORTRAIT
    return LANDSCAPE

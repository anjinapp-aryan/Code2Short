"""Phase 6.1: layout and readability invariants.

These tests check what CAN be checked mechanically — the coordinate frame,
the safe area, and the absence of a global shrink. They deliberately do
NOT claim to measure "readability": whether a human can read the code is
settled by inspecting real frames, and that remains mandatory.

What they do prevent is the specific regression that made the video
unteachable: a scene whose coordinate frame does not match the output
aspect, which Manim letterboxes into a fraction of the image.
"""

from __future__ import annotations

import ast

import pytest

from code2shorts.ai.contracts import (
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import ExecutionTrace, TraceEvent, TraceEventType
from code2shorts.visualization import primitives
from code2shorts.visualization.code_state import DEFAULT_WINDOW_RADIUS
from code2shorts.visualization.manim_renderer import build_scene_source
from code2shorts.visualization.renderer import RenderContext


def _trace() -> ExecutionTrace:
    return ExecutionTrace(
        algorithm_name="Main", language="java", input="RACECAR", output="true",
        succeeded=True, exit_code=0,
        events=[
            TraceEvent(step_index=0, event_type=TraceEventType.VARIABLE_ASSIGN.value,
                       variable_name="chars", new_value="RACECAR", line_number=5,
                       description="chars = RACECAR"),
            TraceEvent(step_index=1, event_type=TraceEventType.VARIABLE_ASSIGN.value,
                       variable_name="left", new_value="0", line_number=5,
                       description="left = 0"),
            TraceEvent(step_index=2, event_type=TraceEventType.VARIABLE_ASSIGN.value,
                       variable_name="right", new_value="6", line_number=6,
                       description="right = 6"),
        ],
    )


def _plan(steps: int = 3) -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="Palindrome Check",
        steps=[
            VisualizationStepPlan(
                order=i, visual_action=VisualAction.HIGHLIGHT, trace_event_index=i,
                narration_text=f"step {i}", duration_seconds=1.0,
            )
            for i in range(steps)
        ],
    )


def _source() -> str:
    return build_scene_source(_plan(), "S", RenderContext(trace=_trace()))


# ---- the root-cause regression -------------------------------------------


def test_the_scene_frame_matches_the_output_aspect() -> None:
    """The Phase 6.1 defect in one test.

    Manim keeps `frame_width` at its 16:9 default even when rendered at
    1080x1920. A scene that does not correct this is letterboxed into
    ~31% of the frame height (measured), and no amount of font tuning can
    recover the lost pixels.
    """
    source = _source()
    assert "config.frame_width" in source
    assert "config.pixel_width" in source and "config.pixel_height" in source
    compile(source, "<generated>", "exec")


def test_the_derived_frame_is_square_pixelled() -> None:
    """4.5 x 8 units over 1080 x 1920 px is 240 px per unit on both axes,
    which is what every constant in primitives.py assumes."""
    assert primitives.FRAME_HEIGHT == 8.0
    assert primitives.FRAME_WIDTH == pytest.approx(8.0 * 1080 / 1920)
    assert primitives.PIXELS_PER_UNIT == pytest.approx(1920 / primitives.FRAME_HEIGHT)
    assert primitives.PIXELS_PER_UNIT == pytest.approx(1080 / primitives.FRAME_WIDTH)


# ---- safe area ------------------------------------------------------------


@pytest.mark.parametrize(
    "y",
    [primitives.TITLE_Y, primitives.SCALARS_Y, primitives.ARRAY_Y,
     primitives.CAPTION_Y, primitives.CODE_Y],
)
def test_every_band_sits_inside_the_vertical_safe_area(y) -> None:
    assert primitives.SAFE_BOTTOM <= y <= primitives.SAFE_TOP


def test_the_bands_are_ordered_and_do_not_collide() -> None:
    bands = [primitives.TITLE_Y, primitives.SCALARS_Y, primitives.ARRAY_Y,
             primitives.CAPTION_Y, primitives.CODE_Y]
    assert bands == sorted(bands, reverse=True), "bands must descend the frame"
    for upper, lower in zip(bands, bands[1:]):
        assert upper - lower >= 0.5, "bands need vertical separation to not collide"


def test_no_element_may_be_wider_than_the_safe_area() -> None:
    assert primitives.CODE_MAX_WIDTH <= primitives.SAFE_WIDTH
    assert primitives.SAFE_WIDTH < primitives.FRAME_WIDTH


def test_the_code_panel_fits_within_its_band() -> None:
    """It must not grow up into the caption band."""
    top_of_code = primitives.CODE_Y + primitives.CODE_MAX_HEIGHT / 2
    assert top_of_code < primitives.CAPTION_Y


# ---- readability floors ---------------------------------------------------


def test_the_source_window_cannot_exceed_the_readability_floor() -> None:
    """Window lines x minimum line height must fit the code band, or the
    text would have to shrink below what a phone viewer can read."""
    visible_lines = DEFAULT_WINDOW_RADIUS * 2 + 1
    needed = visible_lines * primitives.MIN_CODE_LINE_HEIGHT_UNITS
    assert needed <= primitives.CODE_MAX_HEIGHT, (
        f"{visible_lines} lines need {needed:.2f} units but the band is "
        f"{primitives.CODE_MAX_HEIGHT}"
    )


def test_the_code_panel_fills_the_width_rather_than_only_shrinking() -> None:
    """The original defect: the panel was only ever capped, so a short
    snippet stayed small and the code was the least readable element."""
    source = _source()
    assert f"scale_to_fit_width({primitives.CODE_MAX_WIDTH}" in source


def test_array_cells_produce_a_readable_pixel_size() -> None:
    pixels = primitives.CELL_SIDE * primitives.PIXELS_PER_UNIT
    assert pixels >= 100, f"array cells are only {pixels:.0f}px"


def test_cell_glyphs_are_fitted_inside_their_box() -> None:
    """A glyph wider than its cell overlaps the neighbour — observed."""
    from code2shorts.visualization.state import ArraySnapshot, FrameState

    state = FrameState(
        step_index=0, event_type="x", line_number=1, description="d",
        arrays={"a": ArraySnapshot(name="a", cells=["R", "12"])},
    )
    emitted = "\n".join(primitives.array_row(state))
    assert "scale_to_fit_height" in emitted
    assert "scale_to_fit_width" in emitted


# ---- no magic shrink ------------------------------------------------------


def test_no_global_scale_is_applied_to_the_whole_scene() -> None:
    """Solving layout by shrinking everything is what this phase removed."""
    source = _source()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("scale", "scale_to_fit_height", "scale_to_fit_width"):
                target = getattr(node.func.value, "id", "")
                assert target not in ("self", "scene"), "whole-scene scaling"


# ---- the invariant Phase 4.5.1 established --------------------------------


def test_narrowing_the_window_does_not_move_the_highlighted_line() -> None:
    """Readability was NOT bought by changing semantic line numbers."""
    from code2shorts.visualization.code_state import build_code_state
    from code2shorts.core.models import SourceLocation

    source_files = {"Main.java": "\n".join(f"line {i}" for i in range(1, 31))}
    location = SourceLocation(file="Main.java", line=15)

    wide = build_code_state(location, source_files, window_radius=6)
    narrow = build_code_state(location, source_files, window_radius=4)

    assert wide.highlight_line == narrow.highlight_line == 15
    assert len(narrow.lines) < len(wide.lines)
    # the highlighted line is inside the narrower window's range
    assert narrow.start_line <= 15 < narrow.start_line + len(narrow.lines)


# ---- measured band clearances (Phase 6.1 second pass) ---------------------
#
# Band midpoints alone do not prove non-overlap: each band has EXTENT.
# Three real collisions were found by inspecting rendered frames — a
# pointer label through the scalar readout, a 4-line caption through the
# array index row, and a stacked converged-pointer label through the
# scalars. These assert the actual edges clear each other.

_INDEX_ROW = 0.30       # index labels hang below the cells
_LABEL_HEIGHT = 0.35    # a pointer label at POINTER_FONT_SIZE


def _array_bottom() -> float:
    return primitives.ARRAY_Y - primitives.CELL_SIDE / 2 - _INDEX_ROW


def _pointer_top(depth: int = 0) -> float:
    offset = 0.30 + depth * 0.34
    return primitives.ARRAY_Y + primitives.CELL_SIDE / 2 + offset + _LABEL_HEIGHT


def test_pointer_labels_clear_the_scalar_readout() -> None:
    assert _pointer_top(depth=0) < primitives.SCALARS_Y - 0.15


def test_stacked_pointer_labels_also_clear_the_scalar_readout() -> None:
    """Converged pointers stack; the upper label must still clear."""
    assert _pointer_top(depth=1) < primitives.SCALARS_Y - 0.15


def test_the_caption_clears_the_array_index_row() -> None:
    caption_top = primitives.CAPTION_Y + primitives.CAPTION_MAX_HEIGHT / 2
    assert caption_top < _array_bottom()


def test_the_caption_clears_the_code_panel() -> None:
    caption_bottom = primitives.CAPTION_Y - primitives.CAPTION_MAX_HEIGHT / 2
    code_top = primitives.CODE_Y + primitives.CODE_MAX_HEIGHT / 2
    assert caption_bottom > code_top


def test_the_code_panel_stays_inside_the_safe_bottom() -> None:
    code_bottom = primitives.CODE_Y - primitives.CODE_MAX_HEIGHT / 2
    assert code_bottom >= primitives.SAFE_BOTTOM


def test_the_code_band_is_the_largest_single_region() -> None:
    """The code is the primary teaching surface, so it should own more
    vertical space than the caption or the structure panel."""
    assert primitives.CODE_MAX_HEIGHT > primitives.CAPTION_MAX_HEIGHT
    assert primitives.CODE_MAX_HEIGHT > primitives.STRUCTURE_MAX_HEIGHT
    assert primitives.CODE_MAX_HEIGHT * primitives.PIXELS_PER_UNIT >= 550

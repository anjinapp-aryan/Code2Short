"""Trusted Manim source fragments.

Every function here is repository-owned code that emits Python source
built from validated FrameState data. All dynamic values go through
`repr()`, which renders them as Python *string literals* — never as
executable syntax. Nothing in a VisualizationPlan or an ExecutionTrace can
become code by passing through this module; the worst a hostile value can
do is render as visible text inside a Text() label.

Uses only Manim Community primitives already shipped with our existing
dependency (ADR: open-source visualization reuse strategy) — no external
visualization library.
"""

from __future__ import annotations

from code2shorts.visualization.code_state import CodeState
from code2shorts.visualization.state import FrameState

# 9:16 layout. Manim's frame is 8 units tall and ~4.5 wide at this aspect
# ratio, so vertical space is the scarce resource and every element gets an
# explicit, non-overlapping band. Positioning by absolute y (move_to)
# rather than relative chaining (next_to/to_edge) is deliberate: the first
# real render put the caption straight through the code panel, because
# to_edge(DOWN) for the code and a next_to(ORIGIN, DOWN) caption were sized
# independently and collided. Fixed bands cannot collide by construction.
MAX_CELLS_BEFORE_SHRINK = 8
CELL_FONT_SIZE = 44
INDEX_FONT_SIZE = 24
SCALAR_FONT_SIZE = 28
CODE_FONT_SIZE = 20

TITLE_Y = 3.55
SCALARS_Y = 2.75
ARRAY_Y = 0.60          # pointer labels rise ~1.4 above this
CAPTION_Y = -1.15
CODE_Y = -2.65
# Windowing (Phase 4.3) caps the line count, so the panel can afford more
# height than when it had to swallow a whole file — this is what makes the
# code actually readable at 1080x1920 rather than merely present.
CODE_MAX_WIDTH = 6.8
CODE_MAX_HEIGHT = 2.55


def _lit(value: object) -> str:
    """The single escaping choke point: data becomes a Python literal."""
    return repr(value)


def array_row(state: FrameState, var: str = "arr_group") -> list[str]:
    """Array cells as labelled tiles, with index labels beneath."""
    array = state.primary_array
    if array is None or not array.cells:
        return [f"{var} = VGroup()"]

    cell_count = len(array.cells)
    scale = 1.0 if cell_count <= MAX_CELLS_BEFORE_SHRINK else MAX_CELLS_BEFORE_SHRINK / cell_count

    highlighted: set[int] = set()
    for indices in state.written_indices.values():
        highlighted.update(indices)
    read: set[int] = set()
    for indices in state.read_indices.values():
        read.update(indices)

    lines = [f"{var} = VGroup()"]
    for index, cell in enumerate(array.cells):
        if index in highlighted:
            colour = "YELLOW"
        elif index in read:
            colour = "TEAL"
        else:
            colour = "WHITE"
        lines += [
            f"_box_{index} = Square(side_length=0.9, color={colour}, stroke_width=3)",
            f"_txt_{index} = Text({_lit(str(cell))}, font_size={CELL_FONT_SIZE})"
            f".move_to(_box_{index}.get_center())",
            f"_idx_{index} = Text({_lit(str(index))}, font_size={INDEX_FONT_SIZE}, color=GREY)"
            f".next_to(_box_{index}, DOWN, buff=0.12)",
            f"{var}.add(VGroup(_box_{index}, _txt_{index}, _idx_{index}))",
        ]
    lines += [
        f"{var}.arrange(RIGHT, buff=0.12)",
        f"{var}.scale({scale:.4f})",
        f"{var}.move_to([0, {ARRAY_Y}, 0])",
    ]
    return lines


def pointer_arrows(state: FrameState, array_var: str = "arr_group", var: str = "ptr_group") -> list[str]:
    """One labelled arrow per scalar currently addressing a cell.

    Multiple pointers on the same cell are stacked vertically so their
    labels never overlap — this is what keeps `left`/`right` legible when
    they converge, and it generalises to any number of pointers.
    """
    array = state.primary_array
    pointers = state.pointers
    if array is None or not pointers:
        return [f"{var} = VGroup()"]

    by_index: dict[int, list[str]] = {}
    for pointer in pointers:
        by_index.setdefault(pointer.index, []).append(pointer.name)

    lines = [f"{var} = VGroup()"]
    counter = 0
    for index, names in sorted(by_index.items()):
        for depth, name in enumerate(sorted(names)):
            offset = 0.55 + depth * 0.45
            lines += [
                f"_cell_{counter} = {array_var}[{index}]",
                f"_lbl_{counter} = Text({_lit(name)}, font_size={SCALAR_FONT_SIZE}, color=YELLOW)",
                f"_lbl_{counter}.next_to(_cell_{counter}, UP, buff={offset:.2f})",
                f"_arw_{counter} = Arrow(start=_lbl_{counter}.get_bottom(), "
                f"end=_cell_{counter}.get_top(), buff=0.05, stroke_width=4, color=YELLOW)",
                f"{var}.add(VGroup(_lbl_{counter}, _arw_{counter}))",
            ]
            counter += 1
    return lines


def scalar_panel(state: FrameState, var: str = "vars_group") -> list[str]:
    """Non-array variables and their current values, deterministically
    ordered (sorted by name) so the same trace always lays out identically."""
    if not state.scalars:
        return [f"{var} = VGroup()"]
    parts = [f"{name} = {value}" for name, value in sorted(state.scalars.items())]
    text = "   ".join(parts)
    return [
        f"{var} = Text({_lit(text)}, font_size={SCALAR_FONT_SIZE}, color=BLUE_B)",
        # Long value sets (an array printed as a scalar, many variables)
        # must shrink rather than run off a 4.5-unit-wide frame.
        f"{var}.scale_to_fit_width(min({var}.width, 4.0)) if {var}.width > 4.0 else {var}",
        f"{var}.move_to([0, {SCALARS_Y}, 0])",
    ]


def code_panel(state: CodeState, var: str = "code_group") -> list[str]:
    """Source window with the executing line highlighted.

    Built on Manim's `Code` mobject (the reason no third-party
    code-highlighting package was adopted — see docs/OPEN_SOURCE_REUSE.md):
    `code_lines` gives per-line access and `line_numbers_from` lets a
    WINDOW keep the file's true line numbers, so a window over lines 38-46
    is labelled 38-46 rather than 1-9.

    The highlight both boxes the executing line and dims the rest. The
    dimming technique is adapted in DESIGN ONLY from code-video-generator's
    `HighlightLines` (Apache-2.0); none of its code is used, because it
    targets Manim ~0.10 attributes (`.code`, `.line_no_from`) that do not
    exist in the 0.21 we run — verified, see the Phase 4.3 reuse audit.
    """
    if not state.lines:
        return [f"{var} = VGroup()"]

    source = "\n".join(state.lines)
    lines = [
        f"{var} = Code(code_string={_lit(source)}, language='java', "
        f"add_line_numbers=True, line_numbers_from={state.start_line}, "
        f"background='window', "
        f"paragraph_config={{'font_size': {CODE_FONT_SIZE}}})",
    ]

    offset = state.highlight_offset
    if offset is not None:
        # Guard inside the GENERATED source too — belt and braces against a
        # window/line mismatch ever producing an IndexError mid-render.
        lines += [
            f"_hl_idx = {offset}",
            f"if 0 <= _hl_idx < len({var}.code_lines):",
            f"    for _i, _ln in enumerate({var}.code_lines):",
            f"        _ln.set_opacity(1.0 if _i == _hl_idx else 0.45)",
            f"    _hl = SurroundingRectangle({var}.code_lines[_hl_idx], "
            f"color=YELLOW, stroke_width=2, buff=0.03)",
            f"    {var} = VGroup({var}, _hl)",
        ]

    lines += [
        # Fit within BOTH dimensions — a tall snippet that only respected
        # width would grow up into the caption band.
        f"if {var}.width > {CODE_MAX_WIDTH}: {var}.scale_to_fit_width({CODE_MAX_WIDTH})",
        f"if {var}.height > {CODE_MAX_HEIGHT}: {var}.scale_to_fit_height({CODE_MAX_HEIGHT})",
        # Position last, after any regrouping, so the whole group lands in
        # its band rather than only the code mobject.
        f"{var}.move_to([0, {CODE_Y}, 0])",
    ]
    return lines


def title_text(text: str, var: str = "title") -> list[str]:
    return [
        f"{var} = Text({_lit(text)}, font_size=40, weight=BOLD)",
        f"if {var}.width > 4.2: {var}.scale_to_fit_width(4.2)",
        f"{var}.move_to([0, {TITLE_Y}, 0])",
    ]


def caption_text(text: str, var: str = "caption") -> list[str]:
    return [
        f"{var} = Text({_lit(text)}, font_size=26, color=GREY_A)",
        f"if {var}.width > 4.2: {var}.scale_to_fit_width(4.2)",
        f"{var}.move_to([0, {CAPTION_Y}, 0])",
    ]

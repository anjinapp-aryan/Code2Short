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

# 9:16 teaching layout, for the REAL frame: 4.5 units wide x 8 tall.
#
# Phase 6.1: the renderer now derives `frame_width` from the pixel aspect
# (see manim_renderer.build_scene_source), so one unit is 240 px on BOTH
# axes and the whole 1080x1920 image is addressable. Before that fix the
# frame was 14.22 units wide, Manim letterboxed the scene into ~31% of the
# height, and every element was squeezed into a central band.
#
# All sizes below are therefore stated in units AND in the pixels they
# actually produce, because "looks fine in Manim coordinates" was exactly
# the reasoning that produced an unreadable video.
PIXELS_PER_UNIT = 240.0         # 1920 px / 8 units, and 1080 px / 4.5 units

FRAME_WIDTH = 4.5
FRAME_HEIGHT = 8.0

# Safe area: mobile players overlay controls, titles and progress bars at
# the extreme top and bottom. Teaching content stays inside this box.
SAFE_MARGIN_X = 0.18            # 43 px each side
SAFE_MARGIN_Y = 0.55            # 132 px top and bottom
SAFE_WIDTH = FRAME_WIDTH - 2 * SAFE_MARGIN_X    # 4.14 units = 994 px
SAFE_TOP = FRAME_HEIGHT / 2 - SAFE_MARGIN_Y     # +3.45
SAFE_BOTTOM = -FRAME_HEIGHT / 2 + SAFE_MARGIN_Y  # -3.45

# Explicit, non-overlapping bands. Positioning by absolute y (move_to)
# rather than relative chaining is deliberate: the first real render put
# the caption straight through the code panel because independently sized
# to_edge/next_to elements collided. Fixed bands cannot collide.
TITLE_Y = 3.25                  # small band — the lesson is the content
SCALARS_Y = 2.72
ARRAY_Y = 1.08                  # pointer labels rise ~0.9 above this;
                                # measured: a tall label at 1.45 touched
                                # the scalar readout at 2.72
CAPTION_Y = -0.15
CODE_Y = -2.05                  # centre of the code panel

MAX_CELLS_BEFORE_SHRINK = 8

# Font sizes are Manim points at a 8-unit-tall frame. Measured heights at
# 240 px/unit are given so the readability claim is checkable, not asserted.
CELL_FONT_SIZE = 52             # array characters
INDEX_FONT_SIZE = 30            # index labels under cells
SCALAR_FONT_SIZE = 34           # variable readout
POINTER_FONT_SIZE = 30          # pointer labels above the array
CODE_FONT_SIZE = 20             # Code() default; real size is set by width

# The code panel is the primary teaching surface, so it gets the largest
# band and is fitted to the safe WIDTH (not shrunk to fit leftover space).
CODE_MAX_WIDTH = SAFE_WIDTH             # 4.14 units = 994 px
CODE_MAX_HEIGHT = 2.50                  # 600 px
MIN_CODE_LINE_HEIGHT_UNITS = 0.20       # 48 px per line — readability floor

# Array cells sized so a 7-element array fills most of the safe width.
CELL_SIDE = 0.52                        # 125 px per cell
CELL_BUFF = 0.06

# Captions wrap instead of shrinking. A single 180-character narration
# scaled to the safe width rendered at roughly a tenth the height of the
# code panel - present, but not readable on a phone.
CAPTION_FONT_SIZE = 26
CAPTION_CHARS_PER_LINE = 46
CAPTION_MAX_LINES = 4
CAPTION_MAX_HEIGHT = 1.05               # 252 px; keeps clear of the code band

# Map/sequence panels grow DOWNWARD with their contents, unlike the
# single-row array. Anchoring them at a fixed centre let a 3-entry map
# overrun the caption, so they are bounded and top-aligned instead.
STRUCTURE_TOP = 2.40                    # just below the scalar readout
STRUCTURE_MAX_HEIGHT = 1.45             # 348 px, ends clear of the caption


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
            f"_box_{index} = Square(side_length={CELL_SIDE}, color={colour}, stroke_width=4)",
            f"_txt_{index} = Text({_lit(str(cell))}, font_size={CELL_FONT_SIZE})",
            # A glyph wider or taller than its box overlaps the
            # neighbouring cell — fit inside, then centre.
            f"if _txt_{index}.height > {CELL_SIDE * 0.58:.3f}: "
            f"_txt_{index}.scale_to_fit_height({CELL_SIDE * 0.58:.3f})",
            f"if _txt_{index}.width > {CELL_SIDE * 0.80:.3f}: "
            f"_txt_{index}.scale_to_fit_width({CELL_SIDE * 0.80:.3f})",
            f"_txt_{index}.move_to(_box_{index}.get_center())",
            f"_idx_{index} = Text({_lit(str(index))}, font_size={INDEX_FONT_SIZE}, color=GREY)"
            f".next_to(_box_{index}, DOWN, buff=0.12)",
            f"{var}.add(VGroup(_box_{index}, _txt_{index}, _idx_{index}))",
        ]
    lines += [
        f"{var}.arrange(RIGHT, buff={CELL_BUFF})",
        f"{var}.scale({scale:.4f})",
        # Long arrays must stay inside the safe area rather than run
        # off the frame edges.
        f"if {var}.width > {SAFE_WIDTH}: {var}.scale_to_fit_width({SAFE_WIDTH})",
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
            # Stacked labels (pointers converged on one cell) must still
            # clear the scalar readout above: measured, a 0.40 step put
            # the second label through it at the termination frame.
            offset = 0.30 + depth * 0.34
            lines += [
                f"_cell_{counter} = {array_var}[{index}]",
                f"_lbl_{counter} = Text({_lit(name)}, font_size={POINTER_FONT_SIZE}, color=YELLOW)",
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
        f"if {var}.width > {SAFE_WIDTH}: {var}.scale_to_fit_width({SAFE_WIDTH})",
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
        # Phase 6.1: FILL the safe width rather than merely capping it.
        # Previously the panel was only ever shrunk, so a short snippet
        # stayed small and the code was the least readable thing on screen
        # — the opposite of what a code-teaching video needs. Scaling up to
        # the safe width makes the code the largest element by default.
        f"{var}.scale_to_fit_width({CODE_MAX_WIDTH})",
        # Height is the hard constraint: a tall window would otherwise grow
        # up through the caption band. Shrinking here can push the panel
        # below the readability floor, which means the WINDOW was too tall
        # — that is handled upstream by narrowing the source window, not by
        # letting the text become unreadable.
        f"if {var}.height > {CODE_MAX_HEIGHT}: {var}.scale_to_fit_height({CODE_MAX_HEIGHT})",
        # Position last, after any regrouping, so the whole group lands in
        # its band rather than only the code mobject.
        f"{var}.move_to([0, {CODE_Y}, 0])",
    ]
    return lines


def title_text(text: str, var: str = "title") -> list[str]:
    return [
        f"{var} = Text({_lit(text)}, font_size=40, weight=BOLD)",
        f"if {var}.width > {SAFE_WIDTH}: {var}.scale_to_fit_width({SAFE_WIDTH})",
        f"{var}.move_to([0, {TITLE_Y}, 0])",
    ]


def caption_text(text: str, var: str = "caption") -> list[str]:
    """The step's explanation, wrapped rather than shrunk.

    A single long line scaled to the safe width becomes unreadably small —
    a real LLM narration of ~180 characters rendered at roughly a tenth
    the height of the code. Wrapping keeps the font size and grows
    downward instead, and the band is capped so it cannot reach the code
    panel.
    """
    wrapped = _wrap(text, CAPTION_CHARS_PER_LINE, CAPTION_MAX_LINES)
    return [
        f"{var} = Text({_lit(wrapped)}, font_size={CAPTION_FONT_SIZE}, "
        f"color=GREY_A, line_spacing=0.8)",
        # Width first (long unbroken tokens), then the band height.
        f"if {var}.width > {SAFE_WIDTH}: {var}.scale_to_fit_width({SAFE_WIDTH})",
        f"if {var}.height > {CAPTION_MAX_HEIGHT}: "
        f"{var}.scale_to_fit_height({CAPTION_MAX_HEIGHT})",
        f"{var}.move_to([0, {CAPTION_Y}, 0])",
    ]


def _wrap(text: str, width: int, max_lines: int) -> str:
    """Greedy word wrap. Deliberately not `textwrap` on the rendered
    string: the result is passed through `_lit()` as a Python literal, so
    it must stay a plain str with explicit newlines and nothing else.

    Truncates with an ellipsis only when the caption would exceed the
    band — the narration itself is never shortened (ADR-5.11); this is a
    presentation cap on what one frame displays.
    """
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,") + " ..."
    return "\n".join(lines)


def map_panel(state: FrameState, var: str = "map_group") -> list[str]:
    """Observed key -> value entries of a Map.

    Renders EXACTLY what the trace observed and nothing more. There is no
    bucket, capacity or collision rendering, because the trace never
    captures those (ADR-6.2) — drawing them would mean inventing state.

    When the map's order is not semantic (a HashMap, whose iteration order
    the JLS does not specify), the entries were canonically sorted for
    determinism and the panel says so, rather than letting a viewer read
    the order as insertion order.
    """
    snapshot = state.primary_map
    if snapshot is None:
        return [f"{var} = VGroup()"]

    lines = [f"{var} = VGroup()"]
    if not snapshot.keys:
        lines += [
            f"_mempty = Text({_lit(snapshot.name + ' is empty')}, "
            f"font_size={INDEX_FONT_SIZE}, color=GREY)",
            f"{var}.add(_mempty)",
        ]
    else:
        for position, (key, value) in enumerate(snapshot.entries):
            changed = state.changed_collection == snapshot.name
            colour = "YELLOW" if changed and position == len(snapshot.keys) - 1 else "WHITE"
            lines += [
                f"_mbox_{position} = Rectangle(width=1.9, height=0.6, color={colour}, "
                "stroke_width=3)",
                f"_mtxt_{position} = Text({_lit(f'{key} -> {value}')}, "
                f"font_size={INDEX_FONT_SIZE}).move_to(_mbox_{position}.get_center())",
                f"{var}.add(VGroup(_mbox_{position}, _mtxt_{position}))",
            ]
        lines.append(f"{var}.arrange(DOWN, buff=0.12)")

    if not snapshot.ordered and snapshot.keys:
        lines += [
            f"_mnote = Text({_lit('order shown is canonical, not insertion order')}, "
            f"font_size={INDEX_FONT_SIZE - 4}, color=GREY)",
            f"_mnote.next_to({var}, DOWN, buff=0.15)",
            f"{var}.add(_mnote)",
        ]

    lines += [
        f"_mlabel = Text({_lit(snapshot.name)}, font_size={INDEX_FONT_SIZE}, color=GREY)",
        f"_mlabel.next_to({var}, UP, buff=0.18)",
        f"{var}.add(_mlabel)",
        f"if {var}.height > {STRUCTURE_MAX_HEIGHT}: "
        f"{var}.scale_to_fit_height({STRUCTURE_MAX_HEIGHT})",
        f"if {var}.width > {SAFE_WIDTH}: {var}.scale_to_fit_width({SAFE_WIDTH})",
        # Top-aligned: content grows down into free space, never up
        # into the scalars or down through the caption.
        f"{var}.move_to([0, {STRUCTURE_TOP} - {var}.height / 2, 0])",
    ]
    return lines


def sequence_panel(state: FrameState, var: str = "seq_group") -> list[str]:
    """Observed elements of a Deque/List/Queue.

    One primitive serves stack-like and queue-like use. The orientation is
    chosen from the OBSERVED operation's end (`active_end`), never from the
    algorithm's name: `push`/`pop` act on the head, so the sequence is
    drawn as a vertical stack with the active end on top; `offer`/`addLast`
    act on the tail, so it is drawn horizontally front-to-rear.
    """
    snapshot = state.primary_sequence
    if snapshot is None:
        return [f"{var} = VGroup()"]

    # LIFO (insert and remove share an end) reads naturally as a vertical
    # stack; FIFO as a horizontal queue. Until BOTH an insertion and a
    # removal have been observed the discipline is genuinely unknown, so
    # fall back to the end the last operation touched rather than
    # asserting a discipline nothing has demonstrated yet.
    discipline = snapshot.discipline
    if discipline == "lifo":
        stack_like = True
    elif discipline == "fifo":
        stack_like = False
    else:
        stack_like = snapshot.active_end != "back"

    lines = [f"{var} = VGroup()"]
    if not snapshot.elements:
        lines += [
            f"_sempty = Text({_lit(snapshot.name + ' is empty')}, "
            f"font_size={INDEX_FONT_SIZE}, color=GREY)",
            f"{var}.add(_sempty)",
        ]
    else:
        changed = state.changed_collection == snapshot.name
        for position, element in enumerate(snapshot.elements):
            # Position 0 is the collection's own head — the end `push`/`pop`
            # and `poll` operate on.
            colour = "YELLOW" if changed and position == 0 else "WHITE"
            lines += [
                f"_sbox_{position} = Square(side_length=0.7, color={colour}, stroke_width=3)",
                f"_stxt_{position} = Text({_lit(str(element))}, font_size={CELL_FONT_SIZE})"
                f".move_to(_sbox_{position}.get_center())",
                f"{var}.add(VGroup(_sbox_{position}, _stxt_{position}))",
            ]
        direction = "DOWN" if stack_like else "RIGHT"
        lines.append(f"{var}.arrange({direction}, buff=0.1)")

    end_label = "top" if stack_like else "front"
    if discipline == "unknown":
        # Do not teach an order discipline that has not been observed.
        end_label = "front" if snapshot.active_end != "back" else "back"
    lines += [
        f"_slabel = Text({_lit(f'{snapshot.name} ({end_label} first)')}, "
        f"font_size={INDEX_FONT_SIZE}, color=GREY)",
        f"_slabel.next_to({var}, UP, buff=0.18)",
        f"{var}.add(_slabel)",
        f"if {var}.height > {STRUCTURE_MAX_HEIGHT}: "
        f"{var}.scale_to_fit_height({STRUCTURE_MAX_HEIGHT})",
        f"if {var}.width > {SAFE_WIDTH}: {var}.scale_to_fit_width({SAFE_WIDTH})",
        # Top-aligned: content grows down into free space, never up
        # into the scalars or down through the caption.
        f"{var}.move_to([0, {STRUCTURE_TOP} - {var}.height / 2, 0])",
    ]
    return lines

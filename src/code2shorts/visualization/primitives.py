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

from code2shorts.core.models import SourceLocation
from code2shorts.visualization.code_state import CodeState, build_code_state
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

# Syntax theme, chosen by MEASUREMENT rather than taste. Rendering the
# same snippet under each candidate and measuring the code region:
#
#   default      ink 3.68%  strong 2.06%  mean-ink 157.2
#   monokai      ink 4.26%  strong 2.56%  mean-ink 168.5   <- chosen
#   github-dark  ink 3.88%  strong 2.36%  mean-ink 162.7
#   one-dark     ink 4.21%  strong 1.93%  mean-ink 138.4
#
# Manim's default theme targets an editor, not a phone at arm's length;
# its comment and type colours sit close to the panel background.
CODE_THEME = "monokai"

# How far NON-executing lines are dimmed. This was 0.45, which is the
# direct cause of "the surrounding code is too faint": at 45% against a
# dark panel, darker syntax colours fall to near-background. Context
# lines must stay readable — they are what makes the active line make
# sense — so the active line leads by highlight and weight, not by
# everything else being hidden.
CONTEXT_LINE_OPACITY = 0.78
MIN_CONTEXT_LINE_OPACITY = 0.70   # readability floor, asserted by test

# The code panel is the primary teaching surface, so it gets the largest
# band and is fitted to the safe WIDTH (not shrunk to fit leftover space).
CODE_MAX_WIDTH = SAFE_WIDTH             # 4.14 units = 994 px
CODE_MAX_HEIGHT = 2.50                  # 600 px
MIN_CODE_LINE_HEIGHT_UNITS = 0.20       # 48 px per line — GROWTH TARGET:
                                        # the window fitter will not add a
                                        # line that pushes below this.

# The floor the renderer can actually GUARANTEE, which is set by the source
# and not by the layout. The panel is monospace and fitted to the 994 px
# safe width, so the longest visible line fixes the glyph size:
#
#     994 px / 53 chars = 18.8 px per character
#     monospace line height ~= 2.34x character width = 44 px = 0.183 units
#
# 53 characters is a real Java method signature in the supported fixtures
# ("    public static boolean isPalindrome(char[] chars) {"). A window
# containing it CANNOT reach 0.20 units per line at 1080 px wide, and no
# layout change fixes that — only a shorter source line would. Measured,
# not assumed; the value is asserted by the real-render tests.
ABS_MIN_CODE_LINE_HEIGHT_UNITS = 0.175  # 42 px

# Array cells sized so a 7-element array fills most of the safe width.
CELL_SIDE = 0.52                        # 125 px per cell
CELL_BUFF = 0.06

# Captions wrap instead of shrinking. A single 180-character narration
# scaled to the safe width rendered at roughly a tenth the height of the
# code panel - present, but not readable on a phone.
CAPTION_FONT_SIZE = 26
CAPTION_CHARS_PER_LINE = 46
CAPTION_MAX_LINES = 5
CAPTION_MAX_HEIGHT = 1.30               # 312 px

# Vertical gap between composed bands. Small and constant: the point of
# the composer is that leftover space goes to the CODE PANEL, not into
# the gaps between elements.
BAND_GAP = 0.14                         # 34 px

# The code panel's height is a REMAINDER, not a constant. CODE_MAX_HEIGHT
# survives only as the standalone default used when `code_panel` is
# emitted without the composer (and by tests that exercise it alone).
#
# Root cause it replaces: with a fixed band the panel spanned -0.80..-3.30
# no matter what was above it, so a one-line caption left ~255 px of
# unreachable black on the canvas while the code stayed small. Measured
# on a real frame: 0.54 units dead between the index row and the caption,
# 0.52 units dead between the caption and the code panel.

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
            ]
            # Exactly one arrow per cell, drawn from the innermost label.
            # An arrow per pointer would have to start above the stack and
            # therefore pass THROUGH every label beneath it — at the
            # convergence frame the shaft struck out the word "left". The
            # pointers share a cell, so one arrow says the same thing.
            if depth == 0:
                lines += [
                    f"_arw_{counter} = Arrow(start=_lbl_{counter}.get_bottom(), "
                    f"end=_cell_{counter}.get_top(), buff=0.05, stroke_width=4, color=YELLOW)",
                    f"{var}.add(VGroup(_lbl_{counter}, _arw_{counter}))",
                ]
            else:
                lines.append(f"{var}.add(_lbl_{counter})")
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


def _dedent_window(lines: list[str]) -> list[str]:
    """Drop the leading whitespace every visible line shares.

    Java inside a method body starts three or four levels deep, so a
    window can spend a quarter of its width on a margin that carries no
    information. Because the panel is fitted to the safe WIDTH, that
    margin is paid for in font size: removing it makes every glyph
    larger, which is the cheapest readability win available on a phone.

    Strictly presentation, and strictly the SHARED prefix — relative
    indentation between the visible lines is preserved exactly, so the
    block structure a learner reads is unchanged. No non-whitespace
    character is ever removed, and `CodeState` keeps the untouched source
    (the renderer may present the truth better; it may not alter it).
    """
    indents = [
        len(text) - len(text.lstrip(" "))
        for text in lines
        if text.strip()
    ]
    common = min(indents) if indents else 0
    if common == 0:
        return list(lines)
    return [text[common:] if text.strip() else text for text in lines]


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

    source = "\n".join(_dedent_window(state.lines))
    lines = [
        f"{var} = Code(code_string={_lit(source)}, language='java', "
        f"add_line_numbers=True, line_numbers_from={state.start_line}, "
        f"background='window', "
        f"formatter_style={_lit(CODE_THEME)}, "
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
            f"        _ln.set_opacity(1.0 if _i == _hl_idx else {CONTEXT_LINE_OPACITY})",
            # A filled, brighter surround: the active line leads by its own
            # emphasis rather than by the context being suppressed.
            f"    _hl = SurroundingRectangle({var}.code_lines[_hl_idx], "
            f"color=YELLOW, stroke_width=4, buff=0.045, "
            f"fill_color=YELLOW, fill_opacity=0.12)",
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


# Manim's Code mobject is MONOSPACE, so once the panel is fitted to the
# safe width its per-line height is fixed by the longest visible line:
#
#     line_height  ~=  CODE_LINE_ASPECT * SAFE_WIDTH / longest_line_chars
#
# CODE_LINE_ASPECT is calibrated from real renders and deliberately set to
# the LOW end of what was measured (2.31 and 2.40 across two windows), so
# the fitter under-estimates how much context fits. An over-estimate is
# the dangerous direction: it grows the window, the composer then has to
# scale the panel down to fit, and the text ends up below the floor the
# growth was supposed to protect.
CODE_LINE_ASPECT = 2.30
CODE_GUTTER_CHARS = 4                   # the line-number column
MAX_WINDOW_RADIUS = 8                   # 17 lines; beyond this a phone
                                        # viewer is reading a wall of text
CODE_HEIGHT_BUDGET = 2.60               # conservative composed height: the
                                        # real region is 2.9-3.4 units
                                        # depending on caption length


def fit_window_radius(
    location: SourceLocation,
    source_files: dict[str, str],
    minimum: int,
    maximum: int = MAX_WINDOW_RADIUS,
) -> int:
    """How many lines of context this canvas can actually afford to show.

    Section 7: visible line count is DERIVED from the available canvas,
    not fixed. A window of short lines is height-bound and keeps the
    minimum; a window of long lines is WIDTH-bound, so its text is small
    whatever the radius, and the leftover vertical space is better spent
    on context than left black.

    It asks `build_code_state` for each candidate rather than modelling
    the window itself. An earlier version reimplemented the centred
    window and so ignored the enclosing-block preference — it "grew" a
    9-line loop-body window into a 15-line one that dragged in the class
    signature, and the text got SMALLER. The selector is the only thing
    that knows what will be shown.

    Purely a function of the source text's shape — no algorithm, no file
    name, no step kind reaches this.
    """
    if location.file is None or location.file not in source_files:
        return minimum

    def estimate(radius: int) -> tuple[int, float]:
        state = build_code_state(location, source_files, radius)
        if not state.lines:
            return 0, 0.0
        # Estimate against what will actually be DRAWN, which is the
        # dedented window — otherwise the shared margin is counted twice
        # and the fitter under-reports how much context fits.
        drawn = _dedent_window(state.lines)
        widest = max((len(text) for text in drawn), default=1) + CODE_GUTTER_CHARS
        return len(state.lines), CODE_LINE_ASPECT * SAFE_WIDTH / max(widest, 1)

    # The size the text is ALREADY going to be. Where a single long source
    # line has already pushed this window below the target, refusing to
    # add context does not win that size back — it only leaves the space
    # black. Growth is judged against whichever is lower, the target or
    # what this window can actually achieve.
    _, baseline = estimate(minimum)
    if baseline < ABS_MIN_CODE_LINE_HEIGHT_UNITS:
        # Already unreadable at the minimum window, so a source line is
        # pathologically long. More context would only be more noise;
        # that is a source problem, not a layout one.
        return minimum
    acceptable = min(MIN_CODE_LINE_HEIGHT_UNITS, baseline)

    best = minimum
    for radius in range(minimum, maximum + 1):
        count, line_height = estimate(radius)
        if count == 0:
            break
        # Never accept a window that makes the text SMALLER than it
        # already was: one wider line entering the window costs every line.
        if line_height < acceptable:
            break
        if count * line_height > CODE_HEIGHT_BUDGET:
            break
        # Even at the guaranteed floor these lines must fit the region.
        # Without this the fitter could add context that the composer then
        # has to shrink below the floor to fit — growth that costs
        # readability is not growth.
        if count * ABS_MIN_CODE_LINE_HEIGHT_UNITS > CODE_HEIGHT_BUDGET:
            break
        best = radius
    return best


def compose_vertical(
    anchor_vars: list[str],
    caption_var: str = "caption",
    code_var: str | None = "code_group",
) -> list[str]:
    """Give the code panel every unit of vertical space nothing else needs.

    The upper elements (title, scalar readout, array/map/sequence) keep
    their fixed bands: their heights are content-bounded and they must not
    drift between steps, or the array would appear to jump as the caption
    changes length. Below them the layout becomes a REMAINDER:

        caption  -> pulled up under the MEASURED bottom of the structure
        code     -> fills what is left, down to the safe-area bottom

    Emitted as Manim source because the heights are only known once the
    mobjects exist; every value below is a numeric literal, so no data
    reaches the generated program.

    Generic by construction: it reads measured geometry, never a variable
    name, an algorithm or a step kind. With no structure above it the
    caption simply keeps its band, and with no code panel the caption is
    centred in the space that remains.
    """
    lines: list[str] = []

    # Where the composable region starts: under whatever the step drew
    # above it, or at the caption's own band if it drew nothing.
    if anchor_vars:
        tops = ", ".join(f"{name}.get_bottom()[1]" for name in anchor_vars)
        lines.append(f"_avail_top = min([{tops}]) - {BAND_GAP}")
    else:
        lines.append(f"_avail_top = {CAPTION_Y} + {CAPTION_MAX_HEIGHT / 2:.3f}")
    lines.append(f"_avail_top = min(_avail_top, {SAFE_TOP})")

    if code_var is None:
        # No code this step: centre the caption in the whole remainder
        # rather than leaving the lower half of the frame empty.
        lines += [
            f"{caption_var}.move_to([0, (_avail_top + {SAFE_BOTTOM}) / 2, 0])",
        ]
        return lines

    lines += [
        # Caption first: it is the smaller, less compressible element, and
        # the code panel is what should absorb the slack.
        f"{caption_var}.move_to("
        f"[0, _avail_top - {caption_var}.height / 2, 0])",
        f"_code_top = {caption_var}.get_bottom()[1] - {BAND_GAP}",
        f"_code_h = _code_top - ({SAFE_BOTTOM})",
        # Fill the safe width, then take the remaining height. Both are
        # limits, not targets: a small window is scaled UP into the space,
        # which is the whole point.
        f"{code_var}.scale_to_fit_width({CODE_MAX_WIDTH})",
        f"if {code_var}.height > _code_h and _code_h > 0:",
        f"    {code_var}.scale_to_fit_height(_code_h)",
        f"elif {code_var}.height < _code_h:",
        # Growing to fill the height must not push the panel past the safe
        # width, so re-clamp width after the height-driven scale-up.
        f"    {code_var}.scale_to_fit_height(_code_h)",
        f"    if {code_var}.width > {CODE_MAX_WIDTH}:",
        f"        {code_var}.scale_to_fit_width({CODE_MAX_WIDTH})",
        # Hang from the top of the region, directly under the explanation
        # it belongs to. Centring was tried and rejected on a real frame:
        # a width-bound panel that cannot fill the height then floats with
        # black above AND below it, reading as two layout errors instead
        # of one bottom margin.
        f"{code_var}.move_to([0, _code_top - {code_var}.height / 2, 0])",
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

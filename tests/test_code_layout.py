"""PHASE 6 — the code panel as an EXECUTION-FOCUS VIEW, not a screenshot.

Two layers of evidence, deliberately separated:

* Pure unit tests over the WINDOW SELECTION and the EMITTED Manim source.
  These run in the default suite (no Manim, no JVM) and cover the code
  shapes a real algorithm actually produces — short/medium/long files,
  the active line at the start, the middle and the end, long lines and
  nested blocks.

* One `real_render` test that puts the primitives through REAL Manim and
  measures the resulting mobjects. Geometry claims about a video are only
  worth what a renderer confirms — the Phase 6.1 letterboxing defect
  looked perfectly correct in the emitted source.
"""

from __future__ import annotations

import shutil

import pytest

from code2shorts.core.models import SourceLocation
from code2shorts.visualization import primitives
from code2shorts.visualization.code_state import (
    DEFAULT_WINDOW_RADIUS,
    build_code_state,
    enclosing_block,
)

FILE = "src/Main.java"


def _source(*lines: str) -> dict[str, str]:
    return {FILE: "\n".join(lines)}


def _state(lines: list[str], line: int):
    return build_code_state(SourceLocation(file=FILE, line=line), _source(*lines))


# ---------------------------------------------------------------------------
# Section 22 — the window must behave for every code shape, not just the
# palindrome fixture the golden path happens to use.
# ---------------------------------------------------------------------------

SHORT = ["class A {", "    int f() {", "        return 1;", "    }", "}"]

MEDIUM = (
    ["package p;", ""]
    + ["public class M {"]
    + [f"    int v{i} = {i};" for i in range(1, 13)]
    + ["}"]
)

LONG = ["package p;", "", "public class L {"] + [
    f"    int v{i} = {i};" for i in range(1, 60)
] + ["}"]

NESTED = [
    "class N {",
    "    void f(int[] a) {",
    "        for (int i = 0; i < a.length; i++) {",
    "            if (a[i] > 0) {",
    "                while (a[i] > 1) {",
    "                    a[i]--;",
    "                }",
    "            }",
    "        }",
    "    }",
    "}",
]

LONG_LINES = [
    "class W {",
    "    void f() {",
    "        String message = someHelper(firstArgument, secondArgument, third);",
    "    }",
    "}",
]


@pytest.mark.parametrize(
    "lines,line",
    [
        pytest.param(SHORT, 3, id="A-very-short"),
        pytest.param(MEDIUM, 8, id="B-medium"),
        pytest.param(LONG, 30, id="C-long"),
        pytest.param(LONG, 2, id="D-active-line-near-start"),
        pytest.param(LONG, 31, id="E-active-line-in-middle"),
        pytest.param(LONG, len(LONG) - 1, id="F-active-line-near-end"),
        pytest.param(LONG_LINES, 3, id="G-long-lines"),
        pytest.param(NESTED, 6, id="H-nested-blocks"),
        pytest.param(NESTED, 3, id="I-loops-and-conditions"),
    ],
)
def test_the_window_always_contains_the_executing_line(lines, line) -> None:
    """Whatever the shape, the learner can see the line that is running.

    This is the one invariant the presentation window may never trade
    away: a window that scrolled off its own highlight would point the
    viewer at the wrong statement, which is worse than showing no code.
    """
    state = _state(lines, line)
    assert state.highlight_line == line
    assert state.highlight_offset is not None
    assert 0 <= state.highlight_offset < len(state.lines)
    assert state.lines[state.highlight_offset] == lines[line - 1]


@pytest.mark.parametrize(
    "lines,line",
    [
        pytest.param(LONG, 30, id="long"),
        pytest.param(MEDIUM, 8, id="medium"),
        pytest.param(NESTED, 6, id="nested"),
    ],
)
def test_a_long_file_is_windowed_rather_than_shrunk(lines, line) -> None:
    """Never show 60 lines at a size nobody can read (section 8).

    The window is bounded by the radius regardless of file length, so the
    per-line height stays constant as files grow.
    """
    state = _state(lines, line)
    assert len(state.lines) <= DEFAULT_WINDOW_RADIUS * 2 + 1
    assert state.total_lines == len(lines)


def test_the_window_follows_execution() -> None:
    """Section 8: as execution advances the window advances with it."""
    early = _state(LONG, 8)
    late = _state(LONG, 45)
    assert late.start_line > early.start_line
    assert early.highlight_offset is not None and late.highlight_offset is not None


def test_the_window_shows_the_whole_enclosing_block() -> None:
    """Following execution inside one construct beats a blind centred
    window that drags in the package declaration.

    The window may extend BEYOND the block when there is spare room -
    leaving the panel half empty would waste the space the composer just
    reclaimed - but it must never cut the block short.
    """
    block = enclosing_block(NESTED, 6)
    assert block is not None
    state = _state(NESTED, 6)
    end_line = state.start_line + len(state.lines) - 1
    assert state.start_line <= block[0]
    assert end_line >= block[1]


def test_a_short_file_is_shown_whole() -> None:
    state = _state(SHORT, 3)
    assert state.lines == SHORT
    assert state.truncated is False


# ---------------------------------------------------------------------------
# Emitted-source contracts for the composer.
# ---------------------------------------------------------------------------


def _composed(anchors: list[str], code: str | None = "code_group") -> str:
    return "\n".join(primitives.compose_vertical(anchors, "caption", code))


def test_the_code_panel_takes_the_leftover_height() -> None:
    """The defect this closes: the code band was the constant 2.50 units
    whatever was above it, so a one-line caption left roughly 255 px of
    unreachable black on a 1920 px canvas."""
    source = _composed(["arr_group", "vars_group"])
    assert f"_code_h = _code_top - ({primitives.SAFE_BOTTOM})" in source
    # It must be able to GROW into that space, not merely be capped by it.
    assert source.count("scale_to_fit_height(_code_h)") == 2


def test_the_composed_region_starts_below_what_was_actually_drawn() -> None:
    """Measured geometry, not a constant: the caption follows the real
    bottom of the array/map/sequence group for this step."""
    source = _composed(["arr_group", "vars_group"])
    assert "arr_group.get_bottom()[1]" in source
    assert "vars_group.get_bottom()[1]" in source
    assert "min([" in source


def test_the_composer_never_leaves_the_safe_area() -> None:
    source = _composed(["arr_group"])
    assert str(primitives.SAFE_BOTTOM) in source
    assert f"min(_avail_top, {primitives.SAFE_TOP})" in source


def test_a_step_without_code_still_uses_the_whole_frame() -> None:
    """Section 18: no huge empty region just because a step drew no code."""
    source = _composed(["arr_group"], code=None)
    assert "caption.move_to([0, (_avail_top + " in source
    assert "code_group" not in source


def test_a_step_without_structure_still_composes() -> None:
    source = _composed([])
    assert "_avail_top" in source
    assert "code_group.move_to" in source


def test_the_composer_is_not_algorithm_aware() -> None:
    """Section 20: it responds to measured content, never to a name."""
    source = _composed(["arr_group", "vars_group"]) + _composed([], None)
    for name in ("palindrome", "reverse", "two_sum", "sort", "search", "queue"):
        assert name not in source.lower()


# ---------------------------------------------------------------------------
# Real Manim. Everything above reasons about emitted source; this measures
# what the renderer actually produces.
# ---------------------------------------------------------------------------

pytestmark_render = pytest.mark.skipif(
    shutil.which("manim") is None, reason="requires real Manim"
)



PALINDROME = [
    "package com.code2shorts.algorithms;",
    "",
    "public final class Main {",
    "    public static boolean isPalindrome(char[] chars) {",
    "        int left = 0;",
    "        int right = chars.length - 1;",
    "        while (left < right) {",
    "            char a = chars[left];",
    "            char b = chars[right];",
    "            if (a != b) {",
    "                return false;",
    "            }",
    "            left++;",
    "            right--;",
    "        }",
    "        return true;",
    "    }",
    "}",
]

LONG_CAPTION = (
    "The loop condition is evaluated one final time: is left three less "
    "than right three? No, so the loop terminates here."
)


def _render_composed(lines: list[str], active: int, caption: str):
    """Build the real mobjects for one composed frame and return them."""
    from manim import tempconfig

    from code2shorts.core.models import TraceEventType
    from code2shorts.visualization.state import ArraySnapshot, FrameState

    frame = FrameState(
        step_index=0,
        event_type=TraceEventType.VARIABLE_ASSIGN.value,
        line_number=active,
        description="composed",
        arrays={"chars": ArraySnapshot(name="chars", cells=list("RACECAR"))},
        scalars={"left": "0", "right": "6"},
    )
    radius = primitives.fit_window_radius(
        SourceLocation(file=FILE, line=active), _source(*lines),
        minimum=DEFAULT_WINDOW_RADIUS,
    )
    state = build_code_state(
        SourceLocation(file=FILE, line=active), _source(*lines), radius
    )
    body = (
        primitives.array_row(frame, var="arr_group")
        + primitives.pointer_arrows(frame, array_var="arr_group", var="ptr_group")
        + primitives.scalar_panel(frame, var="vars_group")
        + primitives.code_panel(state, var="code_group")
        + primitives.caption_text(caption, var="caption")
        + primitives.compose_vertical(
            ["arr_group", "ptr_group", "vars_group"], "caption", "code_group"
        )
    )
    with tempconfig(
        {
            "pixel_width": 1080,
            "pixel_height": 1920,
            "frame_height": primitives.FRAME_HEIGHT,
            "frame_width": primitives.FRAME_HEIGHT * (1080 / 1920),
        }
    ):
        namespace: dict = {}
        exec("from manim import *", namespace)  # noqa: S102 - repository-owned
        exec("\n".join(body), namespace)  # noqa: S102 - repository-owned
        return namespace, state


@pytest.mark.integration
@pytest.mark.real_render
@pytestmark_render
@pytest.mark.parametrize(
    "lines,active,caption",
    [
        pytest.param(PALINDROME, 7, "Short caption.", id="short-caption"),
        pytest.param(PALINDROME, 7, LONG_CAPTION, id="long-caption"),
        pytest.param(PALINDROME, 5, "Short caption.", id="active-line-near-start"),
        pytest.param(LONG, 30, "Short caption.", id="long-file"),
        pytest.param(NESTED, 6, "Short caption.", id="nested-long-lines"),
    ],
)
def test_real_manim_geometry_never_overflows_or_collides(lines, active, caption) -> None:
    """Measured under REAL Manim, for every code shape.

    Geometry claims about a video are worth only what a renderer confirms:
    the Phase 6.1 letterboxing defect looked entirely correct in the
    emitted source and was wrong on screen.
    """
    namespace, state = _render_composed(lines, active, caption)
    code_group = namespace["code_group"]
    caption_obj = namespace["caption"]

    top = code_group.get_top()[1]
    bottom = code_group.get_bottom()[1]

    assert bottom >= primitives.SAFE_BOTTOM - 1e-6, "code ran past the safe area"
    assert top <= caption_obj.get_bottom()[1] + 1e-6, "code overlapped the caption"
    assert code_group.width <= primitives.CODE_MAX_WIDTH + 1e-6, "code overflowed"

    per_line = (top - bottom) / max(len(state.lines), 1)
    # The guaranteed floor, not the growth target: a window forced to
    # contain a 53-character method signature is width-bound and cannot
    # reach the target however the bands are arranged. See
    # ABS_MIN_CODE_LINE_HEIGHT_UNITS.
    assert per_line >= primitives.ABS_MIN_CODE_LINE_HEIGHT_UNITS, (
        f"{per_line:.3f} units per line is below the guaranteed floor"
    )


@pytest.mark.integration
@pytest.mark.real_render
@pytestmark_render
def test_real_manim_composed_panel_beats_the_old_fixed_band() -> None:
    """The reclaimed space is real, not a rearrangement.

    Measured: the same window that the fixed 2.50-unit band produced now
    renders 2.97 units tall, because the band is a remainder rather than
    a constant sized for the worst-case caption.
    """
    namespace, state = _render_composed(PALINDROME, 7, "Short caption.")
    code_group = namespace["code_group"]
    height = code_group.get_top()[1] - code_group.get_bottom()[1]
    assert height > primitives.CODE_MAX_HEIGHT, (
        f"composed height {height:.2f} did not exceed the old fixed "
        f"{primitives.CODE_MAX_HEIGHT} band"
    )


def test_the_fitter_refuses_growth_that_shrinks_the_text() -> None:
    """Growth that costs readability is not growth.

    Widening the NESTED window past the method pulls in the class
    declaration at column zero, which defeats the shared-margin dedent and
    makes EVERY line smaller. The fitter must decline.
    """
    radius = primitives.fit_window_radius(
        SourceLocation(file=FILE, line=6), _source(*NESTED),
        minimum=DEFAULT_WINDOW_RADIUS,
    )
    chosen = build_code_state(SourceLocation(file=FILE, line=6), _source(*NESTED), radius)
    wider = build_code_state(
        SourceLocation(file=FILE, line=6), _source(*NESTED), radius + 2
    )

    def line_height(state) -> float:
        drawn = primitives._dedent_window(state.lines)
        widest = max(len(text) for text in drawn) + primitives.CODE_GUTTER_CHARS
        return primitives.CODE_LINE_ASPECT * primitives.SAFE_WIDTH / widest

    assert line_height(wider) < line_height(chosen)


def test_the_fitter_takes_growth_that_is_free() -> None:
    """When the extra lines are no wider, context costs nothing and the
    leftover height should become code rather than black."""
    # Uniform width, and wide enough that the panel is width-bound rather
    # than height-bound - the case where extra lines are genuinely free.
    uniform = (
        ["class U {"]
        + [f"    int someLongVariableName{i:02d} = compute({i:02d});" for i in range(40)]
        + ["}"]
    )
    radius = primitives.fit_window_radius(
        SourceLocation(file=FILE, line=20), _source(*uniform), minimum=2
    )
    assert radius > 2


@pytest.mark.integration
@pytest.mark.real_render
@pytestmark_render
def test_a_width_bound_window_still_meets_the_floor() -> None:
    """Long lines make the panel width-bound, so its text cannot grow.
    Whatever the fitter decides, the rendered result must stay legible."""
    namespace, state = _render_composed(NESTED, 6, "Short caption.")
    code_group = namespace["code_group"]
    height = code_group.get_top()[1] - code_group.get_bottom()[1]
    assert height / len(state.lines) >= primitives.ABS_MIN_CODE_LINE_HEIGHT_UNITS


def test_the_window_fitter_respects_the_readability_floor() -> None:
    """A pathological line length must NOT be answered with more lines."""
    wall = ["class X {"] + ["        " + "x" * 120 for _ in range(40)] + ["}"]
    radius = primitives.fit_window_radius(
        SourceLocation(file=FILE, line=20), _source(*wall), minimum=1
    )
    window = wall[max(0, 19 - radius) : 19 + radius + 1]
    widest = max(len(text) for text in window) + primitives.CODE_GUTTER_CHARS
    per_line = primitives.CODE_LINE_ASPECT * primitives.SAFE_WIDTH / widest
    # The floor cannot be met at all here, so the fitter must not grow.
    assert per_line < primitives.MIN_CODE_LINE_HEIGHT_UNITS
    assert radius == 1


def test_the_window_fitter_is_deterministic() -> None:
    location = SourceLocation(file=FILE, line=30)
    first = primitives.fit_window_radius(
        location, _source(*LONG), minimum=DEFAULT_WINDOW_RADIUS
    )
    second = primitives.fit_window_radius(
        location, _source(*LONG), minimum=DEFAULT_WINDOW_RADIUS
    )
    assert first == second


# ---------------------------------------------------------------------------
# Dedent is PRESENTATION. It may make the code bigger; it may not change it.
# ---------------------------------------------------------------------------


def test_dedent_removes_only_the_shared_margin() -> None:
    window = [
        "        while (left < right) {",
        "            char a = chars[left];",
        "        }",
    ]
    assert primitives._dedent_window(window) == [
        "while (left < right) {",
        "    char a = chars[left];",
        "}",
    ]


def test_dedent_never_removes_a_non_whitespace_character() -> None:
    """The renderer may present the truth better; it may not alter it."""
    for window in ([*NESTED], [*PALINDROME], [*LONG_LINES], [*SHORT]):
        drawn = primitives._dedent_window(window)
        assert len(drawn) == len(window)
        for before, after in zip(window, drawn):
            assert before.strip() == after.strip()


def test_dedent_preserves_relative_indentation() -> None:
    """Block structure is what a learner reads the indentation FOR."""
    window = NESTED[2:7]
    drawn = primitives._dedent_window(window)
    depths = [len(t) - len(t.lstrip(" ")) for t in window]
    drawn_depths = [len(t) - len(t.lstrip(" ")) for t in drawn]
    shift = depths[0] - drawn_depths[0]
    assert [d - shift for d in depths] == drawn_depths


def test_dedent_is_a_no_op_when_a_line_starts_at_column_zero() -> None:
    window = ["package p;", "", "public class M {"]
    assert primitives._dedent_window(window) == window


def test_the_code_panel_renders_the_dedented_window() -> None:
    state = build_code_state(
        SourceLocation(file=FILE, line=6), _source(*NESTED), 2
    )
    source = "\n".join(primitives.code_panel(state))
    # The shared eight-space margin is gone from the emitted literal, and
    # no code character went with it.
    assert "while (a[i] > 1) {" in source
    assert "a[i]--;" in source


def test_the_code_state_itself_is_never_dedented() -> None:
    """CodeState is the truth layer: it keeps the source exactly."""
    state = build_code_state(
        SourceLocation(file=FILE, line=6), _source(*NESTED), 2
    )
    assert state.lines[state.highlight_offset] == NESTED[5]
    assert state.lines[state.highlight_offset].startswith("                    ")


def test_the_window_keeps_context_on_both_sides_of_the_active_line() -> None:
    """Section 9: the code around the active line is what makes it mean
    something, and half of that lives ABOVE.

    Observed on a real frame: at `if (a != b) {` the window began on that
    exact line, so `char a = chars[left]` and `char b = chars[right]` -
    the two reads the caption was talking about - were off screen.
    """
    inner = [
        "class N {",
        "    void f(char[] c) {",
        "        int left = 0;",
        "        int right = 6;",
        "        while (left < right) {",
        "            char a = c[left];",
        "            char b = c[right];",
        "            if (a != b) {",
        "                return;",
        "            }",
        "            left++;",
        "            right--;",
        "        }",
        "    }",
        "}",
    ]
    active = 8                      # the `if` line
    state = _state(inner, active)
    assert state.start_line < active, "no context above the executing line"
    assert state.start_line + len(state.lines) - 1 > active, "no context below"
    visible = set(range(state.start_line, state.start_line + len(state.lines)))
    assert {6, 7} <= visible, "the reads the condition compares are off screen"


# ---------------------------------------------------------------------------
# Phase 6.4 — boilerplate must not set the font size for the whole panel.
# ---------------------------------------------------------------------------

METHOD_FILE = [
    "package com.example.algorithms;",
    "",
    "public final class Main {",
    "    public static boolean isPalindrome(char[] chars) {",
    "        int left = 0;",
    "        int right = chars.length - 1;",
    "        while (left < right) {",
    "            char a = chars[left];",
    "            char b = chars[right];",
    "            if (a != b) {",
    "                return false;",
    "            }",
    "            left++;",
    "            right--;",
    "        }",
    "        return true;",
    "    }",
    "}",
]


def _widest_drawn(line: int) -> int:
    """Longest line the panel will actually render, after dedent.

    This is the number that fixes the font size: the panel is scaled so
    the longest visible line spans the safe width, so one long line makes
    every line smaller.
    """
    state = _state(METHOD_FILE, line)
    drawn = primitives._dedent_window(state.lines)
    return max(len(text) for text in drawn)


def test_boilerplate_does_not_set_the_font_size() -> None:
    """The measured Phase 6.4 defect.

    At `int left = 0;` the window used to span lines 1-9, dragging in the
    package declaration and the 54-character method signature. Because the
    longest visible line fixes the scale, the code rendered at 44 px per
    line instead of 79 - a 1.8x reduction caused entirely by text the
    learner does not need to read.
    """
    assert _widest_drawn(5) <= 32, "boilerplate is back in the window"
    assert "package" not in " ".join(_state(METHOD_FILE, 5).lines)


def test_every_line_of_the_method_body_renders_at_a_similar_size() -> None:
    """Readability must not swing by a factor of two between frames of the
    same lesson - that is what makes a video feel inconsistent."""
    widths = [_widest_drawn(line) for line in range(5, 17)]
    assert max(widths) - min(widths) <= 8, widths


def test_the_executing_line_survives_the_narrowing() -> None:
    """Non-negotiable, and the reason the first attempt at this was wrong:
    clamping to the block interior pushed the window past a METHOD_ENTER
    highlight on the signature line, leaving the panel with no highlight.
    """
    for line in range(1, len(METHOD_FILE) + 1):
        state = _state(METHOD_FILE, line)
        assert state.highlight_offset is not None, f"line {line} highlight hidden"
        assert state.lines[state.highlight_offset] == METHOD_FILE[line - 1]


def test_indentation_is_preserved_relative_to_the_block() -> None:
    """Section 11: nesting is part of the teaching."""
    state = _state(METHOD_FILE, 11)          # `return false;` inside the if
    drawn = primitives._dedent_window(state.lines)
    depths = {t.strip(): len(t) - len(t.lstrip(" ")) for t in drawn if t.strip()}
    assert depths["return false;"] > depths["if (a != b) {"]
    assert depths["if (a != b) {"] > depths["while (left < right) {"]

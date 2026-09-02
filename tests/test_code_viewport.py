"""PHASE 6.5.3 — the code panel is a FIXED VIEWPORT.

Phase 6.5.1 drew the panel around whatever body it was given, so the panel
was a *measurement of the content* rather than a container for it.
Measured across the 31 frames of a real plan, that produced:

    panel width   513 px .. 994 px   (the same canvas, five different
                                      code areas)
    panel centre  5.7 px off axis on the widest windows
    line numbers  73 px OUTSIDE the panel's own left border
    code text     18 px past the right border

None of those are visible in the emitted source — they are properties of
mobjects that only exist once Manim has built them — so the containment
tests here are `real_render` tests that measure the real geometry. The
unit tests above them cover the parts that ARE decidable from the source.

What this file does NOT re-test: the Phase 6.5.1 long-line architecture
(representative-width sizing plus per-line outlier scaling). That is
covered by tests/test_code_layout.py and is unchanged; the only assertion
here is the one this phase adds — that its output stays inside the
viewport.
"""

from __future__ import annotations

import shutil

import pytest

from code2shorts.core.models import SourceLocation
from code2shorts.visualization import primitives
from code2shorts.visualization.code_state import (
    DEFAULT_WINDOW_RADIUS,
    build_code_state,
)
from tests.test_code_layout import FILE, PALINDROME, _render_composed, _source

needs_manim = pytest.mark.skipif(
    shutil.which("manim") is None, reason="requires real Manim"
)

LONG_CAPTION = (
    "The loop condition is evaluated one final time: is left three less "
    "than right three? No, so the loop terminates here, and the method is "
    "ready to report its answer to the caller."
)

# The six frame kinds section 15 requires, by the line each one executes.
# `None` is the opening: the introduction step cites no trace line.
FRAME_KINDS = [
    pytest.param(None, "A short caption.", id="A-opening"),
    pytest.param(4, "A short caption.", id="B-method-signature"),
    pytest.param(7, "A short caption.", id="C-loop-condition"),
    pytest.param(10, "A short caption.", id="D-active-if"),
    pytest.param(6, LONG_CAPTION, id="E-long-line-and-long-caption"),
    pytest.param(16, "A short caption.", id="F-final-return"),
]


# ---------------------------------------------------------------------------
# Unit — what the emitted source alone can prove
# ---------------------------------------------------------------------------


def test_the_viewport_width_is_a_constant_not_a_measurement() -> None:
    """The panel's width must not be derived from anything measurable.

    The defect this closes: `_panel_w` used to be computed from the body,
    so a window's longest line decided how wide the code area was.
    """
    assert primitives.CODE_VIEWPORT_WIDTH == primitives.CODE_MAX_WIDTH
    source = "\n".join(
        primitives.code_panel(build_code_state(SourceLocation(file=FILE, line=7), _source(*PALINDROME)))
    )
    assert f"_panel_w = {primitives.CODE_VIEWPORT_WIDTH}" in source
    # ...and the panel is centred on the fixed axis, never on its content.
    assert "_panel.move_to([_body" not in source
    assert f"_panel.move_to([{primitives.CODE_VIEWPORT_CENTER_X}," in source


def test_the_emitted_source_contains_a_hard_containment_clamp() -> None:
    """The outlier FLOORS are a preference; containment is the boundary.

    A line floored at MIN_OUTLIER_SCALE could still stick out — that is
    what the floor's own docstring permits — so a final unconditional
    clamp has to follow it.
    """
    source = "\n".join(
        primitives.code_panel(build_code_state(SourceLocation(file=FILE, line=4), _source(*PALINDROME)))
    )
    assert "_inner_right" in source
    assert "_l.scale(1.0 - _over / _l.width, about_edge=LEFT)" in source


def test_the_viewport_is_restored_after_every_uniform_scale() -> None:
    """A uniform scale shrinks the panel with its contents.

    That is precisely how a tall window ended up in a 513 px panel, so
    both places that scale the group must re-pin the viewport afterwards.
    """
    panel_source = "\n".join(primitives.code_panel(
        build_code_state(SourceLocation(file=FILE, line=7), _source(*PALINDROME))
    ))
    compose_source = "\n".join(primitives.compose_vertical(["arr_group"]))
    for name, source in (("code_panel", panel_source), ("compose_vertical", compose_source)):
        assert f"stretch_to_fit_width({primitives.CODE_VIEWPORT_WIDTH})" in source, (
            f"{name} scales the group without restoring the viewport"
        )


def test_the_opening_window_skips_the_file_preamble() -> None:
    """Section 7: with no executing line, show the algorithm.

    The introduction step cites no trace line, and the whole file was
    shown instead — package declaration, class header, `main` and all.
    Structural rule only: the first block opener that is INDENTED.
    """
    state = build_code_state(
        SourceLocation(file=FILE, line=None), _source(*PALINDROME)
    )
    assert state.lines, "the opening must still show code"
    assert len(state.lines) <= DEFAULT_WINDOW_RADIUS * 2 + 1
    assert not any(line.startswith("package") for line in state.lines)
    assert state.lines[0].strip().startswith("public static boolean isPalindrome")


# ---------------------------------------------------------------------------
# Real render — containment, measured
# ---------------------------------------------------------------------------


def _parts(namespace):
    """panel, line numbers, code, active highlight — by the order
    `code_panel` documents as a contract."""
    group = namespace["code_group"]
    panel, dots, body, highlight = group.submobjects
    numbers, code = body.submobjects
    return group, panel, dots, numbers, code, highlight



def _render_fixed_window(lines: list[str], active: int):
    """Compose one frame at a FIXED window radius.

    `_render_composed` asks the fitter for a radius, which is right for
    every other test here and wrong for a before/after comparison: the
    fitter would choose a different window for the two sources and the
    lines would no longer line up.
    """
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
    state = build_code_state(
        SourceLocation(file=FILE, line=active), _source(*lines), DEFAULT_WINDOW_RADIUS
    )
    body = (
        primitives.array_row(frame, var="arr_group")
        + primitives.pointer_arrows(frame, array_var="arr_group", var="ptr_group")
        + primitives.scalar_panel(frame, var="vars_group")
        + primitives.code_panel(state, var="code_group")
        + primitives.caption_text("A short caption.", var="caption")
        + primitives.compose_vertical(
            ["arr_group", "ptr_group", "vars_group"], "caption", "code_group"
        )
    )
    with tempconfig({
        "pixel_width": 1080,
        "pixel_height": 1920,
        "frame_height": primitives.FRAME_HEIGHT,
        "frame_width": primitives.FRAME_HEIGHT * (1080 / 1920),
    }):
        namespace: dict = {}
        exec("from manim import *", namespace)  # noqa: S102 - repository-owned
        exec("\n".join(body), namespace)       # noqa: S102 - repository-owned
        return namespace, state


def _overflow(panel, mobject) -> tuple[float, float, float, float]:
    """left, right, top, bottom overflow in CANVAS PIXELS."""
    px = 1920 / primitives.FRAME_HEIGHT
    return (
        max(0.0, (panel.get_left()[0] - mobject.get_left()[0]) * px),
        max(0.0, (mobject.get_right()[0] - panel.get_right()[0]) * px),
        max(0.0, (mobject.get_top()[1] - panel.get_top()[1]) * px),
        max(0.0, (panel.get_bottom()[1] - mobject.get_bottom()[1]) * px),
    )


@pytest.mark.integration
@pytest.mark.real_render
@needs_manim
@pytest.mark.parametrize("active,caption", FRAME_KINDS)
def test_nothing_escapes_the_viewport(active, caption) -> None:
    """Section 4: the panel is a containment boundary.

    Measured on the baseline: the line-number column sat 73 px outside the
    left border on the method-signature frame and the code ran 18 px past
    the right border on the `main` window. Both are zero here, for every
    frame kind, or the panel is decoration rather than a viewport.
    """
    namespace, _state = _render_composed(PALINDROME, active, caption)
    _group, panel, dots, numbers, code, highlight = _parts(namespace)

    inspected = {"line numbers": numbers, "code": code, "chrome": dots}
    if highlight.submobjects:
        inspected["active highlight"] = highlight

    for name, mobject in inspected.items():
        left, right, top, bottom = _overflow(panel, mobject)
        assert (left, right, top, bottom) == pytest.approx((0, 0, 0, 0), abs=0.5), (
            f"{name} escaped the viewport: "
            f"L{left:.1f} R{right:.1f} T{top:.1f} B{bottom:.1f} px"
        )


@pytest.mark.integration
@pytest.mark.real_render
@needs_manim
@pytest.mark.parametrize("active,caption", FRAME_KINDS)
def test_the_viewport_width_and_axis_never_move(active, caption) -> None:
    """Section 3: width and horizontal position are the same on every
    frame, whatever the window contains."""
    namespace, _state = _render_composed(PALINDROME, active, caption)
    _group, panel, *_ = _parts(namespace)

    assert panel.width == pytest.approx(primitives.CODE_VIEWPORT_WIDTH, abs=1e-3)
    assert panel.get_center()[0] == pytest.approx(
        primitives.CODE_VIEWPORT_CENTER_X, abs=1e-3
    )


@pytest.mark.integration
@pytest.mark.real_render
@needs_manim
def test_the_line_number_column_keeps_its_internal_margin() -> None:
    """Section 6: numbers sit INSIDE the panel with their own margin, and
    never touch the border."""
    namespace, _state = _render_composed(PALINDROME, 4, "A short caption.")
    _group, panel, _dots, numbers, code, _highlight = _parts(namespace)

    gap = numbers.get_left()[0] - panel.get_left()[0]
    assert gap > 0, "the line-number column reached the panel border"
    # The code column starts to the RIGHT of the numbers, never over them.
    assert code.get_left()[0] > numbers.get_right()[0] - 1e-6


@pytest.mark.integration
@pytest.mark.real_render
@needs_manim
def test_a_long_line_still_does_not_shrink_the_whole_block() -> None:
    """The Phase 6.5.1 guarantee, re-asserted through the viewport.

    Containment must not have quietly reintroduced the defect 6.5.1 fixed:
    one long line making EVERY line smaller. The experiment is the one
    that originally proved it - the SAME window, with and without a long
    line in it - and the measurement is glyph ADVANCE, which is constant
    across a monospace block and therefore reads the block's scale
    directly rather than through whatever glyphs a line happens to hold.
    """
    marker = "// a deliberately long trailing comment"
    stretched = list(PALINDROME)
    assert stretched[7].strip().startswith("char a ="), "fixture shifted"
    stretched[7] = stretched[7] + " " + marker

    def advances(lines: list[str]) -> dict[str, float]:
        # A FIXED radius, so both runs render the same nine file lines and
        # the comparison is line-for-line.
        namespace, state = _render_fixed_window(lines, active=8)
        _g, _p, _d, _n, code, _h = _parts(namespace)
        drawn = primitives._dedent_window(state.lines)
        assert len(drawn) == len(code.submobjects) == 9
        return {
            text.strip(): mobject.width / max(len(text.strip()), 1)
            for mobject, text in zip(code.submobjects, drawn)
        }

    plain = advances(PALINDROME)
    with_outlier = advances(stretched)

    long_text = stretched[7].strip()
    short_text = PALINDROME[7].strip()

    # Every line except the stretched one keeps the block's scale.
    for text, before in plain.items():
        if text == short_text:
            continue
        after = with_outlier[text]
        assert after == pytest.approx(before, rel=0.02), (
            f"'{text}' rescaled from {before:.4f} to {after:.4f} because a "
            f"DIFFERENT line got longer"
        )

    # ...and the long line absorbed the cost on its own.
    assert with_outlier[long_text] < plain[short_text] * 0.75


@pytest.mark.integration
@pytest.mark.real_render
@needs_manim
@pytest.mark.parametrize("active,caption", FRAME_KINDS)
def test_the_code_stays_readable_at_phone_scale(active, caption) -> None:
    """Section 15: 540x960 is half of the delivery resolution.

    Manim units are resolution-independent, so this asserts the property
    that survives the downscale: per-line pitch measured in units, against
    the floor the project guarantees. At 540x960 that floor is 21 px.
    """
    namespace, state = _render_composed(PALINDROME, active, caption)
    group, *_ = _parts(namespace)

    pitch = group.height / max(len(state.lines), 1)
    assert pitch >= primitives.ABS_MIN_CODE_LINE_HEIGHT_UNITS, (
        f"{pitch:.3f} units per line is below the guaranteed floor; at "
        f"540x960 that is {pitch * 120:.1f} px"
    )

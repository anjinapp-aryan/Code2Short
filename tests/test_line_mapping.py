"""Phase 4.5.1: source-line mapping correctness, proven to the last link.

The guarantee under test:

    TraceEvent.line_number
      == SourceLocation.line
      == CodeState.start_line + highlight_offset
      == the line number Manim actually DISPLAYS next to the highlight
      == the real text of that line in the original file

Earlier verification (`driver.py sync`) stopped at CodeState. The defect
this phase closes lived one link further on, in what Manim rendered, so
these tests decode the labels Manim actually produces rather than assuming
they follow `line_numbers_from`.

`decode_displayed_labels` is the key: Manim's line-number column is a
Paragraph whose `lines_text` is the concatenation of every label with no
separator ('9' + '10' + '11' -> '91011'), and whose per-line glyph count
gives each label's digit width. Walking those widths recovers the exact
rendered labels.
"""

from __future__ import annotations

import shutil

import pytest

from code2shorts.core.models import SourceLocation
from code2shorts.visualization.code_state import build_code_state

manim_only = pytest.mark.skipif(
    shutil.which("manim") is None, reason="needs Manim installed"
)

MAIN = "src/main/java/com/x/Main.java"
HELPER = "src/main/java/com/x/Helper.java"


def decode_displayed_labels(code_mobject) -> list[str]:
    """Recover the line numbers Manim will actually draw.

    Not an inference from `line_numbers_from` - that is precisely the
    assumption the original defect hid behind. Manim silently drops
    leading/trailing blank lines while the label column keeps counting, so
    the only trustworthy source is the rendered Paragraph itself.
    """
    line_numbers = code_mobject.line_numbers
    concatenated = line_numbers.lines_text.text
    labels: list[str] = []
    cursor = 0
    for index in range(len(line_numbers)):
        width = len(line_numbers[index].submobjects)
        labels.append(concatenated[cursor : cursor + width])
        cursor += width
    return labels


def build_code_mobject(state):
    """Build the same Code object `primitives.code_panel` emits."""
    from manim import Code

    return Code(
        code_string="\n".join(state.lines),
        language="java",
        add_line_numbers=True,
        line_numbers_from=state.start_line,
        background="window",
        paragraph_config={"font_size": 20},
    )


def assert_chain(sources: dict[str, str], file: str, line: int, window_radius: int = 6):
    """Assert the whole chain for one semantic source line, and return the state."""
    real_lines = sources[file].splitlines()
    state = build_code_state(SourceLocation(file=file, line=line), sources, window_radius)

    # link 1: CodeState locates the semantic line
    assert state.highlight_line == line, "CodeState must preserve the semantic line"
    assert state.highlight_offset is not None, f"line {line} not visible in the window"
    assert state.start_line + state.highlight_offset == line

    # link 2: window content matches the real file, line for line
    for offset, text in enumerate(state.lines):
        assert text == real_lines[state.start_line + offset - 1], (
            f"window content drifted at offset {offset}"
        )

    # link 3: Manim renders every line we gave it (nothing silently dropped)
    code = build_code_mobject(state)
    assert len(code.code_lines) == len(state.lines), (
        f"Manim rendered {len(code.code_lines)} of {len(state.lines)} lines - "
        "blank-line stripping would shift every label"
    )

    # link 4: the DISPLAYED label at the highlight equals the semantic line
    labels = decode_displayed_labels(code)
    assert len(labels) == len(state.lines)
    assert labels[state.highlight_offset] == str(line), (
        f"displayed label {labels[state.highlight_offset]!r} != semantic line {line}"
    )

    # link 5: every displayed label maps to the correct real source text
    for offset, label in enumerate(labels):
        assert state.lines[offset] == real_lines[int(label) - 1], (
            f"label {label} shows the wrong source text"
        )
    return state


# ---- the exact defect, as a permanent regression --------------------------


@manim_only
def test_window_starting_on_a_blank_line_keeps_labels_correct() -> None:
    """THE defect. Before the fix a window beginning on a blank line lost
    that line inside Manim while the label column kept counting, so every
    displayed number read one low and the box landed on the next statement."""
    # Long enough to force windowing, with a blank line sitting exactly
    # where the window would otherwise begin. For line 20 at radius 6 the
    # untrimmed window starts at line 14, so line 14 is blank on purpose.
    lines = [f"stmt{i}();" for i in range(1, 41)]
    lines[13] = ""  # file line 14
    sources = {MAIN: "\n".join(lines)}

    untrimmed_start = 20 - 6
    assert lines[untrimmed_start - 1] == "", "fixture must put a blank at the window start"

    state = assert_chain(sources, MAIN, line=20)
    assert state.truncated is True, "fixture must actually exercise windowing"
    assert state.lines[0] != "", "window must not begin on a blank line"
    assert state.start_line == untrimmed_start + 1, (
        "leading blank must be trimmed and start_line advanced past it"
    )


@manim_only
def test_manim_strips_blank_edges_but_our_windows_never_expose_it() -> None:
    """Documents the underlying Manim behaviour the fix compensates for."""
    from manim import Code

    stripped = Code(
        code_string="\nAAA\nBBB\nCCC", language="java", add_line_numbers=True, line_numbers_from=1
    )
    assert len(stripped.code_lines) == 3, "4 input lines, 3 rendered - Manim drops the blank"

    interior = Code(
        code_string="AAA\n\nBBB", language="java", add_line_numbers=True, line_numbers_from=1
    )
    assert len(interior.code_lines) == 3, "interior blanks must survive"

    whitespace = Code(
        code_string="   \nAAA", language="java", add_line_numbers=True, line_numbers_from=1
    )
    assert len(whitespace.code_lines) == 2, "whitespace-only lines are not stripped"


# ---- basic mapping --------------------------------------------------------


def _plain_source(count: int = 40) -> dict[str, str]:
    return {MAIN: "\n".join(f"int v{i} = {i};" for i in range(1, count + 1))}


@manim_only
@pytest.mark.parametrize("line", [1, 2, 20, 39, 40])
def test_basic_mapping_first_second_middle_last(line: int) -> None:
    assert_chain(_plain_source(40), MAIN, line)


# ---- windowed source ------------------------------------------------------


@manim_only
@pytest.mark.parametrize("line", [1, 3, 100, 197, 200])
def test_windowed_source_start_middle_end_and_clamped(line: int) -> None:
    sources = {MAIN: "\n".join(f"stmt{i}();" for i in range(1, 201))}
    state = assert_chain(sources, MAIN, line)
    assert state.truncated is True
    assert len(state.lines) == 13


@manim_only
def test_multi_digit_labels_decode_correctly() -> None:
    """Labels straddling 9->10 and 99->100 are where a naive decode breaks."""
    sources = {MAIN: "\n".join(f"stmt{i}();" for i in range(1, 201))}
    for line in (9, 10, 11, 99, 100, 101):
        assert_chain(sources, MAIN, line)


# ---- execution behaviour --------------------------------------------------


@manim_only
def test_repeated_execution_of_the_same_line_maps_identically() -> None:
    sources = _plain_source(40)
    states = [assert_chain(sources, MAIN, 20) for _ in range(5)]
    assert all(s == states[0] for s in states), "same line must map identically every time"


@manim_only
def test_loop_branch_and_nested_loop_lines_each_map_correctly() -> None:
    sources = {
        MAIN: "\n".join(
            [
                "class A {",  # 1
                "  void m() {",  # 2
                "    for (int i = 0; i < 3; i++) {",  # 3
                "      for (int j = 0; j < 3; j++) {",  # 4
                "        if (i == j) {",  # 5
                "          x();",  # 6
                "        } else {",  # 7
                "          y();",  # 8
                "        }",  # 9
                "      }",  # 10
                "    }",  # 11
                "  }",  # 12
                "}",  # 13
            ]
        )
    }
    for line in (3, 4, 5, 6, 8, 10):
        assert_chain(sources, MAIN, line)


@manim_only
def test_multiple_source_files_stay_independent() -> None:
    """Line 5 means different code in different files - the mapping must
    follow the file, not just the number."""
    sources = {
        MAIN: "\n".join(f"main{i}();" for i in range(1, 21)),
        HELPER: "\n".join(f"helper{i}();" for i in range(1, 21)),
    }
    main_state = assert_chain(sources, MAIN, 5)
    helper_state = assert_chain(sources, HELPER, 5)
    assert main_state.lines[main_state.highlight_offset] == "main5();"
    assert helper_state.lines[helper_state.highlight_offset] == "helper5();"


# ---- existing functionality must survive ----------------------------------


@manim_only
@pytest.mark.parametrize(
    "text",
    ["// café", "// 中文注释", "// ಕನ್ನಡ", "// naïve", "// Ω ≈ ç √ ∫"],
)
def test_unicode_source_maps_correctly(text: str) -> None:
    sources = {MAIN: "\n".join([f"stmt{i}();" for i in range(1, 10)] + [text] + ["end();"])}
    state = assert_chain(sources, MAIN, 10)
    assert state.lines[state.highlight_offset] == text


@manim_only
def test_emoji_in_source_is_a_known_manim_limitation_not_a_mapping_bug() -> None:
    """Emoji in Java source makes Manim's Code mobject raise, because the
    font renders fewer glyphs than there are characters:

        ValueError: Text '...' rendered fewer glyph(s) than its non-space
        characters even with disable_ligatures=True

    CodeState maps the line correctly - the failure is purely in Manim's
    text shaping. Documented as a limitation rather than worked around,
    since emoji in Java source is vanishingly rare and any workaround
    (font substitution, glyph stripping) would alter displayed source.
    """
    text = "// emoji 😀"
    sources = {MAIN: "\n".join([f"stmt{i}();" for i in range(1, 10)] + [text] + ["end();"])}

    # the mapping layer is fine
    state = build_code_state(SourceLocation(file=MAIN, line=10), sources)
    assert state.lines[state.highlight_offset] == text

    # ...but Manim cannot render it
    with pytest.raises(ValueError, match="fewer glyph"):
        build_code_mobject(state)


@manim_only
@pytest.mark.parametrize(
    "text",
    [
        'String s = "\'); import os; os.system(\'id\'); (\'";',
        "String s = \"$(whoami)\";",
        "String s = \"`id`\";",
        "// comment with && and | and ;",
        'String q = "he said \\"hi\\"";',
    ],
)
def test_hostile_source_text_maps_correctly_and_stays_data(text: str) -> None:
    sources = {MAIN: "\n".join([f"stmt{i}();" for i in range(1, 8)] + [text] + ["end();"])}
    state = assert_chain(sources, MAIN, 8)
    assert state.lines[state.highlight_offset] == text, "source text must not be rewritten"


@manim_only
def test_blank_and_comment_lines_inside_the_window_are_preserved() -> None:
    sources = {
        MAIN: "\n".join(
            [
                "class A {",  # 1
                "",  # 2 interior blank
                "  // a comment",  # 3
                "",  # 4 interior blank
                "  void m() {}",  # 5
                "}",  # 6
            ]
        )
    }
    state = assert_chain(sources, MAIN, 5)
    assert "" in state.lines, "interior blank lines must survive"
    assert "  // a comment" in state.lines


# ---- security: the fix must not weaken anything ---------------------------


def test_fix_introduces_no_dynamic_execution() -> None:
    import ast
    from pathlib import Path

    module = (
        Path(__file__).resolve().parents[1]
        / "src" / "code2shorts" / "visualization" / "code_state.py"
    )
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            attr = getattr(node.func, "attr", None)
            assert name not in ("exec", "eval"), "code_state must stay free of dynamic execution"
            assert attr != "system"
            for keyword in node.keywords:
                assert not (
                    keyword.arg == "shell" and getattr(keyword.value, "value", None) is True
                )


def test_renderer_still_repr_escapes_source_into_the_scene() -> None:
    """The fix touched windowing, not escaping - confirm escaping survived."""
    from code2shorts.visualization.code_state import build_code_state
    from code2shorts.visualization.primitives import code_panel

    hostile = "'); import os; os.system('id'); ('"
    sources = {MAIN: "\n".join([f"stmt{i}();" for i in range(1, 6)] + [hostile])}
    state = build_code_state(SourceLocation(file=MAIN, line=6), sources)
    emitted = "\n".join(code_panel(state))
    import ast

    tree = ast.parse(emitted)  # must remain valid Python
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            assert name not in ("exec", "eval", "system", "__import__")
    assert hostile in emitted, "hostile text should survive as inert data"


# ---- real-trace end-to-end chain (integration) ----------------------------

REAL_CASES = [
    ("fixture", "correct", "HELLO"),
    ("algorithm", "palindrome", "RACECAR"),
    ("algorithm", "two_sum", "9"),
    ("algorithm", "move_zeroes", ""),
    ("algorithm", "remove_duplicates", ""),
]


@pytest.mark.integration
@pytest.mark.real_render
@manim_only
@pytest.mark.skipif(
    shutil.which("mvn") is None or shutil.which("java") is None,
    reason="needs Maven + JDK for a real trace",
)
@pytest.mark.parametrize(("kind", "variant", "arg"), REAL_CASES)
def test_real_trace_line_mapping_holds_to_the_displayed_label(
    kind: str, variant: str, arg: str
) -> None:
    """The Phase 4.5.1 gate, on REAL traces.

    For every executed event: trace line == SourceLocation line ==
    CodeState offset arithmetic == the label Manim DISPLAYS == real source
    text. Earlier checks stopped one link short of the displayed label,
    which is exactly where the defect lived.
    """
    from code2shorts.execution.sandbox import Workspace
    from code2shorts.langadapter.java import JavaAdapter
    from code2shorts.visualization.code_state import resolve_source_locations
    from tests.java_fixtures import load_algorithm_fixture, load_java_fixture

    code = load_java_fixture(variant) if kind == "fixture" else load_algorithm_fixture(variant)
    with Workspace() as workspace:
        trace = JavaAdapter().trace(code, workspace, arg)

    locations = resolve_source_locations(trace, code.source_files, code.entry_point)
    cache: dict[tuple, tuple] = {}
    verified = 0

    for event in trace.events:
        location = locations[event.step_index]
        if location.line is None or location.file is None:
            continue

        assert location.line == event.line_number, "SourceLocation must preserve the trace line"

        state = build_code_state(location, code.source_files, window_radius=6)
        real_lines = code.source_files[location.file].splitlines()

        assert state.highlight_offset is not None, f"event {event.step_index}: highlight hidden"
        assert state.start_line + state.highlight_offset == location.line
        assert state.lines[state.highlight_offset] == real_lines[location.line - 1]

        key = (location.file, state.start_line, len(state.lines))
        if key not in cache:
            mobject = build_code_mobject(state)
            cache[key] = (len(mobject.code_lines), decode_displayed_labels(mobject))
        rendered_count, labels = cache[key]

        assert rendered_count == len(state.lines), (
            f"event {event.step_index}: Manim rendered {rendered_count} of "
            f"{len(state.lines)} lines - labels would shift"
        )
        assert labels[state.highlight_offset] == str(location.line), (
            f"event {event.step_index}: displayed label "
            f"{labels[state.highlight_offset]} != semantic line {location.line}"
        )
        verified += 1

    assert verified > 0, "no events carried a source line"

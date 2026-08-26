"""Phase 4.3: code/execution synchronization.

Proves the executing line is derived from the TRACE, deterministically,
including the cases that break naive implementations: repeated loop lines,
branches, nested loops, cross-file method transitions, long-file windowing
and unicode.
"""

from __future__ import annotations

import pytest

from code2shorts.core.models import (
    ExecutionTrace,
    SourceLocation,
    SupportedLanguage,
    TraceEvent,
    TraceEventType,
)
from code2shorts.visualization.code_state import (
    build_code_state,
    resolve_source_locations,
)

MAIN = "src/main/java/com/x/Main.java"
HELPER = "src/main/java/com/x/Helper.java"


def _event(step: int, kind: str, line: int | None = None, method: str | None = None) -> TraceEvent:
    return TraceEvent(
        step_index=step, event_type=kind, description="", line_number=line, method=method
    )


def _trace(events: list[TraceEvent]) -> ExecutionTrace:
    return ExecutionTrace(
        algorithm_name="t",
        language=SupportedLanguage.JAVA,
        input="",
        output="",
        succeeded=True,
        exit_code=0,
        events=events,
    )


def _sources(main_lines: int = 10, helper_lines: int = 10) -> dict[str, str]:
    return {
        MAIN: "\n".join(f"main line {i}" for i in range(1, main_lines + 1)),
        HELPER: "\n".join(f"helper line {i}" for i in range(1, helper_lines + 1)),
    }


# ---- basic ---------------------------------------------------------------


def test_trace_line_becomes_highlight_line() -> None:
    trace = _trace([_event(0, TraceEventType.VARIABLE_ASSIGN.value, line=10)])
    locations = resolve_source_locations(trace, _sources(), entry_point=MAIN)
    state = build_code_state(locations[0], _sources())
    assert state.highlight_line == 10


def test_sequential_execution_preserves_order() -> None:
    trace = _trace(
        [_event(i, TraceEventType.VARIABLE_ASSIGN.value, line=10 + i) for i in range(4)]
    )
    locations = resolve_source_locations(trace, _sources(main_lines=20), entry_point=MAIN)
    assert [locations[i].line for i in range(4)] == [10, 11, 12, 13]


# ---- repeated lines (loops) ---------------------------------------------


def test_same_line_executed_many_times_highlights_independently() -> None:
    """A while-loop body line executes repeatedly; every execution must be
    independently addressable, not collapsed."""
    events = []
    for i in range(6):
        events.append(_event(i, TraceEventType.LOOP_ITERATION.value, line=7))
    trace = _trace(events)
    locations = resolve_source_locations(trace, _sources(), entry_point=MAIN)

    assert len(locations) == 6
    assert all(locations[i].line == 7 for i in range(6))
    states = [build_code_state(locations[i], _sources()) for i in range(6)]
    assert all(s.highlight_line == 7 for s in states)


def test_nested_loops_map_each_iteration_to_its_own_line() -> None:
    # outer line 5, inner line 7, body line 8 — interleaved as really executed
    pattern = [5, 7, 8, 7, 8, 5, 7, 8]
    trace = _trace(
        [_event(i, TraceEventType.LOOP_ITERATION.value, line=ln) for i, ln in enumerate(pattern)]
    )
    locations = resolve_source_locations(trace, _sources(), entry_point=MAIN)
    assert [locations[i].line for i in range(len(pattern))] == pattern


# ---- branches ------------------------------------------------------------


def test_branch_highlights_the_actually_executed_line() -> None:
    """Only the taken branch appears in the trace, so only it can be
    highlighted — the mechanism cannot highlight the untaken branch."""
    trace = _trace(
        [
            _event(0, TraceEventType.CONDITION_EVALUATED.value, line=10),
            _event(1, TraceEventType.VARIABLE_ASSIGN.value, line=11),  # then-branch
        ]
    )
    locations = resolve_source_locations(trace, _sources(main_lines=20), entry_point=MAIN)
    assert locations[1].line == 11
    assert 14 not in {loc.line for loc in locations.values()}  # else-branch never shown


# ---- method transitions / multi-file ------------------------------------


def test_method_entry_switches_to_the_callee_file() -> None:
    """THE core Phase 4.3 correctness case: line numbers are per-file, so
    the same number means different code in different files."""
    trace = _trace(
        [
            _event(0, TraceEventType.VARIABLE_ASSIGN.value, line=5),  # in main
            _event(1, TraceEventType.METHOD_ENTER.value, line=6, method="Helper.work"),
            _event(2, TraceEventType.VARIABLE_ASSIGN.value, line=7),  # in Helper
            _event(3, TraceEventType.METHOD_EXIT.value, line=9, method="Helper.work"),
            _event(4, TraceEventType.VARIABLE_ASSIGN.value, line=8),  # back in main
        ]
    )
    locations = resolve_source_locations(trace, _sources(), entry_point=MAIN)

    assert locations[0].file == MAIN
    assert locations[1].file == HELPER
    assert locations[2].file == HELPER
    assert locations[2].class_name == "Helper"
    assert locations[2].method == "work"
    assert locations[4].file == MAIN, "must return to the caller's file after exit"


def test_nested_method_calls_track_innermost_file() -> None:
    trace = _trace(
        [
            _event(0, TraceEventType.METHOD_ENTER.value, line=3, method="Main.run"),
            _event(1, TraceEventType.METHOD_ENTER.value, line=4, method="Helper.work"),
            _event(2, TraceEventType.VARIABLE_ASSIGN.value, line=5),
            _event(3, TraceEventType.METHOD_EXIT.value, line=6, method="Helper.work"),
            _event(4, TraceEventType.VARIABLE_ASSIGN.value, line=7),
        ]
    )
    locations = resolve_source_locations(trace, _sources(), entry_point=MAIN)
    assert locations[2].file == HELPER  # innermost
    assert locations[4].file == MAIN  # after pop


# ---- windowing -----------------------------------------------------------


def test_short_file_is_shown_whole() -> None:
    sources = _sources(main_lines=8)
    state = build_code_state(SourceLocation(file=MAIN, line=4), sources, window_radius=6)
    assert state.truncated is False
    assert len(state.lines) == 8
    assert state.start_line == 1
    assert state.highlight_offset == 3


def test_long_file_is_windowed_around_the_executing_line() -> None:
    sources = {MAIN: "\n".join(f"line {i}" for i in range(1, 201))}
    state = build_code_state(SourceLocation(file=MAIN, line=100), sources, window_radius=6)

    assert state.truncated is True
    assert len(state.lines) == 13
    assert state.start_line == 94
    assert state.highlight_line == 100
    assert state.highlight_offset == 6  # centered
    assert state.lines[6] == "line 100"
    assert state.total_lines == 200


def test_window_clamps_at_file_start_and_end_keeping_full_size() -> None:
    sources = {MAIN: "\n".join(f"line {i}" for i in range(1, 201))}

    at_start = build_code_state(SourceLocation(file=MAIN, line=2), sources, window_radius=6)
    assert at_start.start_line == 1
    assert len(at_start.lines) == 13
    assert at_start.highlight_offset == 1  # still visible

    at_end = build_code_state(SourceLocation(file=MAIN, line=199), sources, window_radius=6)
    assert len(at_end.lines) == 13
    assert at_end.start_line == 188
    assert at_end.highlight_offset == 11  # still visible


def test_window_is_deterministic() -> None:
    sources = {MAIN: "\n".join(f"line {i}" for i in range(1, 201))}
    a = build_code_state(SourceLocation(file=MAIN, line=77), sources)
    b = build_code_state(SourceLocation(file=MAIN, line=77), sources)
    assert a == b


def test_window_never_alters_source_content() -> None:
    original = "a\n  b  \n\tc\n"
    sources = {MAIN: original}
    state = build_code_state(SourceLocation(file=MAIN, line=2), sources)
    assert state.lines == original.splitlines()


def test_missing_or_unknown_file_yields_empty_state_not_an_error() -> None:
    assert build_code_state(SourceLocation(file=None, line=5), _sources()).lines == []
    assert build_code_state(SourceLocation(file="nope.java", line=5), _sources()).lines == []


def test_highlight_offset_is_none_when_line_outside_window() -> None:
    sources = {MAIN: "\n".join(f"line {i}" for i in range(1, 201))}
    state = build_code_state(SourceLocation(file=MAIN, line=100), sources, window_radius=6)
    state.highlight_line = 5  # forced out of the window
    assert state.highlight_offset is None


# ---- unicode -------------------------------------------------------------


@pytest.mark.parametrize("text", ["é", "中", "ಕನ್ನಡ", "😀", "naïve café", "日本語のコメント"])
def test_unicode_source_survives_windowing(text: str) -> None:
    sources = {MAIN: "\n".join([f"// {text} {i}" for i in range(1, 40)])}
    state = build_code_state(SourceLocation(file=MAIN, line=20), sources, window_radius=6)
    assert len(state.lines) == 13
    assert text in state.lines[state.highlight_offset]


# ---- schema versioning ---------------------------------------------------


def test_trace_carries_schema_version_by_default() -> None:
    from code2shorts.core.models import TRACE_SCHEMA_VERSION

    trace = _trace([_event(0, TraceEventType.PROGRAM_START.value)])
    assert trace.trace_schema_version == TRACE_SCHEMA_VERSION
    assert trace.trace_schema_version >= 2


def test_schema_version_survives_json_round_trip() -> None:
    trace = _trace([_event(0, TraceEventType.PROGRAM_START.value)])
    trace.instrumenter_version = "1.0.0"
    trace.source_hash = "abc123"
    restored = ExecutionTrace.model_validate_json(trace.model_dump_json())
    assert restored.trace_schema_version == trace.trace_schema_version
    assert restored.instrumenter_version == "1.0.0"
    assert restored.source_hash == "abc123"


def test_window_never_starts_or_ends_on_a_blank_line() -> None:
    """Regression: Manim's Code mobject silently strips leading/trailing
    EMPTY lines while its line-number column keeps counting, so a window
    that began on a blank line rendered every label one too low and boxed
    the *following* statement. Caught by cropping a real rendered frame."""
    sources = {MAIN: "\n".join(["", "", "class A {", "  int x = 1;", "  int y = 2;", "", ""])}

    for line in (3, 4, 5):
        state = build_code_state(SourceLocation(file=MAIN, line=line), sources, window_radius=6)
        assert state.lines, f"line {line}: empty window"
        assert state.lines[0] != "", f"line {line}: window starts on a blank line"
        assert state.lines[-1] != "", f"line {line}: window ends on a blank line"
        # the invariant that makes labels correct: lines[i] is file line start_line+i
        real = sources[MAIN].splitlines()
        for offset, text in enumerate(state.lines):
            assert text == real[state.start_line + offset - 1]
        assert state.lines[state.highlight_offset] == real[line - 1]


def test_blank_line_trim_never_hides_the_highlighted_line() -> None:
    """A highlight legitimately sitting on a blank line must survive the trim."""
    sources = {MAIN: "\n".join(["", "", "class A {", "  int x = 1;"])}
    state = build_code_state(SourceLocation(file=MAIN, line=1), sources, window_radius=6)
    assert state.highlight_offset is not None, "highlight on a blank line was trimmed away"
    assert state.start_line == 1

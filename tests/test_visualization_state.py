"""Unit tests for the generic state-reconstruction layer (Phase 4.2).

No Java, no Manim — pure functions over trace data.
"""

from __future__ import annotations

import pytest

from code2shorts.core.models import (
    ExecutionTrace,
    SupportedLanguage,
    TraceEvent,
    TraceEventType,
)
from code2shorts.visualization.state import (
    parse_array_value,
    parse_indexed_name,
    reconstruct_frames,
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


def _assign(step: int, name: str, value: str, old: str | None = None) -> TraceEvent:
    return TraceEvent(
        step_index=step,
        event_type=TraceEventType.VARIABLE_ASSIGN.value,
        description=f"{name} = {value}",
        variable_name=name,
        old_value=old,
        new_value=value,
    )


def _array_write(step: int, name: str, index: int, value: str) -> TraceEvent:
    return TraceEvent(
        step_index=step,
        event_type=TraceEventType.ARRAY_WRITE.value,
        description=f"{name}[{index}] = {value}",
        variable_name=f"{name}[{index}]",
        new_value=value,
    )


def _array_read(step: int, name: str, index: int, value: str) -> TraceEvent:
    return TraceEvent(
        step_index=step,
        event_type=TraceEventType.ARRAY_READ.value,
        description=f"{name}[{index}] -> {value}",
        variable_name=f"{name}[{index}]",
        new_value=value,
    )


# ---- parsing --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("chars[0]", ("chars", 0)),
        ("nums[12]", ("nums", 12)),
        ("plain", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_indexed_name(text, expected) -> None:
    assert parse_indexed_name(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("[2, 7, 11, 15]", ["2", "7", "11", "15"]),
        ("[]", []),
        ("[1]", ["1"]),
        ("HELLO", None),  # char[] handled separately, not as a bracketed list
        ("42", None),
        (None, None),
    ],
)
def test_parse_array_value(value, expected) -> None:
    assert parse_array_value(value) == expected


# ---- reconstruction -------------------------------------------------------


def test_int_array_seeded_from_initial_value() -> None:
    frames = reconstruct_frames(_trace([_assign(0, "nums", "[2, 7, 11, 15]")]))
    array = frames[0].primary_array
    assert array is not None
    assert array.cells == ["2", "7", "11", "15"]


def test_char_array_seeded_when_used_with_indices() -> None:
    trace = _trace([_assign(0, "chars", "HELLO"), _array_read(1, "chars", 0, "H")])
    frames = reconstruct_frames(trace)
    assert frames[0].arrays["chars"].cells == ["H", "E", "L", "L", "O"]


def test_plain_string_variable_is_not_treated_as_an_array() -> None:
    # never used with an index -> a scalar, not a char array
    frames = reconstruct_frames(_trace([_assign(0, "word", "HELLO")]))
    assert frames[0].arrays == {}
    assert frames[0].scalars["word"] == "HELLO"


def test_writes_mutate_reconstructed_array_over_time() -> None:
    trace = _trace(
        [
            _assign(0, "chars", "HELLO"),
            _array_read(1, "chars", 0, "H"),
            _array_write(2, "chars", 0, "O"),
            _array_write(3, "chars", 4, "H"),
        ]
    )
    frames = reconstruct_frames(trace)
    assert frames[0].arrays["chars"].cells == list("HELLO")
    assert frames[2].arrays["chars"].cells == list("OELLO")
    assert frames[3].arrays["chars"].cells == list("OELLH")


def test_frames_are_independent_snapshots_not_shared_references() -> None:
    trace = _trace(
        [_assign(0, "chars", "AB"), _array_write(1, "chars", 0, "B")]
    )
    frames = reconstruct_frames(trace)
    assert frames[0].arrays["chars"].cells == ["A", "B"]  # not mutated by frame 1
    assert frames[1].arrays["chars"].cells == ["B", "B"]


def test_read_and_write_indices_are_recorded_per_frame() -> None:
    trace = _trace(
        [
            _assign(0, "nums", "[1, 2, 3]"),
            _array_read(1, "nums", 1, "2"),
            _array_write(2, "nums", 2, "9"),
        ]
    )
    frames = reconstruct_frames(trace)
    assert frames[1].read_indices == {"nums": [1]}
    assert frames[1].written_indices == {}
    assert frames[2].written_indices == {"nums": [2]}


# ---- the generic pointer rule --------------------------------------------


def test_in_range_integer_scalars_become_pointers() -> None:
    trace = _trace(
        [
            _assign(0, "chars", "HELLO"),
            _array_read(1, "chars", 0, "H"),
            _assign(2, "left", "0"),
            _assign(3, "right", "4"),
        ]
    )
    frames = reconstruct_frames(trace)
    pointers = {p.name: p.index for p in frames[3].pointers}
    assert pointers == {"left": 0, "right": 4}


def test_out_of_range_and_non_integer_scalars_are_not_pointers() -> None:
    trace = _trace(
        [
            _assign(0, "nums", "[1, 2, 3]"),
            _array_read(1, "nums", 0, "1"),
            _assign(2, "target", "99"),  # out of range
            _assign(3, "label", "hello"),  # not an integer
            _assign(4, "i", "1"),  # valid index
        ]
    )
    frames = reconstruct_frames(trace)
    names = {p.name for p in frames[4].pointers}
    assert names == {"i"}


def test_pointer_rule_contains_no_algorithm_specific_names() -> None:
    """Same rule, arbitrary variable names — proves the generalization
    property directly rather than by inspection."""
    trace = _trace(
        [
            _assign(0, "data", "[9, 8, 7]"),
            _array_read(1, "data", 0, "9"),
            _assign(2, "zebra", "2"),
            _assign(3, "aardvark", "0"),
        ]
    )
    frames = reconstruct_frames(trace)
    pointers = {p.name: p.index for p in frames[3].pointers}
    assert pointers == {"zebra": 2, "aardvark": 0}

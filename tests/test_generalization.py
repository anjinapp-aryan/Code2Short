"""PHASE 4.2 GENERALIZATION GATE.

One generic visualization implementation must handle all five required
algorithms with **no algorithm-specific renderer branches**. These tests
run the real Java pipeline for each algorithm and assert the same generic
code produces sensible array/pointer state for every one.

Also guards the property structurally: a source scan asserts no algorithm
name appears in the visualization package.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java import JavaAdapter
from code2shorts.visualization.state import reconstruct_frames
from tests.java_fixtures import load_algorithm_fixture, load_java_fixture

pytestmark = pytest.mark.integration

# (fixture loader, variant, program argument, expected stdout)
CASES = [
    ("reverse_string", "correct", "HELLO", "OLLEH"),
    ("algorithm", "palindrome", "RACECAR", "true"),
    ("algorithm", "two_sum", "9", "0,1"),
    ("algorithm", "move_zeroes", "", "1,3,12,0,0"),
    ("algorithm", "remove_duplicates", "", "3"),
]


def _load(kind: str, variant: str):
    return load_java_fixture(variant) if kind == "reverse_string" else load_algorithm_fixture(variant)


@pytest.mark.parametrize(("kind", "variant", "arg", "expected"), CASES)
def test_every_algorithm_traces_and_reconstructs_generically(
    kind: str, variant: str, arg: str, expected: str
) -> None:
    adapter = JavaAdapter()
    code = _load(kind, variant)
    with Workspace() as workspace:
        trace = adapter.trace(code, workspace, arg)

    assert trace.succeeded, trace.stderr
    assert trace.output == expected, f"{variant}: got {trace.output!r}"

    frames = reconstruct_frames(trace)
    assert len(frames) == len(trace.events)

    # The SAME generic reconstruction must find a real array with real
    # contents for every one of these algorithms.
    with_array = [f for f in frames if f.primary_array and f.primary_array.cells]
    assert with_array, f"{variant}: no array state reconstructed"

    array = with_array[-1].primary_array
    assert all(cell != "" for cell in array.cells), f"{variant}: array has empty cells"

    # And the same generic pointer rule must find pointers at some point.
    assert any(f.pointers for f in frames), f"{variant}: no pointers derived"


@pytest.mark.parametrize(("kind", "variant", "arg", "expected"), CASES)
def test_reconstructed_array_matches_real_program_result(
    kind: str, variant: str, arg: str, expected: str
) -> None:
    """The reconstruction must agree with what the program actually did —
    the array state is derived from real writes, so its final contents are
    checkable against the real output for the in-place algorithms."""
    adapter = JavaAdapter()
    code = _load(kind, variant)
    with Workspace() as workspace:
        trace = adapter.trace(code, workspace, arg)

    frames = reconstruct_frames(trace)
    final = [f for f in frames if f.primary_array and f.primary_array.cells][-1]
    cells = final.primary_array.cells

    if variant == "correct":  # reverse string, in place
        assert "".join(cells) == "OLLEH"
    elif variant == "move_zeroes":  # in place, stdout is the same array
        assert ",".join(cells) == "1,3,12,0,0"
    else:
        # palindrome / two_sum / remove_duplicates do not print the array,
        # so only structural correctness is assertable here.
        assert len(cells) > 0


def test_no_algorithm_specific_branches_in_visualization_package() -> None:
    """Structural guarantee, not a promise: fail if any algorithm name
    leaks into the generic visualization code."""
    package = Path(__file__).resolve().parents[1] / "src" / "code2shorts" / "visualization"
    forbidden = [
        "reverse_string", "reverseString",
        "two_sum", "twoSum",
        "palindrome",
        "move_zeroes", "moveZeroes",
        "remove_duplicates", "removeDuplicates",
    ]
    offenders: list[str] = []
    for path in package.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in forbidden:
            if name in text:
                offenders.append(f"{path.name}: {name}")
    assert not offenders, f"algorithm-specific logic found: {offenders}"

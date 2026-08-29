"""PHASE 6.1 — narration may not describe a state the learner cannot see.

The defect this module locks closed, reproduced from a real run: one
educational moment expanded into several visualization steps and the
moment's sentence was copied into each of them, so a frame showing

    left = 0   right = 6

was narrated "left becomes 1 and right becomes 5". Every timing validator
passed, because the timing was never wrong — the words were.

The check is deliberately structured rather than lexical. Both halves of
a claim come from the trace: the variable names are the scalars the
reconstructed frame actually holds, and a token counts as a claim only if
it equals a value that variable takes at some other step. A number that
is never one of the variable's values is prose, not an assertion.
"""

from __future__ import annotations

import pytest

from code2shorts.ai.contracts import (
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import ExecutionTrace, TraceEvent, TraceEventType
from code2shorts.visualization.validation import validate_visualization_plan

# left/right walking inward over RACECAR, exactly as the real trace does.
POINTER_STEPS = [
    ("left", "0"),
    ("right", "6"),
    ("left", "1"),
    ("right", "5"),
    ("left", "2"),
    ("right", "4"),
    ("left", "3"),
    ("right", "3"),
]


def _trace() -> ExecutionTrace:
    return ExecutionTrace(
        algorithm_name="Main",
        language="java",
        input="RACECAR",
        output="true",
        succeeded=True,
        exit_code=0,
        events=[
            TraceEvent(
                step_index=i,
                event_type=TraceEventType.VARIABLE_ASSIGN.value,
                description=f"{name} = {value}",
                line_number=i + 5,
                variable_name=name,
                new_value=value,
            )
            for i, (name, value) in enumerate(POINTER_STEPS)
        ],
    )


def _plan(narrations: dict[int, str]) -> VisualizationPlanResponse:
    """One step per trace event; `narrations` overrides the text per event."""
    return VisualizationPlanResponse(
        lesson_title="Two pointers",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.HIGHLIGHT,
                trace_event_index=i,
                narration_text=narrations.get(i, f"Step {i}."),
                duration_seconds=3.0,
            )
            for i in range(len(POINTER_STEPS))
        ],
    )


def _errors(narrations: dict[int, str]) -> list[str]:
    result = validate_visualization_plan(_plan(narrations), _trace())
    return [error for error in result.errors if "narration asserts" in error]


# ---------------------------------------------------------------------------
# Section 14 — the exact regression.
# ---------------------------------------------------------------------------


def test_the_exact_phase_6_failure_is_detected() -> None:
    """Frame shows left=0, right=6; narration claims 1 and 5."""
    errors = _errors({1: "left becomes 1 and right becomes 5."})
    assert errors
    assert any("left=0" in error and "left=1" in error for error in errors)
    assert any("right=6" in error and "right=5" in error for error in errors)


def test_the_same_claim_spelled_out_is_detected() -> None:
    """Real narration speaks numbers as words, and TTS is what the learner
    hears — a check that only reads digits would miss the actual defect."""
    errors = _errors({1: "left becomes one and right becomes five."})
    assert errors


def test_the_corresponding_valid_case_passes() -> None:
    """The transition IS the teaching point; describing it in words rather
    than asserting an unseen value is exactly right (section 7)."""
    assert not _errors({1: "Both pointers move inward after the match."})


def test_the_claim_passes_once_the_state_has_arrived() -> None:
    """Same sentence, later step: by event 3 the values really are 1 and 5,
    so the identical words are now a correct description."""
    assert not _errors({3: "left is 1 and right is 5."})


# ---------------------------------------------------------------------------
# Section 13 — the synchronization patterns a lesson actually uses.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "index,text,valid",
    [
        pytest.param(0, "The left pointer is at index 0.", True, id="A-state"),
        pytest.param(
            2, "Because the characters matched, the pointers moved inward.",
            True, id="B-transition",
        ),
        pytest.param(7, "The pointers now meet at index 3.", True, id="C-result"),
        pytest.param(
            2, "left is less than right, so the loop continues.", True,
            id="D-loop-continuation",
        ),
        pytest.param(
            7, "left is no longer less than right, so the loop ends.", True,
            id="E-loop-termination",
        ),
        pytest.param(
            1, "The characters at both ends are equal.", True,
            id="F-successful-comparison",
        ),
        pytest.param(
            1, "The characters at both ends differ.", True,
            id="G-failed-comparison",
        ),
        pytest.param(
            2, "left has advanced to 1.", True, id="H-pointer-movement",
        ),
        pytest.param(
            7, "The method returns true.", True, id="I-return",
        ),
        pytest.param(
            0, "The pointers start at opposite ends and will move inward.",
            True, id="J-spans-a-transition",
        ),
        pytest.param(
            0, "left becomes 3 and right becomes 3.", False, id="K-future-state",
        ),
    ],
)
def test_synchronization_patterns(index: int, text: str, valid: bool) -> None:
    errors = _errors({index: text})
    assert (not errors) is valid, errors


# ---------------------------------------------------------------------------
# The check must not become a brittle English parser (section 9).
# ---------------------------------------------------------------------------


def test_a_number_that_is_never_a_value_of_that_variable_is_ignored() -> None:
    """`left` never holds 42, so the number is prose, not a claim."""
    assert not _errors({0: "There are 42 ways to write this, but left is 0."})


def test_a_past_value_is_not_reported() -> None:
    """Recap is teaching. At event 4 left is 2; mentioning that it was 1 is
    a description of something the learner has already watched happen."""
    assert not _errors({4: "left was 1 a moment ago and has now advanced."})


def test_a_value_is_attributed_to_the_nearest_named_variable() -> None:
    """'left becomes 1 and right becomes 5' must not read as left=5."""
    errors = _errors({1: "left becomes 1 and right becomes 5."})
    assert not any("left=5" in error for error in errors)
    assert not any("right=1" in error for error in errors)


def test_narration_naming_no_variable_asserts_nothing() -> None:
    assert not _errors({0: "This is the two-pointer technique."})


def test_the_check_is_deterministic() -> None:
    narrations = {1: "left becomes 1 and right becomes 5."}
    assert _errors(narrations) == _errors(narrations)


def test_it_reports_through_the_plan_validator_so_repair_sees_it() -> None:
    """It must live inside `validate_visualization_plan`, because that is
    what `generate_with_repair` feeds back to the model. A separate
    report-only checker would detect the defect and ship it anyway."""
    result = validate_visualization_plan(
        _plan({1: "left becomes 1 and right becomes 5."}), _trace()
    )
    assert not result.passed
    assert result.stage == "semantic"


def test_a_clean_plan_still_validates() -> None:
    """Guard against the new check rejecting ordinary correct narration."""
    result = validate_visualization_plan(_plan({}), _trace())
    assert result.passed, result.errors

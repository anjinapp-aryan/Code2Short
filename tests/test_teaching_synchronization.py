"""Phase 6.1: semantic narration <-> visual synchronization.

Matching total durations proves nothing about teaching: a track can match
in length while every sentence describes a state the learner has already
stopped seeing. These tests cover the relationship the learner actually
experiences.
"""

from __future__ import annotations

import pytest

from code2shorts.ai.contracts import (
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import ExecutionTrace, TraceEvent, TraceEventType
from code2shorts.narration import (
    align_narration,
    fit_plan_to_narration,
    validate_teaching_synchronization,
)


def _trace(events: int = 4) -> ExecutionTrace:
    return ExecutionTrace(
        algorithm_name="Main", language="java", input="RACECAR", output="true",
        succeeded=True, exit_code=0,
        events=[
            TraceEvent(step_index=i, event_type=TraceEventType.VARIABLE_ASSIGN.value,
                       description=f"event {i}", line_number=i + 5)
            for i in range(events)
        ],
    )


def _plan(durations: list[float], event_indices: list[int] | None = None):
    indices = event_indices or list(range(len(durations)))
    return VisualizationPlanResponse(
        lesson_title="T",
        steps=[
            VisualizationStepPlan(
                order=i, visual_action=VisualAction.HIGHLIGHT,
                trace_event_index=indices[i], narration_text=f"n{i}",
                duration_seconds=d,
            )
            for i, d in enumerate(durations)
        ],
    )


def _narration(count: int, step_for: dict[int, int] | None = None):
    mapping = step_for or {i: i for i in range(count)}
    return NarrationResponse(
        segments=[
            NarrationSegment(order=i, text=f"n{i}", visualization_step_order=mapping[i])
            for i in range(count)
        ]
    )


def _audio(durations: list[float]):
    return {i: (f"seg_{i}.wav", d) for i, d in enumerate(durations)}


def _aligned(plan, narration, audio):
    fitted = fit_plan_to_narration(plan, narration, audio).plan
    return align_narration(narration, fitted, audio_by_segment=audio), fitted


# ---- the happy path the real pipeline produces ----------------------------


def test_a_fitted_timeline_is_semantically_synchronized() -> None:
    """TTS measured -> plan widened -> aligned. This is the real order the
    golden path runs in, and it must validate."""
    plan, narration = _plan([1.0, 1.0, 1.0]), _narration(3)
    audio = _audio([3.2, 2.1, 4.4])

    alignment, fitted = _aligned(plan, narration, audio)
    result = validate_teaching_synchronization(alignment, fitted, _trace())

    assert result.passed, result.errors
    assert alignment.overflow_count == 0


def test_every_segment_occupies_exactly_its_own_visual_window() -> None:
    plan, narration = _plan([2.0, 3.0]), _narration(2)
    audio = _audio([1.0, 1.0])
    alignment, fitted = _aligned(plan, narration, audio)

    windows = []
    cursor = 0.0
    for step in fitted.steps:
        windows.append((cursor, cursor + step.duration_seconds))
        cursor += step.duration_seconds

    for segment, (start, end) in zip(alignment.segments, windows):
        assert segment.start_seconds == pytest.approx(start)
        assert segment.end_seconds == pytest.approx(end)


# ---- the failures it exists to catch --------------------------------------


def test_speech_running_past_its_visual_state_is_rejected() -> None:
    """The core defect: narration still explaining a state the learner can
    no longer see. Alignment is done WITHOUT fitting, so the audio
    overflows its step."""
    plan, narration = _plan([1.0, 1.0]), _narration(2)
    audio = _audio([5.0, 1.0])          # 5s of speech in a 1s step
    alignment = align_narration(narration, plan, audio_by_segment=audio)

    result = validate_teaching_synchronization(alignment, plan, _trace())
    assert not result.passed
    assert any("past the visual state" in e for e in result.errors)


def test_a_segment_narrating_a_nonexistent_step_is_rejected() -> None:
    plan = _plan([1.0])
    narration = _narration(1, step_for={0: 7})
    alignment = align_narration(
        narration,
        _plan([1.0] * 8),               # align against a plan that HAS step 7
        audio_by_segment=_audio([0.5]),
    )
    result = validate_teaching_synchronization(alignment, plan, _trace())
    assert not result.passed
    assert any("not in the plan" in e for e in result.errors)


def test_a_step_citing_a_fabricated_trace_event_is_rejected() -> None:
    """Synchronization must not launder ungrounded state."""
    plan = _plan([1.0], event_indices=[999])
    narration = _narration(1)
    audio = _audio([0.5])
    alignment, fitted = _aligned(plan, narration, audio)

    result = validate_teaching_synchronization(alignment, fitted, _trace())
    assert not result.passed
    assert any("999" in e and "does not exist" in e for e in result.errors)


def test_unmeasured_audio_is_rejected() -> None:
    """Timing may never rest on a character-count estimate."""
    plan, narration = _plan([1.0]), _narration(1)
    alignment = align_narration(narration, plan)      # no audio supplied
    result = validate_teaching_synchronization(alignment, plan, _trace())
    assert not result.passed
    assert any("no measured audio" in e for e in result.errors)


def test_segments_heard_out_of_step_order_are_rejected() -> None:
    """Out-of-order narration cannot even be aligned.

    `align_narration` raises rather than producing a timeline in which the
    learner hears step 1 explained before step 0 — a stronger guarantee
    than validating after the fact, and the reason the validator's own
    ordering check is a backstop rather than the primary defence.
    """
    from code2shorts.narration.alignment import AlignmentError

    plan = _plan([1.0, 1.0])
    narration = _narration(2, step_for={0: 1, 1: 0})   # narrates step 1 first
    audio = _audio([0.5, 0.5])

    with pytest.raises(AlignmentError, match="overlaps"):
        align_narration(narration, plan, audio_by_segment=audio)


# ---- the ordering guarantee the pipeline relies on ------------------------


def test_fitting_makes_an_overflowing_timeline_valid() -> None:
    """Widening the visuals is what resolves overflow — never trimming the
    narration (ADR-5.11)."""
    plan, narration = _plan([1.0, 1.0]), _narration(2)
    audio = _audio([4.0, 3.0])

    before = align_narration(narration, plan, audio_by_segment=audio)
    assert not validate_teaching_synchronization(before, plan, _trace()).passed

    alignment, fitted = _aligned(plan, narration, audio)
    assert validate_teaching_synchronization(alignment, fitted, _trace()).passed
    # the narration text is unchanged; only the visuals grew
    assert [s.text for s in alignment.segments] == [s.text for s in narration.segments]
    assert all(
        fitted.steps[i].duration_seconds >= plan.steps[i].duration_seconds
        for i in range(len(plan.steps))
    )


def test_subtitles_come_from_the_same_timeline(tmp_path) -> None:
    """One authoritative timeline drives speech, visuals AND subtitles."""
    import srt

    from code2shorts.narration.subtitles import write_srt

    plan, narration = _plan([1.0, 1.0]), _narration(2)
    audio = _audio([2.5, 1.5])
    alignment, _ = _aligned(plan, narration, audio)

    path = write_srt(alignment, tmp_path / "s.srt")
    cues = list(srt.parse(path.read_text(encoding="utf-8")))

    assert len(cues) == len(alignment.segments)
    for cue, segment in zip(cues, alignment.segments):
        assert cue.start.total_seconds() == pytest.approx(segment.start_seconds, abs=0.01)
        assert cue.end.total_seconds() == pytest.approx(segment.end_seconds, abs=0.01)


def test_the_timeline_carries_no_duration_budget() -> None:
    """ADR-5.11 still holds: synchronization never shortens teaching."""
    import ast
    import inspect

    from code2shorts.narration import alignment as module

    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {
            "MAX_DURATION", "MAX_VIDEO_SECONDS", "TARGET_DURATION", "MAX_STEPS",
        }:
            pytest.fail(f"a duration budget appeared in the timeline: {node.id}")

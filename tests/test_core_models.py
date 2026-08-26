import pytest
from pydantic import ValidationError

from code2shorts.core.models import (
    AlgorithmSpec,
    AnimationSpec,
    AnimationStep,
    Example,
    ExceptionInfo,
    ExecutionTrace,
    LessonSpec,
    SupportedLanguage,
    TraceEvent,
    TraceEventType,
    TraceStatus,
)


def _reverse_string_algorithm_spec() -> AlgorithmSpec:
    return AlgorithmSpec(
        name="Reverse String",
        category="string",
        function_name="reverseString",
        parameters=["char[] s"],
        return_description="void; s is reversed in place",
        constraints=["1 <= s.length <= 10^5"],
        examples=[Example(input="HELLO", output="OLLEH")],
        time_complexity="O(n)",
        space_complexity="O(1)",
    )


def test_lesson_spec_builds_from_algorithm_spec() -> None:
    lesson = LessonSpec(
        topic="Reverse String",
        algorithm=_reverse_string_algorithm_spec(),
        language=SupportedLanguage.JAVA,
    )
    assert lesson.aspect_ratio == "9:16"
    assert lesson.algorithm.examples[0].output == "OLLEH"


def test_execution_trace_requires_sequential_step_index() -> None:
    events = [
        TraceEvent(step_index=0, event_type="pointer_move", description="left=0, right=4"),
        TraceEvent(step_index=2, event_type="swap", description="swap s[0], s[4]"),
    ]
    with pytest.raises(ValidationError):
        ExecutionTrace(
            algorithm_name="Reverse String",
            language=SupportedLanguage.JAVA,
            input="HELLO",
            output="OLLEH",
            succeeded=True,
            exit_code=0,
            events=events,
        )


def test_execution_trace_accepts_sequential_events() -> None:
    events = [
        TraceEvent(step_index=0, event_type="pointer_move", description="left=0, right=4"),
        TraceEvent(step_index=1, event_type="swap", description="swap s[0], s[4]"),
    ]
    trace = ExecutionTrace(
        algorithm_name="Reverse String",
        language=SupportedLanguage.JAVA,
        input="HELLO",
        output="OLLEH",
        succeeded=True,
        exit_code=0,
        events=events,
    )
    assert len(trace.events) == 2


def test_execution_trace_phase2_defaults_are_backward_compatible() -> None:
    # Constructing with only the Phase 0/1 required fields must still work.
    trace = ExecutionTrace(
        algorithm_name="Reverse String",
        language=SupportedLanguage.JAVA,
        input="HELLO",
        output="OLLEH",
        succeeded=True,
        exit_code=0,
    )
    assert trace.status == TraceStatus.COMPLETED
    assert trace.exception is None
    assert trace.truncated is False
    assert trace.event_count == 0
    assert trace.max_call_depth_reached == 0
    assert trace.total_loop_iterations == 0


def test_execution_trace_statistics_are_computed_from_events() -> None:
    events = [
        TraceEvent(
            step_index=0,
            event_type=TraceEventType.LOOP_ITERATION.value,
            description="iteration 1",
            call_depth=1,
            iteration=1,
        ),
        TraceEvent(
            step_index=1,
            event_type=TraceEventType.LOOP_ITERATION.value,
            description="iteration 2",
            call_depth=2,
            iteration=2,
        ),
        TraceEvent(
            step_index=2,
            event_type=TraceEventType.METHOD_EXIT.value,
            description="return olleh",
            call_depth=1,
            return_value="olleh",
        ),
    ]
    trace = ExecutionTrace(
        algorithm_name="Reverse String",
        language=SupportedLanguage.JAVA,
        input="HELLO",
        output="OLLEH",
        succeeded=True,
        exit_code=0,
        events=events,
    )
    assert trace.event_count == 3
    assert trace.max_call_depth_reached == 2
    assert trace.total_loop_iterations == 2


def test_execution_trace_carries_exception_info_on_runtime_error() -> None:
    trace = ExecutionTrace(
        algorithm_name="Reverse String",
        language=SupportedLanguage.JAVA,
        input="",
        output="",
        succeeded=False,
        exit_code=1,
        status=TraceStatus.RUNTIME_ERROR,
        exception=ExceptionInfo(
            exception_class="java.lang.ArrayIndexOutOfBoundsException",
            message="Index 5 out of bounds for length 5",
            thrown_at_line=9,
            thrown_in_method="reverse",
        ),
    )
    assert trace.status == TraceStatus.RUNTIME_ERROR
    assert trace.exception is not None
    assert trace.exception.exception_class.endswith("ArrayIndexOutOfBoundsException")


def test_execution_trace_json_round_trip() -> None:
    trace = ExecutionTrace(
        algorithm_name="Reverse String",
        language=SupportedLanguage.JAVA,
        input="HELLO",
        output="OLLEH",
        succeeded=True,
        exit_code=0,
        events=[
            TraceEvent(
                step_index=0,
                event_type=TraceEventType.VARIABLE_ASSIGN.value,
                description="left = 0",
                variable_name="left",
                new_value="0",
            )
        ],
    )
    round_tripped = ExecutionTrace.model_validate_json(trace.model_dump_json())
    assert round_tripped == trace


def test_animation_spec_total_duration_sums_steps() -> None:
    spec = AnimationSpec(
        lesson_title="Reverse String",
        steps=[
            AnimationStep(
                step_index=0,
                trace_event_index=0,
                visual_action="move_pointer",
                narration_text="left and right pointers start at the ends",
                duration_seconds=2.0,
            ),
            AnimationStep(
                step_index=1,
                trace_event_index=1,
                visual_action="swap",
                narration_text="swap the characters",
                duration_seconds=1.5,
            ),
        ],
    )
    assert spec.total_duration_seconds == 3.5

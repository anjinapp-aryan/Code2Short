import pytest

from code2shorts.ai import (
    ExplanationResponse,
    ExplanationStep,
    SchemaValidationError,
    generate_structured,
    validate_explanation_against_trace,
)
from code2shorts.ai.providers import MockLLMProvider
from code2shorts.core.models import SupportedLanguage
from code2shorts.core.models import ExecutionTrace, TraceEvent, TraceEventType


def _trace_with_events(count: int) -> ExecutionTrace:
    events = [
        TraceEvent(step_index=i, event_type=TraceEventType.VARIABLE_ASSIGN.value, description="x")
        for i in range(count)
    ]
    return ExecutionTrace(
        algorithm_name="Test",
        language=SupportedLanguage.JAVA,
        input="",
        output="",
        succeeded=True,
        exit_code=0,
        events=events,
    )


def test_generate_structured_parses_valid_json() -> None:
    valid_json = ExplanationResponse(
        summary="s", steps=[ExplanationStep(order=0, description="d")]
    ).model_dump_json()
    provider = MockLLMProvider(canned_response=valid_json)

    response = generate_structured(provider, "prompt", ExplanationResponse)

    assert response.summary == "s"
    assert provider.calls == ["prompt"]


def test_generate_structured_raises_on_malformed_json() -> None:
    provider = MockLLMProvider(canned_response="not json at all")
    with pytest.raises(SchemaValidationError):
        generate_structured(provider, "prompt", ExplanationResponse)


def test_generate_structured_raises_on_valid_json_wrong_shape() -> None:
    provider = MockLLMProvider(canned_response='{"totally": "wrong shape"}')
    with pytest.raises(SchemaValidationError):
        generate_structured(provider, "prompt", ExplanationResponse)


def test_mock_provider_without_canned_response_raises() -> None:
    provider = MockLLMProvider()
    with pytest.raises(NotImplementedError):
        provider.complete("prompt")


def test_validate_explanation_passes_with_real_trace_references() -> None:
    trace = _trace_with_events(3)
    response = ExplanationResponse(
        summary="s",
        steps=[ExplanationStep(order=0, description="d", referenced_trace_event_indices=[0, 1])],
        referenced_trace_event_indices=[2],
    )
    result = validate_explanation_against_trace(response, trace)
    assert result.passed
    assert result.errors == []


def test_validate_explanation_fails_on_hallucinated_trace_reference() -> None:
    trace = _trace_with_events(3)  # valid indices: 0, 1, 2
    response = ExplanationResponse(
        summary="s",
        steps=[ExplanationStep(order=0, description="d", referenced_trace_event_indices=[99])],
    )
    result = validate_explanation_against_trace(response, trace)
    assert not result.passed
    assert "99" in result.errors[0]


def test_validate_explanation_fails_with_no_steps() -> None:
    trace = _trace_with_events(1)
    response = ExplanationResponse(summary="s", steps=[])
    result = validate_explanation_against_trace(response, trace)
    assert not result.passed

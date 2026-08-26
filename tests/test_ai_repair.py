"""Unit tests for the generic bounded generate-validate-repair loop
(ai/repair.py), independent of any specific node — proves the loop itself
is bounded and correct before any node built on top of it is trusted.
"""

from __future__ import annotations

import pytest

from code2shorts.ai.contracts import ExplanationResponse, ExplanationStep
from code2shorts.ai.providers import MockLLMProvider
from code2shorts.ai.repair import SemanticValidationError, generate_with_repair
from code2shorts.ai.structured import SchemaValidationError
from code2shorts.core.models import ValidationResult


def _valid_json() -> str:
    return ExplanationResponse(
        summary="s", steps=[ExplanationStep(order=0, description="d")]
    ).model_dump_json()


def _always_pass(_: ExplanationResponse) -> ValidationResult:
    return ValidationResult(stage="semantic", passed=True)


def _always_fail(_: ExplanationResponse) -> ValidationResult:
    return ValidationResult(stage="semantic", passed=False, errors=["always fails"])


def test_succeeds_on_first_attempt_no_repair_needed() -> None:
    provider = MockLLMProvider(canned_response=_valid_json())
    response, history = generate_with_repair(
        provider, build_prompt=lambda: "p", response_model=ExplanationResponse,
        validate=_always_pass, max_repair_attempts=2,
    )
    assert response.summary == "s"
    assert len(history) == 1
    assert len(provider.calls) == 1  # no repair round-trip


def test_repairs_after_malformed_json_then_succeeds() -> None:
    attempts = {"n": 0}

    def respond(prompt: str) -> str:
        attempts["n"] += 1
        return "not json" if attempts["n"] == 1 else _valid_json()

    provider = MockLLMProvider(canned_response=respond)
    response, history = generate_with_repair(
        provider, build_prompt=lambda: "p", response_model=ExplanationResponse,
        validate=_always_pass, max_repair_attempts=2,
    )
    assert response.summary == "s"
    assert attempts["n"] == 2
    assert len(history) == 2
    assert not history[0].passed  # schema failure recorded
    assert history[1].passed


def test_repairs_after_semantic_failure_then_succeeds() -> None:
    calls = {"n": 0}

    def validate(_: ExplanationResponse) -> ValidationResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return ValidationResult(stage="semantic", passed=False, errors=["bad reference"])
        return ValidationResult(stage="semantic", passed=True)

    provider = MockLLMProvider(canned_response=_valid_json())
    response, history = generate_with_repair(
        provider, build_prompt=lambda: "p", response_model=ExplanationResponse,
        validate=validate, max_repair_attempts=2,
    )
    assert response.summary == "s"
    assert len(provider.calls) == 2  # original + one repair round-trip
    assert len(history) == 2


def test_schema_failure_exhausts_attempts_and_raises() -> None:
    provider = MockLLMProvider(canned_response="never valid json")
    with pytest.raises(SchemaValidationError):
        generate_with_repair(
            provider, build_prompt=lambda: "p", response_model=ExplanationResponse,
            validate=_always_pass, max_repair_attempts=2,
        )
    assert len(provider.calls) == 3  # initial + 2 repair attempts, then stop


def test_semantic_failure_exhausts_attempts_and_raises() -> None:
    provider = MockLLMProvider(canned_response=_valid_json())
    with pytest.raises(SemanticValidationError):
        generate_with_repair(
            provider, build_prompt=lambda: "p", response_model=ExplanationResponse,
            validate=_always_fail, max_repair_attempts=1,
        )
    assert len(provider.calls) == 2  # initial + 1 repair attempt, then stop — bounded, not infinite


def test_repair_prompt_includes_previous_output_and_errors() -> None:
    seen_prompts = []

    def respond(prompt: str) -> str:
        seen_prompts.append(prompt)
        return "bad json" if len(seen_prompts) == 1 else _valid_json()

    provider = MockLLMProvider(canned_response=respond)
    generate_with_repair(
        provider, build_prompt=lambda: "original prompt", response_model=ExplanationResponse,
        validate=_always_pass, max_repair_attempts=1,
    )
    assert seen_prompts[0] == "original prompt"
    assert "bad json" in seen_prompts[1]
    assert "validation" in seen_prompts[1].lower()

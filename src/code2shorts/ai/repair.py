"""Bounded AI repair loop, shared by every AI-backed workflow node.

    Generate -> Validate -> PASS: done
                          -> FAIL: repair prompt -> Generate -> Validate -> ...

Never loops unboundedly — `max_repair_attempts` caps it, and a repair
prompt is built from (previous raw output + validation errors), never from
letting the model "try again from scratch" with no feedback. If repair is
still failing when attempts run out, this raises rather than returning
something invalid — no node using this ever produces an artifact from
output that failed validation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from code2shorts.ai.structured import SchemaValidationError, extract_json
from code2shorts.core.models import ValidationResult
from code2shorts.llm.provider import LLMProvider

T = TypeVar("T", bound=BaseModel)


class SemanticValidationError(Exception):
    """Every repair attempt was schema-valid but failed semantic
    validation, and max_repair_attempts was exhausted."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def generate_with_repair(
    provider: LLMProvider,
    build_prompt: Callable[[], str],
    response_model: type[T],
    validate: Callable[[T], ValidationResult],
    max_repair_attempts: int = 1,
    system: str | None = None,
    build_repair_prompt: Callable[[str, list[str]], str] | None = None,
) -> tuple[T, list[ValidationResult]]:
    """Returns (validated response, full history of validation attempts).
    Raises SchemaValidationError / SemanticValidationError if every attempt
    (initial + repairs) fails.
    """
    repair_prompt_builder = build_repair_prompt or _default_repair_prompt

    prompt = build_prompt()
    history: list[ValidationResult] = []
    last_raw = ""

    for attempt in range(max_repair_attempts + 1):
        raw = provider.complete(prompt, system=system)
        last_raw = raw
        try:
            # extract_json, not raw: the SAME parse path as
            # generate_structured. Two divergent parse paths meant a
            # fenced-but-correct reply burned a repair attempt here
            # while succeeding there.
            parsed = response_model.model_validate_json(extract_json(raw))
        except ValidationError as error:
            schema_result = ValidationResult(stage="schema", passed=False, errors=[str(error)])
            history.append(schema_result)
            if attempt >= max_repair_attempts:
                raise SchemaValidationError(str(error)) from error
            prompt = repair_prompt_builder(raw, schema_result.errors)
            continue

        result = validate(parsed)
        history.append(result)
        if result.passed:
            return parsed, history
        if attempt >= max_repair_attempts:
            raise SemanticValidationError(result.errors)
        prompt = repair_prompt_builder(raw, result.errors)

    # Unreachable (the loop always returns or raises), but keeps type
    # checkers happy and fails loudly instead of silently if it ever is.
    raise SemanticValidationError([f"repair loop exhausted without resolution (last output: {last_raw[:200]!r})"])


def _default_repair_prompt(previous_raw_output: str, errors: list[str]) -> str:
    error_lines = "\n".join(f"- {error}" for error in errors)
    return (
        "Your previous response failed validation. Fix it and return ONLY "
        "corrected JSON matching the same schema — do not change the "
        "required structure, do not add commentary.\n\n"
        f"Validation errors:\n{error_lines}\n\n"
        f"Previous response:\n{previous_raw_output}"
    )

"""Semantic validation: AI-generated claims must never silently become
trusted facts. Reuses `workflow.state.ValidationResult` rather than a
second validation-result type — schema validation (ai/structured.py) and
semantic validation (here) both report into the same shape so
Code2ShortsState.validation stays one accumulator, not two.

    LLM output
        v
    Schema validation      (ai/structured.py — Pydantic parse)
        v
    Semantic validation     (here — cross-check claims against the real
                             ExecutionTrace)
        v
    Accepted Artifact
"""

from __future__ import annotations

from code2shorts.ai.contracts import ExplanationResponse
from code2shorts.core.models import ExecutionTrace, ValidationResult


def validate_explanation_against_trace(
    response: ExplanationResponse, trace: ExecutionTrace
) -> ValidationResult:
    """The AI must reference real TraceEvent step_indexes only. A
    reference to an index that doesn't exist in the trace is a
    hallucinated execution fact — always a validation failure, never
    silently accepted.
    """
    valid_indices = {event.step_index for event in trace.events}

    referenced: set[int] = set(response.referenced_trace_event_indices)
    for step in response.steps:
        referenced |= set(step.referenced_trace_event_indices)

    hallucinated = sorted(referenced - valid_indices)
    errors = []
    if hallucinated:
        errors.append(
            f"referenced trace event index(es) not present in the trace: {hallucinated}"
        )
    if not response.steps:
        errors.append("explanation has no steps")

    return ValidationResult(stage="semantic", passed=not errors, errors=errors)

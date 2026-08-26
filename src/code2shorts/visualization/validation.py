"""Semantic validation for VisualizationPlanResponse. Invalid plans must
never reach the renderer — this is the trust gate between AI output and
`VisualizationRenderer` (see visualization/renderer.py).

Two defense layers, deliberately both present:

1. STRUCTURAL (the schema itself, ai/contracts.py): `visual_action` is a
   closed enum and no field is shaped like "code"/"command"/"script" — an
   executable payload cannot even be schema-validated into the model. This
   is the primary defense and cannot be bypassed by anything checked here.
2. SEMANTIC (this module): the plan's *claims* are cross-checked against
   the real ExecutionTrace, plus a defense-in-depth text scan (narration
   text is never executed by the renderer, but a plan containing shell/
   Python-shaped text is still rejected outright rather than trusted to
   "probably be harmless").
"""

from __future__ import annotations

import re

from code2shorts.ai.contracts import VisualizationPlanResponse
from code2shorts.core.models import ExecutionTrace, ValidationResult

_SUSPICIOUS_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bimport\s+os\b",
        r"\bimport\s+subprocess\b",
        r"\bsubprocess\.",
        r"\bos\.system\b",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"rm\s+-rf",
        r"`[^`]*`",  # backtick shell substitution
        r"\$\(",  # $(command) shell substitution
        r"&&|\|\|",  # shell chaining
        r";\s*(rm|curl|wget|bash|sh)\b",
    ]
]


def _scan_for_executable_payload(text: str) -> str | None:
    for pattern in _SUSPICIOUS_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def validate_visualization_plan(
    plan: VisualizationPlanResponse, trace: ExecutionTrace
) -> ValidationResult:
    errors: list[str] = []

    valid_indices = {event.step_index for event in trace.events}
    events_by_index = {event.step_index: event for event in trace.events}

    if not plan.steps:
        errors.append("visualization plan has no steps")

    # order must be sequential 0..N-1, same contract TraceEvent.step_index
    # already enforces — a visualization plan is itself a trace-like
    # sequence and should honor the same invariant.
    expected_orders = list(range(len(plan.steps)))
    actual_orders = [step.order for step in plan.steps]
    if actual_orders != expected_orders:
        errors.append(
            f"step order must be sequential starting at 0; got {actual_orders}"
        )

    previous_trace_index: int | None = None
    for step in plan.steps:
        if step.trace_event_index not in valid_indices:
            errors.append(
                f"step {step.order} references trace_event_index="
                f"{step.trace_event_index}, which does not exist in the trace"
            )
        elif previous_trace_index is not None and step.trace_event_index < previous_trace_index:
            errors.append(
                f"step {step.order} references trace_event_index="
                f"{step.trace_event_index}, which is BEFORE the previously "
                f"visualized event ({previous_trace_index}) — visualization "
                "must follow the trace's chronological order"
            )
        if step.trace_event_index in events_by_index:
            previous_trace_index = step.trace_event_index

        if step.variable_name is not None and step.trace_event_index in events_by_index:
            real_event = events_by_index[step.trace_event_index]
            if real_event.variable_name and step.variable_name != real_event.variable_name:
                errors.append(
                    f"step {step.order} claims variable_name={step.variable_name!r} "
                    f"but trace event {step.trace_event_index} actually concerns "
                    f"{real_event.variable_name!r}"
                )

        for text in (step.narration_text, plan.lesson_title):
            hit = _scan_for_executable_payload(text)
            if hit:
                errors.append(
                    f"step {step.order} narration/title contains a suspicious "
                    f"pattern ({hit!r}) — rejected as a potential executable payload"
                )

    errors += _array_and_pointer_errors(plan, trace)

    return ValidationResult(stage="semantic", passed=not errors, errors=errors)


def _array_and_pointer_errors(
    plan: VisualizationPlanResponse, trace: ExecutionTrace
) -> list[str]:
    """Phase 4.2: the plan may now drive array/pointer rendering, so the
    claims it makes about array state must be checkable against the real
    reconstructed state — not merely against the list of event indices.

    Imported lazily to keep `visualization.state` out of the import path
    for callers that only need the base schema checks.
    """
    from code2shorts.visualization.state import reconstruct_frames

    errors: list[str] = []
    frames = {frame.step_index: frame for frame in reconstruct_frames(trace)}

    for step in plan.steps:
        frame = frames.get(step.trace_event_index)
        if frame is None:
            continue  # already reported as a bad trace reference above

        array = frame.primary_array
        touched = [
            index
            for indices in list(frame.written_indices.values())
            + list(frame.read_indices.values())
            for index in indices
        ]
        if array is not None:
            for index in touched:
                if not (0 <= index < len(array.cells)):
                    errors.append(
                        f"step {step.order} refers to trace event "
                        f"{step.trace_event_index}, which touches "
                        f"{array.name}[{index}] — outside the reconstructed "
                        f"array bounds (0..{len(array.cells) - 1})"
                    )

        # A named variable claim must match a variable that genuinely
        # exists in the reconstructed state at that moment.
        if step.variable_name is not None:
            known = set(frame.scalars) | set(frame.arrays)
            base = step.variable_name.split("[")[0]
            if base not in known:
                errors.append(
                    f"step {step.order} claims variable {step.variable_name!r}, "
                    f"which does not exist at trace event "
                    f"{step.trace_event_index} (known: {sorted(known)})"
                )

    return errors

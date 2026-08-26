"""Semantic validation for NarrationResponse. Narration must not invent
execution facts — it's grounded not directly in the trace but through the
visualization plan (which is itself already trace-grounded), so validation
here checks the narration -> visualization -> trace chain stays intact
rather than re-deriving trace facts independently.
"""

from __future__ import annotations

from code2shorts.ai.contracts import NarrationResponse, VisualizationPlanResponse
from code2shorts.core.models import ValidationResult


def validate_narration(
    narration: NarrationResponse, plan: VisualizationPlanResponse
) -> ValidationResult:
    errors: list[str] = []

    if not narration.segments:
        errors.append("narration has no segments")

    expected_orders = list(range(len(narration.segments)))
    actual_orders = [segment.order for segment in narration.segments]
    if actual_orders != expected_orders:
        errors.append(f"segment order must be sequential starting at 0; got {actual_orders}")

    valid_step_orders = {step.order for step in plan.steps}
    for segment in narration.segments:
        if segment.visualization_step_order not in valid_step_orders:
            errors.append(
                f"segment {segment.order} references visualization_step_order="
                f"{segment.visualization_step_order}, which does not exist in "
                "the visualization plan"
            )

    return ValidationResult(stage="semantic", passed=not errors, errors=errors)

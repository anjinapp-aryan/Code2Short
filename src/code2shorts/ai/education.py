"""LearningCompleteness: does this plan actually teach the algorithm?

Deterministic, and deliberately about *coverage*, not length. The rule
this module encodes (ADR-5.11):

    A video is educationally incomplete when a conceptual transition has
    neither a visual nor a spoken explanation. It is NOT incomplete
    merely because some execution events were not individually shown.

Complete understanding, not complete event playback.

Required concepts are declared **per algorithm shape**, derived from what
the real trace contains, rather than one universal list — a two-pointer
swap and a hash lookup do not need to teach the same things.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from code2shorts.ai.contracts import (
    EducationalPlanResponse,
    LearningConcept,
)
from code2shorts.core.models import ExecutionTrace, TraceEventType, ValidationResult

C = LearningConcept

# Concepts every algorithm must teach, whatever its shape.
UNIVERSAL_CONCEPTS: tuple[LearningConcept, ...] = (
    C.INTRODUCTION,
    C.INITIALIZATION,
    C.CORE_CONCEPT,
    C.TERMINATION,
    C.RESULT,
)


class AlgorithmShape(StrEnum):
    """A structural description of what the execution did — derived from
    the trace, never from the algorithm's name.

    Naming it by shape rather than by algorithm keeps the
    'no algorithm-specific branching' rule intact: nothing here says
    "reverse_string".
    """

    POINTER_TRAVERSAL = "pointer_traversal"
    """Integer scalars that index an array, moving over it."""

    ARRAY_MUTATION = "array_mutation"
    """Writes back into an array."""

    SCALAR_COMPUTATION = "scalar_computation"
    """No array writes; computes a value."""


SHAPE_CONCEPTS: dict[AlgorithmShape, tuple[LearningConcept, ...]] = {
    AlgorithmShape.POINTER_TRAVERSAL: (
        C.DATA_STRUCTURE,
        C.POINTER_MOVEMENT,
        C.DECISION,
        C.LOOP_BEHAVIOUR,
    ),
    AlgorithmShape.ARRAY_MUTATION: (
        C.DATA_STRUCTURE,
        C.DATA_MOVEMENT,
        C.STATE_TRANSITION,
    ),
    AlgorithmShape.SCALAR_COMPUTATION: (C.STATE_TRANSITION,),
}


class LearningCompleteness(BaseModel):
    """Which required concepts a plan covers, and which it misses."""

    shapes: list[AlgorithmShape] = Field(default_factory=list)
    required: list[LearningConcept] = Field(default_factory=list)
    covered: list[LearningConcept] = Field(default_factory=list)
    missing: list[LearningConcept] = Field(default_factory=list)
    unexplained_moments: list[str] = Field(
        default_factory=list,
        description="moment ids that have neither narration nor explanation",
    )
    estimated_narration_words: int = 0
    """Informational only. Never a pass/fail criterion — see ADR-5.11."""

    @property
    def passed(self) -> bool:
        return not self.missing and not self.unexplained_moments


def detect_algorithm_shapes(trace: ExecutionTrace) -> list[AlgorithmShape]:
    """Classify structurally, from the trace alone.

    Uses the same generic signal the renderer already relies on: an
    integer scalar whose value is a valid array index is a pointer. No
    algorithm name is ever consulted.
    """
    shapes: list[AlgorithmShape] = []
    event_types = {event.event_type for event in trace.events}

    array_names = {
        (event.variable_name or "").split("[", 1)[0]
        for event in trace.events
        if event.variable_name and "[" in (event.variable_name or "")
    }
    has_array = bool(array_names) or bool(
        {TraceEventType.ARRAY_READ, TraceEventType.ARRAY_WRITE} & event_types
    )

    if has_array and TraceEventType.LOOP_ITERATION in event_types:
        shapes.append(AlgorithmShape.POINTER_TRAVERSAL)
    if TraceEventType.ARRAY_WRITE in event_types:
        shapes.append(AlgorithmShape.ARRAY_MUTATION)
    if not shapes:
        shapes.append(AlgorithmShape.SCALAR_COMPUTATION)
    return shapes


def required_concepts(trace: ExecutionTrace) -> list[LearningConcept]:
    """The concepts THIS execution must teach."""
    required: list[LearningConcept] = list(UNIVERSAL_CONCEPTS)
    for shape in detect_algorithm_shapes(trace):
        for concept in SHAPE_CONCEPTS[shape]:
            if concept not in required:
                required.append(concept)
    return required


def evaluate_learning_completeness(
    plan: EducationalPlanResponse, trace: ExecutionTrace
) -> LearningCompleteness:
    covered = {moment.concept for moment in plan.moments}
    required = required_concepts(trace)

    # A moment nobody can see or hear teaches nothing, whatever its label.
    unexplained = [
        moment.id
        for moment in plan.moments
        if not moment.narration.strip() and not moment.explanation.strip()
    ]

    return LearningCompleteness(
        shapes=detect_algorithm_shapes(trace),
        required=required,
        covered=sorted(covered, key=lambda c: c.value),
        missing=[concept for concept in required if concept not in covered],
        unexplained_moments=unexplained,
        estimated_narration_words=sum(
            len(moment.narration.split()) for moment in plan.moments
        ),
    )


def validate_learning_completeness(
    plan: EducationalPlanResponse, trace: ExecutionTrace
) -> ValidationResult:
    """Fails on missing understanding — never on length."""
    completeness = evaluate_learning_completeness(plan, trace)
    errors: list[str] = []

    if completeness.missing:
        errors.append(
            "plan never teaches: "
            + ", ".join(concept.value for concept in completeness.missing)
        )
    for moment_id in completeness.unexplained_moments:
        errors.append(
            f"moment {moment_id!r} has neither narration nor explanation, so "
            "the viewer is shown a transition nobody accounts for"
        )

    return ValidationResult(
        stage="semantic", passed=not errors, errors=errors
    )

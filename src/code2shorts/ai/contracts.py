"""Structured AI contracts. An AI call in Code2Shorts never returns
arbitrary text where the application needs structured data — every AI
operation has a typed Request/Response pair.

TraceEvents are referenced by `step_index` (the same integer
`ExecutionTrace.events[i].step_index` / `AnimationStep.trace_event_index`
already use elsewhere in this codebase — reusing that existing identifier
rather than inventing a parallel UUID scheme for the same events).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AIRequestMetadata(BaseModel):
    """Threaded through every AI call so it stays traceable to exactly
    what produced it — required for reproducibility once real providers
    are wired up (see ARCHITECTURE_DECISIONS.md "prompt versioning").
    """

    provider: str
    model: str
    model_version: str | None = None
    prompt_version: str
    schema_version: int = 1
    temperature: float | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)


# ---- Explanation --------------------------------------------------------


class ExplanationStep(BaseModel):
    order: int
    description: str
    referenced_trace_event_indices: list[int] = Field(default_factory=list)


class ExplanationRequest(BaseModel):
    trace_artifact_id: str
    metadata: AIRequestMetadata


class ExplanationResponse(BaseModel):
    summary: str
    learning_objectives: list[str] = Field(default_factory=list)
    steps: list[ExplanationStep]
    referenced_trace_event_indices: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---- Visualization plan --------------------------------------------------


class VisualAction(StrEnum):
    """The ENTIRE vocabulary of instructions a visualization step may
    contain. Deliberately closed — there is no field anywhere in
    VisualizationStepPlan shaped like "code" or "command", and
    `visual_action` itself can only ever be one of these values because
    Pydantic rejects anything else at schema-validation time, before any
    custom validation code runs. An AI response that tries to smuggle in
    `"visual_action": "rm -rf /"` fails to parse — it never reaches
    business logic, let alone a renderer.
    """

    INTRO = "intro"
    VARIABLE_INIT = "variable_init"
    VARIABLE_UPDATE = "variable_update"
    CONDITION_EVAL = "condition_eval"
    BRANCH_SELECT = "branch_select"
    LOOP_ITERATION = "loop_iteration"
    METHOD_CALL = "method_call"
    RETURN_VALUE = "return_value"
    ARRAY_ACCESS = "array_access"
    DATA_STRUCTURE_UPDATE = "data_structure_update"
    """Phase 6: an observed Map/Deque/List mutation. A vocabulary word
    for a KIND OF STATE, not for an algorithm — the same action covers
    a map put, a stack push and a queue poll."""
    HIGHLIGHT = "highlight"
    COMPARE = "compare"
    SWAP = "swap"
    MOVE_POINTER = "move_pointer"
    EXCEPTION = "exception"
    COMPLETION = "completion"


class VisualizationStepPlan(BaseModel):
    order: int
    visual_action: VisualAction
    trace_event_index: int
    narration_text: str
    duration_seconds: float = Field(gt=0)
    variable_name: str | None = Field(
        default=None,
        description="For variable/array-related actions, which variable this "
        "step highlights — must match the referenced trace event's own "
        "variable_name (validated, never trusted blindly).",
    )


class VisualizationPlanRequest(BaseModel):
    explanation_artifact_id: str
    trace_artifact_id: str
    metadata: AIRequestMetadata


class VisualizationPlanResponse(BaseModel):
    lesson_title: str
    steps: list[VisualizationStepPlan]


# ---- Narration ------------------------------------------------------------


class NarrationRequest(BaseModel):
    visualization_plan_artifact_id: str
    metadata: AIRequestMetadata


class NarrationSegment(BaseModel):
    """One spoken line, synchronized to a specific visualization step (and,
    transitively through it, to a specific trace event) — never a bare
    string with no grounding.
    """

    order: int
    text: str
    visualization_step_order: int = Field(
        description="Which VisualizationStepPlan.order this segment narrates"
    )


class NarrationResponse(BaseModel):
    segments: list[NarrationSegment]


# ---- Educational planning (Phase 5.3) -------------------------------------


class LearningConcept(StrEnum):
    """The CLOSED vocabulary of educational moments.

    Closed for the same reason `VisualAction` is: an out-of-vocabulary
    category fails Pydantic validation before any project code inspects
    it, so the LLM cannot invent a concept type that later branches into
    unexpected behaviour.

    These are *conceptual* moments, not animation frames. One moment may
    cite many execution events; many repetitive events may collapse into
    one moment. Neither is a licence to drop a conceptual transition.
    """

    INTRODUCTION = "introduction"
    INITIALIZATION = "initialization"
    DATA_STRUCTURE = "data_structure"
    CORE_CONCEPT = "core_concept"
    STATE_TRANSITION = "state_transition"
    DECISION = "decision"
    POINTER_MOVEMENT = "pointer_movement"
    DATA_MOVEMENT = "data_movement"
    LOOP_BEHAVIOUR = "loop_behaviour"
    INVARIANT = "invariant"
    TERMINATION = "termination"
    RESULT = "result"
    COMPLEXITY = "complexity"


class ClaimKind(StrEnum):
    """What KIND of statement a moment is making.

    This distinction is the point of Phase 5.3. `OBSERVED` claims assert
    something that happened at runtime ("fast = 3") and are only
    believable if a real trace event says so. `EXPLANATION` and
    `COMMENTARY` reason about the algorithm and are not checked against
    the trace, because there is no trace event for "why".

    Conflating them is how a plausible-sounding hallucinated value gets
    presented to a learner as fact.
    """

    OBSERVED = "observed"
    """Asserts runtime state. REQUIRES trace evidence."""

    EXPLANATION = "explanation"
    """Explains why the algorithm does something. Grounded in the source
    and algorithm semantics, not in a single event."""

    COMMENTARY = "commentary"
    """Pedagogical framing — motivation, complexity, analogy."""


class EducationalMoment(BaseModel):
    """One thing the learner needs to understand."""

    id: str = Field(description="stable identifier, e.g. 'm3'")
    concept: LearningConcept
    claim_kind: ClaimKind
    explanation: str = Field(description="what the learner should understand")
    evidence_event_indices: list[int] = Field(
        default_factory=list,
        description=(
            "TraceEvent.step_index values supporting this moment. MUST be "
            "non-empty when claim_kind is 'observed' — validated against "
            "the real ExecutionTrace, never trusted."
        ),
    )
    source_lines: list[int] = Field(
        default_factory=list, description="1-based Java source lines this moment concerns"
    )
    narration: str = Field(description="what is spoken for this moment")
    importance: int = Field(
        default=3, ge=1, le=5, description="5 = essential; 1 = optional colour"
    )
    prerequisite_ids: list[str] = Field(
        default_factory=list, description="moment ids that must come earlier"
    )


class EducationalPlanRequest(BaseModel):
    trace_artifact_id: str
    explanation_artifact_id: str
    metadata: AIRequestMetadata


class EducationalPlanResponse(BaseModel):
    """A pedagogically ordered account of one real execution.

    Deliberately has NO duration field. Duration is a presentation
    concern, computed downstream from real measured speech — see
    ADR-5.11 "Educational Integrity Over Duration". Nothing here may be
    dropped to hit a runtime target.
    """

    lesson_title: str
    problem_statement: str = Field(description="what problem the algorithm solves")
    moments: list[EducationalMoment]
    time_complexity: str | None = None
    space_complexity: str | None = None

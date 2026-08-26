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

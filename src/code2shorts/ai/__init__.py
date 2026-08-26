from code2shorts.ai.contracts import (
    AIRequestMetadata,
    ExplanationRequest,
    ExplanationResponse,
    ExplanationStep,
    NarrationRequest,
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanRequest,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.ai.repair import SemanticValidationError, generate_with_repair
from code2shorts.ai.structured import SchemaValidationError, generate_structured
from code2shorts.ai.validation import validate_explanation_against_trace

__all__ = [
    "AIRequestMetadata",
    "ExplanationRequest",
    "ExplanationResponse",
    "ExplanationStep",
    "NarrationRequest",
    "NarrationResponse",
    "NarrationSegment",
    "SchemaValidationError",
    "SemanticValidationError",
    "VisualAction",
    "VisualizationPlanRequest",
    "VisualizationPlanResponse",
    "VisualizationStepPlan",
    "generate_structured",
    "generate_with_repair",
    "validate_explanation_against_trace",
]

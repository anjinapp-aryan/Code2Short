"""Phase 7.0: generation identity, versioning and job tracking.

Sits BETWEEN the UI and the existing pipeline and contains none of the
pipeline itself. `GenerationManager` composes the `workflow/nodes.py`
nodes that already exist and runs them on the `WorkflowRunner` that
already exists; Java execution, tracing, the LLM stages, TTS, Manim and
FFmpeg are untouched by this package.
"""

from code2shorts.generation.fingerprint import (
    PIPELINE_VERSION,
    RENDERER_VERSION,
    ContentFingerprint,
    RequestFingerprint,
    TeachingConfig,
    fingerprint_request,
    hash_source_files,
)
from code2shorts.generation.jobs import Job, JobManager, Stage, StageState
from code2shorts.generation.manager import (
    STAGE_LABELS,
    GenerationDecision,
    GenerationManager,
    GenerationRequest,
    PipelineFactory,
)
from code2shorts.generation.registry import (
    GenerationRegistry,
    GenerationStatus,
    GenerationVersion,
)

__all__ = [
    "PIPELINE_VERSION",
    "RENDERER_VERSION",
    "STAGE_LABELS",
    "ContentFingerprint",
    "GenerationDecision",
    "GenerationManager",
    "GenerationRegistry",
    "GenerationRequest",
    "GenerationStatus",
    "GenerationVersion",
    "Job",
    "JobManager",
    "PipelineFactory",
    "RequestFingerprint",
    "Stage",
    "StageState",
    "TeachingConfig",
    "fingerprint_request",
    "hash_source_files",
]

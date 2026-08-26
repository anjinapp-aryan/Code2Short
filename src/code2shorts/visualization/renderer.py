"""VideoRenderer: the trusted execution boundary between a validated
VisualizationPlan and an actual video file. AI never generates
Manim/Python source that gets executed — it only ever produces the
VisualizationPlanResponse DATA (already schema- and semantically-
validated by the time it reaches here); this module's job is to
interpret that data using code WE wrote, never code the AI wrote.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from code2shorts.ai.contracts import VisualizationPlanResponse


class RenderResult(BaseModel):
    output_path: str
    checksum: str
    duration_seconds: float
    resolution: str = Field(description="e.g. '1080x1920'")
    fps: int
    renderer_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RenderingFailure(Exception):
    """The renderer could not produce a video — a rendering-specific
    failure kind, never confused with an AI/validation failure."""


class RenderContext(BaseModel):
    """Everything beyond the plan that a rich renderer needs, all of it
    already-validated fact rather than AI output.

    Optional so Phase 4's callers keep working unchanged: without it a
    renderer falls back to caption-only output. `trace` is the canonical
    ExecutionTrace — passing it here does not make it renderer-owned; the
    renderer only reads it.
    """

    model_config = {"arbitrary_types_allowed": True}

    trace: Any | None = None
    source_files: dict[str, str] | None = None
    """ALL source files, keyed by path. Phase 4.3 needs the full map, not a
    single blob: a trace mixes line numbers from several files, so
    resolving "line 12" to real source requires knowing which file the
    executing method lives in."""
    entry_point: str | None = None
    source_code: str | None = None
    """Deprecated single-file form, retained so Phase 4.2 callers keep
    working. Prefer `source_files` + `entry_point`."""
    lesson_title: str | None = None


class VideoRenderer(ABC):
    @abstractmethod
    def render(
        self,
        plan: VisualizationPlanResponse,
        output_dir: Path,
        context: RenderContext | None = None,
    ) -> RenderResult:
        """Render `plan` (already validated) into a video under
        `output_dir`. Raises RenderingFailure on error — never returns a
        partially-written/corrupt result."""

"""Artifact-first architecture: every pipeline stage's output is an
Artifact, stored once in an ArtifactStore and referenced by id everywhere
else (WorkflowState included) — never duplicated inline. See
ARCHITECTURE_DECISIONS.md, "Phase 3: artifact-first architecture".
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ArtifactType(StrEnum):
    SOURCE = "source"
    COMPILATION = "compilation"
    TRACE = "trace"
    EXPLANATION = "explanation"
    EDUCATIONAL_PLAN = "educational_plan"
    """Phase 5.3: the pedagogical plan — what the learner must
    understand — between explanation and visualization."""
    VISUALIZATION_PLAN = "visualization_plan"
    ANIMATION = "animation"
    NARRATION = "narration"
    AUDIO = "audio"
    VIDEO = "video"
    RENDERED_VIDEO = "rendered_video"
    """Visual-only render output (ManimVideoRenderer/FakeVideoRenderer) —
    before narration audio is composed in. Distinct from FINAL_VIDEO."""
    FINAL_VIDEO = "final_video"
    """rendered_video + audio, composed by MediaComposer. The end of the
    lineage chain — see ARCHITECTURE_DECISIONS.md Phase 4."""
    SUBTITLES = "subtitles"
    """Phase 8.1: the SRT written from the narration alignment, so the web
    path carries the same subtitle file the golden path always wrote."""


def _checksum(content: dict[str, Any]) -> str:
    """sha256 of the content's canonical JSON form. Two artifacts with
    identical content always get identical checksums regardless of key
    order — this is what find_by_checksum/dedup/caching key off of.
    """
    canonical = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Artifact(BaseModel):
    """Generic envelope around one pipeline stage's output.

    Three DIFFERENT version concepts, kept deliberately separate (see
    ARCHITECTURE_DECISIONS.md ADR "Artifact lineage/versioning" — do not
    conflate these):

    - `id`: identity of THIS artifact instance (a fresh uuid4 every time
      one is created, even if the content is identical to a prior one).
    - `schema_version`: the shape of `content` for this ArtifactType — bump
      when the fields inside `content` change (e.g. TraceEvent gains a
      field). Independent of any single artifact instance.
    - `artifact_version`: which attempt/regeneration this is within the
      SAME lineage (e.g. a retried node produces artifact_version=2 from
      the same inputs). Independent of schema.
    - `checksum`: content-addressed — detects whether the CONTENT actually
      changed (same source code -> same checksum -> safe to reuse/cache),
      independent of both version numbers above.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: ArtifactType
    schema_version: int = 1
    artifact_version: int = 1
    producer: str = Field(description="e.g. 'JavaAdapter.compile', node name, or provider name")
    input_artifact_ids: list[str] = Field(default_factory=list, description="lineage parents")
    content: dict[str, Any]
    checksum: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        type: ArtifactType,
        producer: str,
        content: dict[str, Any],
        input_artifact_ids: list[str] | None = None,
        schema_version: int = 1,
        artifact_version: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        """The standard construction path — computes the checksum for you
        so callers can never construct an Artifact with a checksum that
        doesn't match its own content.
        """
        return cls(
            type=type,
            producer=producer,
            input_artifact_ids=input_artifact_ids or [],
            content=content,
            checksum=_checksum(content),
            schema_version=schema_version,
            artifact_version=artifact_version,
            metadata=metadata or {},
        )

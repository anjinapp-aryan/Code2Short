"""What identifies one generated video.

The naive check - "does final.mp4 exist?" - is wrong in both directions.
It says a video is current when the voice, the model, the renderer or the
Java source have all changed underneath it, and it says nothing at all
about a run that produced a file which then failed validation. So a
generation is identified by its INPUTS instead.

Two fingerprints, because they are known at different times:

    RequestFingerprint   everything knowable BEFORE the pipeline runs -
                         source, teaching configuration, voice, provider,
                         renderer version. This is the one that decides
                         "you already have this video".

    ContentFingerprint   the request fingerprint plus hashes of what the
                         run actually produced - the execution trace and
                         the plans derived from it. Recorded afterwards.

The split is not a nicety. A decision taken before tracing cannot depend
on the trace's hash, and pretending otherwise would mean running the
expensive half of the pipeline in order to discover it need not have run.
The content fingerprint exists for auditing and for future partial
regeneration: it is what tells you the plan changed while the trace did
not, which is precisely when only the visual layers need rebuilding.

Hashing reuses `hashlib.sha256` over canonical JSON - the same technique
`artifacts/models.py::_checksum` already uses for artifact content, so two
identical inputs hash identically regardless of key order.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from code2shorts.core.video_profile import (
    DEFAULT_VIDEO_PROFILE,
    VideoProfile,
    resolve_video_profile,
)

RENDERER_VERSION = "6.5.3"
"""Bumped when a change alters what the renderer PRODUCES.

Phase 6.5.3 made the code panel a fixed viewport, so every frame from it
differs from a 6.5.1 frame. A video generated before that change is not
the video the current code would produce, and the fingerprint has to say
so - otherwise the library silently serves stale renders after every
visual phase.

This is a deliberate manual constant. Deriving it from a file hash of the
visualization package would invalidate every video on a comment change,
which trains people to ignore it."""

PIPELINE_VERSION = "8.1"
"""Bumped when the STAGES change - a node added, removed or reordered.

8.1 added `narration_timing` and made composition use the aligned track.
Every 7.0 video was muxed from one unplaced narration track that `-t` could
cut, so none of them is the video the current pipeline would make."""


def _digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_source_files(source_files: dict[str, str]) -> str:
    """One hash over every source file, path included.

    Paths are part of the hash because moving a class between files
    changes what the trace can resolve, even when the bytes are the same.
    """
    return _digest({path: text for path, text in sorted(source_files.items())})


class TeachingConfig(BaseModel):
    """The choices a user actually makes, and nothing else.

    Deliberately small. Every field here widens the space of videos that
    count as different, so an engineering knob that does not change what
    the learner sees does not belong in it.
    """

    audience: str = "beginner"
    teaching_style: str = "step_by_step"
    video_format: str = DEFAULT_VIDEO_PROFILE.identity_key
    voice: str = "af_heart"
    tts_provider: str = "kokoro"

    @field_validator("video_format")
    @classmethod
    def _known_video_format(cls, value: str) -> str:
        """Any profile id or identity key in, the identity key out.

        So "vertical_hd" and "youtube_short" are the same request and hash
        the same, an unknown format fails here rather than at render time,
        and the default keeps the digest every Phase 7 version recorded.
        """
        return resolve_video_profile(value).identity_key

    @property
    def video_profile(self) -> VideoProfile:
        return resolve_video_profile(self.video_format)

    def normalised(self) -> dict[str, str]:
        return {
            field: str(getattr(self, field)).strip().lower()
            for field in sorted(type(self).model_fields)
        }


class RequestFingerprint(BaseModel):
    """Identity of a generation REQUEST. Decides reuse."""

    algorithm: str
    source_hash: str
    config: dict[str, str]
    llm_provider: str
    renderer_version: str = RENDERER_VERSION
    pipeline_version: str = PIPELINE_VERSION

    @property
    def digest(self) -> str:
        return _digest(self.model_dump())

    def matches(self, other: RequestFingerprint) -> bool:
        return self.digest == other.digest

    def differences(self, other: RequestFingerprint) -> list[str]:
        """Which fields differ - so the UI can say WHY a rebuild is
        needed rather than only that one is."""
        changed = []
        mine, theirs = self.model_dump(), other.model_dump()
        for field in sorted(mine):
            if mine[field] != theirs.get(field):
                changed.append(field)
        return changed


class ContentFingerprint(BaseModel):
    """Identity of what a run PRODUCED. Recorded after the fact."""

    request: RequestFingerprint
    trace_hash: str | None = None
    explanation_hash: str | None = None
    educational_plan_hash: str | None = None
    visualization_plan_hash: str | None = None
    narration_hash: str | None = None

    @property
    def digest(self) -> str:
        return _digest(self.model_dump())

    def stages_shared_with(self, other: ContentFingerprint) -> list[str]:
        """Which produced stages are identical to another run's.

        The basis for partial regeneration later: a run whose trace and
        educational plan match an existing one need not re-run the JVM or
        the LLM for those stages. Phase 7.0 only REPORTS this; it does not
        act on it.
        """
        shared = []
        for stage in (
            "trace_hash",
            "explanation_hash",
            "educational_plan_hash",
            "visualization_plan_hash",
            "narration_hash",
        ):
            mine, theirs = getattr(self, stage), getattr(other, stage)
            if mine is not None and mine == theirs:
                shared.append(stage.removesuffix("_hash"))
        return shared


def fingerprint_request(
    algorithm: str,
    source_files: dict[str, str],
    config: TeachingConfig,
    llm_provider: str,
) -> RequestFingerprint:
    return RequestFingerprint(
        algorithm=algorithm,
        source_hash=hash_source_files(source_files),
        config=config.normalised(),
        llm_provider=llm_provider.strip().lower(),
    )


def hash_content(content: Any) -> str:
    """Hash one stage's output, whatever shape it is."""
    if isinstance(content, BaseModel):
        content = content.model_dump()
    if not isinstance(content, dict):
        content = {"value": content}
    return _digest(content)

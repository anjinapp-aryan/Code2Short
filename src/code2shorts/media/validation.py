"""Formal media quality validation.

`media/probe.py` reports what a file *is*. This module asserts what it
*must be* — the policy layer no third-party library encodes, because the
policy is Code2Shorts-specific (9:16 educational shorts whose audio must
never dictate the visual timeline).

The governing rule:

    A media mismatch is a VALIDATION FAILURE with an actionable diagnostic.
    It is never resolved by truncating video, stretching video, or dropping
    audio. Silently producing incorrect educational content is worse than
    failing.

Every check returns a `ValidationResult` (the same type the workflow
already accumulates), so failures flow into `Code2ShortsState.validation`
alongside every other validation in the system.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from code2shorts.core.models import ValidationResult
from code2shorts.media.probe import VideoProbeError, probe_audio, probe_video
from code2shorts.narration.alignment import AlignmentResult

EXPECTED_WIDTH = 1080
EXPECTED_HEIGHT = 1920
EXPECTED_VIDEO_CODEC = "h264"
EXPECTED_AUDIO_CODEC = "aac"

# Container/encoder timestamps make exact equality unrealistic; a fifth of
# a second is far tighter than any real truncation or stretch would be.
DURATION_TOLERANCE_SECONDS = 0.2


class MediaPolicy(BaseModel):
    """What a finished Code2Shorts video must satisfy."""

    width: int = EXPECTED_WIDTH
    height: int = EXPECTED_HEIGHT
    video_codec: str = EXPECTED_VIDEO_CODEC
    audio_codec: str | None = EXPECTED_AUDIO_CODEC
    fps: int | None = 30
    require_audio: bool = True
    min_duration_seconds: float = 0.5
    duration_tolerance_seconds: float = DURATION_TOLERANCE_SECONDS
    allowed_channel_counts: tuple[int, ...] = (1, 2)
    min_sample_rate: int = 8000


def validate_video_file(path: Path, policy: MediaPolicy | None = None) -> ValidationResult:
    """Probe the real file and assert the video policy."""
    policy = policy or MediaPolicy()
    errors: list[str] = []

    try:
        video = probe_video(path)
    except VideoProbeError as error:
        return ValidationResult(
            stage="domain", passed=False, errors=[f"video unreadable ({path.name}): {error}"]
        )

    if (video.width, video.height) != (policy.width, policy.height):
        errors.append(
            f"resolution is {video.width}x{video.height}, expected "
            f"{policy.width}x{policy.height} — fix the renderer's --resolution, "
            "do not rescale the finished file"
        )
    if video.codec != policy.video_codec:
        errors.append(f"video codec is {video.codec!r}, expected {policy.video_codec!r}")
    if video.duration_seconds < policy.min_duration_seconds:
        errors.append(
            f"video duration {video.duration_seconds:.3f}s is below the "
            f"{policy.min_duration_seconds}s minimum — the render likely produced "
            "a fragment rather than the assembled scene"
        )
    if video.frame_count <= 0:
        errors.append("video contains no decodable frames")
    if policy.fps is not None and abs(video.fps - policy.fps) > 0.5:
        errors.append(f"frame rate is {video.fps}, expected {policy.fps}")

    return ValidationResult(stage="domain", passed=not errors, errors=errors)


def validate_audio_file(path: Path, policy: MediaPolicy | None = None) -> ValidationResult:
    policy = policy or MediaPolicy()
    errors: list[str] = []

    try:
        audio = probe_audio(path)
    except VideoProbeError as error:
        return ValidationResult(
            stage="domain", passed=False, errors=[f"audio unreadable ({path.name}): {error}"]
        )

    if policy.audio_codec is not None and audio.codec != policy.audio_codec:
        errors.append(f"audio codec is {audio.codec!r}, expected {policy.audio_codec!r}")
    if audio.channels not in policy.allowed_channel_counts:
        errors.append(
            f"audio has {audio.channels} channel(s), expected one of "
            f"{list(policy.allowed_channel_counts)}"
        )
    if audio.sample_rate < policy.min_sample_rate:
        errors.append(
            f"sample rate {audio.sample_rate}Hz is below the "
            f"{policy.min_sample_rate}Hz minimum"
        )
    if audio.duration_seconds <= 0:
        errors.append("audio duration is zero")

    return ValidationResult(stage="domain", passed=not errors, errors=errors)


def validate_final_video(
    path: Path,
    policy: MediaPolicy | None = None,
    expected_duration_seconds: float | None = None,
) -> ValidationResult:
    """Full policy check on a finished, composed MP4.

    `expected_duration_seconds` should be the RENDERED video's duration.
    A mismatch means the composition step truncated or stretched the
    picture — the exact failure Phase 4.1 caught in production (a bare
    `-shortest` silently cut an 18.0s render to 5.2s). It is reported, not
    absorbed.
    """
    policy = policy or MediaPolicy()
    errors: list[str] = []

    video_result = validate_video_file(path, policy)
    errors += video_result.errors

    if policy.require_audio:
        audio_result = validate_audio_file(path, policy)
        errors += audio_result.errors

    if not video_result.errors:
        video = probe_video(path)

        if expected_duration_seconds is not None:
            drift = video.duration_seconds - expected_duration_seconds
            if abs(drift) > policy.duration_tolerance_seconds:
                verb = "truncated" if drift < 0 else "stretched"
                errors.append(
                    f"final video is {video.duration_seconds:.3f}s but the render was "
                    f"{expected_duration_seconds:.3f}s — the picture was {verb} by "
                    f"{abs(drift):.3f}s during composition. Fix the composition "
                    f"arguments; never accept a {verb} picture."
                )

        if policy.require_audio:
            try:
                audio = probe_audio(path)
            except VideoProbeError:
                audio = None
            if audio is not None:
                overrun = audio.duration_seconds - video.duration_seconds
                if overrun > policy.duration_tolerance_seconds:
                    errors.append(
                        f"audio ({audio.duration_seconds:.3f}s) outlasts video "
                        f"({video.duration_seconds:.3f}s) by {overrun:.3f}s — narration "
                        "would be cut off. Shorten the narration or lengthen the "
                        "visual plan; do not extend the video to fit audio."
                    )

    return ValidationResult(stage="domain", passed=not errors, errors=errors)


def validate_timeline(
    alignment: AlignmentResult, video_duration_seconds: float, tolerance: float | None = None
) -> ValidationResult:
    """Assert subtitle/narration timing against the authoritative video.

    The video's duration is authoritative: every cue must fall inside it,
    timestamps must be non-negative and monotonic, and no cue may overlap
    the next.
    """
    tolerance = DURATION_TOLERANCE_SECONDS if tolerance is None else tolerance
    errors: list[str] = []

    if not alignment.segments:
        errors.append("timeline has no narration segments")

    previous_end = 0.0
    for segment in alignment.segments:
        if segment.start_seconds < 0 or segment.end_seconds < 0:
            errors.append(f"segment {segment.segment_id} has a negative timestamp")
        if segment.end_seconds <= segment.start_seconds:
            errors.append(
                f"segment {segment.segment_id} ends at or before it starts "
                f"({segment.end_seconds:.3f} <= {segment.start_seconds:.3f})"
            )
        if segment.start_seconds + 1e-9 < previous_end:
            errors.append(
                f"segment {segment.segment_id} overlaps the previous segment"
            )
        if segment.end_seconds > video_duration_seconds + tolerance:
            errors.append(
                f"segment {segment.segment_id} ends at {segment.end_seconds:.3f}s, "
                f"beyond the {video_duration_seconds:.3f}s video — the cue would be "
                "invisible. Extend the visual plan or shorten narration."
            )
        previous_end = max(previous_end, segment.end_seconds)

    if alignment.overflow_count:
        errors.append(
            f"{alignment.overflow_count} narration segment(s) have audio longer than "
            "their visual step; the timeline was NOT altered to accommodate them"
        )

    return ValidationResult(stage="domain", passed=not errors, errors=errors)


class MediaValidationReport(BaseModel):
    """Everything checked about one finished job, for logging/reporting."""

    final_video: ValidationResult
    timeline: ValidationResult | None = None
    checks: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.final_video.passed and (self.timeline is None or self.timeline.passed)

    @property
    def errors(self) -> list[str]:
        errors = list(self.final_video.errors)
        if self.timeline is not None:
            errors += self.timeline.errors
        return errors

"""VideoProfile: the output format a video is rendered in.

One small, immutable description of an output format - aspect ratio,
orientation and NATIVE pixel size - and nothing about teaching. The
execution trace, the educational plan and the narration never see it:

    Java -> ExecutionTrace -> EducationalPlan -> narration      (format-free)
                                                    |
                                              VideoProfile
                                                    |
                                        renderer -> MP4          (format-aware)

Four product profiles exist, and all four render. VERTICAL_HD is the
accepted Phase 6.5.1/6.5.3 portrait composition and LANDSCAPE_HD the Phase
8.2C column composition. The 4K profiles (Phase 8.3) use the SAME
compositions at twice the pixel density: the layout is chosen by
orientation and stated in scene units, so 4K changes only how many pixels
Manim rasterises. A profile can still be declared unrenderable with an
`unsupported_reason`, and is then refused before any work starts. See
docs/VIDEO-FORMATS.md and docs/PHASE_8_3_4K_IMPLEMENTATION.md.

`pixel_width` x `pixel_height` is always the NATIVE render size. There is
no upscaling here: a 4K profile means Manim renders 4K, never "render
1080p, scale it up". If output scaling is ever wanted it must be a
separate, explicit field, not a reinterpretation of these two.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import gcd


class VideoProfileId(StrEnum):
    VERTICAL_HD = "vertical_hd"
    LANDSCAPE_HD = "landscape_hd"
    VERTICAL_4K = "vertical_4k"
    LANDSCAPE_4K = "landscape_4k"


class Orientation(StrEnum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


class InvalidVideoProfileError(ValueError):
    """A profile whose numbers contradict each other."""


class UnknownVideoProfileError(ValueError):
    """A format name that no profile answers to."""


class UnsupportedVideoProfileError(Exception):
    """A real profile that cannot be rendered yet. Raised BEFORE any work
    starts, so a refused format never leaves a half-made version behind."""


@dataclass(frozen=True)
class VideoProfile:
    id: VideoProfileId
    label: str
    aspect_width: int
    aspect_height: int
    pixel_width: int
    pixel_height: int
    identity_key: str
    """What generation identity records for this profile.

    Separate from `id` for one reason: every Phase 7 version was
    fingerprinted with `video_format="youtube_short"`, which is exactly
    VERTICAL_HD. Recording "vertical_hd" instead would change the digest of
    every existing video and silently turn the whole library into
    "needs regenerating". A profile's identity_key must never be reused
    for different pixels - a new size is a new key."""
    unsupported_reason: str | None = None
    """None when renderable. Otherwise the phase that will make it so."""

    def __post_init__(self) -> None:
        if min(self.aspect_width, self.aspect_height, self.pixel_width, self.pixel_height) <= 0:
            raise InvalidVideoProfileError(
                f"{self.id}: aspect and pixel dimensions must be positive"
            )
        # H.264 with 4:2:0 chroma - what Manim and the composer produce -
        # cannot encode an odd dimension.
        if self.pixel_width % 2 or self.pixel_height % 2:
            raise InvalidVideoProfileError(
                f"{self.id}: {self.pixel_width}x{self.pixel_height} has an odd "
                "dimension, which H.264 4:2:0 cannot encode"
            )
        if gcd(self.aspect_width, self.aspect_height) != 1:
            raise InvalidVideoProfileError(
                f"{self.id}: aspect ratio {self.aspect_ratio} is not in lowest terms"
            )
        if self.pixel_width * self.aspect_height != self.pixel_height * self.aspect_width:
            raise InvalidVideoProfileError(
                f"{self.id}: {self.pixel_width}x{self.pixel_height} is not "
                f"{self.aspect_ratio}"
            )
        if self.pixel_width == self.pixel_height:
            raise InvalidVideoProfileError(
                f"{self.id}: square output has no orientation and no layout"
            )

    @property
    def aspect_ratio(self) -> str:
        return f"{self.aspect_width}:{self.aspect_height}"

    @property
    def orientation(self) -> Orientation:
        if self.pixel_width < self.pixel_height:
            return Orientation.PORTRAIT
        return Orientation.LANDSCAPE

    @property
    def resolution(self) -> str:
        """`WIDTHxHEIGHT`, the form `ManimVideoRenderer` and `RenderResult`
        already use."""
        return f"{self.pixel_width}x{self.pixel_height}"

    @property
    def is_renderable(self) -> bool:
        return self.unsupported_reason is None

    def require_renderable(self) -> None:
        if self.unsupported_reason is not None:
            raise UnsupportedVideoProfileError(
                f"{self.label} ({self.aspect_ratio}, {self.resolution}) cannot be "
                f"rendered yet: {self.unsupported_reason}"
            )


VERTICAL_HD = VideoProfile(
    id=VideoProfileId.VERTICAL_HD,
    label="Vertical HD",
    aspect_width=9,
    aspect_height=16,
    pixel_width=1080,
    pixel_height=1920,
    identity_key="youtube_short",
)
LANDSCAPE_HD = VideoProfile(
    id=VideoProfileId.LANDSCAPE_HD,
    label="Landscape HD",
    aspect_width=16,
    aspect_height=9,
    pixel_width=1920,
    pixel_height=1080,
    identity_key="landscape_hd",
)
VERTICAL_4K = VideoProfile(
    id=VideoProfileId.VERTICAL_4K,
    label="Vertical 4K",
    aspect_width=9,
    aspect_height=16,
    pixel_width=2160,
    pixel_height=3840,
    identity_key="vertical_4k",
)
LANDSCAPE_4K = VideoProfile(
    id=VideoProfileId.LANDSCAPE_4K,
    label="Landscape 4K",
    aspect_width=16,
    aspect_height=9,
    pixel_width=3840,
    pixel_height=2160,
    identity_key="landscape_4k",
)

PROFILES: dict[VideoProfileId, VideoProfile] = {
    profile.id: profile
    for profile in (VERTICAL_HD, LANDSCAPE_HD, VERTICAL_4K, LANDSCAPE_4K)
}

DEFAULT_VIDEO_PROFILE = VERTICAL_HD
"""The accepted Phase 6.5.3 baseline. Used whenever no format is given."""


def resolve_video_profile(name: str | None) -> VideoProfile:
    """A profile from its id or its identity key; the default when blank.

    Accepting the identity key is what lets a stored fingerprint config
    (`video_format="youtube_short"`) be read back as a profile.
    """
    key = (name or "").strip().lower()
    if not key:
        return DEFAULT_VIDEO_PROFILE
    for profile in PROFILES.values():
        if key in (profile.id.value, profile.identity_key):
            return profile
    known = ", ".join(profile.id.value for profile in PROFILES.values())
    raise UnknownVideoProfileError(f"unknown video format {name!r}. Known: {known}")

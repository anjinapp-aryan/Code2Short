"""Real media inspection — reads what a video file ACTUALLY contains,
never what we predicted it would contain.

Phase 4 computed `RenderResult.duration_seconds` arithmetically
(`2.0 + sum(step.duration_seconds)`) and never checked it, because
`FakeVideoRenderer` was the only renderer any test exercised. Phase 4.1's
first real Manim render proved that number wrong (predicted 3.0s, actual
6.0s — Manim's own transitions/`Write` animations add real time the plan
does not model). Artifact metadata that disagrees with the artifact is
worse than no metadata, so the renderer now probes instead of guessing.

Uses PyAV rather than shelling out to `ffprobe`: PyAV is already present
wherever Manim is (it is Manim's own dependency), and `imageio-ffmpeg` —
the practical way to get FFmpeg on Windows — ships `ffmpeg` but NOT
`ffprobe`, so an ffprobe-based probe would add a tool requirement this
project cannot otherwise satisfy. A library call also avoids parsing
FFmpeg's human-readable stderr, which is not a stable machine interface.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class VideoProbeError(Exception):
    """The file could not be opened, or contains no video stream."""


class VideoMetadata(BaseModel):
    width: int
    height: int
    fps: float
    duration_seconds: float
    frame_count: int
    codec: str

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


def probe_video(path: Path) -> VideoMetadata:
    """Open `path` and report its real stream properties.

    `frame_count` is obtained by actually decoding the stream rather than
    trusting the container's header count, which is absent or wrong for
    some encoders.
    """
    try:
        import av
    except ImportError as error:  # pragma: no cover - environment-dependent
        raise VideoProbeError(
            "PyAV is required to probe video files; install the render extra "
            "(pip install 'code2shorts[render]') which pulls it in via Manim"
        ) from error

    if not path.is_file():
        raise VideoProbeError(f"no such file: {path}")

    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise VideoProbeError(f"file contains no video stream: {path}")
            stream = container.streams.video[0]
            codec_context = stream.codec_context
            width = codec_context.width
            height = codec_context.height
            codec = codec_context.name
            fps = float(stream.average_rate) if stream.average_rate else 0.0
            duration_seconds = (
                float(container.duration) / av.time_base if container.duration else 0.0
            )
            frame_count = sum(1 for _ in container.decode(video=0))
    except VideoProbeError:
        raise
    except Exception as error:  # noqa: BLE001 - PyAV raises assorted av.* errors
        raise VideoProbeError(f"could not probe {path}: {type(error).__name__}: {error}") from error

    return VideoMetadata(
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration_seconds,
        frame_count=frame_count,
        codec=codec,
    )

class AudioMetadata(BaseModel):
    codec: str
    sample_rate: int
    channels: int
    duration_seconds: float


def probe_audio(path: Path) -> AudioMetadata:
    """Read a file's real audio stream properties.

    Same reasoning as `probe_video`: narration timing must come from the
    audio that actually exists, never from an estimate such as
    characters x seconds-per-character. A synthesiser's real output length
    is the only number safe to align subtitles against.
    """
    try:
        import av
    except ImportError as error:  # pragma: no cover - environment-dependent
        raise VideoProbeError(
            "PyAV is required to probe audio files; install the render extra"
        ) from error

    if not path.is_file():
        raise VideoProbeError(f"no such file: {path}")

    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise VideoProbeError(f"file contains no audio stream: {path}")
            stream = container.streams.audio[0]
            codec_context = stream.codec_context
            duration_seconds = (
                float(container.duration) / av.time_base if container.duration else 0.0
            )
            return AudioMetadata(
                codec=codec_context.name,
                sample_rate=codec_context.sample_rate or 0,
                channels=codec_context.channels or 0,
                duration_seconds=duration_seconds,
            )
    except VideoProbeError:
        raise
    except Exception as error:  # noqa: BLE001 - PyAV raises assorted av.* errors
        raise VideoProbeError(
            f"could not probe audio {path}: {type(error).__name__}: {error}"
        ) from error

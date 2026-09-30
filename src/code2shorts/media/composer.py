"""MediaComposer: trusted rendered-video + narration-audio -> final.mp4,
via fixed-argument FFmpeg invocation only. Same trusted-subprocess pattern
as ManimVideoRenderer/JavaCompiler — the AI never supplies any part of the
command, only the (already-validated) inputs this composes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from code2shorts.execution.sandbox import ProcessResult, run_subprocess

COMPOSER_VERSION = "code2shorts-ffmpeg-composer-1.0"


class ComposeResult(BaseModel):
    output_path: str
    checksum: str
    duration_seconds: float
    composer_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MediaCompositionFailure(Exception):
    pass


AUDIO_SAMPLE_RATE = 44100
AUDIO_BITRATE = "128k"
"""Final AAC settings. Mono speech at 128 kbit/s is transparent; the point
is to stop the encoder adding its own artefacts on top of a source that
already measured hot."""


class MediaComposer:
    def __init__(
        self,
        timeout_seconds: float = 120.0,
        run_subprocess_fn: Callable[..., ProcessResult] | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._run_subprocess_fn = run_subprocess_fn or run_subprocess

    def compose(
        self,
        video_path: Path,
        audio_path: Path,
        output_path: Path,
        video_duration_seconds: float,
    ) -> ComposeResult:
        # Absolute paths only: the subprocess runs with cwd set to the
        # output directory, so relative inputs would be resolved a second
        # time against it. See the same fix in ManimVideoRenderer.render.
        video_path = video_path.resolve()
        audio_path = audio_path.resolve()
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            # Phase 6.1. The old default encoded a 22.05 kHz mono source at
            # ~58 kbit/s. The narration track is now assembled and
            # loudness-normalised at 44.1 kHz, so state the rate and give
            # the encoder enough bits that it is not the weakest link;
            # measured, the previous file's true peak sat at -0.0 dBFS with
            # no headroom for AAC's inter-sample overshoot.
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-b:a",
            AUDIO_BITRATE,
            # Pad the audio with silence, then cap the output at exactly the
            # video's duration.
            #
            # Two bugs were fixed here, both found by real FFmpeg runs:
            # (1) bare "-shortest" truncated the VIDEO to the audio length —
            #     an 18.0s render with 5.2s of narration silently produced a
            #     5.2s final video, discarding two thirds of the
            #     visualization. Losing rendered content to fit the narration
            #     is never the right trade; the video is the artifact.
            # (2) "-af apad -shortest" pads audio without bound, and the
            #     "-shortest" stop condition did not fire — composition hung
            #     until the timeout killed it. An explicit "-t" is
            #     deterministic and cannot hang, so the duration is stated
            #     outright rather than inferred from stream interactions.
            "-af",
            "apad",
            "-t",
            f"{video_duration_seconds:.3f}",
            str(output_path),
        ]
        result = self._run_subprocess_fn(
            command, cwd=output_path.parent, timeout_seconds=self._timeout_seconds
        )
        if result.timed_out or result.returncode != 0:
            raise MediaCompositionFailure(f"ffmpeg composition failed: {result.stderr[-2000:]}")
        if not output_path.exists():
            raise MediaCompositionFailure("ffmpeg reported success but produced no output file")

        checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return ComposeResult(
            output_path=str(output_path),
            checksum=checksum,
            duration_seconds=video_duration_seconds,
            composer_version=COMPOSER_VERSION,
        )

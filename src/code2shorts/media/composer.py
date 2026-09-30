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
from code2shorts.narration.alignment import AlignmentResult

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


def assemble_narration_track(
    alignment: AlignmentResult,
    output_path: Path,
    timeout_seconds: float = 300.0,
    run_subprocess_fn: Callable[..., ProcessResult] | None = None,
) -> Path:
    """One narration track with every segment at its aligned start.

    Moved unchanged from `scripts/run_phase5_golden_path.py`, the golden
    path that produced the accepted Phase 6.5.3 video, so the workflow and
    the script place speech with the same code rather than two copies of
    it. `adelay` puts each segment at `start_seconds`; `amix` without
    normalisation sums them (they never overlap - `align_narration`
    asserts that). The track runs to `total_duration_seconds`, which is
    the rendered scene's length, closing fade included.
    """
    run = run_subprocess_fn or run_subprocess
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paths = [Path(s.audio_path).resolve() for s in alignment.segments]
    command = ["ffmpeg", "-y"]
    for path in paths:
        command += ["-i", str(path)]
    filters = [
        f"[{i}:a]adelay={int(s.start_seconds*1000)}|{int(s.start_seconds*1000)}[a{i}]"
        for i, s in enumerate(alignment.segments)
    ]
    filters.append("".join(f"[a{i}]" for i in range(len(paths)))
                   + f"amix=inputs={len(paths)}:normalize=0[out]")
    command += ["-filter_complex", ";".join(filters), "-map", "[out]",
                "-t", f"{alignment.total_duration_seconds:.3f}", str(output_path)]
    result = run(command, cwd=output_path.parent, timeout_seconds=timeout_seconds)
    if result.timed_out or result.returncode != 0:
        raise MediaCompositionFailure(f"audio concat failed: {result.stderr[-1000:]}")
    return output_path


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

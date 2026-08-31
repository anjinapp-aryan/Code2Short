"""Independent forensic measurement of a finished MP4's audio.

Deliberately separate from `media/validation.py`: that module checks what
the pipeline INTENDED against what it produced. This one asks FFmpeg what
is actually in the file and does not consult the pipeline at all, because
the Phase 6.1 defect passed every internal check — the application's own
timeline agreed with itself, and the file still ended with 22.1 s of
silence.

Everything is measured, nothing is estimated: `silencedetect` for silence
runs, `ebur128` for loudness and true peak, `astats` for peak and DC
offset, and the container for rate and channels.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from code2shorts.core.models import ValidationResult
from code2shorts.execution.sandbox import run_subprocess

SILENCE_THRESHOLD_DB = -50.0
MIN_REPORTED_SILENCE_SECONDS = 0.35


class SilenceRun(BaseModel):
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class AudioForensics(BaseModel):
    """What FFmpeg says is in the file."""

    duration_seconds: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    integrated_lufs: float | None = None
    true_peak_dbtp: float | None = None
    peak_level_db: float | None = None
    dc_offset: float | None = None
    silences: list[SilenceRun] = Field(default_factory=list)

    @property
    def meaningful_audio_end_seconds(self) -> float:
        """When the last audible sound stops.

        A silence run that reaches the end of the file is the tail; the
        audio effectively ends where that run begins.
        """
        for run in self.silences:
            if run.end_seconds >= self.duration_seconds - 0.05:
                return run.start_seconds
        return self.duration_seconds

    @property
    def silent_tail_seconds(self) -> float:
        return max(0.0, self.duration_seconds - self.meaningful_audio_end_seconds)


class AudioForensicsError(RuntimeError):
    """The measurement itself failed.

    Raised rather than returning empty results. The first draft of this
    module returned "0 silence runs" for a file it had never opened - it
    passed a relative path with the parent as cwd, so ffmpeg could not
    find the input, and nothing checked the exit code. A forensic tool
    that reports "nothing wrong" when it measured nothing is worse than
    no tool at all.
    """


def _ffmpeg_stderr(path: Path, audio_filter: str) -> str:
    path = path.resolve()
    result = run_subprocess(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", audio_filter, "-f", "null", "-"],
        cwd=path.parent,
        timeout_seconds=600.0,
    )
    # FFmpeg writes its analysis to stderr and exits 0. A non-zero code
    # means the file could not be read, and must never look like a clean
    # measurement.
    if result.timed_out:
        raise AudioForensicsError(f"ffmpeg analysis timed out for {path.name}")
    if result.returncode != 0:
        raise AudioForensicsError(
            f"ffmpeg could not analyse {path.name}: {result.stderr[-500:]}"
        )
    return result.stderr


def _last_float(text: str, pattern: str) -> float | None:
    """The LAST match, which for ffmpeg's analysis filters is the summary.

    `ebur128` prints a running measurement for every block it processes
    and only then the final summary. Taking the first match reported the
    value before any audio had been read - a silent -70.0 LUFS for a
    track that actually measures -20.0.
    """
    matches = re.findall(pattern, text)
    return float(matches[-1]) if matches else None


def measure_audio(path: Path) -> AudioForensics:
    """Measure the audio stream of a real media file."""
    from code2shorts.media.probe import probe_audio

    path = path.resolve()

    probed = probe_audio(path)
    forensics = AudioForensics(
        duration_seconds=probed.duration_seconds,
        sample_rate=probed.sample_rate,
        channels=probed.channels,
    )

    silence_output = _ffmpeg_stderr(
        path,
        f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB"
        f":d={MIN_REPORTED_SILENCE_SECONDS}",
    )
    starts = [float(v) for v in re.findall(r"silence_start:\s*(-?[\d.]+)", silence_output)]
    ends = [float(v) for v in re.findall(r"silence_end:\s*(-?[\d.]+)", silence_output)]
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else forensics.duration_seconds
        forensics.silences.append(
            SilenceRun(start_seconds=max(0.0, start), end_seconds=end)
        )

    loudness_output = _ffmpeg_stderr(path, "ebur128=peak=true")
    forensics.integrated_lufs = _last_float(
        loudness_output, r"I:\s*(-?[\d.]+)\s*LUFS"
    )
    forensics.true_peak_dbtp = _last_float(
        loudness_output, r"Peak:\s*(-?[\d.]+)\s*dBFS"
    )

    stats_output = _ffmpeg_stderr(path, "astats=metadata=1:reset=0")
    forensics.peak_level_db = _last_float(
        stats_output, r"Peak level dB:\s*(-?[\d.]+)"
    )
    forensics.dc_offset = _last_float(stats_output, r"DC offset:\s*(-?[\d.]+)")
    return forensics


def validate_audio_experience(
    forensics: AudioForensics,
    video_duration_seconds: float,
    allowed_silent_tail_seconds: float,
    target_lufs: float,
    loudness_tolerance_lu: float = 2.0,
    true_peak_ceiling_dbtp: float = -1.0,
    max_internal_gap_seconds: float = 1.0,
    expected_sample_rate: int | None = None,
    allowed_lead_in_seconds: float = 0.0,
    designed_silence_intervals: list[tuple[float, float]] | None = None,
) -> ValidationResult:
    """The listener-facing gate, asserted against measured reality.

    Silence is classified by CAUSE, never banned by size. Three kinds are
    designed and must not be reported:

      * the opening title card, before any narration exists
        (`allowed_lead_in_seconds`);
      * the cross-fade plus breath between two steps
        (`max_internal_gap_seconds`), and any longer pause the PLAN itself
        declared by asking for a step longer than its narration - pacing
        is content (ADR-5.11), so those intervals are passed in as
        `designed_silence_intervals` rather than judged by length;
      * the closing fade (`allowed_silent_tail_seconds`).

    Every caller must derive those three from the pipeline's own timing
    constants, so the thresholds move only when the design moves. They are
    not tolerances to widen until a file passes: the defect this exists to
    catch measured 22.1 s against a 0.3 s closing fade, and no honest
    reading of these numbers makes that pass.
    """
    errors: list[str] = []

    tail = forensics.silent_tail_seconds
    if tail > allowed_silent_tail_seconds + 0.25:
        errors.append(
            f"unexplained silent tail: audio stops at "
            f"{forensics.meaningful_audio_end_seconds:.2f}s but the file runs to "
            f"{forensics.duration_seconds:.2f}s ({tail:.2f}s of silence, only "
            f"{allowed_silent_tail_seconds:.2f}s of closing fade is intended)"
        )

    if abs(forensics.duration_seconds - video_duration_seconds) > 0.25:
        errors.append(
            f"audio stream is {forensics.duration_seconds:.2f}s but video is "
            f"{video_duration_seconds:.2f}s"
        )

    designed = designed_silence_intervals or []

    def is_designed(run: SilenceRun) -> bool:
        """Does this run sit inside a silence the pipeline meant to leave?

        A designed interval runs from where a segment's speech ends to
        where the next segment's speech begins: the trimmed edge, the
        breath, whatever slack the plan declared beyond what the narration
        needed, and the cross-fade. All of that is pacing the pipeline
        chose; only silence OUTSIDE those intervals is unaccounted for.
        """
        return any(
            start - 0.12 <= run.start_seconds and run.end_seconds <= end + 0.12
            for start, end in designed
        )

    internal = [
        run
        for run in forensics.silences
        if run.end_seconds < forensics.duration_seconds - 0.05
        # The title card is silent because there is nothing to say yet.
        and run.end_seconds > allowed_lead_in_seconds
        and run.duration_seconds > max_internal_gap_seconds
        and not is_designed(run)
    ]
    if internal:
        worst = max(internal, key=lambda run: run.duration_seconds)
        errors.append(
            f"{len(internal)} pause(s) longer than {max_internal_gap_seconds:.2f}s "
            f"between narration segments; worst is {worst.duration_seconds:.2f}s at "
            f"{worst.start_seconds:.2f}s — artificial silence, not phrasing"
        )

    if forensics.integrated_lufs is not None:
        if abs(forensics.integrated_lufs - target_lufs) > loudness_tolerance_lu:
            errors.append(
                f"integrated loudness {forensics.integrated_lufs:.1f} LUFS is more "
                f"than {loudness_tolerance_lu:.1f} LU from the {target_lufs:.1f} "
                "LUFS target"
            )

    if forensics.true_peak_dbtp is not None:
        if forensics.true_peak_dbtp > true_peak_ceiling_dbtp:
            errors.append(
                f"true peak {forensics.true_peak_dbtp:.2f} dBTP exceeds the "
                f"{true_peak_ceiling_dbtp:.2f} dBTP ceiling — no headroom for the "
                "lossy encoder, which is where audible distortion comes from"
            )

    if expected_sample_rate is not None and forensics.sample_rate != expected_sample_rate:
        errors.append(
            f"sample rate is {forensics.sample_rate} Hz, expected "
            f"{expected_sample_rate} Hz"
        )

    if forensics.channels not in (1, 2):
        errors.append(f"unexpected channel count {forensics.channels}")

    return ValidationResult(stage="media", passed=not errors, errors=errors)

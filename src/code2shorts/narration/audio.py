"""Audio finishing for the narration track.

Every number here came from measuring the Phase 6 output, not from taste.
FFmpeg does the work through the project's single subprocess seam; no new
Python dependency is introduced.

What the measurements showed, on the real 181 s MP4:

  * every SAPI wav carries ~0.10 s of leading and ~0.76 s of TRAILING
    silence. That silence was counted as speech, so the fitter widened
    each visual step to hold it and then added its own 0.35 s headroom on
    top. Measured inter-segment gaps were 1.21-1.24 s, thirty times over
    - roughly 36 s of the video was dead air. That is the "repeated
    pauses" complaint, and it is a TTS artefact being mistaken for
    content, not a pacing choice;
  * integrated loudness -20.0 LUFS with a true peak of -0.0 dBFS: quiet
    overall yet already touching full scale, which leaves no room for the
    encoder and is what "rough / disturbed" sounds like on a lossy codec.

So: trim what the synthesiser padded, keep a deliberate breath between
sentences, and normalise loudness with a true-peak ceiling.
"""

from __future__ import annotations

from pathlib import Path

from code2shorts.execution.sandbox import run_subprocess

TRIM_THRESHOLD_DB = -50.0
"""Below this is silence. The SAPI tail measures as digital silence well
under -60 dB; -50 dB keeps a margin without eating quiet consonants."""

KEEP_EDGE_SILENCE_SECONDS = 0.04
"""A little silence is kept at each edge on purpose. Cutting exactly to
the first sample of speech clips plosives and puts a waveform step at the
join, which is audible as a click."""

BREATH_SECONDS = 0.12
"""The deliberate pause between narration segments, ON TOP of the 0.65 s
cross-fade the renderer already draws between steps.

Educational narration needs breathing room and this keeps it. What it does
not need is the same room paid for three times. Measured composition of a
single inter-segment gap:

    trimmed edge      0.04 s
    breath            THIS
    fade out previous 0.25 s   } the renderer's cross-fade, during which
    fade in next      0.40 s   } the picture is already changing
    trimmed edge      0.04 s

At 0.28 s that summed to 1.01 s of dead air after every sentence; the
transition itself is the pause, so the extra beat can be short."""

TARGET_LUFS = -16.0
"""Integrated loudness target. -16 LUFS is the usual mono speech target
for online video: YouTube normalises toward -14 LUFS, and starting a
little below it means the platform never has to attenuate, while the
dialogue still sits well above the -20 LUFS the old track measured."""

TRUE_PEAK_CEILING_DBTP = -2.5
"""Headroom for the lossy encoder, applied BEFORE encoding.

The old track peaked at -0.0 dBFS, so AAC had nowhere to go and
inter-sample overshoot clipped. The first attempt at a fix asked loudnorm
for -1.5 dBTP and the finished MP4 still measured -0.80 dBTP: `loudnorm`
constrains the PCM it is given, while the validator measures the file
after AAC, and the encoder overshot by ~0.7 dB. The pre-encode target
must therefore sit below the post-encode ceiling, not at it."""

LOUDNESS_RANGE_LU = 7.0
"""Target loudness range. Speech is naturally narrow; forcing it narrower
is what makes a voice sound pumped and lifeless."""

TRACK_SAMPLE_RATE = 44100
"""The mix is assembled at 44.1 kHz. The installed SAPI voices synthesise
at 22.05 kHz, so this adds no bandwidth - it stops the mix and the AAC
encoder from resampling a second time on top of that."""


class AudioProcessingError(RuntimeError):
    """FFmpeg could not process the track. Never swallowed: a silently
    unprocessed track would ship at the wrong loudness."""


def _run(command: list[str], cwd: Path, what: str) -> None:
    result = run_subprocess(command, cwd=cwd, timeout_seconds=300.0)
    if result.timed_out or result.returncode != 0:
        raise AudioProcessingError(f"{what} failed: {result.stderr[-800:]}")


def trim_edge_silence(path: Path) -> Path:
    """Remove the synthesiser's leading and trailing silence, in place.

    Interior pauses are untouched - they are the narrator's phrasing. Only
    the padding at the two edges goes, because that padding is what the
    timeline mistook for speech.
    """
    path = path.resolve()
    trimmed = path.with_name(f"{path.stem}__trimmed{path.suffix}")
    threshold = f"{TRIM_THRESHOLD_DB}dB"
    _run(
        [
            "ffmpeg", "-y", "-i", str(path),
            "-af",
            # start_periods/stop_periods=1 trims exactly one run at each
            # edge; detection is on the peak so a quiet breath is not
            # mistaken for silence.
            f"silenceremove=start_periods=1:start_silence={KEEP_EDGE_SILENCE_SECONDS}"
            f":start_threshold={threshold}:detection=peak,"
            "areverse,"
            f"silenceremove=start_periods=1:start_silence={KEEP_EDGE_SILENCE_SECONDS}"
            f":start_threshold={threshold}:detection=peak,"
            "areverse",
            str(trimmed),
        ],
        path.parent,
        f"trimming {path.name}",
    )
    trimmed.replace(path)
    return path


def normalize_track(path: Path, sample_rate: int = TRACK_SAMPLE_RATE) -> Path:
    """Loudness-normalise the assembled narration track, in place.

    Single-pass `loudnorm` with an explicit true-peak ceiling. Deliberately
    NOT a compressor or a limiter chain: the source is clean synthetic
    speech, and the brief is explicit that over-processing (pumping,
    metallic artefacts) is worse than a slightly uneven level.
    """
    path = path.resolve()
    normalized = path.with_name(f"{path.stem}__norm{path.suffix}")
    _run(
        [
            "ffmpeg", "-y", "-i", str(path),
            "-af",
            f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK_CEILING_DBTP}"
            f":LRA={LOUDNESS_RANGE_LU},"
            # Plain `aresample`, deliberately. soxr was tried first and
            # this FFmpeg build reports "Requested resampling engine is
            # unavailable", failing the whole run — and a resampler the
            # binary may not have is not worth a hard dependency for the
            # small quality difference on a 22 kHz speech source.
            f"aresample={sample_rate}",
            "-ar", str(sample_rate), "-ac", "1",
            str(normalized),
        ],
        path.parent,
        f"normalising {path.name}",
    )
    normalized.replace(path)
    return path

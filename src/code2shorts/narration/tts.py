"""TTSProvider: a clean vendor-neutral abstraction, same pattern as
LLMProvider. Phase 4 ships MockTTSProvider only — deliberately no real
vendor integration (ElevenLabs/Google TTS/AWS Polly/local) is wired up
yet; nothing in narration/workflow depends on which one eventually is.
Adding a real provider later is exactly "implement TTSProvider", not a
domain-model change.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from code2shorts.execution.sandbox import ProcessResult, run_subprocess


class TTSResult(BaseModel):
    audio_path: str
    duration_seconds: float
    checksum: str
    provider: str
    voice: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TTSFailure(Exception):
    pass


class TTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        """Synthesize `text` to speech, writing audio to `output_path`.
        Raises TTSFailure on error."""


class SyntheticTTSProvider(TTSProvider):
    """Produces REAL, decodable audio (silence) of a duration derived from
    the text length, via FFmpeg's built-in `anullsrc` generator.

    Not a speech synthesizer and not pretending to be one — it exists
    because `MockTTSProvider` writes fake bytes that real FFmpeg refuses
    to demux, which would make it impossible to prove that media
    composition genuinely works. Phase 4.1 explicitly permits synthetic
    audio but requires composition itself to be real; this is the
    narrowest thing that satisfies both. A real speech vendor
    (ElevenLabs/Google/Polly/local) is a later phase and slots in behind
    this same `TTSProvider` interface with no change above it.
    """

    def __init__(
        self,
        seconds_per_char: float = 0.06,
        sample_rate: int = 44100,
        timeout_seconds: float = 60.0,
        run_subprocess_fn: Callable[..., ProcessResult] | None = None,
    ) -> None:
        self._seconds_per_char = seconds_per_char
        self._sample_rate = sample_rate
        self._timeout_seconds = timeout_seconds
        self._run_subprocess_fn = run_subprocess_fn or run_subprocess

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        if not text.strip():
            raise TTSFailure("cannot synthesize empty text")
        # Absolute path: the subprocess runs with cwd set to this file's
        # directory (see ManimVideoRenderer.render for the same fix).
        output_path = output_path.resolve().with_suffix(".m4a")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = max(0.5, len(text) * self._seconds_per_char)

        # Fixed argument list, no shell — same trusted-subprocess pattern as
        # every other external tool invocation in this codebase.
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={self._sample_rate}:cl=mono",
            "-t",
            f"{duration:.3f}",
            "-c:a",
            "aac",
            str(output_path),
        ]
        result = self._run_subprocess_fn(
            command, cwd=output_path.parent, timeout_seconds=self._timeout_seconds
        )
        if result.timed_out or result.returncode != 0:
            raise TTSFailure(f"synthetic audio generation failed: {result.stderr[-2000:]}")
        if not output_path.is_file():
            raise TTSFailure("ffmpeg reported success but produced no audio file")

        checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            checksum=checksum,
            provider="synthetic-silence",
            voice=None,
            metadata={"synthetic": True, "speech": False, "sample_rate": self._sample_rate},
        )


class MockTTSProvider(TTSProvider):
    """Deterministic stand-in — same role as MockLLMProvider/FakeVideoRenderer.
    Writes a real (tiny, fake) audio file and computes a real checksum, so
    downstream lineage/checksum code exercises real logic without needing
    an actual TTS vendor or network access.
    """

    def __init__(self, voice: str = "mock-voice", seconds_per_char: float = 0.06) -> None:
        self._voice = voice
        self._seconds_per_char = seconds_per_char

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        if not text.strip():
            raise TTSFailure("cannot synthesize empty text")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"FAKE_AUDIO:{self._voice}:{text}".encode()
        output_path.write_bytes(payload)
        checksum = hashlib.sha256(payload).hexdigest()
        duration = max(0.5, len(text) * self._seconds_per_char)
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            checksum=checksum,
            provider="mock",
            voice=self._voice,
        )

SAPI_SCRIPT_NAME = "c2s_sapi.ps1"

# Fixed, repository-owned PowerShell. Narration text is NEVER interpolated
# into this script — it is passed as a bound -Text parameter, so no quote,
# newline, backtick or $() in the text can alter the command. Same trust
# model as every other external tool we invoke.
_SAPI_SCRIPT = """param(
    [Parameter(Mandatory=$true)][string]$Text,
    [Parameter(Mandatory=$true)][string]$OutFile,
    [string]$Voice = ""
)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice -ne "") { try { $synth.SelectVoice($Voice) } catch { } }
$synth.SetOutputToWaveFile($OutFile)
$synth.Speak($Text)
$synth.Dispose()
"""


class SapiTTSProvider(TTSProvider):
    """Real speech via the Windows Speech API (System.Speech).

    Chosen for Phase 4.4 because it is the only candidate that is
    simultaneously offline, key-free, zero-cost and **zero new
    dependency** — it is an operating-system component, not a package. The
    neural alternatives (kokoro, coqui-tts) sound better but pull a
    multi-gigabyte torch stack; piper is GPL-3.0 with no Windows wheel.
    See docs/PHASE_4_4_REUSE_AUDIT.md.

    Windows-only by nature. That is a real limitation, bounded by the
    `TTSProvider` seam: another platform or a cloud voice is a new
    implementation of this one interface, with nothing above it changing.

    Duration is **measured from the produced file**, never estimated.
    """

    def __init__(
        self,
        voice: str | None = None,
        timeout_seconds: float = 120.0,
        run_subprocess_fn: Callable[..., ProcessResult] | None = None,
    ) -> None:
        self._voice = voice
        self._timeout_seconds = timeout_seconds
        self._run_subprocess_fn = run_subprocess_fn or run_subprocess

    @staticmethod
    def is_available() -> bool:
        return sys.platform == "win32" and shutil.which("powershell") is not None

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        if not text.strip():
            raise TTSFailure("cannot synthesize empty text")
        if not self.is_available():
            raise TTSFailure(
                "Windows SAPI is unavailable (needs Windows with powershell on PATH)"
            )

        output_path = output_path.resolve().with_suffix(".wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        script_path = output_path.parent / SAPI_SCRIPT_NAME
        script_path.write_text(_SAPI_SCRIPT, encoding="utf-8")

        command = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Text",
            text,
            "-OutFile",
            str(output_path),
        ]
        if self._voice:
            command += ["-Voice", self._voice]

        result = self._run_subprocess_fn(
            command, cwd=output_path.parent, timeout_seconds=self._timeout_seconds
        )
        if result.timed_out:
            raise TTSFailure(f"SAPI synthesis timed out after {self._timeout_seconds}s")
        if result.returncode != 0:
            raise TTSFailure(f"SAPI synthesis failed: {result.stderr[-2000:]}")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise TTSFailure("SAPI reported success but produced no audio")

        duration = self._measure_duration(output_path)
        checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            checksum=checksum,
            provider="windows-sapi",
            voice=self._voice,
            metadata={"synthetic": False, "speech": True, "measured": True},
        )

    @staticmethod
    def _measure_duration(path: Path) -> float:
        """Probe the real file. A synthesiser's output length is the only
        number subtitles may be aligned against."""
        from code2shorts.media.probe import VideoProbeError, probe_audio

        try:
            return probe_audio(path).duration_seconds
        except VideoProbeError as error:
            raise TTSFailure(f"could not measure synthesized audio: {error}") from error

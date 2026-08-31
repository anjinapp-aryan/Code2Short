"""Kokoro-82M neural speech, driven directly through ONNX Runtime.

Licence position, which is the reason this module is shaped the way it is:

    onnxruntime   MIT
    gruut         MIT   (grapheme -> IPA)
    Kokoro-82M    Apache-2.0   (weights, fetched separately)
    num2words     LGPL-2.1  (gruut leaf; use does not force a relicence)

Nothing GPL is imported, and nothing GPL is required at runtime.
Code2Shorts stays MIT. The `kokoro-onnx` convenience wrapper is
deliberately NOT used: importing it pulls espeak-ng, which is GPL-3.0, so
the graph is driven directly instead. Its symbol table is vendored as
data — see `narration/phonemes.py`.

The model is not vendored. It is ~340 MB of weights, so it is fetched
once into `models/kokoro/` and the provider reports a clear, actionable
failure when it is absent rather than silently degrading.
"""

from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass, field
from pathlib import Path

from code2shorts.narration.audio import AudioProcessingError, trim_edge_silence
from code2shorts.narration.phonemes import (
    MAX_PHONEME_LENGTH,
    encode,
    to_kokoro_phonemes,
    unsupported_symbols,
)
from code2shorts.narration.tts import TTSFailure, TTSProvider, TTSResult

SAMPLE_RATE = 24000
"""Kokoro's native output rate. Twenty-four kilohertz against SAPI's
22.05 kHz — and, more to the point, a neural vocoder rather than a
concatenative one."""

DEFAULT_MODEL_DIR = Path("models/kokoro")
MODEL_FILENAME = "kokoro-v1.0.onnx"
VOICES_FILENAME = "voices-v1.0.bin"

DEFAULT_VOICE = "af_heart"
"""The narrator Phase 6.3 selected, by listening rather than by metric.

Kept here as the library default AND mirrored by `Settings.kokoro_voice`,
so a caller that constructs the provider directly gets the same narrator
the pipeline uses. Any packaged voice can be chosen by configuration; the
point of naming one is that a video must never change narrator between
runs."""

DEFAULT_SPEED = 1.0
"""Unmodified pace. Speeding speech up to shorten a video is prohibited
(ADR-5.11): teaching quality outranks duration, and a rushed narrator is
exactly the failure this project keeps rejecting."""

MODEL_HELP = (
    "Kokoro model files are not vendored (~340 MB). Download them once:\n"
    "  https://github.com/thewh1teagle/kokoro-onnx/releases/"
    "download/model-files-v1.0/kokoro-v1.0.onnx\n"
    "  https://github.com/thewh1teagle/kokoro-onnx/releases/"
    "download/model-files-v1.0/voices-v1.0.bin\n"
    f"and place them in {DEFAULT_MODEL_DIR}/"
)


@dataclass
class Prosody:
    """Engine-independent delivery controls.

    Section 8 of the brief: the narration layer owns pacing, the engine
    only renders it. Keeping these here rather than in the renderer is
    what lets a future provider honour the same intent.
    """

    speed: float = DEFAULT_SPEED
    pronunciations: dict[str, str] = field(default_factory=dict)
    """Word -> replacement text, applied before phonemisation. The escape
    hatch for a term the G2P mishandles; empty by default, because a
    per-word override table that grows unchecked becomes its own bug
    surface."""


class KokoroTTSProvider(TTSProvider):
    """Neural TTS behind the same `TTSProvider` seam SAPI uses.

    Nothing above `narration/` learns which engine produced the audio:
    the pipeline sees a `TTSResult` with a measured duration, exactly as
    before. Failure raises `TTSFailure` so a caller can fall back to SAPI
    explicitly — never silently, because a voice that changes mid-video is
    worse than one that is merely synthetic.
    """

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        model_dir: Path | None = None,
        prosody: Prosody | None = None,
        trim_silence: bool = True,
    ) -> None:
        self._voice = voice
        self._model_dir = Path(model_dir or DEFAULT_MODEL_DIR)
        self._prosody = prosody or Prosody()
        self._trim_silence = trim_silence
        self._session = None
        self._voices = None

    # -- availability ----------------------------------------------------

    @property
    def model_path(self) -> Path:
        return self._model_dir / MODEL_FILENAME

    @property
    def voices_path(self) -> Path:
        return self._model_dir / VOICES_FILENAME

    def is_available(self) -> bool:
        """True when this provider could actually synthesise right now."""
        if not (self.model_path.is_file() and self.voices_path.is_file()):
            return False
        try:
            import gruut  # noqa: F401, PLC0415
            import onnxruntime  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    # -- model -----------------------------------------------------------

    def _load(self) -> None:
        """Load the graph and voice pack once, on first use.

        Deferred rather than done in __init__ so that constructing the
        provider — which the pipeline does whether or not it ends up
        speaking — costs nothing.
        """
        if self._session is not None:
            return
        if not self.model_path.is_file() or not self.voices_path.is_file():
            raise TTSFailure(f"Kokoro model files missing.\n{MODEL_HELP}")
        try:
            import numpy as np  # noqa: PLC0415
            import onnxruntime as rt  # noqa: PLC0415
        except ImportError as error:  # pragma: no cover - env dependent
            raise TTSFailure(
                "Kokoro needs onnxruntime (MIT) and gruut (MIT): "
                "pip install onnxruntime gruut"
            ) from error

        self._session = rt.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        self._voices = np.load(self.voices_path)
        if self._voice not in self._voices.files:
            available = ", ".join(sorted(self._voices.files)[:8])
            raise TTSFailure(
                f"unknown Kokoro voice {self._voice!r}; available include {available}"
            )

    # -- text -> phonemes -------------------------------------------------

    def phonemise(self, text: str) -> str:
        """Text -> a Kokoro phoneme string, via gruut.

        Public because the mapping is the part most worth testing: it is
        where two different IPA conventions meet, and a regression here is
        a mispronunciation rather than a crash.
        """
        from gruut import sentences  # noqa: PLC0415

        for word, replacement in self._prosody.pronunciations.items():
            text = text.replace(word, replacement)

        spoken: list[str] = []
        for sentence in sentences(text, lang="en-us"):
            for word in sentence:
                if word.phonemes:
                    spoken.append(to_kokoro_phonemes(word.phonemes))
        return " ".join(spoken)

    # -- synthesis --------------------------------------------------------

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        if not text.strip():
            raise TTSFailure("cannot synthesize empty text")
        self._load()

        import numpy as np  # noqa: PLC0415

        phonemes = self.phonemise(text)
        if not phonemes.strip():
            raise TTSFailure(f"gruut produced no phonemes for {text[:60]!r}")

        unmapped = unsupported_symbols(phonemes) - {" "}
        if unmapped:
            # Reported, not hidden: an unmapped symbol is a silent
            # mispronunciation, and the fix belongs in the mapping table.
            raise TTSFailure(
                f"phonemes {sorted(unmapped)} have no Kokoro symbol; "
                "narration/phonemes.py needs a mapping entry"
            )

        output_path = output_path.resolve().with_suffix(".wav")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        chunks = [
            self._render(tokens, np) for tokens in self._chunk(phonemes)
        ]
        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        self._write_wav(audio, output_path, np)

        if self._trim_silence:
            try:
                trim_edge_silence(output_path)
            except (AudioProcessingError, OSError):
                pass

        duration = self._measure_duration(output_path)
        checksum = hashlib.sha256(output_path.read_bytes()).hexdigest()
        return TTSResult(
            audio_path=str(output_path),
            duration_seconds=duration,
            checksum=checksum,
            provider="kokoro-onnx",
            voice=self._voice,
            metadata={
                "synthetic": False,
                "speech": True,
                "measured": True,
                "neural": True,
                # Reproducibility needs the VOICE, not just the engine: two
                # runs of "kokoro" with different voices are different
                # videos. Recorded as one string so it survives into any
                # artifact that only keeps a producer field.
                "producer": f"kokoro:{self._voice}",
                "model": self.model_path.name,
                "sample_rate": SAMPLE_RATE,
                "speed": self._prosody.speed,
                "phoneme_count": len(phonemes),
            },
        )

    def _chunk(self, phonemes: str) -> list[list[int]]:
        """Split on word boundaries so no chunk exceeds the model's limit.

        Kokoro accepts at most `MAX_PHONEME_LENGTH` tokens. Splitting at a
        space keeps whole words intact; splitting by token count alone
        would cut a word in half and synthesise the halves as if they were
        separate words.
        """
        words = phonemes.split(" ")
        chunks: list[list[int]] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(encode(candidate)) > MAX_PHONEME_LENGTH and current:
                chunks.append(encode(current))
                current = word
            else:
                current = candidate
        if current:
            chunks.append(encode(current))
        return [c for c in chunks if c] or [[]]

    def _render(self, tokens: list[int], np):
        """One forward pass. The style vector is indexed by token count,
        which is how Kokoro conditions delivery on utterance length."""
        if not tokens:
            return np.zeros(0, dtype=np.float32)
        style = self._voices[self._voice][len(tokens)]
        audio = self._session.run(
            None,
            {
                "tokens": np.array([tokens], dtype=np.int64),
                "style": np.asarray(style, dtype=np.float32),
                "speed": np.array([self._prosody.speed], dtype=np.float32),
            },
        )[0]
        return np.asarray(audio, dtype=np.float32).reshape(-1)

    @staticmethod
    def _write_wav(audio, path: Path, np) -> None:
        """Write 16-bit PCM with the stdlib, no extra dependency.

        Peaks are scaled down only when the float output would wrap on
        conversion; leaving it to int16 overflow is how a vocoder's
        occasional overshoot becomes a click.
        """
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1.0:
            audio = audio / peak
        samples = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(samples.tobytes())

    @staticmethod
    def _measure_duration(path: Path) -> float:
        """Probe the real file — the same rule SAPI follows. A synthesiser's
        output length is the only number subtitles may be aligned against."""
        from code2shorts.media.probe import VideoProbeError, probe_audio  # noqa: PLC0415

        try:
            return probe_audio(path).duration_seconds
        except VideoProbeError as error:
            raise TTSFailure(f"could not measure synthesized audio: {error}") from error

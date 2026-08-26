# Phase 4.4 — Focused Reuse Audit (TTS, Alignment, Subtitles, Audio)

Scope limited to narration/audio/subtitles, per the brief. Repositories
conclusively evaluated in Phase 4.2A / 4.3 are not re-audited.

All licence and maintenance data read from the GitHub/PyPI APIs on
**2026-08-24**. Candidates that mattered were **executed**, not taken on
README claims.

## Decision matrix

| Candidate | Capability | License | Maintenance | Quality | Offline | API Key | Cost | Compatibility | Security | Decision |
|---|---|---|---|---|---|---|---|---|---|---|
| **Windows SAPI** (`System.Speech`) | TTS to WAV | OS component (no third-party licence) | ships with Windows | fair, robotic | **yes** | **no** | **free** | **verified working** | subprocess, fixed args | 🟢 **DIRECT REUSE** |
| **`srt`** (cdown) | SRT parse/compose | MIT ✅ | 2024-03-19; format is frozen | n/a | yes | no | free | **already installed** (Manim transitive dep) | pure-python, tiny | 🟢 **DIRECT REUSE** |
| **PyAV** | audio/video probing | BSD-3 ✅ | 2026-08-22 | n/a | yes | no | free | **already a dependency** | library call | 🟢 **DIRECT REUSE** |
| **FFmpeg** | mux, encode | LGPL/GPL build | active | n/a | yes | no | free | already used (Phase 4.1) | fixed-arg subprocess | 🟢 **DIRECT REUSE** |
| rhasspy/piper | local neural TTS | MIT ✅ | **ARCHIVED 2025-08-26** | good | yes | no | free | superseded | — | 🔴 **REJECT** (archived) |
| OHF-Voice/piper1-gpl (`piper-tts`) | local neural TTS | **GPL-3.0** ⚠️ | 2026-08-22, 5.2k★ | good | yes | no | free | **no Windows wheel** | copyleft | 🔴 **REJECT** |
| hexgrad/kokoro | local neural TTS | Apache-2.0 ✅ | 2025-08-06, 8.5k★ | very good | yes (after model DL) | no | free | needs **torch + transformers** | large surface | 🔵 **REFERENCE** (revisit for quality) |
| coqui-ai/TTS | local neural TTS | MPL-2.0 ✅ | **2024-08-16 (stale)** | good | yes | no | free | superseded by fork | — | 🔴 **REJECT** (unmaintained) |
| idiap/coqui-ai-TTS (`coqui-tts`) | local neural TTS | MPL-2.0 ✅ | 2026-06-10 | good | yes | no | free | heavy ML stack | large surface | 🔵 **REFERENCE** |
| edge-tts | cloud-ish neural TTS | **LGPL-3.0** ⚠️ | 2026-03-22 | **excellent** | **no** (network) | no | free | small deps (aiohttp) | undocumented MS endpoint | 🔵 **REFERENCE** (strong future option) |
| pyttsx3 | SAPI/NSSpeech wrapper | unclear/none declared ⚠️ | 2025-07-08 | = SAPI | yes | no | free | wraps what we already reach | licence unverifiable | 🔴 **REJECT** |
| Google / ElevenLabs / Azure / Polly | cloud TTS | proprietary | active | excellent | no | **yes** | **paid** | violates "no key for tests" | vendor lock | 🔵 **REFERENCE** (deferred) |
| faster-whisper | word/segment timestamps | MIT ✅ | 2025-11-19, 25k★ | excellent | yes (after model DL) | no | free | needs CTranslate2 + model | large | 🔵 **REFERENCE** (not needed at MVP) |
| openai/whisper | ASR alignment | MIT ✅ | 2026-07-28 | excellent | yes | no | free | needs torch | large | 🔵 **REFERENCE** |
| ASS/libass | styled subtitles | ISC/BSD | active | n/a | yes | no | free | overkill for MVP | — | 🔵 **REFERENCE** |
| Custom alignment layer | narration ↔ visual timeline | ours | — | — | yes | no | free | exact | controlled | 🟢 **BUILD** |

## Decisive evidence

### Piper is not viable here — two independent blockers

1. The original `rhasspy/piper` (MIT, 11,279★) is **archived** (2025-08-26).
2. Its successor `OHF-Voice/piper1-gpl` is **GPL-3.0**, and its PyPI
   distribution `piper-tts` 1.7.0 ships **manylinux wheels only** — no
   Windows wheel:

```
piper_tts-1.7.0-cp39-abi3-manylinux_2_17_aarch64...whl
piper_tts-1.7.0-cp39-abi3-manylinux_2_17_x86_64...whl
```

Either blocker alone would be sufficient. The GPL-3.0 change also matters
architecturally: importing it as a library would raise copyleft questions
for the whole project, whereas invoking a binary as a subprocess would not.
Not worth resolving when it cannot install on the target platform anyway.

### Neural local TTS costs multiple gigabytes for this phase

`kokoro` requires `torch`, `transformers`, `huggingface-hub`, `misaki`,
`numpy`. `coqui-tts` is comparable. Both are genuinely good and Apache-2.0 /
MPL-2.0 respectively — but pulling a multi-GB ML stack (plus runtime model
downloads) to narrate a 15-second video is exactly the "giant external
framework for a small problem" the brief forbids. Kept as 🔵 REFERENCE:
they are the right answer when narration *quality* becomes the priority,
and the `TTSProvider` seam means adopting one later changes nothing above it.

### Windows SAPI — executed, not assumed

Zero third-party dependencies, offline, no key, no cost. Verified:

```
voices: Microsoft David Desktop, Microsoft Zira Desktop
WAV written: 359,822 bytes
probed: pcm_s16le, 22050 Hz, mono, duration 8.158 s
```

Input included Java/algorithm terminology ("Two pointers … swapping
characters … The HashMap lookup runs in O of 1 time"). Pronunciation is
serviceable; "O of 1" is spoken naturally because the narration text
already spells complexity out rather than relying on symbol pronunciation.

Its real limitation is **Windows-only** and robotic prosody — both
documented, both bounded by the provider seam.

### `srt` and PyAV are free wins

`srt` (MIT) is **already installed** as a Manim transitive dependency —
`.venv/Lib/site-packages/srt.py`. PyAV likewise arrived with Manim and is
already used by `media/probe.py` since Phase 4.1. Using both adds **zero**
new dependencies.

### Whisper-family alignment is not needed at MVP

`faster-whisper` (MIT, 25k★) is excellent, but word-level alignment solves
a problem we do not have: narration segments are authored **per
visualization step**, so segment boundaries are already known exactly. ASR
would re-derive timings we can measure directly from the synthesised audio.
Deferred, not rejected — it is the right tool for word-level karaoke
subtitles later.

## Selected stack

| Concern | Choice | New dependency |
|---|---|---|
| TTS (default, dev + golden path) | Windows SAPI via subprocess | **none** |
| TTS (deterministic tests) | existing `MockTTSProvider` / `SyntheticTTSProvider` | none |
| Audio duration | PyAV (`media/probe.py`) | none (already present) |
| Subtitles | `srt` (MIT) | none (already present) |
| Mux | FFmpeg fixed-arg subprocess | none (already present) |
| Narration↔visual alignment | **BUILD** (`narration/alignment.py`) | none |

**Net new production dependencies: zero.**

## API keys, cost, offline

- **API key required:** none, for any path used by tests or the golden path.
- **Cost:** zero.
- **Free tier:** not applicable — nothing metered is used.
- **Offline:** yes. SAPI, PyAV, `srt` and FFmpeg all run without network.
- **Cloud alternative:** deliberately not implemented. Adding one later
  means implementing `TTSProvider` once; no domain model changes.

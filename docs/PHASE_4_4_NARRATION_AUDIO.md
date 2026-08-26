# Phase 4.4 — Narration, Audio Alignment, Subtitles

Reuse analysis lives in
[PHASE_4_4_REUSE_AUDIT.md](PHASE_4_4_REUSE_AUDIT.md). This document covers
the design, the security model, and what was actually verified.

## Objective

Add production-quality narration, audio alignment and subtitles without
letting audio dictate the visual timeline.

## The governing rule

> The **visual timeline is authoritative**. Audio adapts to it. Animation
> timing is never changed because a synthesiser produced a different
> duration.

This is enforced structurally, not by convention. `align_narration()`
computes every segment's start and end **from the plan's declared step
durations**. Measured audio duration is used for exactly one purpose:
detecting and reporting *overflow*. There is no code path in which an
audio duration can move a visual step.

Proven by `test_audio_longer_than_its_step_is_flagged_not_absorbed`: given
5.0s of audio against a 1.0s step, the timeline stays 2.0s total and the
segment is flagged.

## Architecture

```
ExecutionTrace          canonical
      |
      v
VisualizationPlan       authoritative for ORDER and TIMING
      |
      v
NarrationResponse       segment -> visualization_step_order
      |
      v
align_narration()  -->  AlignmentResult (start/end per segment)
      |                        |
      v                        v
  TTSProvider              build_srt()
   (measured                    |
    duration)                   v
      |                    SubtitleTrack (.srt)
      v
 MediaComposer  ---------> Final MP4
```

`NarrationSegment` / `NarrationResponse` / `TTSProvider` are the **existing
Phase 4 contracts, unchanged** — no duplicate abstraction was created. The
new work is the alignment layer, subtitle generation, and one real provider.

## Selected technology

| Concern | Choice | New dependency |
|---|---|---|
| Real speech | **Windows SAPI** (`System.Speech`) via subprocess | **none** (OS component) |
| Cross-platform fallback | `SyntheticTTSProvider` (real, decodable silence via FFmpeg) | none |
| Deterministic tests | `MockTTSProvider` | none |
| Audio duration | PyAV (`media/probe.py::probe_audio`) | none (already present) |
| Subtitles | `srt` (MIT) | none (already present via Manim) |
| Mux | FFmpeg, fixed argument list | none |

**Net new production dependencies: zero.**

### API keys and cost

- **API key required:** none, anywhere — not for tests, not for the golden
  path.
- **Cost:** zero.
- **Free tier:** not applicable; nothing metered is used.
- **Offline:** yes. SAPI, PyAV, `srt` and FFmpeg all work without network.
- **Cloud alternative:** deliberately not implemented. Adding one is a
  single new `TTSProvider` implementation; no domain model changes.

## Security model

Narration and subtitle text is **untrusted data** throughout.

**TTS subprocess.** Narration text is passed as a **bound PowerShell
parameter** (`-Text`), never interpolated into a script. The script itself
is fixed, repository-owned, and contains no `Invoke-Expression`. Verified
by `test_sapi_passes_text_as_a_bound_argument_never_as_script` (the payload
appears exactly once, as its own argv element) and
`test_sapi_script_never_interpolates_text`.

**Subtitles.** Text is written to a data file that FFmpeg reads as data —
it never reaches a shell. Quotes, apostrophes, ampersands, pipes,
semicolons, `$(...)`, backticks, Unicode and emoji all pass through
untouched.

### A real vulnerability found and fixed

`test_srt_injection_cannot_forge_extra_cues` **failed on first run**. A
narration payload of the form

```
safe

2
00:00:10,000 --> 00:00:20,000
FORGED CUE
```

parsed back as a **second subtitle cue with attacker-chosen timestamps**.
The first sanitizer collapsed `\n\n` to `\n`, which removed the blank line
but left the injected timing line intact — and the `srt` parser is lenient
enough to start a new cue without a preceding blank line.

Fixed by collapsing **every** newline form (`\r\n`, `\r`, `\n`, `U+2028`,
`U+2029`, `\x0b`, `\x0c`, `\x85`) to a space, so a timing line can never
begin. `validate_srt` now also rejects any surviving newline. A narration
segment is one spoken utterance and has no legitimate need for internal
line breaks.

Unchanged guarantees: no `exec`, no `eval`, no `shell=True`, fixed argument
lists, enforced timeouts.

## Verification

### Real speech (Windows SAPI)

```
seg 0: 3.40s pcm_s16le 22050Hz ch=1
seg 1: 3.32s pcm_s16le 22050Hz ch=1
seg 2: 3.07s pcm_s16le 22050Hz ch=1
seg 3: 3.41s pcm_s16le 22050Hz ch=1
```

Durations are **measured from the produced files**, never estimated.

### Overflow detection demonstrated on the real pipeline

The first golden-path run used 3.0s steps. Real speech needed 3.07–3.41s,
so the system reported **overflow on all four segments** and left the
timeline at 12.0s rather than stretching it. The fix belongs at
plan-authoring time, so step durations were re-sized **from the measured
speech** to 4.0s; the second run reported `overflow=0`. That sequence is
the rule working as designed, not a workaround.

### Final media (probed, not assumed)

| Property | Value |
|---|---|
| Video | 1080x1920, h264, 20.10s, 603 frames |
| Audio | aac, 22050 Hz, mono, 20.10s |
| Subtitles | 4 cues, 00:00:00,000 -> 00:00:16,000, monotonic, valid |
| Truncation | none — audio and video durations match exactly |

Subtitle file:

```
1
00:00:00,000 --> 00:00:04,000
We reverse the string HELLO using two pointers.
...
4
00:00:12,000 --> 00:00:16,000
The pointers meet, and the result is OLLEH.
```

## Performance

| Stage | Seconds |
|---|---|
| Java compile | 1.28 |
| JUnit | 1.97 |
| Execute + trace | 1.99 |
| **TTS (4 segments, real speech)** | **1.07** |
| Manim render | 21.30 |
| FFmpeg (concat + mux) | 0.23 |
| **Total** | **28.17** |

TTS is essentially free (1.07s for four spoken lines). Manim remains the
dominant cost.

## Known limitations

- **SAPI is Windows-only** and robotic. Bounded by the `TTSProvider` seam —
  a neural or cloud voice is one new implementation, with nothing above it
  changing.
- **Segment-level alignment only.** Word-level timing (karaoke subtitles)
  would need `faster-whisper`; deliberately not built, per the brief.
- **Plan durations must be sized to fit narration.** The system detects and
  reports mismatch but will not auto-resize, because that would violate the
  authoritative-timeline rule. Automatic plan-time duration estimation is
  the natural next step.
- **No burned-in subtitles.** The `.srt` is produced as a sidecar file; it
  is not muxed into the MP4 or rendered into frames.

## Deferred work

Neural local TTS (kokoro / coqui-tts) for quality; cloud provider; word-level
alignment via faster-whisper; subtitle muxing or burn-in; narration-aware
automatic step-duration planning.

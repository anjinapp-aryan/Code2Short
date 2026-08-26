# Phase 4.5 — Performance Baseline

Measured end to end with real tooling (Maven, JDK, Manim, FFmpeg, Windows SAPI) on Windows 11, Python 3.12.10. Seconds per stage.

Measurement only: nothing was optimized in this phase.

| Stage | correct | palindrome | two_sum | move_zeroes | remove_duplicates | mean |
|---|---|---|---|---|---|---|
| compile | 1.28 | 1.36 | 1.29 | 1.26 | 1.26 | **1.29** |
| junit | 2.04 | 0.00 | 0.00 | 0.00 | 0.00 | **0.41** |
| execute_trace | 1.91 | 1.78 | 1.73 | 1.85 | 1.69 | **1.79** |
| frame_state | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | **0.00** |
| plan_validate | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | **0.00** |
| manim | 20.24 | 24.19 | 30.52 | 27.63 | 24.96 | **25.51** |
| tts | 0.94 | 0.91 | 0.94 | 0.94 | 0.95 | **0.94** |
| subtitles | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | **0.00** |
| ffmpeg | 0.07 | 0.07 | 0.07 | 0.07 | 0.07 | **0.07** |
| TOTAL | 26.48 | 28.30 | 34.55 | 31.76 | 28.94 | **30.00** |

## Top bottlenecks

1. **manim** — 25.51s mean (85% of pipeline)
2. **execute_trace** — 1.79s mean (6% of pipeline)
3. **compile** — 1.29s mean (4% of pipeline)

## Reading these numbers

- **Manim dominates** and is inherent to rendering 1080x1920 vector animation; it is not a Code2Shorts inefficiency.
- **Java stages** (compile/JUnit/trace) each pay a fresh JVM plus Maven startup. Reusing one workspace across stages would cut this, at the cost of the isolation guarantee — not a trade worth making without evidence of a real problem.
- **TTS, subtitles, plan validation and FrameState are effectively free** (sub-second or milliseconds).

## Deliberately not optimized

No async, no caching, no parallelism, no distributed rendering. The brief forbids premature optimization, and nothing here is slow enough to justify the complexity or the loss of determinism.

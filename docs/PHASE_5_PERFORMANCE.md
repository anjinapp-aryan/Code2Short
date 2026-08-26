# Phase 5 performance — what a real LLM costs

Measured on this machine (Windows 11, JDK 25 compiling release 17, Maven
3.9.15, Manim CE 0.21.0, FFmpeg 7.1, Windows SAPI). Every number below came
from an actual run of `scripts/run_phase5_golden_path.py`; nothing is
estimated.

The headline: **the LLM now dominates the pipeline.** Through Phase 4.5 the
Manim render was the slow stage. With a free-tier routed model, the three
AI nodes cost more than everything else combined.

---

## 1. Deterministic baseline (mock provider, Reverse String)

The credential-free path, for comparison. Same pipeline, same validators,
no network.

| Stage | Seconds |
|---|---|
| Workflow (compile + trace + 3 AI nodes) | 3.33 |
| TTS (4 segments, Windows SAPI) | 1.02 |
| Manim render | 18.78 |
| FFmpeg composition | 0.19 |
| **Output** | 1080×1920 h264, **20.10 s**, 603 frames |

## 2. Real LLM (OmniRoute → free-tier routing, Reverse String)

Two full runs, same configuration (`--provider omniroute --model auto
--timeout 300`):

| Stage | Run A (s) | Run B (s) |
|---|---|---|
| Workflow incl. 3 LLM nodes | **289.01** | **485.94** |
| TTS (25 segments) | 6.23 | 6.12 |
| Manim render | 134.53 | 134.69 |
| FFmpeg composition | 1.58 | 2.20 |
| Final video length | 140.58 s | 153.68 s |
| Repair attempts consumed | 1 | 1 |
| Media validation | PASS | PASS |
| Timeline validation | PASS | PASS |

**The LLM stage is 68–78 % of wall-clock time**, and it varies by **68 %
between two identical runs** (289 s vs 486 s). That variance is the free
tier, not the pipeline — the deterministic stages moved by under 2 %.

An earlier run against the AI seam alone measured a single structured
`ExplanationResponse` at **30.1 s**, and a bare `"ping"` round trip at
**7.2 s**. The full-trace explanation prompt is far larger than either.

## 3. Why the default timeout had to change

`Settings.ai_timeout_seconds` defaults to **30 s**, which is reasonable for
a paid endpoint. The first real run failed on it immediately:

```
OpenAICompatibleTransientError: Timeout: litellm.Timeout: APITimeoutError
```

Classified transient and retried, exactly as designed — but no number of
retries fixes a budget that is smaller than the work. The Phase 5 golden
path therefore takes `--timeout` and defaults to **180 s**; 300 s was used
for the measured runs above.

## 4. Worst-case call bound

Retry and repair are separate and both bounded, so the ceiling is explicit:

```
(1 + max_retries) × (1 + max_repair_attempts)  =  3 × 3  =  9 calls per node
```

At free-tier latency that is a long time per node, which is why the bound
being *explicit* matters. Observed runs consumed **1** repair attempt on
the visualization plan and none elsewhere.

Before the retry-amplification fix, the same configuration issued **9
upstream HTTP requests for what the config called 3** — litellm's own retry
loop multiplying with ours. Load, not just latency.

## 5. Output length is now model-controlled

| Run | Plan steps | Final video |
|---|---|---|
| Mock | 4 | 20.10 s |
| Real (Reverse String) | 25 | 140.58 s / 153.68 s |
| Real (Move Zeroes) | 67 | see §6 |

The model proposes one step per trace event. Every step is grounded,
validated and correctly timed — the videos are *right* — but far longer
than the short format intends. Timeline fitting widens steps to hold their
narration (1.0 s → 7.3–7.8 s for the first step), so longer plans compound:
step count × speech duration sets the runtime.

**Constraining plan length is content policy, not a correctness gate**, and
is deliberately not implemented in Phase 5. It is the obvious next lever.

## 6. Move Zeroes (real LLM)

| Stage | Seconds |
|---|---|
| Workflow incl. 3 LLM nodes | **569.34** |
| TTS (67 segments) | 16.17 |
| Manim render | 336.38 |
| FFmpeg composition | 4.08 |
| Trace | 67 events, `''` → `1,3,12,0,0` (expected) |
| Plan steps | 67, all widened by fitting (step 0: 1.0 s → 6.07 s) |
| Overflow after fitting | 0 |
| Final video | 1080×1920 h264, **384.02 s**, 11 522 frames, aac 22050 Hz matching |
| Media validation | PASS |
| Timeline validation | PASS |

Total ≈ 926 s, of which the LLM is **61 %**. The 67-step plan is what makes
this the longest run in every stage: TTS, render and composition all scale
with step count, and the model chose one step per trace event.

## 7. Where the time actually goes

```
real-LLM run, Reverse String (Run A, 431 s total)

  LLM   ███████████████████████████████████████████  289 s   67 %
  Manim ████████████████████                         135 s   31 %
  TTS   ▏                                              6 s    1 %
  FFmpeg▏                                              2 s    0 %
```

Phases 0–4.5 optimised the right-hand side. Phase 5 made the left-hand side
the only one that matters, and it is the one this project controls least.

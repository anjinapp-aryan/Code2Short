# Phase 8.3 — Native 4K

4K is two more **profiles**, not a new pipeline. `VERTICAL_4K` (2160 × 3840)
and `LANDSCAPE_4K` (3840 × 2160) were already declared in Phase 8.0, with
unique identity keys. Each was refused for exactly one reason:
`unsupported_reason="… not benchmarked (Phase 8.3)"`.

This phase measured 4K, then removed that guard. Apart from wording on
the Create page, nothing else in the render path changed.

```
VideoProfile (4K) ─► layout_for(orientation) ─► the SAME CompositionLayout as HD
                 ─► the SAME scene source (byte-identical to HD)
                 ─► manim --resolution 3840,2160 | 2160,3840   ← native 4K frames
                 ─► FFmpeg -c:v copy (no scaling) ─► MediaPolicy(profile pixels)
```

## 1. Supported profiles

| Profile | Aspect | Native resolution | Identity key | Status |
|---|---|---|---|---|
| `VERTICAL_HD` (default) | 9:16 | 1080 × 1920 | `youtube_short` | Renderable, unchanged |
| `LANDSCAPE_HD` | 16:9 | 1920 × 1080 | `landscape_hd` | Renderable, unchanged |
| `VERTICAL_4K` | 9:16 | 2160 × 3840 | `vertical_4k` | **Renderable (8.3)** |
| `LANDSCAPE_4K` | 16:9 | 3840 × 2160 | `landscape_4k` | **Renderable (8.3)** |

The refusal mechanism stays in place. A profile that is given an
`unsupported_reason` is still neither offered nor accepted, and it is
refused before any job, version or directory exists. Tests now exercise
that with a profile made unrenderable inside the test.

## 2. Native resolutions

All layout geometry is in Manim **scene units**. The scene header fixes
`config.frame_height` per layout: 8.0 for portrait and 4.5 for landscape.
It derives the frame width from the pixel aspect.

So the only effect of 4K is to raise the density from **240 px per unit
(HD) to 480 px per unit (4K)**. The composition does not change.

## 3. UI behaviour

The existing picker now offers four cards. All of them come from the
profile registry (`FORMATS` = every renderable profile):

| Label | Pixels | Line |
|---|---|---|
| 9:16 Vertical | 1080 × 1920 | Shorts / Reels / TikTok (**checked**) |
| 16:9 Landscape | 1920 × 1080 | YouTube / Desktop / TV |
| 9:16 Vertical 4K | 2160 × 3840 | Ultra HD |
| 16:9 Landscape 4K | 3840 × 2160 | Ultra HD |

The labels come from the profile's own metadata. The aspect ratio and
orientation come from the profile, and "4K" / "Ultra HD" is added when
the profile's short side is at least 2160 px. No profile list is
duplicated in JavaScript, and none in the templates. The default stays
`VERTICAL_HD`.

## 4. Backend behaviour

The behaviour is unchanged from Phase 8.2D; only the accepted set is
larger:
- An **absent** field means `VERTICAL_HD`.
- A **supplied** value must name a renderable profile.
- These are all rejected with HTTP 422, and none falls back to another
  profile: `""`, `"8k"`, `"4k"`, `"4096x2160"`, `"3840x2160"`, `"random"`
  and `"imax"`.

## 5. Pipeline flow

Unchanged. The flow is the one documented in Phase 8.2D §3; 4K travels
through it as just another `video_format`.

## 6. Rendering architecture

| Concern | HD → 4K | Evidence |
|---|---|---|
| Layout | same object (`layout_for`) | `test_4k_uses_the_layout_of_its_orientation` |
| Scene source | **byte-identical** | test, plus a bench of the real plans (`scene_sha256` equal, and equal to the accepted HD scene) |
| Region bounds (units) | identical | `test_every_region_has_the_same_unit_bounds_at_hd_and_4k` |
| Manim resolution | `--resolution 3840,2160` / `2160,3840` | command assertion; render-stage MP4 probe |
| `-qh` vs `--resolution` | the explicit resolution wins | output folders `2160p30` / `3840p30`, probed sizes |
| Composition | `-c:v copy`, so no scale filter can apply | final MP4 frames = render frames |

`RENDERER_VERSION` was **not** bumped. Renderer behaviour didn't change:
the scene is byte-identical and HD output is unaffected. No 4K video
existed before, so no cached artifact can be stale. `PIPELINE_VERSION`
was not bumped either, because no stage changed.

## 7. Safe areas

The safe areas are the layouts' own, unchanged:
- **Landscape:** 5% on every edge.
- **Portrait:** the accepted Phase 6.5 margins of 0.18 u (x) and 0.55 u
  (y). These are **not** 5%.

| Layout | Margin (units) | HD px | 4K px |
|---|---|---|---|
| Landscape x / y | 0.40 / 0.225 | 96 / 54 | **192 / 108** |
| Portrait x / y | 0.18 / 0.55 | 43 / 132 | 86 / 264 |

The prompt's "portrait 108 / 192 px at 4K" assumed 5% for portrait as
well. Portrait has never used 5%, and its baseline is protected, so it
was not changed.

**Measured on real frames.** These are the content bounding boxes of five
frames per render, from the real palindrome plans.

- **Landscape 4K:**
  - Left margin is 187 px and right is 182 px, against 192 px.
  - Top and bottom margins are at least 160 px and 179 px, against
    108 px.
  - At HD the same frames measure 94 px and 91 px, against 96 px.
- **The 5–10 px overrun is the stroke width.**
  - Manim's bounding boxes exclude stroke width, so a 4-wide stroke
    extends about 0.02 u (about 5 px at HD, 10 px at 4K) outside the box
    it was laid out by.
  - This already existed at HD, and it scales exactly 2×. Every frame
    keeps at least 182 px of clearance from the edge.
- **Portrait:**
  - Mostly 82 px left and right at 4K, which is 86 px less the half
    stroke.
  - One step measures 46 px at 4K (23 px at HD). That is pre-existing
    portrait content reaching past the safe line, and it is in the
    accepted scene byte for byte.

## 8. Code-panel behaviour

The panel is unchanged: the same fixed viewport, representative width,
outlier shrink, containment, fill-then-reclamp and re-pin. It is all
computed in units, so at 4K every readability floor is exactly twice
the pixels.

| Quantity | Units | HD px | 4K px |
|---|---|---|---|
| Code viewport width | 4.14 | 994 | 1988 |
| Line-height growth target | 0.20 | 48 | 96 |
| Absolute line-height floor | 0.175 | 42 | 84 |
| Array cell | 0.52 | 125 | 250 |

## 9. Typography (measured)

Text is rendered by Pango as vector paths and laid out in units. Stroke
widths scale with the frame, which was the Phase 8.0 open question.

- **Ink ratio:** 4K frame ink ÷ HD frame ink = **3.987–4.004** on ten
  real frames (2² = 4). A stroke that stayed N px wide at 4K would pull
  this well under 4; one frame read 4.076 during a fade.
- **Same picture:** the 4K frame box-downsampled 2× differs from the HD
  frame by **0.08–0.34 / 255** mean absolute difference.

So every text element is exactly 2× its HD pixel size. That covers the
title, explanation, labels, code, line numbers and pointer labels. Text
takes the same fraction of the frame as in HD, so it doesn't become tiny,
and no font was enlarged.

## 10. Performance benchmarks (measured)

**Machine:** Intel Core Ultra 7 265K (20 threads), 31.6 GiB RAM, Windows
11, Manim's CPU (Cairo) renderer. No GPU was used.

**Method:** `build_renderer(profile)` (the web path's renderer) on the
real, LLM-produced, narration-fitted palindrome plans of the accepted
real-test versions:
- portrait: `real-test/palindrome` v2;
- landscape: `real-test/landscape` v1.

**Memory:** the peak working set of the whole render process tree,
sampled about every 0.5 s.

**Sequential:**

| Profile | Duration | Frames | fps | Render wall | × realtime | Peak RAM | CPU s (lower bound) | Render MP4 | Final MP4 | Render dir incl. partials |
|---|---|---|---|---|---|---|---|---|---|---|
| VERTICAL_HD | 132.32 s | 3970 | 30 | 129.0 s | 0.97 | 662 MiB | 241 | 9.8 MiB | 11.5 MiB | 22.9 MiB |
| VERTICAL_4K | 132.32 s | 3970 | 30 | **234.1 s** | 1.77 | **1765 MiB** | 659 | 18.5 MiB | 20.3 MiB | 40.5 MiB |
| LANDSCAPE_HD | 111.05 s | 3332 | 30 | 115.3 s | 1.04 | 650 MiB | 195 | 8.5 MiB | 10.0 MiB | 20.2 MiB |
| LANDSCAPE_4K | 111.05 s | 3332 | 30 | **218.0 s** | 1.96 | **1737 MiB** | 585 | 16.8 MiB | 18.3 MiB | 36.7 MiB |

So 4K costs **1.8–1.9× the render time, about 2.7× the RAM, about 3× the
CPU time and about 1.9× the file size** of HD. That is far below the 4×
the pixel count would suggest, because much of the per-frame cost is
fixed overhead.

Both 4K renders fit the web renderer's 900 s timeout with a 3.8× margin.
Raw data:
- `output/phase8_3/bench/bench_*.json`
- `output/phase8_3/bench/frames_report.json`
- `output/phase8_3/compose/compose_report.json`

## 11. Memory and resource observations

**Concurrent (measured), 4K and HD rendered at the same time:**

| Pair | Pair wall | 4K wall | HD wall | Peak RAM (both) | Output |
|---|---|---|---|---|---|
| LANDSCAPE_4K + LANDSCAPE_HD | 228.6 s | 223.5 s | 130.8 s | 2363 MiB | 3840×2160 and 1920×1080 ✓ |
| VERTICAL_4K + VERTICAL_HD | 266.4 s | 259.7 s | 156.3 s | 2415 MiB | 2160×3840 and 1080×1920 ✓ |

- No failure, no exhaustion, and no cross-talk. Each 4K render slowed by
  only 3–11% and each HD render by 13–21%.
- `JobManager` concurrency is still **unbounded**: one thread per distinct
  request. It was **not** changed, because the measurement shows no
  problem on this machine. The deployment risk is in §17.
- Kokoro TTS and the LLM stages are not in these numbers. They run before
  the render, not alongside it.

## 12. Media validation

These are the four finals, composed exactly as `ComposeMediaNode` does
it. Each takes the bench render plus the **real** Kokoro narration of the
version its plan came from, passes through `assemble_narration_track` →
`normalize_track` → `MediaComposer` → `write_srt`, and then runs through
the node's two gates.

| Profile | Final | Codec / fps | Frames = render | Audio | Loudness | `validate_final_video` | `validate_timeline` |
|---|---|---|---|---|---|---|---|
| VERTICAL_HD | 1080×1920, 132.315 s | h264 / 30 | ✓ | aac 44.1 kHz mono, 132.315 s | −16.7 LUFS, −2.1 dBTP | pass | pass |
| VERTICAL_4K | 2160×3840, 132.316 s | h264 / 30 | ✓ | aac 44.1 kHz mono, 132.316 s | −16.7 LUFS, −2.1 dBTP | pass | pass |
| LANDSCAPE_HD | 1920×1080, 111.048 s | h264 / 30 | ✓ | aac 44.1 kHz mono, 111.048 s | −16.6 LUFS, −2.3 dBTP | pass | pass |
| LANDSCAPE_4K | 3840×2160, 111.047 s | h264 / 30 | ✓ | aac 44.1 kHz mono, 111.047 s | −16.6 LUFS, −2.3 dBTP | pass | pass |

- **No dimension surprises:** no rotation metadata, no sample aspect
  ratio, yuv420p, and no 3840×2168, 4096×2160 or swapped orientation.
- **Timing doesn't depend on resolution:**
  - same narration segments (30 portrait, 31 landscape);
  - same speech end (132.084 s and 111.025 s), so no truncation;
  - the subtitle file is **byte-identical** to the accepted version's.

### Native, not upscaled

Every test that claims 4K checks two things: the **render-stage** MP4
(probed before any composition runs) and the final MP4. On the real
frames:

- **Energy beyond HD Nyquist:** a native 4K frame holds **0.0130–0.0148**
  of its spectral energy there. A bicubic upscale of the HD frame holds
  0.0014–0.0015 and Lanczos 0.0010, so native has **9–14×** more.
- **Not a nearest upscale:** the native frame is not made of 2×2 blocks.
  A nearest upscale is, and scores exactly 0.

`test_a_real_4k_render_is_native_at_the_render_stage` runs these checks
on a real Manim render on every run.

## 13. Fingerprint isolation

`video_format` was already in `RequestFingerprint.config`. All four
identity keys are distinct and pinned to their pixels, so the four
digests differ: `test_same_content_in_a_different_profile_never_collides`
now asserts HD≠4K for both orientations. Fingerprinting did not change.

## 14. Registry behaviour

The registry is unchanged. `test_each_format_is_its_own_video_and_each_is_reused`
generates all four formats into one library:
- 4 distinct versions, 4 distinct digests, exactly 4 builds;
- each repeat request returns **its own** version;
- each probed file has its own pixel size.

A 4K request never receives the HD video. The registry records the
profile, `video_format`, fingerprint, version, status and output path;
the resolution is probed from the file on the details page.

## 15. Concurrency results

- **Jobs:** `test_concurrent_jobs_in_different_formats_do_not_cross_talk`
  covers (4K, HD) pairs for both orientations, using the real routes, job
  manager, pipeline factory and renderer, with a stand-in Manim. Each job
  gets its own job, version, reported format and probed pixel size.
- **Renders:** the real Manim concurrency measurement is in §11.

## 16. Real E2E results

**NOT VERIFIED.** Native 4K rendering is verified, but the full
LLM-to-MP4 E2E is not.

Three real runs of the live web server, driven over HTTP the way the
Create form submits, all stopped at the LLM stages:

| Run | Library | Outcome |
|---|---|---|
| 1 | `output/real-test/4k/library` | Every job failed at compile: `mvn` was not on the server's PATH (environment error) |
| 2 | `output/real-test/4k/library_run2` | compile ✓ trace ✓ explain ✓, then the LLM chain failed (details below) |
| 3 | `output/real-test/4k/library_run3` | the same as run 2, with a 180 s LLM timeout for the server process only; `educational_plan` also failed its validation and repair once |

Run 2's chain failures:
- **Gemini:** 429 on the free tier, 20 requests per day per model.
- **Groq:** 429, its free tier allows 8,000 tokens per minute and the
  requests were 2.0–5.2k tokens each.
- **NVIDIA:** timed out at the 30 s LLM timeout.

The user approved using the configured failover chain (Gemini → Groq →
NVIDIA) for this run; the brief said not to switch providers. That
consent didn't change the outcome, because no provider produced the full
set of LLM stages.

The failed versions are kept as audit evidence. No accepted artifact was
touched.

**What is verified for real:**
- Java compile, trace and explain on the live server, at 4K, through the
  form.
- The four-option picker as served.
- The 422 rejection of invalid values.
- The native 4K render and composition of **real** LLM-produced plans with
  real Kokoro narration (§10–12).

These are replays of LLM output from accepted runs, not a fresh LLM call.

## 17. Deployment considerations

**Local success does not mean production is safe.** All the figures
below are from one 20-thread desktop.

| Resource | HD | 4K | Two concurrent (4K + HD) |
|---|---|---|---|
| Peak RAM, render tree | ~0.65 GiB | ~1.75 GiB | ~2.4 GiB |
| CPU time per video | ~200–240 s | ~590–660 s | additive |
| Render wall time here | ~1.0× realtime | ~1.8–2.0× realtime | 4K slows 3–11% |
| Temp disk (render dir incl. partials) | ~20–23 MiB | ~37–41 MiB | additive |
| Final MP4 (about 2 min) | ~10–12 MiB | ~18–20 MiB | — |

Add Kokoro (about 338 MB of weights in memory), the web process and the
JVM stages on top. A 4K render used 590–660 CPU-seconds spread over many
cores here. On a host with 1–2 vCPUs it would take roughly that long in
wall time, about 10 minutes, which is close to the 900 s render timeout.
**Not measured on a server.**

The Phase 7 audit found the intended Render.com deployment not ready
(no Dockerfile; Linux needs Cairo/Pango; one instance; a persistent
disk). For 4K specifically:
- A small instance (≤ 2 GiB) cannot hold one 4K render plus Kokoro and
  the app with headroom.
- Because job concurrency is unbounded, two 4K jobs could exhaust a
  4 GiB instance.

A server deployment that offers 4K should first measure on the target
instance. It should then either cap concurrent renders (one 4K at a time)
or hide 4K on small instances. Neither was done here: both are
infrastructure decisions that need separate approval.

## 18. Known limitations

- **Real LLM-to-MP4 E2E at 4K is not verified** (§16). Free-tier limits
  blocked all three providers on this date.
- **Job concurrency is unbounded.** That is safe on this machine
  (2.4 GiB for 4K + HD) and a risk on small servers.
- **Free-tier fallbacks inside the chain:**
  - Groq's 8k tokens/minute limit is exceeded by our prompts, and the
    chain doesn't wait for `retry-after`.
  - NVIDIA's free model is slow at the 30 s default timeout.
  These are provider properties, not 4K ones.
- **Pre-existing, unchanged:**
  - The portrait safe margins aren't 5%.
  - One portrait step's content reaches about 20 px (HD) past the
    portrait safe line.
  - Stroke half-widths extend about 5 px (HD) or 10 px (4K) past
    layout bounds.

## 19. Tests

`tests/test_4k_native.py` adds 10 tests:
- the layout matches HD's;
- byte-identical scenes and per-profile `--resolution`;
- identical unit bounds at HD and 4K;
- 2× pixel margins (192/108 for landscape);
- a **real** Manim render per orientation, proving render-stage size, the
  same picture, the ink ratio, and native detail against bilinear,
  bicubic, Lanczos and nearest upscales.

Contract changes in existing tests (4K is now accepted):
- `test_format_selection.py` now covers four formats for:
  - the picker and the labels;
  - form to config;
  - review and details;
  - propagation to the job, the registry and the probed pixels;
  - reuse of all four in one library;
  - concurrency of (4K, HD) pairs.
- The invalid set now includes `8k`, `4k`, `4096x2160`, `3840x2160` and
  `random`.
- The refusal tests (`test_video_profile.py`, `test_pipeline_parity.py`,
  plus a new picker/backend test) now run against a profile made
  unrenderable for the test, so the mechanism stays covered.

Full suite: **1024 passed, 51 failed, 27 skipped**.
- The **identical** 51 pre-existing failures as after 8.2D: Maven and JDK
  not on the suite's PATH, SAPI, and the comment-grep rule-7 test.
- 8.2D was 993 passed. Of the 31 more, 7 are the Groq and NVIDIA provider
  tests added earlier the same day and 24 are Phase 8.3.

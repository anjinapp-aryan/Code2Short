# Phase 8.2C — 16:9 landscape composition

**Result:** `LANDSCAPE_HD` (16:9, 1920 × 1080) is implemented and verified with
a real end-to-end palindrome run. The portrait composition is unchanged: its
scene is byte-identical (sha256 `5b0b81e0…d357`).

Built on the Phase 8.2A design (`docs/PHASE_8_2A_LANDSCAPE_DESIGN.md`) and
the 8.2B seam. No trace, plan, narration, timeline, fingerprint or pipeline
change.

---

## 1. Architecture

```
VideoProfile ─ layout_for(profile) ─► CompositionLayout
                                         PORTRAIT   composer="vertical"  (Phase 6.5.x, unchanged)
                                         LANDSCAPE  composer="columns"   (this phase)
build_renderer(profile) ─► ManimVideoRenderer(resolution, layout)
   aspect guard ─► build_scene_source(layout)
      header: config.frame_height = layout.frame_height        (8.0 | 4.5)
      primitives(..., layout=layout)                            (shared)
      COMPOSERS[layout.composer]                                (compose_vertical | compose_columns)
```

What was added:

| Piece | Where | What |
|---|---|---|
| Column composer | `primitives.compose_columns` | Scalars hang from the region top; the array is anchored by its cells' top edge (so stacking labels never move it); code fills its column with the **same** sizing rules as `compose_vertical`, hangs from the top, and is re-pinned to the fixed viewport (`repin_viewport`) |
| Pointer separation | `primitives._separated_pointer_arrows` | Used only when `layout.separate_pointer_labels` (landscape). Same labels, colours and one-arrow-per-cell rule; keeps labels inside the state column and lifts a label that would overlap an earlier one by one level (0.34 u) until clear. Deterministic, purely geometric |
| Title height cap | `primitives.title_text` | Emits a height cap only when `layout.title_max_height` is set (landscape 0.18 u); portrait emits nothing new |
| Layout fields | `visualization/layout.py` | `composer`, `content_top`, `content_bottom`, `array_top`, `title_max_height`, `separate_pointer_labels`, all with defaults that leave portrait untouched |
| Composer dispatch | `manim_renderer.COMPOSERS` | `{"vertical": compose_vertical, "columns": compose_columns}` |
| Enable | `core/video_profile.py` | `LANDSCAPE_HD.unsupported_reason` removed; the 4K profiles stay refused ("not benchmarked, Phase 8.3") |

There is no second renderer, no copy of the code panel, no new text system and
no new dependency. `compose_vertical` is unchanged.

## 2. Geometry (8.0 × 4.5 units at 240 px/unit; x −4…4, y −2.25…2.25)

| Region | Units | Pixels |
|---|---|---|
| Safe area (5%) | x −3.60…3.60, y −2.025…2.025 | 96 px / 54 px margins |
| Title band | y 1.625…2.025, height ≤ 0.18 | ≤ 43 px tall |
| Content region | y −1.085…1.485 (2.57 u) | 617 px |
| Code column | x −3.60…0.54 (4.14 u) | **994 px** (the portrait viewport) |
| State column | x 0.74…3.60 (2.86 u) | **686 px** |
| Explanation band | y −2.025…−1.225, full width | ≤ 80 chars × 3 lines |
| Array top edge | y −0.125 | |
| Map / sequence | top 0.885, height ≤ 1.97 | |

### Measured, not assumed

Real Manim mobjects were built in-process (`tests/test_landscape_layout.py`).

| Case | Result |
|---|---|
| Code panel | exactly x −3.60…0.54 in every case; fills the region; loop-body window 0.243 u/line (58 px), signature-line window 0.208 u (50 px), long file 0.236 u |
| 7-cell array | 0.372 u cells (89 px), x 0.74…3.60 |
| Short arrays (A, AA, ABC, ABBA) | unscaled 0.52 u cells, lowest index row −1.064 ≥ region bottom −1.085 |
| Scalars | top 1.485, bottom 1.11–1.31, always above the highest stacked label (0.92) |
| Explanation (3 lines) | y −2.002…−1.248, inside its band |
| Title | h 0.18, y 1.735…1.915 |

The numbers from the 8.2A design survived measurement unchanged, apart from
two that the 8.2B/8.2C work pinned down:
- **Vertical safe margin:** 54 px (the approved 5%) instead of the 60/84 px
  proposed in 8.2A.
- **Array anchor:** `array_top = −0.125`. Two stacked labels rise 1.05 u
  above their cells and must clear the tallest scalars row (bottom 1.025)
  with a 0.10 gap.

## 3. Tests

`tests/test_landscape_layout.py`: 32 tests, under real Manim, without rendering.

- **Containment:** every region in its box, for RACECAR (edges, inner,
  converged), ABBA, A, AA, ABC and ABCCBA.
- **No overlap** between title, code, array, scalars, explanation and every
  pointer label.
- **Pointer labels:** never overlap; one arrow per addressed cell, ending on
  that cell; adjacent pointers (ABBA, AA, ABCCBA) are lifted to separate
  levels; the array doesn't move when labels stack.
- **Code:** stays in its fixed-width panel at or above the readable floor for
  the loop body, the 53-char signature and a 60-line file; 90+ character lines
  stay contained; line numbers stay inside the panel.
- **Whole scene:** a full landscape scene from the accepted plan is native
  16:9 (`frame_height = 4.5`) and imports only `manim`.

`tests/test_layout_seam.py`, `tests/test_video_profile.py` and
`tests/test_pipeline_parity.py` were **updated for the new contract**.
Wherever a test asserted "16:9 is refused", it now asserts the same refusal
against a still-refused profile (4K) or a deliberately unimplemented layout
(`DRAFT`), plus that `LANDSCAPE_HD` renders natively (`--resolution 1920,1080`,
`frame_height = 4.5`). No test was deleted or skipped.

The portrait gate, `test_the_portrait_scene_is_byte_identical_to_the_accepted_video`,
passes.

Full suite: **965 passed, 51 failed, 26 skipped**, against 934 / 51 / 26
before. The failing set is identical: 48 need Maven on the default PATH, and 3
are pre-existing (the comment-grep rule-7 test, still the same two offenders,
and two stale SAPI alignment tests).

## 4. Visual validation

- **Accepted-plan render** (`output/phase8_2c/landscape_render/`): the 6.5.3
  plan and trace rendered natively at 1920 × 1080, 30 fps, 3894 frames,
  129.78 s. That's the same frame count as portrait, because the timeline
  doesn't depend on layout.
- **Adjacent pointers** (`output/phase8_2c/abba/`): a **real Java trace of
  `ABBA`** through the existing adapter (the Java is unchanged; only the input
  differs), rendered at `left = 1, right = 2`. `right` is lifted a level with
  its own arrow to cell 2; nothing overlaps or clips.
- **Real E2E frames** (`output/real-test/landscape/frames/`): all ten teaching
  states were inspected (§5).

## 5. Real E2E result: VERIFIED

**Path:** the real palindrome Java source → Maven compile → instrumented JVM
trace → Gemini (`gemini-3.6-flash`) explanation, educational plan,
visualization plan and narration → Kokoro `af_heart` per segment → fit and
alignment → landscape Manim → FFmpeg → validation → registry. It was driven
through `JobManager` → `GenerationManager` → `DefaultPipelineFactory` with
`video_format="landscape_hd"`; the web form can't choose a format yet.

| | Value |
|---|---|
| Java | `RACECAR` → `true`; trace 30 events, return `true` |
| Plans | educational plan 9 moments; visualization plan 31 steps (numbered in order) |
| Narration | 31 Kokoro segments, 85.35 s of speech |
| Timing | proposed 88.1 s scene → fitted 111.0 s (26 steps widened); overflow 0 |
| Render | 1920 × 1080, 30 fps, 3332 frames, 111.05 s, h264 |
| Final MP4 | `output/real-test/landscape/library/palindrome/v1/aa53f2d182d643d1a2db868b36a8dd32/compose/final.mp4`, 1920 × 1080 (16:9), 111.05 s, AAC 44.1 kHz mono 111.05 s |
| Timing check | last speech ends 110.60 s < video 111.05 s; no cue past the end; largest gap 0.92 s |
| Validation | media ✓ (1920 × 1080 policy), timeline ✓, SRT ✓ (31 cues), teaching sync ✓, audio forensics ✓ (−16.6 LUFS, −2.3 dBTP, 0.46 s tail), final gate ✓ |
| Registry | v1 `completed`, `video_format = landscape_hd`, pipeline 8.1 |
| Duplicate request | "An existing video already exists for this configuration."; versions 1 → 1; no stage re-ran |
| Wall time | 848 s, dominated by Gemini 503/500 "high demand" errors during the visualization plan (593 s, handled by the provider's bounded retry); render 120 s |

Visual review of the ten states:

| State | Result |
|---|---|
| Opening | title fades in on black (same as portrait) |
| Intro / input | code left, explanation below; state column empty because no structure is observed yet, same as portrait |
| Array creation | array appears right; `input = RACECAR` above |
| left/right init | pointers at 0 and 6, labels clamped inside the column, arrows to the edge cells |
| while condition | line 7 highlighted |
| First comparison | cells 0 and 6 read-highlighted |
| Pointer movement | pointers 1 / 5 |
| Next comparison | line 10 highlighted |
| Middle condition | both on 3, stacked `right` over `left`, one arrow |
| Return true | line 16 `return true;` highlighted |
| Ending | fade-out |

No clipping, no overlap, no safe-area violation in any inspected frame.

## 6. Versioning

- **`RENDERER_VERSION` not bumped.** The portrait output is unchanged
  (byte-identical scene). No 16:9 video existed before, since the profile was
  always refused, so no cached video can be stale.
- **`PIPELINE_VERSION` not bumped.** No stage was added, removed or
  reordered.
- **Fingerprint unchanged.** `landscape_hd` and `youtube_short` identity keys
  were already distinct and tested.

## 7. Known limitations

- **UI.** The Create form can't choose 16:9 yet (template and
  `_config_from_form` unchanged). Landscape is reachable through
  `TeachingConfig(video_format="landscape_hd")` and the generation API below
  the routes.
- **Empty state column on early steps.** Intro and input steps show nothing
  there, because scalars are drawn only beside an observed structure. This is
  existing behaviour in both formats.
- **Very long source lines.** 90+ characters render small (0.080 u/line),
  identical to portrait, because the panel width is identical. They stay
  contained.
- **Active-line outlier shrink.** The long `String input = …` line is drawn
  smaller when active, as in portrait.
- **Pointer labels vs array.** Labels are unscaled (30 pt) while a 7-cell
  array is scaled to 71%. They read large beside the cells. Portrait
  typography was deliberately kept.
- **Portrait pointer overlap.** Adjacent-pointer label overlap still exists in
  **portrait** (not fixed here, as instructed). A candidate for a portrait
  robustness phase.
- **Adjacent-pointer evidence.** In the real E2E video it wasn't exercised,
  because the RACECAR input never has adjacent pointers. It is covered by the
  real-Manim geometry tests and the real-trace ABBA render.
- **4K** is still refused, pending the Phase 8.3 benchmark.
- **Maven** is still not on the default PATH on this machine. The E2E used a
  session-only PATH entry for the installed Maven 3.9.15.

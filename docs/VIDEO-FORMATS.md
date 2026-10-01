# Video formats

Code2Shorts renders **one educational content model** into **one of several
output formats**. The execution trace, the educational plan, the narration
and the teaching steps are format-free. Only the renderer learns which
format it is drawing.

```
Java → ExecutionTrace → EducationalPlan → VisualizationPlan → Narration    (format-free)
                                                  │
                                    TeachingConfig.video_format
                                                  │
                                          VideoProfile (core/video_profile.py)
                                                  │
                         ┌────────────────────────┼─────────────────────────┐
                 generation identity      renderer (pixel size)       UI label (future)
                 (fingerprint)            Manim --resolution          Create screen
                                                  │
                                          FFmpeg: stream copy → MP4
```

Status labels:

- **IMPLEMENTED ARCHITECTURE**: exists in code and is covered by tests.
- **NOT YET IMPLEMENTED**: named and planned; refuses to run today.

---

## 1–4. Supported formats

| Profile | Aspect | Orientation | Native resolution | Identity key | Status |
|---|---|---|---|---|---|
| `VERTICAL_HD` (default) | 9:16 | portrait | 1080 × 1920 | `youtube_short` | **Renderable.** The accepted Phase 6.5.1/6.5.3 baseline |
| `LANDSCAPE_HD` | 16:9 | landscape | 1920 × 1080 | `landscape_hd` | **Renderable** (Phase 8.2C). Native column composition: code left, state right, explanation band |
| `VERTICAL_4K` | 9:16 | portrait | 2160 × 3840 | `vertical_4k` | **Renderable** (Phase 8.3). The portrait composition, rendered natively at 480 px/unit |
| `LANDSCAPE_4K` | 16:9 | landscape | 3840 × 2160 | `landscape_4k` | **Renderable** (Phase 8.3). The column composition, rendered natively at 480 px/unit |

A profile is a frozen dataclass: `id`, `label`, `aspect_width`/`aspect_height`,
`pixel_width`/`pixel_height`, `identity_key`, and `unsupported_reason`
(`None` when renderable). `aspect_ratio`, `orientation` and `resolution`
(`"1080x1920"`) are derived, so they can't disagree with the pixels.

Construction rejects contradictions with `InvalidVideoProfileError`:
non-positive or odd dimensions (H.264 4:2:0 can't encode odd sizes), a
ratio that isn't in lowest terms, pixels that don't match the ratio, and
square output.

**Native resolution only.** `pixel_width × pixel_height` is what Manim
renders. There is no "render 1080p, upscale to 4K". If output scaling is
ever approved, it must be a separate, explicit field.

## 5. Default profile

`VERTICAL_HD`. `resolve_video_profile(None or "")` returns it, and so does
`TeachingConfig()` when no format is given. Every Phase 7 request (the web
form never sends a format) therefore resolves to 9:16 at 1080 × 1920.

## 6. Fingerprint implications

- The format is part of generation identity through the **existing**
  `TeachingConfig.video_format` field, already hashed into
  `RequestFingerprint.config`. No second format field was added.
- `video_format` accepts a profile id (`vertical_hd`) or an identity key
  (`youtube_short`), case- and space-insensitive. It is normalised to the
  identity key, so both spellings are the same video. An unknown value fails
  validation immediately.
- **Why VERTICAL_HD's key is `youtube_short`:** every Phase 7 version was
  recorded with `video_format="youtube_short"`. Keeping that key keeps the
  default fingerprint **byte-identical**, so existing library videos stay
  reusable. A test pins this.
- Each profile has a distinct key, so the same program in a different format
  or resolution can never be served from another format's cache.
  `PALINDROME + 9:16 ≠ PALINDROME + 16:9`, and
  `PALINDROME + 1080×1920 ≠ PALINDROME + 2160×3840`.
- **Rule:** an identity key is a promise about pixels. Changing a profile's
  size requires a new key. A test pins key → pixels so a silent change
  fails.
- `RENDERER_VERSION` / `PIPELINE_VERSION` were not bumped. The default
  output is unchanged (§7).

## 7. Current implementation status

**IMPLEMENTED ARCHITECTURE**

| Piece | Where | What it does |
|---|---|---|
| Profiles | `core/video_profile.py` | The four profiles, validation, `resolve_video_profile`, `require_renderable` |
| Format selector | `generation/fingerprint.py::TeachingConfig.video_format` | Validated and normalised; `.video_profile` property |
| Early refusal | `generation/manager.py::GenerationManager.generate` | Unrenderable profile raises `UnsupportedVideoProfileError` **before** a version row exists or the pipeline is built |
| Renderer wiring | `webapp/pipeline.py::build_renderer(profile)` | `ManimVideoRenderer(resolution=profile.resolution)`; replaces the hard-coded `"1080x1920"` |

How a format reaches each stage:

- **Manim.** The profile reaches Manim only through the renderer's existing
  `resolution` constructor argument, which becomes `--resolution W,H`. The
  scene already derives `frame_width = 8.0 × pixel_width / pixel_height`
  (Phase 6.1), so Manim's frame follows the pixel aspect automatically.
  `visualization/` is unchanged.
- **FFmpeg.** `MediaComposer` copies the rendered video stream
  (`-c:v copy`), so output dimensions are inherited from Manim. No codec,
  bitrate, audio or synchronization change was made or needed.
- **Media validation.** Since Phase 8.1 the web path validates every
  final MP4. `DefaultPipelineFactory` builds
  `MediaPolicy(width=profile.pixel_width, height=profile.pixel_height)` for
  `ComposeMediaNode`, so the file is checked against the requested
  profile's pixels (see `docs/PHASE_8_1_PIPELINE_PARITY.md`).

**Regression evidence (Phase 8.0):** the accepted 6.5.3 palindrome `plan.json`
+ `trace.json` were re-rendered through `build_renderer(VERTICAL_HD)` into
`output/phase8_0/palindrome/`. The generated `scene.py` is byte-identical to
`output/phase6_5_3/palindrome/render/scene.py` (sha256 `5b0b81e05bc703df…`).
The render is 1080 × 1920, 30 fps, 3894 frames, 129.78 s, the same frame
count as 6.5.3.

The 4K render and benchmark, and the UI format selector, were
implemented later (Phases 8.3 and 8.2D).

## 8. 16:9 (Phase 8.2) — IMPLEMENTED

- **Layout seam (8.2B).** `visualization/layout.py::CompositionLayout`,
  selected by `layout_for(profile)` from the profile's orientation and
  passed to `ManimVideoRenderer` beside the resolution. Layout values
  carry geometry only; typography and readability floors stay shared
  constants, and both layouts keep 240 px per scene unit.
- **Portrait.** `primitives.PORTRAIT` is built from the existing
  constants. The portrait scene is byte-identical to the accepted 6.5.3
  scene; a test pins its sha256.
- **Landscape composition (8.2C).**
  - `primitives.compose_columns`, next to `compose_vertical`, on an
    8.0 × 4.5-unit frame with a 5% safe area.
  - The code column is on the left at the portrait viewport width
    (994 px); the state column is on the right (686 px); the explanation
    is a full-width bottom band (≤ 80 chars × 3 lines).
  - Pointer labels that would collide on adjacent cells are lifted a
    level (landscape only).
  - See `docs/PHASE_8_2C_LANDSCAPE_IMPLEMENTATION.md`.
- **Guards.** A layout whose composition isn't implemented is refused
  before Manim runs. A layout whose aspect doesn't match the requested
  pixels is refused too, so a portrait arrangement can't be drawn into a
  16:9 frame.
- **Versioning.**
  - `RENDERER_VERSION` was **not** bumped. The portrait output is
    unchanged, and no 16:9 video existed before (the profile was always
    refused), so no cached video could be stale.
  - `PIPELINE_VERSION` was not bumped, because no stage changed.
- Media validation already takes the profile's dimensions on the web path (Phase 8.1).

## 9. 4K (Phase 8.3) — IMPLEMENTED

- **A profile, not a pipeline.** The two 4K profiles lost their
  `unsupported_reason`; nothing else in the render path changed.
  `layout_for` picks the HD layout of the same orientation, and the
  renderer writes a **byte-identical scene** for HD and 4K. Only Manim's
  `--resolution` differs, so 4K is 480 px per scene unit instead of 240.
- **The open questions from 8.0, answered by measurement:**
  - **Stroke widths and text scale with the frame.** A 4K frame holds
    4.00× the ink of the HD frame of the same plan (an unscaled stroke
    would pull that under 4). Box-downsampled 2×, the 4K frame matches the
    HD one to about 0.3/255.
  - **The `PIXELS_PER_UNIT = 240` arithmetic** is HD-only commentary. Every
    readability floor is in units, so at 4K each is exactly twice the
    pixels.
  - **`--resolution` wins over `-qh`.** Render-stage MP4s probe at
    3840 × 2160 and 2160 × 3840.
- **Native, proven:** the render-stage frame has about 10× more spectral
  energy beyond HD Nyquist than any smooth upscale of the HD frame, and
  is not 2×2 blocks (that would be a nearest upscale).
- **Cost:** 1.8–2.0× the HD render time and about 1.7 GiB peak (HD:
  about 0.65 GiB).
- See `docs/PHASE_8_3_4K_IMPLEMENTATION.md`.

---

## Appendix: format assumptions found in the Phase 8.0 audit

| File | Location | Assumption | Why it exists | Profile-driven? | Risk |
|---|---|---|---|---|---|
| `webapp/pipeline.py` | renderer construction | `resolution="1080x1920"` | Phase 7 wiring | **Done** (`build_renderer`) | — |
| `visualization/manim_renderer.py` | `ManimVideoRenderer.__init__` | default `resolution="1080x1920"` | Default for callers | Already a parameter; default is the baseline | Low |
| `visualization/manim_renderer.py` | `build_scene_source` | `frame_height=8.0`, width from pixel aspect | Phase 6.1 letterbox fix | Already derived | Low |
| `visualization/primitives.py` | top-level constants | 4.5 × 8 frame, 240 px/unit, safe margins, band y-positions, fonts, cell sizes, code viewport, caption wrap | Accepted 6.5.1/6.5.3 portrait layout | Yes, in Phase 8.2 (per orientation) | **High**: this is the baseline |
| `visualization/code_state.py` | comments / window radius | 9:16 code band | Readability | With the layout | Medium |
| `visualization/fake_renderer.py` | ctor | `"1080x1920"` default | Test double | No | Low |
| `visualization/renderer.py` | `RenderResult.resolution` | description only | Documentation | No (records the actual probe) | None |
| `visualization/timing.py` | fades | Format-independent | — | No | None |
| `media/validation.py` | `EXPECTED_WIDTH/HEIGHT`, `MediaPolicy` defaults | 1080 × 1920 | Baseline policy | Callers pass profile dims | Low |
| `media/composer.py` | ffmpeg args | `-c:v copy`; no scaling | Keeps render pixels | No change needed | None |
| `workflow/nodes.py` | `_entry_source` comment | 9:16 frame | Documentation | No | None |
| `config.py` | `Settings.video_resolution="1080x1920"`, `video_fps=30` | Early config | **Unused anywhere**; a pre-existing duplicate, left untouched | Remove or derive later | Low (confusing) |
| `core/models.py` | `LessonSpec.aspect_ratio`, `AnimationSpec.aspect_ratio` = `"9:16"` | Phase 0 models | **Unused by the pipeline** (only `test_core_models`) | Leave; not the format source | Low |
| `generation/fingerprint.py` | `TeachingConfig.video_format="youtube_short"` | Phase 7 identity | **Reused as the format selector** | **Done** | — |
| `webapp/templates/create.html` | disabled input | "YouTube Short · 1080 × 1920" | Phase 7 UI | Yes (UI phase) | Low |
| `webapp/templates/review.html` | Format row | "1080 × 1920" | Phase 7 UI | Yes, `config.video_profile` | Low |
| `webapp/templates/_macros.html` | poster placeholder | "1080 × 1920" | Phase 7 UI | Yes | Low |
| `scripts/run_phase5_golden_path.py` | renderer construction | `VERTICAL_HD.resolution` (Phase 8.1) | Current golden path | **Done** | — |
| `scripts/run_golden_path.py`, `run_narrated_golden_path.py`, `run_phase6_golden_path.py`, `measure_performance.py` | renderer construction | `"1080x1920"` | Historical phase reproductions | Deliberately explicit | Low |
| `tests/test_real_render.py`, `tests/test_visual_readability.py` | assertions | 1080 × 1920, 240 px/unit | Protect the baseline | No: they **are** the regression guard | None |

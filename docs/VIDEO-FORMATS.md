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
| `LANDSCAPE_HD` | 16:9 | landscape | 1920 × 1080 | `landscape_hd` | Declared; **refuses to render** (no 16:9 composition yet, Phase 8.2) |
| `VERTICAL_4K` | 9:16 | portrait | 2160 × 3840 | `vertical_4k` | Declared; **refuses to render** (no 4K benchmark yet, Phase 8.3) |
| `LANDSCAPE_4K` | 16:9 | landscape | 3840 × 2160 | `landscape_4k` | Declared; **refuses to render** (needs Phases 8.2 and 8.3) |

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

**NOT YET IMPLEMENTED**

- Any 16:9 composition. `visualization/primitives.py` is the portrait
  layout (a 4.5 × 8 unit frame; bands, safe margins, code viewport and fonts
  are all module constants).
- Any 4K render or benchmark.
- A format selector in the UI. The Create page shows a disabled
  "YouTube Short · 1080 × 1920" field; Review shows "Format 1080 × 1920".
- Per-profile layout, typography, caption, code-panel or safe-area data.
  Deliberately **not** added to `VideoProfile` until a second composition
  exists to justify their shape.

## 8. Future 16:9 phase (Phase 8.2)

What must change, found by the Phase 8.0 audit:

- `primitives.py` layout constants (`FRAME_WIDTH=4.5`, `SAFE_*`, band `*_Y`,
  `CODE_VIEWPORT_WIDTH`, `CAPTION_CHARS_PER_LINE`, `STRUCTURE_*`,
  `CODE_HEIGHT_BUDGET`, …) become one layout value per orientation,
  selected by `profile.orientation`. No `if width == …` chains.
- `compose_vertical` is portrait-specific. A landscape composition
  (code beside the visualization) is a new function, not a flag.
- Then remove `unsupported_reason` from `LANDSCAPE_HD` and bump
  `RENDERER_VERSION`.
- Media validation already takes the profile's dimensions on the web path (Phase 8.1).

## 9. Future 4K phase (Phase 8.3)

- Portrait 4K shares the portrait composition: the scene's unit frame is
  still 4.5 × 8, now at 480 px/unit. Whether stroke widths, the
  `PIXELS_PER_UNIT=240` readability arithmetic and text rasterisation hold
  at 4K is **not verified**.
- Benchmark render time, CPU, memory, temporary disk (partial movie files)
  and final size before enabling it.
- `-qh` is passed together with `--resolution`. Confirm the explicit
  resolution wins at 4K.
- Then clear `unsupported_reason` for the 4K profiles.

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

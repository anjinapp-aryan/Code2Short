# Phase 4.1 — Real Render Verification

Phase 4 proved the pipeline's *shape* was correct with a
`FakeVideoRenderer` and an injected fake FFmpeg. Phase 4.1 replaces both
with the real tools and proves the pipeline produces **one real, playable,
correct 1080×1920 MP4**.

It is a verification phase, not a feature phase. No new algorithms, no new
language adapters, no new providers.

## 1. Environment requirements

| Tool | Version verified | Required for |
|---|---|---|
| Python | 3.12.10 | everything |
| JDK | 25.0.3 (compiles to release 17) | compile/test/execute/trace |
| Maven | 3.9.15 | Java build boundary |
| Manim Community | 0.21.0 | real rendering |
| FFmpeg | 7.1 (gyan.dev essentials, via `imageio-ffmpeg` 0.6.0) | composition, frame extraction |
| PyAV | 18.1.0 (installed as a Manim dependency) | media probing |

Maven, the JDK, and Python were already present from Phases 1–4. Manim and
FFmpeg were **not** present and were installed for this phase.

## 2. Installation / setup

```bash
# Manim + a real FFmpeg binary (imageio-ffmpeg bundles a static build)
pip install manim imageio-ffmpeg
```

`imageio-ffmpeg` ships `ffmpeg` but **not** `ffprobe`. Media inspection
therefore uses PyAV (`code2shorts.media.probe`) rather than shelling out to
`ffprobe` — see `src/code2shorts/media/probe.py` for the full reasoning.

FFmpeg must be reachable as `ffmpeg` on `PATH`. On Windows, copying the
bundled binary into the venv's `Scripts/` directory is sufficient:

```powershell
copy .venv\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe `
     .venv\Scripts\ffmpeg.exe
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
```

## 3. Running the golden path

```bash
python scripts/run_golden_path.py                    # -> output/golden/
python scripts/run_golden_path.py --output-dir DIR   # custom location
```

The authoritative gate is the test suite, not the script:

```bash
pytest tests/test_real_render.py     # real Manim + real FFmpeg
pytest                               # everything (115 tests)
pytest -m "not integration"          # fast unit-only (74 tests, no tools needed)
```

`tests/test_real_render.py` is marked `@pytest.mark.real_render` and
**auto-skips** when either Manim or FFmpeg is missing, so the normal
developer workflow never depends on them.

## 4. Output location

```
output/golden/
  render/                 # Manim working dir (scene.py + media/)
  narration.m4a           # synthetic silent audio
  final.mp4               # <- the deliverable
  frames/frame_{0..3}.png # extracted for visual inspection
```

Final artifact: `output/golden/final.mp4`

## 5. Generated video metadata (measured, not assumed)

| Property | Rendered | Final (composed) |
|---|---|---|
| Resolution | 1080×1920 | 1080×1920 |
| Codec | h264 | h264 |
| Duration | 18.00 s | 18.00 s |
| Frames | 540 | 540 |
| FPS | 30 | 30 |
| Audio | — | AAC mono 44.1 kHz (silent) |

Metadata is read back out of the file with `probe_video()`; the renderer no
longer reports a predicted duration (see §9).

## 6. Visual inspection result

Four frames were extracted with FFmpeg and inspected individually.

| Frame | Content | Verdict |
|---|---|---|
| 0 | "Reverse a String in Java" | centred, legible, in-frame |
| 1 | "Reversing HELLO with two pointers." | centred, legible, in-frame |
| 2 | "Swap: chars[3] = E" | real trace content; dim (sampled mid-transition) |
| 3 | "Result: OLLEH" | correct final value, in-frame |

Checked and clear: no text clipping, no overlapping elements, no malformed
characters, no empty or unintentionally black frames, no excessive text
density, nothing outside the 9:16 safe area, and step order matches trace
order.

**Not applicable — and this is the honest limitation of this phase:** the
trusted renderer (`build_scene_source`) currently draws *sequential text
cards only*. There are no array tiles, no pointer glyphs, and no rendered
Java code, so the corresponding checks ("array tiles outside frame",
"pointer positions outside frame", "incorrect pointer labels", "unreadable
Java code") have nothing to fail against. The video is **correct and
truthful, but visually minimal** — it narrates the algorithm rather than
depicting it. Richer visual primitives are the obvious next increment and
are deliberately out of Phase 4.1's scope.

An automated check (`_frame_is_not_blank`) asserts each sampled frame has
real luminance contrast, so a silently-black render fails the gate.
Judging layout quality remains a **manual acceptance criterion**.

## 7. Performance baseline

Machine: Windows 11 Pro (10.0.26200), Python 3.12.10, JDK 25.0.3,
Maven 3.9.15, Manim 0.21.0, FFmpeg 7.1.

| Stage | Seconds |
|---|---|
| Java compile | 1.31 |
| JUnit tests (6/6) | 1.96 |
| Execute + trace | 1.92 |
| **Manim render** | **9.47** |
| Synthetic audio (TTS) | 0.04 |
| FFmpeg composition | 0.06 |
| **Total** | **15.50** |

Manim dominates at ~61% of wall time, as expected. No optimisation was
attempted — this is a baseline only.

Test suite: 115 tests in ~127 s (real rendering runs three times across the
suite); 74 unit tests in ~0.3 s.

## 8. Trace → plan → video consistency

The visualization plan is built **from the real trace** and validated
against it before rendering, so the video cannot depict a state the program
did not reach:

- trace: 25 events, `HELLO` → `OLLEH`, `step_index` sequential 0…24
- events asserted present: `ARRAY_READ`, `ARRAY_WRITE`, `LOOP_ITERATION`,
  `CONDITION_EVALUATED`, and variables `left` / `right`
- every `VisualizationStepPlan.trace_event_index` is checked to exist in
  the trace (`validate_visualization_plan`)
- frame 2 shows `chars[3] = E`, a genuine `ARRAY_WRITE` from the run

## 9. Bugs found by rendering for real

All four were invisible to Phase 4 because the renderer and FFmpeg were
faked. Each now has a regression test.

1. **Video silently truncated to audio length.** `MediaComposer` used a
   bare `-shortest`, cutting an 18.0 s render down to the 5.2 s of
   narration — two thirds of the visualization discarded. Fixed by padding
   audio (`-af apad`) and capping at the video's duration (`-t`).
   *(`test_composer_caps_output_at_video_duration_never_truncating_video`)*
2. **Composition hung.** The first fix, `-af apad -shortest`, padded audio
   without bound and never terminated; the timeout caught it. Replaced with
   an explicit `-t`, which cannot hang.
3. **Relative paths resolved twice.** Subprocesses run with `cwd` set to
   the output directory, so a relative scene path became
   `…/render/…/render/scene.py` and Manim reported `FileNotFoundError`.
   Only reproducible from a working directory other than the tests'.
   Fixed by resolving to absolute paths in the renderer, composer, and TTS
   provider. *(`test_manim_renderer_uses_absolute_scene_path`,
   `test_composer_uses_absolute_paths`)*
4. **Renderer metadata was a guess.** `duration_seconds` was computed as
   `2.0 + sum(step durations)` = 3.0 s while the real video was 6.0 s;
   `_find_output_file` also picked the alphabetically-first `.mp4`, avoiding
   Manim's `partial_movie_files/` fragments only by luck. Fixed with real
   probing and explicit fragment exclusion.
   *(`test_manim_renderer_ignores_partial_movie_files`)*

## 10. Limitations and known issues

- **Visually minimal output** — text cards only; no array/pointer/code
  visuals (see §6). The single largest gap between this and a publishable
  educational short.
- **Audio is silent.** `SyntheticTTSProvider` generates real, decodable
  AAC silence so composition is genuinely exercised; it is not speech. A
  real vendor slots in behind the same `TTSProvider` interface.
- **The plan is a deterministic fixture, not live LLM output.** Phase 4.1
  deliberately avoids a live Gemini dependency; the AI seam is unchanged
  and covered by Phase 4's tests.
- **FFmpeg needs manual `PATH` setup on Windows** (see §2) because
  `imageio-ffmpeg` does not expose its binary as `ffmpeg`.
- **No `ffprobe`**, hence PyAV for probing.
- Byte-identical MP4s across runs are **not** guaranteed (encoders embed
  timestamps) and not required; logical output is identical — same trace,
  same plan, same frame count, same duration.

## 11. PASS / FAIL criteria

Phase 4.1 passes only if all of the following hold:

- [x] Phase 0–4 tests still pass (115/115 total)
- [x] Real Maven compile succeeds
- [x] Real JUnit validation succeeds (6/6)
- [x] Real Java execution succeeds, `HELLO` → `OLLEH`
- [x] Real `ExecutionTrace` produced, 25 events, sequential
- [x] Trace contains genuine two-pointer reversal operations
- [x] `VisualizationPlan` validates against the real trace
- [x] Trusted repository-owned renderer used (no AI-generated Manim source)
- [x] Real Manim execution succeeds
- [x] Real MP4 produced and **inspected** (not assumed)
- [x] Resolution exactly 1080×1920
- [x] Duration > 0 (18.00 s), frame count > 0 (540), codec h264
- [x] Representative frames extracted and reviewed
- [x] No clipping / overlap / blank-frame defects
- [x] Video content corresponds to the validated trace
- [x] Real FFmpeg composition succeeds
- [x] Final MP4 playable, full duration preserved
- [x] Artifact lineage intact end-to-end
- [x] Workspace isolation intact; no security guarantee weakened
- [x] Golden path succeeds repeatedly
- [x] Performance baseline recorded

**PHASE 4.1 GATE: PASS**

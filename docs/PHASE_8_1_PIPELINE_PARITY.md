# Phase 8.1 — Pipeline parity and production correctness

**Goal:** make the web/UI generation path run the *same* mechanisms as the
golden path that produced the accepted Phase 6.5.3 video, before any second
visual format is built on top of it.

**Not in this phase:** visual redesign, 16:9 composition, 4K rendering, a
job queue or database, provider failover changes. `visualization/` is
untouched.

Evidence levels used below: **UNIT** (in-process, no tools), **INTEGRATION**
(real FFmpeg, and where stated real Kokoro and Manim), **E2E** (Java → LLM →
MP4 through the web path), **MANUAL**.

---

## 1. Problems discovered

| # | Problem | Where it showed up |
|---|---|---|
| P1 | Web path never fitted the visual timeline to the measured speech | Phase 7.1: 30 steps totalling 67.0 s proposed; rendered as proposed |
| P2 | Web path truncated narration | Phase 7.1: 99.178 s of Kokoro speech muxed into an 88.415 s video; ~10.8 s cut |
| P3 | Final gate failed every run where an LLM repair succeeded | Phase 7.1: `final_validation` failed on one `superseded: true` attempt |
| P4 | No subtitles, no alignment, no media/timeline validation on the web path | 7.1 output had no `alignment.json` / `subtitles.srt` |
| P5 | Concurrent registry writes lost rows / crashed on Windows | Phase 8.0 audit probe: 20 writers → 2 rows + 15 `PermissionError` |
| P6 | A version directory could be reused | Audit probe: a lost index row → next version reused `v1/` |
| P7 | Job start was check-then-act outside the lock | Code reading (race not reproduced) |

## 2. Root causes

- **P1/P2 — one root cause.** After `NarrationNode`, the web path ran
  `RenderVideoNode` → `ComposeMediaNode`, the Phase 4 shortcut.
  `ComposeMediaNode` joined all narration text, synthesised it as **one**
  track, and muxed it with `ffmpeg … -af apad -t <video duration>`. Nothing
  measured speech before rendering, so the video had the LLM's proposed
  length: `total_video_seconds(plan)` = 87.95 s by the shared timing model,
  88.415 s probed. The unplaced 99.178 s track was cut at the video's end by
  `-t`, which the composer applies correctly: the picture is authoritative.
  The defect was upstream. The golden path never hits this because it fits
  the plan to the measured speech **before** rendering.
- **P3.** `FinalValidationNode` counted `not r.passed` over all results,
  including repair attempts `record_history` had marked `superseded`.
  `ValidationSummary.all_passed` already excluded them (ADR-5.6); the final
  gate did not.
- **P4.** Those stages lived only in `scripts/run_phase5_golden_path.py`,
  never in a workflow node.
- **P5.** `GenerationRegistry.save` was an unlocked read-modify-write of
  `index.json`. On Windows, `replace` also fails while another thread has
  the file open.
- **P6.** Version numbers came from the index alone, and
  `mkdir(exist_ok=True)` accepted an existing directory.
- **P7.** `JobManager.start` checked for a running job, released the lock,
  then inserted.

## 3. Fixes (minimum, reusing existing mechanisms)

| Fix | File | What |
|---|---|---|
| New `NarrationTimingNode` (`narration_timing`) | `workflow/nodes.py` | The golden path's timing stages as a node, same functions, same order: per-segment `tts.synthesize(to_spoken(text))` → `fit_plan_to_narration` → `align_narration`. Stores the fitted plan as its own artifact (`timed_visualization_plan`), keeping the LLM's plan intact for audit, plus the alignment (`narration_timing`) |
| `RenderVideoNode` renders the fitted plan when present | `workflow/nodes.py` | One-line preference; Phase 4 callers without timing are unchanged |
| `ComposeMediaNode` timed path | `workflow/nodes.py` | When timing exists: `assemble_narration_track` → `normalize_track` → `MediaComposer.compose` → `write_srt` → `validate_final_video` + `validate_timeline` on **this run's file**, recorded against this run's final artifact; failure raises `VALIDATION`. Without timing: original behaviour |
| `assemble_narration_track` | `media/composer.py` | The golden path's `_concat_audio`, **moved** (not copied) into `src/`. The script now imports it |
| Final gate ignores superseded attempts | `workflow/nodes.py` | Same rule as `ValidationSummary.all_passed` |
| `ArtifactType.SUBTITLES` | `artifacts/models.py` | Additive enum value so the SRT is a recorded, linkable artifact |
| Web factory wiring | `webapp/pipeline.py` | Inserts `NarrationTimingNode`; one TTS instance serves timing and compose; `MediaPolicy` from the profile's pixels |
| Stage list | `generation/manager.py` | `narration_timing: "Speech timing"` (kept equal to the factory's node order by test) |
| `PIPELINE_VERSION` 7.0 → 8.1 | `generation/fingerprint.py` | Required by the constant's own rule (a node was added). Consequence: 7.0 videos, all muxed by the truncating path, are no longer served as "you already have this" |
| Registry locking | `generation/registry.py` | One `RLock` per library root around read-modify-write and version allocation; bounded retry (20 × 20 ms) on Windows `PermissionError` during replace |
| Version directories never reused | `generation/registry.py` | Next number = max(index, `v*` dirs on disk) + 1; `mkdir(exist_ok=False)` |
| Atomic job start | `generation/jobs.py` | Check and insert under one lock hold |
| Golden script | `scripts/run_phase5_golden_path.py` | Uses the shared `assemble_narration_track`; resolution named as `VERTICAL_HD.resolution` (same value) |

No new dependency. No queue, no database.

## 4. Golden path vs web path

| Stage | Golden path (`run_phase5_golden_path.py`) | Web path before 8.1 | Web path after 8.1 | Same now? |
|---|---|---|---|---|
| Compile, trace | `CompileNode`, `TraceNode` | same | same | Yes |
| Explain → educational plan → viz plan → narration | the four LLM nodes, `max_repair_attempts=2`, timeout 180 s | same nodes, default 1 repair, `Settings` timeout (30 s) | unchanged | **Nodes yes; repair/timeout config differs** (see §11) |
| TTS | per segment, `to_spoken(text)` | one call, raw joined text | per segment, `to_spoken(text)` (`NarrationTimingNode`) | Yes |
| Timeline fit | `fit_plan_to_narration` before render | none | same function, before render | Yes |
| Alignment | `align_narration` | none | same function | Yes |
| Teaching-sync / content / distinct checks | printed, **not gating** | none | not run | Equivalent (neither gates) |
| Subtitles | `write_srt` | none | `write_srt` | Yes |
| Render | `ManimVideoRenderer(1080x1920)` on the fitted plan | on the unfitted plan | `build_renderer(profile)` on the fitted plan | Yes (same args for VERTICAL_HD) |
| Audio track | `_concat_audio` (adelay + amix) + `normalize_track` | single raw track | `assemble_narration_track` (the same code, moved) + `normalize_track` | Yes |
| Mux | `MediaComposer.compose(…, video duration)`, timeout 300 s | same call, default timeout 120 s | same call, default timeout 120 s | **Timeout differs** |
| Final-video validation | `validate_final_video` (gating) | none | same function (gating) | Yes |
| Timeline validation | `validate_timeline` (gating) | none | same function (gating) | Yes |
| Audio forensics | `measure_audio` + `validate_audio_experience` (gating) | none | not run | **No** (§11) |
| Final gate | none (script checks) | `FinalValidationNode` (superseded bug) | `FinalValidationNode` (fixed) | n/a |
| Registry / playback | none | registry + `/media` | unchanged | n/a |

## 5. Timeline behaviour

- The visual timeline is still authoritative (rule 4). Fitting is
  **widen-only** (rule 10): a step shorter than its speech plus
  `BREATH_SECONDS` is lengthened; a longer step is kept, since pacing is
  content.
- The rendered length is `visualization.timing.total_video_seconds(fitted
  plan)`: title fade + hold, per-step fades, step durations, closing fade.
  The aligner and the renderer read the same model.
- The model's proposal and the rendered plan are separate artifacts
  (`visualization_plan`, `timed_visualization_plan`).

## 6. Audio behaviour

- Each segment is placed at its step window's start
  (`adelay`), summed without normalisation (`amix normalize=0`, segments
  never overlap), and padded to `alignment.total_duration_seconds`.
- The composer's `-af apad -t <video duration>` is **unchanged**. After
  fitting, every segment ends inside the video, so `-t` removes nothing but
  padding. `validate_timeline` fails the run if any cue would end beyond the
  produced video or any segment overflowed its step. Truncation is now
  detected, not silent.
- Kokoro, voice, speed and narration generation are unchanged.

## 7. Validation / version behaviour

- Media and timeline results carry `subject_artifact_id` = this run's
  final-video artifact, and are recorded in this run's state only.
- A superseded repair attempt stays in `results` as audit evidence and no
  longer fails the gate. A standing failure still does.
- A failed v2 doesn't change v1's status or files, and `current_version`
  keeps returning v1.

## 8. Concurrency behaviour

- Single process: concurrent `create_version` / `save` on one library root
  are serialised. 20 concurrent writers → 20 rows. 4 concurrent forced
  generations → v1–v4, each completed with its own file.
- `JobManager.start` for the same program returns the existing job
  (8 simultaneous starts → 1 job).
- **Not addressed:** multiple processes or workers on one library (the lock
  is in-process), restart reconciliation of `running` rows, cancellation.

## 9. Unsupported format behaviour

Unchanged from Phase 8.0, now also tested through the job manager:
`LANDSCAPE_HD`, `VERTICAL_4K` and `LANDSCAPE_4K` raise
`UnsupportedVideoProfileError` in `GenerationManager.generate` **before** a
version row, a directory or a pipeline exists. A job fails with the reason
("… the 16:9 composition does not exist yet (Phase 8.2)"). Nothing renders a
portrait layout into a landscape frame.

## 10. Tests

`tests/test_pipeline_parity.py`, 20 tests (UNIT unless marked):

| Area | Tests |
|---|---|
| A. Timeline | stage order (timing before render); stage labels = factory order; **INTEGRATION (FFmpeg)**: every rendered step holds its speech and the fit acted; final video length = `total_video_seconds(fitted)` |
| B. Audio | **INTEGRATION**: final audio reaches the end of the last placed segment; media + timeline results recorded against this run's final artifact, SRT present |
| C. Validation | superseded attempt doesn't fail the gate; standing failure still fails |
| D. Ownership | v2 fails its own validation; v1 stays completed, current and playable |
| E. Duplicate | same request (and `video_format="vertical_hd"` spelled out) reuses v1; one build |
| F. Force | v2 added; v1's bytes unchanged; separate directories |
| Concurrency | 4 concurrent forced generations → v1–v4; 20 concurrent writers → 20 rows; lost index row → no directory reuse; 8 simultaneous job starts → 1 job |
| G. Unsupported | 3 profiles: no build, no version, no directory; job fails cleanly with reason |
| H. Default | no format → VERTICAL_HD; compose policy 1080 × 1920 |

Before the fix, 10 of these 20 failed. That was the reproduction, run before
any source change. After it, all 20 pass. The INTEGRATION tests use a real
black 1080 × 1920 H.264 file of the timing model's length in place of Manim,
and real FFmpeg/PyAV for everything else.

## 11. Remaining limitations

- **Real E2E not verified.** `mvn` isn't on PATH on this machine (the User
  PATH entry `I:\Software\apache-maven-3.9.15-…` doesn't exist), so Compile
  and Trace can't run, and no web-initiated Java → LLM → MP4 run was
  possible.
- **What WAS run for real (INTEGRATION, not E2E):** the recorded Phase 7.1
  LLM outputs (`visualization_plan.json`, `narration.json`, `trace.json`)
  replayed through `NarrationTimingNode` → `RenderVideoNode` →
  `ComposeMediaNode` → `FinalValidationNode`. Real Kokoro `af_heart`, real
  Manim, real FFmpeg, into `output/phase8_1/replay_7_1/`.

  | | Phase 7.1 web path | Phase 8.1 web path, same LLM output |
  |---|---|---|
  | Steps widened by the fit | 0 (no fit) | 20 of 30 |
  | Final video | 88.415 s | 117.315 s, 1080 × 1920 h264 |
  | Final audio | 88.415 s, speech cut | 117.315 s AAC 44.1 kHz |
  | Last speech ends | beyond the video | 116.788 s (inside it; RMS 0.139 in its window) |
  | Overflowed segments | n/a | 0 |
  | Subtitles | none | `subtitles.srt`, 30 cues |
  | Validation | failed (superseded bug) | media ✓, timeline ✓, final gate ✓ |

  Per-segment spoken-form speech totals 87.96 s against the 99.18 s single
  raw-text track of 7.1. The segments are synthesised separately and
  edge-trimmed, as in the golden path.
- **Audio-experience forensics** (`validate_audio_experience`: silent tail,
  internal gaps, loudness) still gate only the golden path. Porting it needs
  `_designed_silences` and the budget arithmetic moved into `src/`: a
  separate, small change.
- **LLM repair/timeout configuration** differs: the golden path uses
  `max_repair_attempts=2` and 180 s; the web path uses node defaults and
  `Settings.ai_timeout_seconds`. Left unchanged because provider
  configuration is out of scope.
- **Composer timeout** differs: 300 s golden vs 120 s default.
- Other scripts (`run_golden_path.py`, `run_narrated_golden_path.py`,
  `run_phase6_golden_path.py`, `measure_performance.py`) and
  `tests/test_e2e_golden_path.py` keep explicit `"1080x1920"` and their own
  `_concat_audio` copies. They reproduce historical phases and are left
  as-is.
- Cross-process registry safety, restart reconciliation, cancellation, and
  serving failed versions' MP4s: unchanged (see
  `docs/CURRENT-STATE-AUDIT.md`).
- The legacy single-track `ComposeMediaNode` path still exists for Phase 4
  callers and still truncates if speech outlasts the video. The web path no
  longer uses it.

# Code2Shorts Current State Audit

- **Date:** 2026-09-28
- **Branch:** `feature/phase-7` at `93c061a`, **with uncommitted Phase 6.5.4/7.x work in the tree** (see §3)
- **Mode:** audit only. No source, test, config or dependency was changed. The only file written to the repository is this one. All experiments ran against copies in a temporary scratch directory.

Classification vocabulary used throughout:

| Label | Meaning |
|---|---|
| **VERIFIED** | Observed working in this audit (command output, probed media, live route) |
| **PARTIALLY VERIFIED** | Some of the claim was observed; the rest was not, or was observed failing |
| **NOT VERIFIED** | Could not be observed in this audit; no claim is made either way |
| **NOT IMPLEMENTED** | Code for it doesn't exist, or exists but is never called |
| **PLANNED ONLY** | Appears in docs or comments only |

Test-evidence levels: UNIT (in-process, fakes), INTEGRATION (real external tool), E2E (Java source to final MP4), MANUAL (an artifact on disk from an earlier session), NONE.

---

## 1. Executive Summary

**Code2Shorts has a real, working trace-to-video engine. That engine has produced a correct, narrated, subtitled 1080×1920 palindrome video through its golden-path script (`output/phase6_5_3/palindrome`). The Phase 7 web UI does not run that engine. It runs an older, weaker node chain. The only real UI-driven generation on record (Phase 7.1, 2026-09-05) ended `FAILED`.**

Evidence-backed headline findings:

1. **The UI pipeline and the verified pipeline are two different pipelines.** `scripts/run_phase5_golden_path.py` runs the workflow nodes only up to `NarrationNode`. It then does per-segment TTS with `to_spoken()`, `fit_plan_to_narration` (before rendering), `align_narration`, teaching-sync validation, `write_srt`, audio concatenation and `validate_timeline` **inside the script**. The UI (`webapp/pipeline.py`) instead uses `RenderVideoNode` and `ComposeMediaNode`. Those nodes make one TTS call over the concatenated raw narration text, never fit the timeline, never align, write no subtitles, and compose with `ffmpeg -t <video_duration>`. No workflow node calls `align_narration`, `fit_plan_to_narration`, `write_srt`, `validate_timeline` or `validate_final_video` (grep, §22).
2. **Real consequence, measured:** in the Phase 7.1 UI run, Kokoro produced **99.178 s** of narration and the final MP4 is **88.415 s**. About **10.8 s of speech was silently cut**, which breaks CLAUDE.md rules 4, 5 and 10. The golden-path output (6.5.3) fits: 129.14 s of narration in a 129.78 s video.
3. **`FinalValidationNode` counts superseded repair attempts as failures.** `workflow/nodes.py:972` filters `not r.passed` without checking `r.superseded`. This contradicts ADR-5.6, rule 11 and `ValidationSummary.all_passed`. It is **the recorded cause of the Phase 7.1 failure**: `validation_probe.json` shows one `superseded: true` semantic failure and then `"1 earlier validation result(s) did not pass"`. Any run in which LLM repair works will fail final validation.
4. **Phase 7.0/7.1 is uncommitted.** `generation/`, `webapp/`, `ai/providers/failover.py` and four test files are untracked, and eleven files have uncommitted changes. The harness that produced `output/phase7_1/e2e_report.json` isn't in the repository, so the 7.1 run can't be reproduced from source.
5. **The registry and job system are single-process, memory-bound, and unsafe under concurrency or restart** (all reproduced in scratch, §6–7). 20 concurrent writers left 2 rows plus 15 Windows `PermissionError`s. A corrupt index followed by one write wipes the history and **reuses the existing `v1` directory**. A `running` status survives a restart forever. Cancel is accepted and ignored.
6. **Test suite (this machine, today):** 949 collected. Full run: **872 passed, 51 failed, 26 skipped**. 48 failures are the environment (Maven isn't on PATH). 3 are real, pre-existing test/code disagreements. Unit-only: **804 passed, 0 failed, 28 skipped, 117 deselected**. CLAUDE.md's "465 tests / 354 unit" is stale.
7. **Nothing is deployable today.** There's no Dockerfile, no CI workflow, no deploy manifest, no auth, and no persistent job store. Program source is read from `tests/fixtures/`, the Kokoro weights come from a CWD-relative `models/` path, and the TTS fallback chain assumes Windows.

**Verdict: DEVELOPMENT READY** (§23). The engine is integration-ready through its script, but the product path (UI to MP4) has never produced a passing video.

**Recommended next phase:** **Phase 7.2 — Pipeline Parity: make the UI run the verified pipeline** (§20). This is not a deployment, queue or storage phase. Those gaps are real, but they sit behind a product path that doesn't yet produce a correct video.

---

## 2. Actual Architecture

### 2.1 Repository inventory

| Area | Path | Status | Notes |
|---|---|---|---|
| Domain models | `src/code2shorts/core/` | ACTIVE | `ExecutionTrace`, `ValidationResult` (with `superseded`) |
| Subprocess seam | `execution/sandbox.py` | ACTIVE | Only `subprocess.Popen`/`run` sites in `src/`; no `shell=True` found |
| Java adapter | `langadapter/java/` + `resources/Code2ShortsTrace.java` | ACTIVE | Needs `mvn` on PATH |
| Instrumenter | `tools/java-instrumenter/` (Maven, JavaParser) | ACTIVE | Built jar present in `target/` |
| Workflow | `workflow/` (`nodes.py` 986 lines, runner, retry, checkpoint) | ACTIVE | 9 nodes |
| AI layer | `ai/` (contracts, education, grounding, repair, structured, providers/) | ACTIVE | |
| LLM providers | `ai/providers/{factory,gemini,openai_compatible,mock,failover}.py` | ACTIVE (`failover.py` **untracked**) | |
| Legacy LLM seam | `llm/provider.py` | ACTIVE (ABC) | |
| Visualization | `visualization/` (primitives 929 lines, state, code_state, manim_renderer, timing, validation) | ACTIVE | |
| Narration/TTS | `narration/` (tts, tts_kokoro, phonemes, alignment, fitting, subtitles, speech_text, audio) | ACTIVE, but **alignment/fitting/subtitles/speech_text are reachable only from scripts and tests** | |
| Media | `media/` (composer, probe, validation, audio_forensics) | ACTIVE; `validation.py` is script/test-only | |
| Artifacts | `artifacts/` (in-memory store) | ACTIVE | No persistent store implementation |
| Generation (Phase 7) | `generation/{fingerprint,registry,manager,jobs}.py` | ACTIVE, **untracked** | |
| Web UI (Phase 7) | `webapp/` (FastAPI, Jinja, 9 templates, 1 JS, 1 CSS) | ACTIVE, **untracked** | |
| Codegen | `codegen/` | Minimal (41+20 lines) | Interface only |
| CLI | `cli.py` | ACTIVE: `version`, `serve` only | Docstring says `generate` "lands in Phase 1+". It doesn't exist |
| Placeholders | `animation/ compose/ lessonplan/ qa/ render/ tts/` | DEAD (2-line `__init__`) | As CLAUDE.md states |
| Scripts | `scripts/run_*golden_path.py`, `measure_performance.py`, `production_config_check.py` | ACTIVE; they are the **real** pipeline for Phase 5/6 | They import `tests.java_fixtures` |
| Tests | `tests/` (48 test modules, Java fixtures) | ACTIVE | |
| Docs | `ARCHITECTURE_DECISIONS.md` (100 KB), `SECURITY_SANDBOX.md`, `README.md`, `docs/` (23 files) | Mixed; README is stale (§16) | No Phase 6.2–6.5.x docs; no Phase 7 ADR |
| Config | `.env.example`, `.env.production.example`, `.env.local` (gitignored, not tracked) | ACTIVE | |
| CI/CD | `.github/modernize/java-upgrade/hooks/*` only | **NOT IMPLEMENTED** | No `.github/workflows` |
| Deployment | — | **NOT IMPLEMENTED** | No Dockerfile, compose, `render.yaml`, `vercel.json`, Procfile |
| Output | `output/` (1.3 GB, gitignored), `media/` (25 MB Manim cache at root), `models/` (338 MB Kokoro weights) | Local disk only | `Reverse_String.mp4` is committed at the root |
| Build metadata | `src/code2shorts.egg-info/` | Generated | Gitignored |

### 2.2 Reconstructed architecture (as it actually runs)

```
                                   Code2Shorts
                                        |
        ┌───────────────────────────────┼─────────────────────────────────┐
        |                               |                                 |
       UI (untracked)                 Core                            Pipeline
        |                               |                                 |
 webapp/app.py (FastAPI)       core/models.py                 ┌───────────┴──────────────┐
 Jinja templates, job.js       ExecutionTrace (canonical)     |                          |
        |                               |                PATH A: UI                PATH B: golden script
 JobManager (thread, memory)   langadapter/java/          generation/manager.py     scripts/run_phase5_golden_path.py
        |                      compile→junit→instrument          |                          |
 GenerationManager             →execute→trace             Compile,Trace,Explain,     same 6 nodes up to NarrationNode
  fingerprint→registry                  |                 EduPlan,VizPlan,Narration        |
  (index.json on disk)          visualization/state.py           |                   per-segment TTS (to_spoken)
        |                      (replay ARRAY_WRITE)        RenderVideoNode           fit_plan_to_narration  ← BEFORE render
   /media/... FileResponse              |                  (no fitting)              ManimVideoRenderer
        |                      code_state.py                     |                   align_narration + sync checks
   <video> playback            (file/line/window)          ComposeMediaNode          write_srt
                                        |                  one TTS call, raw text    concat per-segment audio
                               VisualizationPlan           ffmpeg -t video_dur       MediaComposer
                               (validated vs trace)        ⇒ TRUNCATES long audio    validate_timeline / validate_final_video
                                        |                        |                          |
                                 manim_renderer.py         FinalValidationNode              ↓
                                  build_scene_source       (counts superseded ⇒ FAIL)   final.mp4 + subtitles.srt
                                        |                        |                   (6.5.3: 129.8 s, audio fits)
                                        ↓                        ↓
                                       MP4               final.mp4 left on disk, version FAILED
                                                         (7.1: 88.4 s video, 99.2 s speech)
```

### 2.3 Component table

| Component | File | Responsibility | Caller | Input → Output | Tests | Status |
|---|---|---|---|---|---|---|
| Routes | `webapp/app.py` | Pages, JSON API, media | uvicorn via `cli.serve` | HTTP → HTML/JSON/files | `test_webapp.py`, `test_phase71_safety.py` (UNIT, fake pipeline) | VERIFIED in-process (§5) |
| Pipeline wiring | `webapp/pipeline.py` | Choose LLM/TTS/renderer | `create_app` | Settings → node list | UNIT only | Wires the **weaker** Path A |
| Catalog | `webapp/catalog.py` | Programs offered (palindrome only) | routes | slug → source from `tests/fixtures/...` | UNIT | ACTIVE; `lru_cache` makes source stale until restart |
| JobManager | `generation/jobs.py` | Background thread per job, event projection | routes | request → `Job` (memory) | UNIT | PARTIAL (§7) |
| GenerationManager | `generation/manager.py` | Decide/reuse/run/record | JobManager | request → `GenerationVersion` | UNIT (fake nodes) | PARTIAL (§6) |
| Registry | `generation/registry.py` | `index.json` + version dirs | manager, routes | versions ↔ JSON file | UNIT | PARTIAL, unsafe concurrently (§6) |
| Fingerprint | `generation/fingerprint.py` | Request/content identity | manager | inputs → sha256 | UNIT | PARTIAL, missing fields (§6) |
| Workflow runner | `workflow/runner.py` | Sequential nodes, retry, events | manager, scripts | state → result | UNIT | VERIFIED (unit) |
| Compile/Trace nodes | `workflow/nodes.py` + `langadapter/java` | Maven, JUnit, instrument, JVM | runner | source → `ExecutionTrace` | INTEGRATION (fail here: no mvn) | MANUAL evidence 7.1 (`trace.json`) |
| LLM nodes | `nodes.py` Explain/EduPlan/VizPlan/Narration | Structured generation + validation + repair | runner | trace → plans | UNIT + live Gemini ping | 7.1: all four completed with Gemini |
| RenderVideoNode | `nodes.py:777` | Manim render | runner (Path A) | plan → MP4 | `test_real_render.py` (fail: no mvn) | 7.1: completed |
| ComposeMediaNode | `nodes.py:876` | One TTS + ffmpeg | runner (Path A) | narration text + MP4 → final.mp4 | UNIT with fakes | 7.1: completed but **truncated audio** |
| FinalValidationNode | `nodes.py:952` | Last gate | runner | state → pass/fail | UNIT | **Defective** (superseded) |
| Composer | `media/composer.py` | Fixed-arg ffmpeg, `-af apad -t` | both paths | video+audio → MP4 | UNIT + INTEGRATION | VERIFIED |
| Alignment/fitting/SRT/timeline | `narration/*`, `media/validation.py` | Timeline truth | **scripts and tests only** | — | UNIT + INTEGRATION | NOT on the UI path |

---

## 3. Current Phase Status

| Phase | Claimed | Actually implemented | Verified | Status |
|---|---|---|---|---|
| 0–3 | Scaffold, Java compile/test/execute, trace, workflow/AI | Yes | Integration tests fail **today** only because `mvn` is absent (48 × `MavenNotFoundError`). 7.1 run produced a real `trace.json` on 2026-09-05 | PARTIALLY VERIFIED (env) |
| 4.x | Manim render, code sync, narration/alignment/SRT, media validation, line mapping | Yes, in code | Unit green. Real-render/E2E gates skipped/failed for environment reasons. `output/phase4*`/`golden` artifacts exist | PARTIALLY VERIFIED |
| 5.x | Real LLM, structured output, repair, fitting, config, education | Yes | Unit green. Live Gemini "pong" passed today. Failover: unit only. OmniRoute/xAI live tests skipped | PARTIALLY VERIFIED |
| 6.x (6.1–6.4) | Collections, readable layout, Kokoro, sync | Yes | Kokoro unit tests pass with real weights (`test_neural_tts.py`). No 6.2–6.4 docs | PARTIALLY VERIFIED |
| 6.5.1 / 6.5.3 | Custom code panel, fixed viewport | Committed (`dd03e33`, `93c061a`) | `test_code_viewport.py`/`test_code_layout.py` pass (including a real Manim geometry test). `output/phase6_5_3/palindrome/final.mp4` probed: 1080×1920, 129.78 s, audio 129.78 s, SRT present | VERIFIED via golden script (MANUAL + probe) |
| 6.5.4 | Automatic provider failover | `failover.py` + factory/config edits, **uncommitted**. ADR-6.9 present in the uncommitted `ARCHITECTURE_DECISIONS.md` diff | UNIT only (`test_llm_failover.py`, fakes). No live chain run | PARTIALLY VERIFIED |
| 7.0 | UI, registry, fingerprints, manager, jobs, versioning | Yes, **all untracked** | Routes VERIFIED in-process today. Screenshots exist in `output/phase7_0/screenshots` (MANUAL) | PARTIALLY VERIFIED |
| 7.1 | Real UI → real palindrome → real MP4; duplicate/force/failure safety; quality | One real run recorded | `e2e_report.json`: `"1_first_generation": false`, `final_validation: failed`. Only scenario 6 ("page load does no generation") passed. Harness isn't in the repo | **FAILED** |

---

## 4. Real End-to-End Palindrome Status

Question: can a user open the UI, select Palindrome, click Generate, and get a correct, playable MP4 with no mocks?

**Answer: no.** The one real attempt (2026-09-05, 270.8 s) produced an MP4, but the pipeline itself rejected the run, and the MP4 has truncated narration.

| # | Stage | Result | Evidence |
|---|---|---|---|
| 1 | User opens UI | **PARTIALLY VERIFIED** | Every GET route returned 200/303/404 as designed in-process today (§5). Not driven in a browser this audit; 7.0 screenshots exist |
| 2 | Select Palindrome | **VERIFIED** | Catalog has exactly `palindrome`; `/create` renders |
| 3 | Click Generate | **PARTIALLY VERIFIED** | `/create/review` verified today (decision only). `/create/start` not invoked this audit (it would start a real render). 7.1 report shows a v1 was started |
| 4 | Real Java execution | **PARTIALLY VERIFIED** | 7.1 `compile: completed`, `compilation.json`. **Cannot run today**: no Maven |
| 5 | Real trace | **PARTIALLY VERIFIED** | 7.1 `trace.json`, trace hash recorded |
| 6 | Real LLM | **PARTIALLY VERIFIED** | Fingerprint `llm_provider: gemini`; explain/plan/narration completed. Model name isn't recorded in the fingerprint. Live Gemini ping passed today |
| 7 | Real educational plan | **PARTIALLY VERIFIED** | `educational_plan.json` present |
| 8 | Real narration | **PARTIALLY VERIFIED** | `narration.json`: 30 segments |
| 9 | Real Kokoro | **VERIFIED** (artifact) | `audio.json`: `provider: kokoro-onnx`, `producer: kokoro:af_heart`, 24 kHz, `neural: true` |
| 10 | Real alignment | **FAIL / NOT IMPLEMENTED on UI path** | No node calls `align_narration`. No `alignment.json` in 7.1 output |
| 11 | Real timeline (fit before render) | **FAIL / NOT IMPLEMENTED on UI path** | No node calls `fit_plan_to_narration`. Speech 99.18 s > video 88.42 s |
| 12 | Real Manim | **VERIFIED** (artifact) | `render/.../1920p30/output.mp4` probed: 1080×1920, 30 fps, 88.415 s, h264 |
| 13 | Real FFmpeg | **VERIFIED** (artifact), **incorrect result** | `final.mp4` probed: 88.415 s video + 88.415 s AAC. Source WAV 99.178 s ⇒ ~10.8 s cut by `-t` |
| 14 | Real MP4 | **FAIL** | MP4 exists but the version is `failed`: `"pipeline failed at final_validation"` |
| 15 | Registry | **PARTIALLY VERIFIED** | `index.json` records v1 `failed` with both fingerprints and artifacts |
| 16 | UI playback | **PARTIALLY VERIFIED** | `/videos/palindrome/v1` renders `<video>`; `/media/.../final_video` returns 206 with Range. It plays the **rejected** video and offers Export. `/videos/palindrome` redirects to Create because no completed version exists |

Duplicate protection, force regeneration and failure safety in a real run: **NOT VERIFIED.** The 7.1 report contains no such scenarios beyond #6. Logic is unit-tested and was re-probed in scratch (§6).

Visual quality of the 7.1 MP4 (frames in `output/phase7_1/frames`, inspected):
- Code panel, syntax colouring, line numbers, active-line highlight and pointer labels render correctly (frames D, F).
- **The panel's size and position differ between frames.** Opening frame: panel ≈ y 985–1620, smaller font, and the long signature line is shrunk further. Later frames: ≈ y 1000–1790, larger font. That conflicts with a strict "fixed viewport" reading of 6.5.3. **NOT VERIFIED** whether this is intended (intro layout versus step layout).
- The end frame shows `right`/`left` labels stacked when the pointers coincide (legible, not overlapping).
- **Periodic near-black frames**: 98 of 885 sampled frames have a mean luma below 6, in 30 runs of 0.2–0.4 s roughly every 2.7 s. The accepted 6.5.3 golden output has the same count (98/1298), so this is existing renderer behaviour (per-step fade), not a 7.x regression. Whether it's desirable is a product question, **NOT VERIFIED**.

---

## 5. UI Audit

Method: the real `create_app()` routes were driven with FastAPI's `TestClient`, against a **copy** of the real Phase 7.1 library and with a pipeline factory that refuses to build (so nothing could generate). No browser session was run in this audit. Visual/responsive/accessibility items rely on source and the 7.0 screenshots.

| Item | Status | Evidence / gap |
|---|---|---|
| Home | IMPLEMENTED, VERIFIED | 200. Shows "Palindrome **not generated**" although a failed v1 exists, so failures are invisible from Home |
| Videos (+ status filter) | IMPLEMENTED, VERIFIED | 200; `?status=failed` filters on `current`, which is never a failed version, so a failed filter can't show anything |
| Create | IMPLEMENTED, VERIFIED | 200; unknown program returns 404 |
| Review (decision gate) | IMPLEMENTED, VERIFIED | Shows program, input, config, provider, the 9 stage labels, and the decision |
| Generation status (job page + poller) | IMPLEMENTED; NOT VERIFIED live | `/api/jobs/x` returns 404. Stage state comes from real events (unit). Jobs vanish on restart (§7) |
| Video playback | PARTIALLY IMPLEMENTED | Range/206 works. `Content-Disposition: attachment` is sent on the inline player source too. **Plays and exports FAILED versions** |
| Artifact viewer | PARTIALLY IMPLEMENTED | Links to raw JSON downloads; no viewer. `audio.json`, `final_video.json` and `rendered_video.json` expose **absolute server paths** (`D:\WORK_SPACE\...`) |
| Re-generation | IMPLEMENTED; NOT VERIFIED live | Hidden-field form with `force=1` |
| Duplicate protection | IMPLEMENTED (unit) | Enforced in `GenerationManager.generate` and `/create/start`. Fingerprint gaps in §6 |
| Versioning | IMPLEMENTED; defects in §6 | |
| Error handling | PARTIAL | Failed version shows the error string. Unknown slug/version returns FastAPI's **JSON** 404, not an HTML page |
| Loading / empty / failed states | PARTIAL | Empty states exist in templates. No distinct "interrupted/orphaned" state |
| Cancellation | **MOCKED** in effect | `/api/jobs/{id}/cancel` returns `{"cancelled": true}` but nothing reads `cancel_requested` (§7) |
| Responsive | NOT VERIFIED | CSS only; no viewport test |
| Accessibility basics | NOT VERIFIED | Tabs are CSS radio inputs; not assessed with tooling |
| Security | See §13 | Traversal blocked (4 variants, all 404); XSS probe not echoed; no auth; no CSRF; `/api/docs` and `/openapi.json` public |

---

## 6. Generation Registry Audit

Files: `generation/fingerprint.py`, `registry.py`, `manager.py`.

### 6.1 Fingerprint correctness

| Scenario | Expected | Actual | Status |
|---|---|---|---|
| Same input | Same fingerprint, reuse | Canonical sorted-JSON sha256; `find_reusable` requires COMPLETED + `final_video` | VERIFIED (unit) |
| Changed Java source | New fingerprint | `hash_source_files` includes paths. **But** `catalog.source_for` is `lru_cache`d, so an edit to the fixture during server life isn't seen until restart | PARTIAL |
| Changed input value | New fingerprint | **`input_value` isn't in `RequestFingerprint`** (probe: `"input" in fields → False`). Harmless today (input is fixed per catalog entry); wrong as soon as input is configurable | GAP |
| Changed entry point | New fingerprint | Not included | GAP (low today) |
| Changed TTS voice | New fingerprint | `config.voice` included | VERIFIED (unit) |
| Changed TTS engine actually used | New fingerprint | Fingerprint records the **requested** `tts_provider="kokoro"`. `build_tts` silently falls back to SAPI or Synthetic, so a SAPI-voiced video would be recorded, and reused, as Kokoro. The `pipeline.py` docstring claims the opposite | GAP (contradiction) |
| Changed LLM model | New fingerprint | Only the provider **name**; model absent. With `failover`, the answering provider is absent too | GAP |
| Changed renderer | New fingerprint | Manual `RENDERER_VERSION="6.5.3"`, unrelated to `manim_renderer.RENDERER_VERSION="code2shorts-manim-renderer-1.1"`. Nothing enforces a bump | PARTIAL (by design; unenforced) |
| Changed pipeline | New fingerprint | Manual `PIPELINE_VERSION="7.0"` | PARTIAL |
| Explicit regenerate | New version | `force=True` skips the decision and `create_version` allocates the next number | VERIFIED (unit) |

### 6.2 Reliability experiments (scratch copies, real classes, Windows)

| Experiment | Result |
|---|---|
| 20 threads × `create_version` (different algorithms) | **2 rows survived; 15 `PermissionError` (WinError 5) from `os.replace`.** Read-modify-write has no lock, and on Windows `replace` fails while another handle is open |
| 10 threads × `create_version` (same algorithm) | Index ends with **only `[1]`**: lost updates, duplicate version numbers |
| Corrupt `index.json`, then one write | History reset to `[v1]` while `v1/ v2/ v3/` exist on disk. `all_versions()` swallows `JSONDecodeError` → `[]`, and the next save **rewrites the index from nothing and reuses `v1/`** (`mkdir(exist_ok=True)`). This violates "a new version is always a new directory" |
| `running` version, then "restart" (new registry instance) | Still `running`. No reconciliation code exists |

### 6.3 Other findings

- **Partial artifacts:** a failed run keeps `final.mp4` (7.1: 11 MB), and the UI plays and exports it.
- **Crash path:** in `manager.generate`'s `except` branch, `duration_seconds` and `content_fingerprint` are not recorded. `registry.save` after the run is outside the `try`, so a save failure (see the `PermissionError` above) propagates, leaving the version `running` and the job `failed`.
- **Stale reads:** every page load re-reads and re-parses the whole index. That's acceptable at tens of rows, but a reader holding the file is exactly what triggers the Windows `replace` failure.
- **Absolute paths in the version dir:** the stage JSONs are written verbatim, including absolute paths, so the library isn't relocatable despite the "relative paths" design note.

**Verdict:** duplicate prevention is **reliable only for a single process with one writer at a time**, with the fingerprint gaps above. It is **not** reliable under concurrency, index corruption or restart.

---

## 7. Job System Audit

`generation/jobs.py`, traced line by line.

| Question | Answer | Evidence |
|---|---|---|
| In-process or external | **In-process**, one `threading.Thread(daemon=True)` per job | `jobs.py:156` |
| Persistent | **Memory only** (`self._jobs: dict`) | `jobs.py:98` |
| Survives restart / deploy | **No.** Job IDs 404 afterwards; the registry version stays `running` | §6.2 |
| Concurrent jobs | Yes for different algorithms (one catalog entry today). Same algorithm: deduped by `job_for_algorithm`. The check is outside the lock (TOCTOU in code), but 8 concurrent `start()` calls produced **1 job** in the probe (not reproduced) | probe #4 |
| Cancellation | **NOT IMPLEMENTED.** `cancel_requested` is written at `jobs.py:180` and read nowhere in `src/` (grep). Probe: cancel accepted, all stages ran anyway | probe #5 |
| Retry | Node-level only (`WorkflowRunner` retry on TRANSIENT). No job-level retry | |
| Recovery / resume | **NOT IMPLEMENTED.** `WorkflowRunner.run(start_index=…)` exists, but nothing uses it; the artifact store is in-memory, so there's nothing to resume from | |
| Orphan detection | **NOT IMPLEMENTED** | |
| Job history | Memory only, last-N from the dict; lost on restart. The registry keeps version history, not job logs | |
| Multiple workers | **No.** Two uvicorn workers would each have their own `JobManager` and race on the same `index.json` (§6.2) | |
| Log retention | 500-line in-memory deque; never written to disk | |

**What happens if the process dies during rendering:** the daemon thread dies with the process. The Manim/FFmpeg child is spawned via `run_subprocess` with `start_new_session=True` on POSIX, so on POSIX it may **outlive** the parent until its own timeout (up to 900 s for Manim). On Windows, NOT VERIFIED. The registry row stays `running` forever, the version directory holds a partial render, and the job record is gone. On restart the UI shows no job and a `running` version that will never finish. Nothing detects or cleans it.

---

## 8. Storage Audit

| Data | Where | Class | Survives Render restart | New deploy | Shared by 2 workers | Vercel | Persistent worker |
|---|---|---|---|---|---|---|---|
| Source code (programs) | `tests/fixtures/java/algorithms/*` read at runtime | GIT (via local disk) | Yes (in image) | Yes | Yes (read-only) | Would need bundling | Yes |
| Trace | In-memory `ArtifactStore`; `trace.json` copy in version dir | MEMORY + LOCAL DISK | Only with a persistent disk | Only with a persistent disk | Only on a shared volume | No | Yes, with a disk |
| Educational plan / viz plan / narration / explanation | Same | MEMORY + LOCAL DISK | Same | Same | Same | No | Same |
| Audio (`narration.wav`) | `<version>/<exec_id>/compose/` | LOCAL DISK | Only with a persistent disk | Same | Same | No | Same |
| Subtitles | **Not produced on the UI path** | — | — | — | — | — | — |
| Timeline / alignment | **Not produced on the UI path** | — | — | — | — | — | — |
| Rendered frames / Manim cache | `<version>/<exec_id>/render/media/...` (partial movie files, SVG text cache) | LOCAL DISK | Same | Same | Same | No | Same |
| Final MP4 | `<version>/<exec_id>/compose/final.mp4` | LOCAL DISK | Same | Same | Same | No | Same |
| Registry | `output/library/index.json` (path relative to CWD) | LOCAL DISK (JSON) | Same | Same | **No** (races, §6.2) | No | Single process only |
| Job state | `JobManager._jobs` | MEMORY | **No** | **No** | **No** | No | Lost on restart |
| Metadata / logs | Job logs in memory; Python `logging` to stderr | MEMORY | No | No | No | — | — |
| Kokoro weights (338 MB) | `models/kokoro/` (CWD-relative), gitignored | LOCAL DISK | Only if baked in or on a disk | Must be provisioned | Read-only OK | Too large | Yes |

No database and no object storage are implemented anywhere.

---

## 9. LLM Provider Audit

| Item | Status | Evidence |
|---|---|---|
| Provider abstraction | **IMPLEMENTED** | `llm/provider.py` ABC; `factory.build_llm_provider` is the only selection seam |
| Mock (default) | IMPLEMENTED, VERIFIED | Default `llm_provider="mock"` |
| Gemini | **IMPLEMENTED, live-VERIFIED today** | `test_direct_gemini_still_works` passed (real call); 7.1 run used Gemini |
| OmniRoute | IMPLEMENTED (via `OpenAICompatibleProvider`, default `http://localhost:20128/v1`); **NOT VERIFIED today** | Live tests skipped: "NO LIVE OPENAI-COMPATIBLE GATEWAY" |
| OpenRouter | IMPLEMENTED (**uncommitted**); **NOT VERIFIED live** | Factory + config; no live test ran; no key in `.env.local` |
| xAI | IMPLEMENTED; NOT VERIFIED | Skipped: the configured credential's prefix identifies it as Groq, not xAI |
| Failover chain | IMPLEMENTED (**uncommitted**), UNIT only | `failover.py`; `test_llm_failover.py` uses fakes; no live multi-provider run |
| Retry | IMPLEMENTED | Bounded per provider; litellm internal retries pinned to 0; chain pins per-provider retries to 0 |
| Timeout | IMPLEMENTED | `ai_timeout_seconds=30.0`. The UI passes no override (the Phase 5 script defaults to 180 s because free tiers took 277–486 s) |
| 429 / 5xx | IMPLEMENTED → transient | `TRANSIENT_STATUS = {408,425,429,500,502,503,504}` |
| 401 / 403 | IMPLEMENTED → permanent; stops the chain | `PERMANENT_STATUS` checked before class name |
| API key config | IMPLEMENTED | `SecretStr`, env-switched dotenv. `.env.local` gitignored, not tracked; no key patterns found in tracked or untracked source/docs/outputs |
| Startup validation | **NOT WIRED into the UI** | `validate_configuration` is called only from `scripts/production_config_check.py`; `create_app` uses a bare `Settings()` |
| Logging | IMPLEMENTED | Prompt length only, never content. The transient warning logs the upstream exception text (INFO risk) |
| Provider/model provenance | PARTIAL | `pipeline.py` passes the static `settings.llm_provider` as `provider_name`. With failover, lineage says `failover` (or the first-built name), not the answering provider, unless nodes read `provider.describe` (NOT VERIFIED). The model isn't in the fingerprint |

---

## 10. TTS Audit

| Item | Status | Evidence |
|---|---|---|
| Kokoro-82M via ONNX Runtime (no `kokoro-onnx` import) | IMPLEMENTED, VERIFIED | `tts_kokoro.py`; `test_neural_tts.py` real synthesis passes; 7.1 `audio.json` is Kokoro |
| `af_heart` | IMPLEMENTED | Default and the only voice offered in the UI |
| SAPI | IMPLEMENTED (Windows only) | PowerShell script via `run_subprocess` |
| Provider selection / fallback | IMPLEMENTED in `webapp/pipeline.build_tts`: Kokoro → SAPI → Synthetic | **Silent** fallback with a `logger.warning`; not recorded in the fingerprint (§6) |
| Pronunciation (`to_spoken`) | IMPLEMENTED, **golden path only** | Not called from any node. The UI speaks raw text such as `chars[0]` |
| Alignment / subtitles | IMPLEMENTED, **golden path only** | §1 |
| Audio artifact | IMPLEMENTED | WAV + `audio.json` with producer, model, sample rate |
| Model provisioning | Manual download, CWD-relative `models/kokoro` | Fails to "unavailable" and then falls back silently when started from another CWD |
| Licensing | Consistent in code | `onnxruntime` MIT, `gruut` MIT, `num2words` LGPL-2.1, weights Apache-2.0. **Note:** `kokoro-onnx 0.6.1` is installed in `.venv` (not imported, and not in `pyproject.toml`); its GPL dependencies `phonemizer`/`espeakng-loader` are **not** installed. It's leftover environment state (INFO) |

---

## 11. Rendering Audit

| Behaviour | Classification | Evidence |
|---|---|---|
| Output resolution 1080×1920, 30 fps | **E2E VERIFIED** (probe) | 7.1 and 6.5.3 MP4s |
| Custom code panel (6.5.1) | TESTED + VISUALLY INSPECTED | `test_code_layout.py` passes, including `test_real_manim_geometry_never_overflows_or_collides` (real Manim); frames A/D/F |
| Fixed viewport (6.5.3) | TESTED; **visually inconsistent** | `test_code_viewport.py` passes. The 7.1 opening frame's panel differs in height and font from later frames (§4) |
| Line containment / long lines | TESTED; VISUALLY INSPECTED | Long signature line is shrunk to fit (frame A), readable but smaller |
| Active-line visibility | VISUALLY INSPECTED | Yellow box on the correct line (D: line 7, F: line 16) |
| Syntax highlighting / line numbers | VISUALLY INSPECTED | Present |
| Pointer containment | TESTED + VISUALLY INSPECTED | Arrows on the correct tiles; coincident pointers stacked |
| Array visualization | TESTED + VISUALLY INSPECTED | Tiles and indices from the reconstructed trace |
| Captions | VISUALLY INSPECTED | Caption text matches the narration moment |
| Transitions | E2E measured | Full-frame fade to near-black between every step (≈11% of 7.1 runtime; the same pattern in 6.5.3) |
| Opening / final frame | VISUALLY INSPECTED | Opening: title + code, no array yet. Final: `return true;` highlighted |
| Rule 7 (no algorithm names in `visualization/`) | **TEST FAILS** | `test_no_algorithm_specific_branches_in_visualization_package` flags `primitives.py: palindrome` and `code_state.py: remove_duplicates`. Both are **comments** (`primitives.py:187`, `code_state.py:291`), introduced in `c00b77a`. The test is a text grep, which contradicts the CLAUDE.md "parse the AST" convention. No real branching found |

---

## 12. Testing Audit

### 12.1 Results (this machine, 2026-09-28; Python 3.12.10, Java 25.0.3, ffmpeg on PATH, **Maven absent**)

| Run | Collected | Passed | Failed | Skipped | Errors | Time |
|---|---|---|---|---|---|---|
| `pytest` (full) | 949 | **872** | **51** | 26 | 0 | 100 s |
| `pytest -m "not integration"` | 832 selected (117 deselected) | **804** | 0 | 28 | 0 | 38 s |

### 12.2 Failure classification

| Tests | Count | Cause | Class |
|---|---|---|---|
| `test_java_adapter_integration` (13), `test_java_trace_integration` (13), `test_generalization` (10 of 11), `test_phase3_pipeline_integration` (5), `test_phase4_pipeline_integration` (2), `test_real_render` (3), `test_trace_runtime_helper` (2) | 48 | `MavenNotFoundError: mvn executable not found on PATH`. The User PATH points to `I:\Software\apache-maven-3.9.15-...`, which doesn't exist | **ENVIRONMENT** |
| `test_generalization::test_no_algorithm_specific_branches_in_visualization_package` | 1 | Text grep matches comments (`c00b77a`) | **PRE-EXISTING** (committed code; brittle test) |
| `test_real_narration::test_real_speech_aligns_to_the_visual_timeline` (12.75 ≠ 10.0), `::test_overflow_is_detected_when_speech_exceeds_its_step` (2.6 ≠ 0.5) | 2 | Tests assert total = sum of step durations. Alignment now adds lead-in/tail time (Phase 6 timing) | **PRE-EXISTING** (stale test vs changed contract; `narration/` is not in the uncommitted diff) |

Note: 48 Maven-dependent tests **fail** rather than skip, while `test_repeatability`, `test_line_mapping` and `test_e2e_golden_path` **skip** for the same cause. That's inconsistent gating. The production gate `test_e2e_golden_path.py` did **not run** (skipped).

### 12.3 Does the suite protect what matters?

| Concern | Finding |
|---|---|
| Mocks too much | `test_webapp.py`, `test_phase71_safety.py` and `test_generation_registry.py` run with fake pipelines. Correct for unit scope, but **no test runs the UI's `DefaultPipelineFactory` chain** |
| Tests that never run the real pipeline | The E2E gate (`test_e2e_golden_path.py`) exercises the **script-style** pipeline, not `GenerationManager` + `DefaultPipelineFactory`. The pipeline users get is untested end to end |
| Missing: superseded + FinalValidation | No test covers `FinalValidationNode` with a superseded result, so the P0 bug in §1 slipped through |
| Missing: audio longer than video on the node path | No node-level test asserts no truncation in `ComposeMediaNode` |
| Missing concurrency tests | None for registry writers or `JobManager` races |
| Missing persistence/restart tests | None for orphaned `running` versions or corrupt index recovery |
| Missing cancellation test that asserts effect | None |
| Missing: a reproducible 7.1 harness | Its output exists; the script doesn't |
| CI | **None.** Nothing runs any of this automatically |

---

## 13. Security Audit

| # | Finding | Severity | Evidence |
|---|---|---|---|
| S1 | **No authentication; no CSRF protection on `POST /create/start`**. Any reachable client can trigger multi-minute CPU/LLM-quota work. Mitigated only by the default `127.0.0.1` bind, which the doc says not to expose | **HIGH** if exposed / LOW on localhost | `app.py:205`, `cli.py` |
| S2 | **Traced JVM inherits the full server environment**, including any API keys supplied as env vars (the production mode) | **MEDIUM** today (catalog programs are trusted fixtures); **CRITICAL** the moment user-supplied Java is accepted | `langadapter/java/adapter.py:146` `env={**os.environ, ...}` |
| S3 | Java execution has no FS/network/user isolation (documented in `SECURITY_SANDBOX.md`) | MEDIUM today / CRITICAL with user code | `SECURITY_SANDBOX.md:470–499` |
| S4 | **Absolute server paths disclosed** via `/media/<slug>/v<n>/audio`, `final_video.json` and `rendered_video.json`. Contradicts the doc's "no absolute path is ever handed to a browser" | LOW | Live: `"audio_path": "D:\\WORK_SPACE\\Code2Shorts\\output\\..."` |
| S5 | `/api/docs` and `/openapi.json` are public | LOW / INFO | Live 200 |
| S6 | Failed (validation-rejected) media is served and exportable | LOW (integrity, not confidentiality) | §5 |
| S7 | Settings page reveals which credentials are present | INFO | By design |
| S8 | Transient-error logs include upstream exception text | INFO | `openai_compatible.py` warning |
| — | Path traversal on `/media` | **No finding**: 4 variants → 404; containment after `resolve()` | Live |
| — | XSS via form fields | **No finding** in probe: value coerced and autoescaped | Live |
| — | `shell=True` / `os.system` | **No finding** in `src/` | grep |
| — | Secrets in git | **No finding**: `.env.local` ignored, not tracked; key-pattern scan clean | `git ls-files`, `git grep` |
| — | File uploads | None exist | — |
| — | CORS | No CORS middleware (same-origin only), acceptable for server-rendered pages | — |

---

## 14. Observability Audit

"What happened to this generation?" Answerable only partially, and only while the process lives.

| Field | Available | Where | Survives restart |
|---|---|---|---|
| Job ID | Yes | Memory | No |
| Generation ID / version | Yes | `index.json` | Yes |
| Stage + per-stage start/end | Yes (job) | Memory | **No**; the registry keeps only overall created/completed |
| Duration | Overall only | `index.json` (missing on the crash path) | Yes |
| Provider | Name only | Fingerprint | Yes |
| Model | **No** | — | — |
| Answering provider under failover | Logged only | stderr | No |
| Error | Short string (`"pipeline failed at final_validation"`) | `index.json` | Yes, but **the actual validation errors are not persisted**. They were only recoverable through the separate 7.1 probe |
| Artifact paths | Yes (relative map) | `index.json` | Yes |
| Logs | 500-line tail of event names | Memory | No |
| Structured logs / metrics / traces | **No** | — | — |
| Correlation ID across logs | `execution_id` exists in state; not in the log format | — | — |

---

## 15. Deployment Audit

### Resource profile (from repository behaviour; no load test was run)

| Component | Profile | Basis |
|---|---|---|
| Maven + JVM (compile, JUnit, instrumented run) | **MODERATE** (hundreds of MB, seconds) | Per-run temp Maven workspace; exact memory NOT MEASURED |
| Python app | LIGHT | FastAPI, JSON registry |
| Kokoro | **MODERATE–HEAVY** | 338 MB weights loaded in-process on CPU; 99 s of speech synthesised in one call |
| Manim | **HEAVY** | Largest stage (~135 s historically, per CLAUDE.md); 268 KB generated scene; 900 s timeout |
| FFmpeg | LIGHT–MODERATE | Video stream copy, AAC encode |
| Disk | **HEAVY** | One 7.1 version ≈ 11 MB MP4 + 4.8 MB WAV + Manim partials/SVG cache; `output/` is 1.3 GB |
| LLM | External; slow on free tiers | 4 LLM stages; total 7.1 run 270.8 s |

### Targets

| Target | Can it run today? | Blocking evidence |
|---|---|---|
| **Local (Windows dev box)** | **PARTIALLY.** UI serves; generation needs Maven, which is currently missing on this machine | 48 `MavenNotFoundError` |
| **Render** | **NOT READY** | No Dockerfile or `render.yaml`. Needs JDK + Maven + FFmpeg + Manim system deps (Cairo/Pango, LaTeX if used) + 338 MB weights. The SAPI fallback is Windows-only, so Linux falls through to *Synthetic* (silent/beep) if Kokoro is missing. Requires a persistent disk for `output/library`. A single instance is mandatory (§6–7). Free/low tiers' RAM and CPU are likely insufficient for Manim + Kokoro (NOT MEASURED). A web-service restart kills in-flight jobs |
| **Vercel** | **NO** for the pipeline | Serverless: no long-lived threads, execution time limits far below 270 s, read-only/ephemeral FS, no JVM/Maven/FFmpeg/Manim binaries, 338 MB model. Only static pages or a thin API proxy could live there, and that needs an external worker plus shared storage that don't exist. `jobs.py` states this itself |
| **Oracle Cloud (OCI)** | **Architecturally compatible** as a single long-lived VM (e.g. an Arm/x86 compute instance with block storage): a persistent process, local disk and full toolchain all fit the current design. **Not tested; nothing is provisioned.** Arm images would need arm64 builds of onnxruntime/Manim deps (NOT VERIFIED) | — |

Cross-cutting deploy blockers: program source read from `tests/fixtures`. The registry root and model dir are **relative to CWD**. `validate_configuration` isn't called at app startup. No health check beyond `{"status":"ok"}`. No CI to build an image.

---

## 16. Documentation vs Reality

| Claim | Documentation says | Code says | Test says | Runtime evidence | Reality |
|---|---|---|---|---|---|
| UI runs the same pipeline as the golden paths | `PHASE_7_UI.md`, `manager.py`/`pipeline.py` docstrings: "same nodes… used identically by the golden-path scripts" | UI uses Render/ComposeMedia nodes; the Phase 5/6 scripts do fitting/alignment/SRT/validation themselves | No test runs `DefaultPipelineFactory` | 7.1 audio 99.2 s vs video 88.4 s; no SRT | **FALSE** |
| Superseded attempts don't fail a run | ADR-5.6, CLAUDE.md rule 11 | `all_passed` respects it; `FinalValidationNode` does not | Only `ValidationSummary` is tested | 7.1 failed exactly this way | **FALSE at the final gate** |
| Never truncate audio | CLAUDE.md rules 4, 5, 10; composer comments | `-t video_duration` truncates on the node path; `validate_timeline` isn't wired in | Script-path tests only | 10.8 s cut | **FALSE on the UI path** |
| Cancellation is cooperative at stage boundaries | `PHASE_7_UI.md`, `jobs.py` | Flag never read | None asserting effect | Probe: ignored | **NOT IMPLEMENTED** |
| Provider that actually spoke is recorded in the fingerprint | `pipeline.build_tts` docstring | Requested provider recorded | None | — | **FALSE** |
| No absolute path reaches the browser | `PHASE_7_UI.md`, `app.py` docstring | Stage JSON with absolute paths served verbatim | `test_phase71_safety` covers registry paths, not JSON contents | Live `D:\WORK_SPACE\...` | **FALSE** |
| A new version is always a new directory | `registry.py` docstring | True only while the index is intact | No corruption test | Probe: `v1/` reused | **CONDITIONAL** |
| Registry writes are atomic | `registry.py` | A single write is atomic; read-modify-write isn't serialized | No concurrency test | Probe: lost updates, `PermissionError` | **PARTIAL** |
| 465 tests / 354 unit pass | CLAUDE.md | — | 949 collected; 804 unit pass | Today's run | **STALE** |
| "Phases 0–5 complete" (current state) | CLAUDE.md | Phases 6.x and 7.x exist | — | — | **STALE** |
| `code2shorts generate` lands in Phase 1+ | `cli.py` docstring | No `generate` command | — | — | **FALSE / STALE** |
| README status | Describes Phases 1–5; "Data structures (Phase 6, planned)" | Phase 6 implemented | — | — | **STALE** |
| No automatic failover | ADR-5.10 (committed) | Uncommitted failover + ADR-6.9 | Unit | — | Superseded, **uncommitted** |
| Phase 7.1 verified real UI → MP4 | Phase history (brief) | — | — | `e2e_report.json`: first generation `false` | **NOT ACHIEVED** |
| Phase 7 ADR | CLAUDE.md requires ADRs for contract changes | New registry/job/fingerprint contracts | — | No ADR-7.x in `ARCHITECTURE_DECISIONS.md` | **MISSING** |
| No algorithm names in `visualization/` | CLAUDE.md rule 7 | Comments contain two names | Test fails | — | **Test red** (comments only) |

---

## 17. Complete Gap Matrix

| Area | Current state | Evidence | Gap | Severity | Production impact |
|---|---|---|---|---|---|
| Core pipeline | Two divergent pipelines | §1, §2.2 | UI path lacks fit/align/SRT/speech-text/timeline validation | **P0** | UI videos have truncated, mispronounced narration and no subtitles |
| Java execution | Real; needs Maven | 7.1 artifacts; 48 env failures | Toolchain not reproducible on this box; no container | P1 | Can't generate here today |
| Trace | Real, canonical | 7.1 `trace.json` | — (env only) | P3 | — |
| Educational plan | Real (Gemini) | 7.1 | Model not recorded | P2 | Weak provenance |
| Visualization plan | Real, validated, repaired | 7.1 probe | Repair → final-gate bug | **P0** | Every repaired run fails |
| LLM | Gemini live; others unit-only; failover uncommitted | §9 | UI timeout 30 s vs observed 277–486 s free-tier; no startup config validation | P1 | Real runs may time out |
| TTS | Kokoro real | `audio.json` | Silent fallback not in identity; CWD-relative weights; Windows-only fallback | P1 | Wrong voice served as cached Kokoro |
| Alignment | Real in scripts | 6.5.3 `alignment.json` | Not on UI path | **P0** | See core |
| Timeline | Real in scripts | fitting.py | Not on UI path | **P0** | See core |
| Rendering | Real, 1080×1920 | Probes, frames | Panel geometry differs intro vs steps; frequent black fades; rule-7 test red | P2 | Cosmetic / test hygiene |
| FFmpeg | Real | Probe | `-t` truncation unguarded on node path | **P0** (with core) | Lost speech |
| UI | Routes work | §5 | Plays failed videos; failures hidden on Home; JSON 404s; cancel no-op | P1 | Misleading product |
| Registry | JSON file | §6.2 | Lost updates, corrupt-index reset + dir reuse, no restart reconciliation | P1 | Data loss / stale `running` |
| Fingerprinting | Implemented | §6.1 | input, entry point, model, actual TTS missing; source cache stale | P1 | Wrong reuse decisions |
| Jobs | Thread + memory | §7 | No persistence, cancel, recovery, orphan detection | P1 | Restarts lose work silently |
| Storage | Local disk + memory | §8 | No DB/object storage; relative roots | P1 (deploy) | Not multi-instance or serverless capable |
| Security | Localhost-safe | §13 | No auth/CSRF; env inherited by JVM; path disclosure | P1 (if exposed) | Abuse / key exposure |
| Observability | Minimal | §14 | Validation errors not persisted; no structured logs | P1 | Failures undiagnosable after the fact |
| Testing | 804 unit green | §12 | No test of the UI pipeline; no superseded/final gate test; no concurrency or restart tests | **P0** (for the missing UI-pipeline E2E) | Bugs above shipped undetected |
| Deployment | None | §15 | No image, manifest or persistent worker | P1 | Can't deploy |
| CI/CD | None | `.github` | Nothing runs tests | P1 | Regressions unguarded |
| Documentation | Detailed but stale in places | §16 | README/CLAUDE.md stale; no Phase 7 ADR; no 6.2–6.5 docs | P2 | Misleading onboarding |
| Version control | Phase 6.5.4/7 uncommitted | `git status` | Large untracked work on one machine | **P0** (process) | One disk failure loses Phase 7 |

---

## 18. Top 5 Production Gaps

1. **The UI runs an unverified pipeline** (P0). The accepted Phase 5/6 behaviour (fit before render, per-segment TTS with pronunciation, alignment, SRT, timeline/media validation) lives in `scripts/run_phase5_golden_path.py`, not in workflow nodes. The UI therefore produces truncated narration: 99.18 s → 88.42 s, measured.
2. **`FinalValidationNode` ignores `superseded`** (P0). This is the proven cause of the only real UI run's failure, and it will fail every run where repair succeeds.
3. **No reproducible, committed evidence for the product path** (P0). Phase 7 code is untracked. The 7.1 harness isn't in the repo. No test exercises `GenerationManager` + `DefaultPipelineFactory` for real. CI doesn't exist.
4. **Registry and job durability** (P1). Lost updates under concurrent writes, history loss and directory reuse on a corrupt index, permanent `running` after restart, a cancel no-op, and memory-only jobs.
5. **Generation identity is incomplete** (P1). Input value, LLM model, the actually-used TTS engine and live source changes are missing from the fingerprint, and failed MP4s are served. That undermines the core promise of Phase 7.0, "you already have this video".

Deployment (Render/Vercel/OCI), auth and object storage are real gaps. They rank below these because they would deploy a path that doesn't yet produce a correct video.

---

## 19. Reuse Audit

Scoped to the top gaps only. Versions, dates and licences were fetched from the PyPI JSON API on 2026-09-28. "Maturity" draws on project history and wasn't independently re-measured.

### Gap 1–2: pipeline parity and the final-gate fix

| Candidate | Purpose | Licence | Relevance | Effort | Verdict |
|---|---|---|---|---|---|
| **Code2Shorts' own `scripts/run_phase5_golden_path.py` + `narration/{fitting,alignment,subtitles,speech_text}` + `media/validation`** | The verified implementation | MIT (in-repo) | Exact | Low–moderate: move script steps into nodes | **REUSE (internal)** |
| LangGraph / Prefect / Dagster | Workflow orchestration | MIT / Apache-2.0 | Low; the runner already exists and LangGraph is ADR-deferred | High | **REFERENCE only** (not needed) |

### Gap 3: CI and reproducible E2E

| Candidate | Purpose | Licence | Verdict |
|---|---|---|---|
| GitHub Actions (`actions/setup-java`, `setup-python`, `stCarolas/setup-maven` or runner-image Maven) | CI for unit and integration | MIT (actions) | **REUSE** (no Python dependency added) |
| pytest markers already in repo | Gate integration vs unit | — | **REUSE** |

### Gap 4: registry and job durability

| Candidate | Version / date | Licence | Needs | Relevance | Verdict |
|---|---|---|---|---|---|
| **`sqlite3` (stdlib)** | Python 3.12 | PSF | Nothing | Transactional registry + job table, atomic version allocation, survives restart; zero new dependency (matches the repo's reuse bar) | **REUSE (stdlib), BUILD thin layer** |
| `filelock` | 4.0.5 / 2026-09-28 | MIT | Nothing | Cross-process lock around the JSON index; smallest fix, but keeps the JSON model | ADAPT (fallback option) |
| `portalocker` | 4.4.0 / 2026-09-19 | BSD-3 | Nothing | Same as filelock | REFERENCE |
| **Huey** | 3.4.0 / 2026-09-04 | MIT (PyPI metadata blank; project licence MIT) | SQLite backend available; no Redis | Persistent task queue, retries, separate consumer process | **ADAPT (later phase, when a worker split is needed)** |
| procrastinate | 3.10.0 / 2026-09-23 | MIT | PostgreSQL | Durable queue with job table | REFERENCE (needs Postgres) |
| RQ | 2.12.0 / 2026-08-30 | BSD-2 | Redis | Simple queue | REFERENCE (adds Redis) |
| arq | 0.28.0 / 2026-04-16 | MIT | Redis, asyncio | Async queue | REFERENCE |
| Celery | 5.6.3 / 2026-03-26 | BSD-3 | Broker | Heavy for one-job-at-a-time | Not recommended |
| Dramatiq | 2.2.1 / 2026-09-02 | **LGPL-3.0+** | Broker | Licence review needed for the MIT project | Not recommended |

### Gap 5: identity and observability (supporting)

| Candidate | Version / date | Licence | Verdict |
|---|---|---|---|
| `hashlib` + existing canonical-JSON digest | stdlib | PSF | **REUSE**: add fields, no library needed |
| structlog | 26.1.0 / 2026-06-06 | MIT OR Apache-2.0 | ADAPT later (stdlib `logging` with a JSON formatter suffices first) |
| OpenTelemetry SDK | 1.45.0 / 2026-09-25 | Apache-2.0 | REFERENCE (premature) |
| fsspec / boto3 (object storage) | 2026.9.0 / 1.43.103 | BSD-3 / Apache-2.0 | REFERENCE for the deployment phase, not now |

---

## 20. Recommended Next Phase

### PHASE 7.2 — Pipeline Parity: make the UI run the verified pipeline

**Why (evidence):** The single largest real gap is §1/§18 #1–#3. The engine has a verified correct output (`output/phase6_5_3/palindrome`: audio fits, SRT present). The product path users click produces a different, measurably wrong video (10.8 s of speech lost) and then rejects its own run because of the superseded bug. Durability, deployment and auth all multiply the value of a correct video, and today's product path produces none. Fixing parity also turns the existing E2E gate into a gate for the real product path.

**Scope (only this):**

1. Move the golden-path narration/timeline steps into workflow nodes, reusing the existing functions unchanged:
   per-segment TTS with `to_spoken`, then `fit_plan_to_narration` **before** `RenderVideoNode`, then `align_narration` + existing sync validators, then `write_srt`, then concatenated audio, then `MediaComposer`, then `validate_timeline` / `validate_final_video`. Bump `PIPELINE_VERSION`.
2. Fix `FinalValidationNode` to ignore `superseded` results (test first, per CLAUDE.md).
3. Make `DefaultPipelineFactory` and the golden-path script use the **same** node list, so there is one pipeline.
4. Record the actually-used TTS provider and the LLM model in the version (fingerprint + record), and add `input_value`/`entry_point` to the request fingerprint.
5. Don't serve or export `FAILED` versions' MP4 as the product video (show it as a diagnostic at most), and persist the validation errors into the version record.
6. Add a committed, reproducible UI → MP4 E2E test (`GenerationManager` + `DefaultPipelineFactory`, real tools, `integration` marker) asserting: completed status, audio duration ≤ video duration within tolerance, SRT present, 1080×1920, reuse on a second request, new version on force.
7. Commit the Phase 6.5.4/7.x work (on the feature branch) and add a Phase 7 ADR covering registry/fingerprint contracts.
8. Housekeeping directly tied to trusting the gate: fix the two stale alignment tests and the comment-grep rule-7 test (AST-based), and make Maven-dependent tests skip consistently.

**Exit criterion:** one real UI-initiated palindrome generation reaches `COMPLETED`, with probed audio and video durations within `DURATION_TOLERANCE_SECONDS`, subtitles present, reproducible from committed code.

**IN SCOPE:** items 1–8 above.

**OUT OF SCOPE:** see §21.

---

## 21. Explicit Out-of-Scope Items

For Phase 7.2, deliberately deferred:

- Deployment to Render, Vercel or OCI; Dockerfiles; manifests.
- External job queue / worker split (Huey, RQ, etc.); multi-worker support.
- SQLite or any database migration of the registry. The concurrency and corruption fixes are **the following phase** (suggested: Phase 7.3 Durable Registry & Jobs: stdlib SQLite, restart reconciliation, real cancellation).
- Object storage; CDN.
- Authentication, CSRF, rate limiting (keep the `127.0.0.1` bind until then).
- JVM environment scrubbing / sandbox containerisation (required before accepting user-supplied Java; not needed while the catalog is fixtures only).
- New algorithms in the catalog; user-supplied code.
- Partial regeneration, compare-versions, timeline scrubbing.
- Visual redesign: the intro vs step panel geometry and per-step black fades are logged (§4, §11) for product review, not for this phase.
- Changes to failover behaviour or new LLM providers.
- CI pipeline setup (recommended immediately after, or alongside if trivially small; not a 7.2 deliverable).

---

## 22. Evidence / Commands Used

All read-only against the repository. Experiments ran on copies in the session scratch directory.

```text
git status --short ; git diff --stat ; git log --oneline        # uncommitted Phase 7; 8 commits
find src tests scripts tools -type f | xargs wc -l                # inventory
ls .github -R ; git ls-files | grep -i docker|render|vercel|workflows   # no CI / deploy
cat output/phase7_1/e2e_report.json                               # first_generation=false
cat output/phase7_1/validation_probe.json                         # superseded failure → domain fail
cat output/phase7_1/library/index.json                            # v1 FAILED, fingerprints
python -c probe_video/probe_audio(...)                            # 7.1: video 88.415 s, wav 99.178 s
                                                                  # 6.5.3: video 129.782 s, wav 129.140 s
grep -rn "align_narration|fit_plan_to_narration|write_srt|validate_timeline|validate_final_video|to_spoken" src
                                                                  # no call sites in workflow/webapp/generation
grep -n "cancel_requested" -r src                                 # written, never read
sed -n 952,986p src/code2shorts/workflow/nodes.py                 # `not r.passed` without superseded
PYAV frame luma scan of 7.1 and 6.5.3 final.mp4                   # 98 dark samples in each
Read output/phase7_1/frames/{A_opening,C_mid,D_active,F_end}.png  # visual inspection
pytest -p no:cacheprovider -rfEs -q --durations=15                # 872 passed, 51 failed, 26 skipped (949)
pytest -p no:cacheprovider -m "not integration" -q                # 804 passed, 28 skipped, 117 deselected
pytest --collect-only -q                                          # 949 collected
Get-Command mvn ; User PATH → I:\Software\apache-maven-3.9.15-...  # path does not exist
TestClient(create_app(registry=<scratch copy>, pipeline_factory=<refusing>))
   GET / /videos /create /jobs /settings /videos/palindrome[/v1|/v9] /api/* /api/docs /openapi.json
   GET /media/palindrome/v1/{audio,final_video(Range)} + 4 traversal variants
   POST /create/review (+ XSS probe), POST /api/videos/palindrome/decide
race_probe.py (scratch): concurrent registry writers, same-algo writers, corrupt index,
   concurrent JobManager.start, cancel effect, restart with RUNNING, fingerprint fields
git ls-files / git grep for key patterns ; .env.local key NAMES only (values redacted)
curl https://pypi.org/pypi/<pkg>/json                             # reuse-audit versions/licences
```

---

## 23. Final Verdict

## **DEVELOPMENT READY**

- **Not INTEGRATION READY** for the product: the UI path's stages don't integrate correctly (audio truncation, final-gate bug), and the Java integration suite can't run on the current machine.
- **Not E2E VERIFIED**: the only real UI → MP4 run is recorded as `FAILED`. The golden-path script did produce a correct video (6.5.3), but that isn't the path users run.
- **Not DEPLOYMENT READY**: no image, manifest, CI, auth, persistent jobs or shared storage.

What *is* solid: a real JVM-trace-grounded engine, a trusted renderer that produces a 1080×1920 video, real Kokoro speech, a real Gemini integration that answered live today, and a green 804-test unit suite. The next step is to make the product path run that engine as it was verified, then prove it with a committed E2E test.

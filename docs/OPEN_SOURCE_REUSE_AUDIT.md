# Open-Source Reuse Audit (post-Phase 8.3)

**Date:** 2026-10-01.

**Status:** this is an audit only. No Code2Shorts source, test, config or
dependency was changed. The candidates were cloned and run **outside the
repository**, in a scratch directory with a separate virtualenv.

**Stance:** Code2Shorts was treated as inherited code. Previous effort
counted for nothing. A decision needed source, test, licence or run
evidence; stars, READMEs and demos were treated as visibility, not quality.

**Current-state caveat:** native 4K rendering is verified (Phase 8.3). A
fresh LLM → 4K → MP4 run is **not**: free-tier provider limits blocked
it. Nothing below describes the system as production-verified.

---

## 1. Executive Summary

**Stop building:**
1. **More Java instrumentation features.** `tools/java-instrumenter` is
   818 lines of JavaParser source rewriting. It rejects lambdas and nested
   classes, and needed hand-written `repr()` overloads for arrays.
   **cs1302-tracer** (MIT, University of Georgia) is a JDI-based Java
   tracer, verified in source and run on our palindrome:
   - full stack frames and a heap graph with references, objects, generics,
     inheritance, exceptions and records;
   - bounded tracing and 52 test files;
   - CI on JDK 21 and 25, last commit 2026-09-25.

   It already does what every object, collection, linked-list or tree phase
   on our roadmap would have to build. **Decision: ADAPT it as a second
   trace backend, behind the existing `LanguageAdapter` seam, and stop
   extending the instrumenter.** If parity holds on the 5 golden algorithms,
   it **REPLACES** the instrumenter for all new language features.
2. **AST, CFG and algorithm-classification phases.**
   - The one project that claims them (code-teaching-video-agent) doesn't
     contain them: its `classifier.py` doesn't exist and `tree-sitter` is
     never imported.
   - Our design doesn't need them: the real trace *is* the executed control
     flow, and visualisation is generic by rule.
   - **Cancel them.**
3. **A job-queue system.** The measured risk is unbounded concurrency
   (4K ≈ 1.75 GiB per render). For the mandatory single instance, the
   bound is a stdlib executor, not infrastructure. Adopt **Huey** (MIT,
   SQLite backend, no broker) only when jobs must survive a restart.
4. **Any LLM-writes-Manim feature: generation, "self-healing" or RAG over
   Manim docs.**
   - Every project that does this executes AI-authored Python, sometimes
     with `shell=True` or with only a denylist AST filter.
   - None has meaningful tests.
   - It is the exact property Code2Shorts was designed to exclude
     (CLAUDE.md rule 3).

**Keep, as genuinely differentiated** (I searched and found no OSS
equivalent):
- the trace-grounded educational plan (ClaimKind and evidence checks);
- the closed-vocabulary `VisualizationPlan`;
- repository-owned scene generation;
- the measured-speech → widen-only timeline fit;
- probe-based media validation;
- the fixed-viewport code panel.

**Reuse more:**
- frame-level deterministic QA. Phase 8.3 already wrote it ad hoc (ink
  ratio, bounding boxes, spectral checks); it should become a pipeline
  validator;
- the *idea* of Code2Video's and TheoremExplainAgent's render → inspect →
  critique loop, applied to **data** (layout parameters), never to code.

**Remove:** the six empty Phase-0 placeholder packages (`animation/`,
`compose/`, `lessonplan/`, `qa/`, `render/`, `tts/`).

---

## 2. Current Code2Shorts Architecture (from source, not from reports)

```
webapp/ (734 lines)  Create → Review → Job → Details; FORMATS from the profile registry
   │ video_format (422 on invalid)
generation/ (1157)   TeachingConfig → RequestFingerprint → GenerationManager
   │                 → JobManager (one thread per distinct request, UNBOUNDED)
   │                 → GenerationRegistry (fs + JSON index, per-root lock)
workflow/ (1757)     nodes + runner + retry (failure-kind-aware) + checkpoints
   ├─ langadapter/java (819) + tools/java-instrumenter (818 Java, JavaParser)
   │     compile (mvn) → JUnit → instrumented execute → ExecutionTrace  ← canonical
   │     all through execution/sandbox.py::run_subprocess (141): fixed args, timeout, tree-kill
   ├─ ai/ (1780) + llm/ (43)  explanation → EducationalPlan → VisualizationPlan → narration
   │     litellm providers, failover chain, generate_structured, bounded repair
   ├─ narration/ (1670) Kokoro-82M via ONNX directly → to_spoken → fit (widen-only) → align → SRT
   ├─ visualization/ (2967)  state reconstruction, code_state, primitives, layout, manim_renderer
   │     VideoProfile → CompositionLayout → byte-stable scene source → manim --resolution
   └─ media/ (818)    assemble track → normalise → FFmpeg (-c:v copy) → probe → validate
tests/ (15,041 lines, 56 files; 1024 pass, 51 pre-existing env failures, 27 skipped)
```

**Dependencies:**
- **Core:** pydantic, pydantic-settings, typer, litellm.
- **Optional:** manim and imageio-ffmpeg (render); fastapi, uvicorn,
  jinja2 (web); onnxruntime and gruut (TTS).

## 3. Current Custom Components

| # | Component | Size (lines) | Already reuses |
|---|---|---|---|
| 1 | Java instrumenter (source rewrite) | 818 Java + 819 Python | JavaParser (Apache-2.0/LGPL dual) |
| 2 | Trace model + state reconstruction | ~1,000 | Pydantic |
| 3 | Educational plan, grounding, ClaimKind | part of 1,780 | litellm, Pydantic |
| 4 | Visualization plan (closed `VisualAction` enum) | part of `ai/` | Pydantic |
| 5 | Scene generation (`build_scene_source`) + primitives | ~2,100 | Manim CE `Code`, `Text`, `Arrow`, `Square`, Pygments |
| 6 | Code panel (fixed viewport, outlier shrink, re-pin) | ~600 in primitives | Manim `Code` |
| 7 | CompositionLayout (portrait and columns) | 180 | — |
| 8 | TTS (Kokoro via ONNX) | ~400 | onnxruntime, gruut, Kokoro-82M weights |
| 9 | Timeline fit and alignment, SRT | ~700 | `srt` |
| 10 | Media composer and validation | 818 | FFmpeg, PyAV |
| 11 | Workflow runner, retry, checkpoints | 1,757 | stdlib |
| 12 | Fingerprint, registry, versioning, dedupe | ~800 | stdlib |
| 13 | JobManager | ~200 | `threading` |
| 14 | LLM providers and failover | ~900 | litellm |
| 15 | Web UI | 734 + templates | FastAPI, Jinja2 |

## 4. GitHub Research

Every query from the brief was run against the GitHub search API, plus
targeted queries for JDI tracers, Python Tutor, manim-voiceover and job
queues. **Signal was thin:**
- "algorithm visualization + execution trace" returned Streamlit and React
  demos with 0–3 stars;
- "java execution visualization" returned dormant projects (latest 2021)
  and one new 1-star JDI demo;
- "manim agent" returned prompt-skill files (`SKILL.md` collections) and
  research code.

**Shortlist that survived filtering:**

| Candidate | Why it's on the shortlist |
|---|---|
| **cs1302uga/cs1302-tracer** | The only maintained, tested Java execution tracer found |
| **showlab/Code2Video** | Mandated; research code with a real critic loop and an evaluation rubric |
| **TIGER-AI-Lab/TheoremExplainAgent** | 1.5k★; the best-known planner → coder → VLM-critic implementation |
| **ExploreMaths/CodeVideoRenderer** | Mandated; the direct code-rendering comparison |
| **ManimCommunity/manim-voiceover** | The standard audio-driven Manim timing approach |
| **Huey / RQ / Procrastinate / Celery / Dramatiq** | Job-queue options for the concurrency question |
| **ManimCommunity/manim** (already used), **3b1b/manim** | Renderer baseline |
| wanuo54/code-teaching-video-agent, sakhadib/AiManim, sohxmg/manim-shorts | Mandated; inspected and rejected |
| daveagp/java_visualize | Java on the Python Tutor frontend; AGPL-3.0 |

## 5. Repository Deep Dives

Evidence labels used below: **VERIFIED IN SOURCE** (V), **PARTIALLY
VERIFIED** (P), **README ONLY** (R), **NOT VERIFIED** (N).

### 5.1 cs1302-tracer (MIT)

**Profile:** last commit 2026-09-25; JDK 21+; Maven.

**Source:** `src/main/java/cs1302/tracer/execution/TraceSession.java`,
`trace/DebugTraceHelper.java`, `trace/GuestLauncher.java`,
`trace/PersistentGuestSession.java` (JDI, `com.sun.jdi`).

**Dependencies:**
- asm 9.10.1 (BSD-3)
- picocli 4.7.7 (Apache-2.0)
- javaparser-core and javaparser-symbol-solver (Apache-2.0/LGPL-3.0 dual;
  we already depend on JavaParser)
- gson 2.14.0 (Apache-2.0)

**Tests and CI:** 52 test files. CI builds on JDK 21 and 25, on Ubuntu
and macOS, and runs a trace-compatibility check (`examples/verify.py`).

**Run here:**
- Built in **24 s** (fat jar 7.3 MB).
- Traced our palindrome (with `"RACECAR"` hard-coded, because the CLI has
  no program-arguments flag) in **3.5 s wall**, including the JVM.
- Produced **29 line snapshots**: `[19,20,21,4,5,6,7,8,9,12,13,14,6,…,15,22]`.
  (The line numbers are shifted by −1 because I removed the `package` line
  in the trial copy.)
- Each snapshot carries every frame's locals with **correct block scoping**
  (`a`/`b` vanish after their block), plus a **heap graph**:
  `"107": ["LIST", "R","A","C","E","C","A","R"]` with `["REF",107]` from
  locals.

| Claim | Status |
|---|---|
| JDI tracing, frames, heap, references | V |
| Bounded tracing (10 s, 10k snapshots, 1 MiB output, heap and element caps, exit code 3) | V (`docs/BOUNDED_TRACING.md` and CLI flags) |
| Python Tutor and "modern" JSON formats | V (both produced) |
| Sandboxing | **The README says it does not sandbox** (V); its isolation is delegated to a "runner contract" |

**Compared with ours:**
- **What it lacks:** our trace is **mutation events** (`ARRAY_WRITE`,
  `CONDITION_EVALUATED` with result, `METHOD_ENTER`/`EXIT` with return
  value, call depth), and theirs is **line snapshots**. Condition outcomes
  and array writes would have to be **derived by diffing** consecutive
  snapshots. That is deterministic, but it is new adapter code.
- **What it has that ours doesn't:** objects, references, aliasing,
  generics, inheritance, exceptions, records, varargs and stdout, with
  examples 0–27 covering them. Lambdas and nested classes aren't refused
  at instrument time.

### 5.2 showlab/Code2Video (MIT)

**Profile:** last commit 2026-08-24; 3,921 lines in `src/`; **no tests**.

**Architecture (V):** `agent.py::TeachingVideoAgent` runs:
1. `generate_outline` → `generate_storyboard`;
2. `generate_section_code`, where the LLM writes Manim;
3. `debug_and_fix_code`, which runs `subprocess.run(["manim","-ql",…],
   timeout=180)` and sends stderr to `ScopeRefineFixer.fix_code_smart`;
4. `get_mllm_feedback`, where Gemini watches the MP4 plus a reference
   **grid image** and returns layout problems and solutions;
5. `optimize_with_feedback`, which regenerates the code;
6. `merge_videos`.

**Other findings:**
- `prompts/base_class.py` defines a 4×4 **anchor grid** (`place_at_grid`):
  layout as named slots, so the critic can talk in slots.
- **Evaluation (V):** `eval_AES.py` (a 5×20-point aesthetic rubric:
  layout, attractiveness, logic flow, accuracy, consistency) and
  `eval_TQ.py` (TeachQuiz, which measures learning via a quiz).
- **Security:** LLM-generated Python is executed by Manim with no sandbox.
  Section rendering is parallel (`ThreadPoolExecutor(max_workers=6)`) with
  no memory bound.

**Fit:** Code2Video teaches a *topic*. There is no program, no execution
and no evidence. The planner has no grounding contract.

### 5.3 TIGER-AI-Lab/TheoremExplainAgent (MIT)

**Profile:** last commit 2025-07-26 (14 months ago); about 7.5k lines; no
test directory.

**Visual critic (V):**
- `code_generator.py::visual_self_reflection(code, media_path)` sends a
  snapshot image or the MP4 to a VLM and returns rewritten code.
- `video_renderer.py::render_scene` loops on render, reflect and retry,
  and uses `image_with_most_non_black_space` to pick a frame.

**Other findings:**
- **RAG** over Manim docs (Chroma), and Langfuse tracing.
- **Security:** `subprocess.run(…, shell=True)` in `video_renderer.py:156`
  (V).

**Fit:** it is the same LLM-writes-Manim paradigm. Its critic needs code
to rewrite, and ours has none to rewrite.

### 5.4 ExploreMaths/CodeVideoRenderer (MIT, v1.2.3)

**Profile:** last commit 2026-06-20; 391-line `renderer.py`; 1 test file
with 20 tests that cover only construction and validation, not rendered
output. CI has a Python 3.8–3.14 matrix.

**What it is (V):** `CameraFollowCursorCV` builds the **whole file** as
one Manim `Code` mobject in a `MovingCameraScene`. It types it
**character by character**, with a cursor and a camera that follows the
cursor, then applies a **MoviePy glow post-process** that re-encodes the
video.

**Experiment** (isolated venv, 1080p30, `interval_range=(0.034,0.034)`):

| Case | Result |
|---|---|
| Interval 0.02 s | Rejected ("must be ≥ 1/30 s") |
| Short Java (11 lines) | Manim render OK, then **`KeyError: None` in its glow post-process**; no output file (the named MP4 is 261 bytes) |
| Long line (≥ 100 chars) | Same `KeyError`. The raw Manim MP4 (18.4 s, 552 frames, **391 partial movie files** for about 270 characters) shows the long line **clipped at the frame edge** in the final frame (screenshot inspected) |
| 60-line file | **`MemoryError`** allocating a 7.9 MiB frame buffer, then x264 `malloc` failures, on a 31.6 GiB machine |

**Against our code panel:**

| Case | CodeVideoRenderer | Ours |
|---|---|---|
| Short Java | typed in; it crashed on our run | fixed viewport |
| Long Java line, 90+ chars, active long line | clipped at the frame edge | representative width, outlier shrink, containment |
| 60-line file | out of memory | windowed viewport around the trace's line |
| Line numbers | yes | yes |
| Syntax highlighting | Pygments via Manim `Code` | same mechanism |
| Animation | typing and glow | step transitions |
| Camera | follows the cursor | static |
| 1080p | crashed | yes |
| 4K | not tested; the per-character design implies about 4× the partial files' cost | native, verified |

It has **no** concept of an active line driven by execution.

### 5.5 wanuo54/code-teaching-video-agent (MIT)

**Profile:** last commit 2026-06-13; **548 lines of Python in total**;
no tests.

**The README claims** AST/CFG analysis, "10 categories, 70+ algorithms"
classification, call-stack animation and `classifier.py`. **Source:**
there is no `classifier.py`, `tree-sitter` is listed in requirements but
**never imported**, and there is no AST or CFG code. All of these claims
are **README only**.

**What exists:**
- `timeline_planner.py` (70 lines): 85% of the speech duration goes to
  animation, with a 0.15 s lead-in;
- `tts_generator.py`: edge-tts, plus `os.system(f"ffmpeg … {concat_list}
  …")` (**shell string**);
- `video_composer.py`.

### 5.6 sakhadib/AiManim (MIT)

**Profile:** last commit 2026-08-08; 1,989 lines; 10 small test files.

**Mechanism (V):** a LangGraph flow; the LLM writes the `construct`
body. `validate_construct_body` walks the AST against
`FORBIDDEN_CALLS`/`FORBIDDEN_ATTRIBUTES`, which is a **denylist**: it is
bypassable through attribute chains, `getattr` and similar.

**Other findings:**
- `executor/runner.py` runs Manim with **no timeout**.
- `CodeFixer` re-asks the LLM up to 3 times.

### 5.7 sohxmg/manim-shorts (MIT)

**Profile:** last commit 2025-11-01; 1,158 lines.

**Stack:** LangChain, Chroma RAG, OpenAI `gpt-4o-mini-tts` (paid) and
MoviePy. `test.py` is a provider "hello" call; there is no test suite.

### 5.8 ManimCommunity/manim-voiceover (MIT)

**Profile:** last commit 2026-06-15; 14 test files.

**Mechanism (V):**
- `VoiceoverScene.voiceover()` synthesises speech **inside the render**,
  and the visuals wait on the audio
  (`wait_for_voiceover`, `wait_until_bookmark`).
- Runtime dependencies include sox, pydub, mutagen and **`pip`**.
- Bookmarks need Whisper (an optional extra).

### 5.9 Manim CE (MIT, last commit 2026-09-29) and 3b1b/manim (MIT, last commit 2026-09-09)

- **Manim CE** is our renderer. Its `Code`, Pygments, `Text` and Cairo
  pieces are all already reused.
- **3b1b/manim (manimgl)** is OpenGL-first, with a different API and a
  different `Code` story. Switching would rewrite every primitive for no
  measured gain.

### 5.10 Job queues (LICENSE files read from the repositories)

| Queue | Licence | Last commit | Infrastructure it needs |
|---|---|---|---|
| Huey | MIT | 2026-09-14 | SQLite, Redis or in-memory; one process is possible |
| RQ | BSD-2-style | 2026-10-01 | Redis |
| Procrastinate | MIT | 2026-09-28 | PostgreSQL |
| Celery | BSD-3 | 2026-09-30 | a broker (Redis or RabbitMQ) and workers |
| Dramatiq | **LGPL-3.0** | 2026-09-14 | Redis or RabbitMQ |

## 6. Capability Matrix

| Capability | Code2Shorts | cs1302-tracer | Code2Video | TheoremExplainAgent | CodeVideoRenderer | manim-voiceover |
|---|---|---|---|---|---|---|
| Executes real user code | ✅ JVM | ✅ JVM (JDI) | ❌ | ❌ | ❌ | ❌ |
| Trace as canonical truth | ✅ mutation events | ✅ line snapshots and heap | ❌ | ❌ | ❌ | ❌ |
| Heap, objects, references | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Grounded educational plan | ✅ ClaimKind and evidence | ❌ | ⚠️ outline, ungrounded | ⚠️ plan, ungrounded | ❌ | ❌ |
| AI output is data, not code | ✅ | n/a | ❌ executes LLM code | ❌ (`shell=True`) | n/a | n/a |
| Code panel driven by execution | ✅ | ❌ | ❌ | ❌ | ❌ typing only | ❌ |
| Speech-fitted timeline | ✅ widen-only fit | ❌ | ⚠️ | ⚠️ | ❌ | ✅ audio-driven, coupled to render |
| Visual critic loop | ❌ | ❌ | ✅ MLLM + grid | ✅ VLM | ❌ | ❌ |
| Learning evaluation | ❌ | ❌ | ✅ TeachQuiz, AES | ✅ eval suite | ❌ | ❌ |
| Probed media validation | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Tests | ✅ 1,024 passing | ✅ 52 files plus CI | ❌ | ❌ | ⚠️ construction only | ✅ 14 |
| 9:16, 16:9 and native 4K | ✅ | n/a | ❌ | ❌ | ❌ | n/a |

## 7. REUSE Candidates

- **Python stdlib `concurrent.futures`/`threading.BoundedSemaphore`** for a
  cost-weighted render bound in `JobManager`. Zero dependencies; fixes the
  measured 4K risk.
- **Already reused, keep reusing:**
  - Manim CE `Code`/Pygments for highlighting;
  - JavaParser;
  - litellm;
  - PyAV;
  - FFmpeg;
  - `srt`;
  - onnxruntime, gruut and Kokoro-82M.

## 8. ADAPT Candidates

- **cs1302-tracer** becomes a second `LanguageAdapter` trace backend
  (§20.1). This is the most valuable finding of the audit.
- **Huey (SQLite backend)** is used **only when** durable jobs are
  required, which means at deployment (§20.2).

## 9. REPLACE Candidates

- **The Java instrumenter, for any *new* language feature.** This is
  conditional on the §20.1 parity spike.
  - Today's subset (arrays, scalars, loops, conditions, simple
    collections) keeps working on the current instrumenter until parity
    is proven.
  - Once parity holds, the instrumenter is **frozen** and then removed.
  - This is a real REPLACE: the JDI tracer is materially more complete for
    the problems on our roadmap, it is tested, and it is maintained by an
    institution.
- **Nothing else qualifies.** No other external component was shown, in
  source or by running it, to be better than ours at our problem.

## 10. KEEP Candidates

- **Grounded planning:** trace-grounded `EducationalPlan` (ClaimKind,
  evidence checks, no duration budget).
- **Visualization plan:** the closed `VisualAction` vocabulary and
  validation against the trace.
- **Scene generation:** repository-owned `build_scene_source`, with
  `repr()`-quoted values.
- **The code panel:** proven better than the only direct competitor
  (§5.4).
- **Layout:** `CompositionLayout`, `VideoProfile` and native 4K.
- **Timing:** narration fit (widen-only), alignment and SRT.
- **Media:** composition (`-c:v copy`, `apad -t`) and probe-based
  validation.
- **LLM layer:** litellm providers, the failover chain, and repair that is
  separate from retry.
- **Generation bookkeeping:** fingerprint, registry and versioning. MLflow
  and DVC are artifact trackers for ML experiments; heavier, and not a
  better fit.
- **Execution seam:** `run_subprocess`, the single sandbox seam.
- **Web UI:** the existing format picker.

## 11. REFERENCE Candidates

| Candidate | What to take from it |
|---|---|
| Code2Video | The **render → inspect → critique** loop and the **anchor-grid** vocabulary. TeachQuiz-style **learning evaluation** for measuring planner quality |
| TheoremExplainAgent | Choosing a representative frame (`image_with_most_non_black_space`); VLM critique prompts; Langfuse-style LLM tracing |
| manim-voiceover | Bookmark-based alignment for sub-segment sync, if ever needed |
| Online Python Tutor format (via cs1302-tracer) | A standard trace interchange format |
| java_visualize | **AGPL-3.0**: concepts only, no code |

## 12. REJECT Candidates

| Candidate | Reason |
|---|---|
| CodeVideoRenderer | Crashed on our cases (`KeyError`, out of memory); clips long lines; typing-demo purpose; MoviePy re-encode breaks our no-transcode rule |
| code-teaching-video-agent | Advertised features are README-only; 548 lines; `os.system` shell string |
| AiManim | Runs LLM-written code behind a denylist filter; no render timeout |
| manim-shorts | No tests; dormant; paid TTS; LangChain plus Chroma footprint |
| Code2Video and TheoremExplainAgent **as dependencies** | Both execute AI-authored code (one with `shell=True`); no tests; TheoremExplainAgent is stale |
| manim-voiceover as our TTS and timing | Couples TTS into the render (no speech caching across renders, and slower re-renders); the visuals wait on audio, which inverts our authoritative timeline; `pip` is a runtime dependency |
| 3b1b/manim | API rewrite for no gain |
| Celery, RQ, Procrastinate **now** | They need broker or database infrastructure the single-instance app doesn't have |
| Dramatiq | LGPL-3.0 and a broker |
| tree-sitter, CFG builders, algorithm classifiers | No consumer in our architecture (§21) |

## 13. Feature Gaps

**HIGH**
1. **Heap, object and reference state**, covering objects, linked lists,
   trees, aliasing and recursion stacks. cs1302-tracer provides it now.
2. **Frame-level deterministic visual QA as a pipeline validator.** Phase
   8.3 already proved the techniques on real frames:
   - content bounding boxes against the safe area;
   - ink ratio;
   - spectral "native" checks.

   Missing pieces: per-step overlap and clipping checks against the
   scene's own mobject bounds (which we can compute exactly, because we
   generate the scene), with a validation result per step.
3. **A bounded render concurrency** (measured: 4K + HD peaks at 2.4 GiB).

**MEDIUM**
4. **Learning-quality evaluation**, TeachQuiz-like. We validate truth but
   never measure whether a viewer learned anything.
5. **Advisory VLM review** of a few representative frames. It would report
   only, with no automatic code rewrite.
6. **LLM observability:** tokens, cost and which provider answered, across
   runs.
7. **Provider `retry-after`:** the Groq 8k tokens/minute limit defeated
   the failover chain in Phase 8.3.

**LOW**
- Typing animation, camera moves, glow.
- RAG over Manim docs (we don't generate Manim code).
- Voice cloning.
- Multi-section parallel rendering.

## 14. Reinvented Wheels (honest list)

| Wheel | Verdict |
|---|---|
| Java source-rewriting instrumenter | **Yes, reinvented.** JDI-based tracing existed and now exists in a maintained, tested, MIT form. The `repr()` overloads and the lambda and nested-class refusals are costs that JDI doesn't have. Ours does give exact condition and array-write events cheaply, which is why this is ADAPT-then-REPLACE, not delete-today |
| Workflow runner and retry (1,757 lines) | **Partly.** Prefect, Dagster or Temporal would be heavier, and the Phase 4.5 audit rejected tenacity correctly. Keep it, but **freeze its scope**: no new orchestration features |
| JobManager threads | **Small.** The bound should be stdlib; durability should be Huey later, not custom |
| Phase documentation volume | **Process cost, not code:** each phase ships thousand-line reports. That is engineering time spent, and is noted in §23 |
| Code panel, layout, timeline, validation | **Not reinvented.** No OSS equivalent passed source or run evidence |

## 15. License Audit (LICENSE files read)

| Candidate | Repository licence | Relevant dependencies | Commercial use | Copyleft risk |
|---|---|---|---|---|
| cs1302-tracer | MIT (`LICENSE`, plus an SPDX header in the README) | asm BSD-3; picocli Apache-2.0; javaparser Apache-2.0/LGPL-3.0 dual (Apache is selectable; already our dependency); gson Apache-2.0 | ✅ | None if used as a **separate CLI jar** via subprocess. Ship its `show-licenses` output |
| Huey | MIT | none mandatory (SQLite is stdlib) | ✅ | None |
| Code2Video, TheoremExplainAgent, manim-voiceover | MIT | — | ✅ (REFERENCE only) | — |
| CodeVideoRenderer, AiManim, manim-shorts, code-teaching-video-agent | MIT | — | REJECTed anyway | — |
| java_visualize | **AGPL-3.0** | — | ⚠️ | **Network copyleft**: do not copy |
| Dramatiq | **LGPL-3.0** | — | ✅ with conditions | REJECTed |

There are no new model licences: Kokoro-82M is Apache-2.0 and already in
use.

## 16. Security Audit

- **Ours:**
  - one subprocess seam: fixed arguments, `shell=False`, `stdin=DEVNULL`,
    timeout, whole-tree kill;
  - AI output is data validated by Pydantic against closed enums;
  - credentials are `SecretStr`;
  - the trace runs in temporary workspaces with in-JVM limits.
- **cs1302-tracer:**
  - The README says **it does not sandbox**.
  - It must therefore run **only** through `run_subprocess`, with the same
    workspace and timeout, and with its own `--timeout-ms`/`--max-*`
    budgets set explicitly.
  - JDI opens a debug connection to the guest JVM, which is a local socket
    or shared memory. Check that the connector is loopback-only and that
    the guest can't reach it. **NOT VERIFIED.**
  - The adoption must preserve the in-JVM loop, call-depth and event caps
    that the instrumenter enforces today (ADR-004).
- **Every LLM-to-Manim project** executes model-authored Python, some via
  `shell=True` or `os.system` f-strings. Adopting any of them would
  silently remove our strongest security property.
- **Huey:** a local SQLite file, with no network listener.

## 17. Maintenance / Maturity (facts, not a score)

| Repo | Last commit | Tests | CI | Notes |
|---|---|---|---|---|
| cs1302-tracer | 2026-09-25 | 52 files | ✅ JDK 21 and 25, Ubuntu and macOS | University-maintained; versioned 3.2.0; documentation site |
| Code2Video | 2026-08-24 | 0 | ❌ | Research artefact (arXiv 2510.01174) |
| TheoremExplainAgent | 2025-07-26 | 0 | ❌ | Research; stale; about 1.5k★ |
| CodeVideoRenderer | 2026-06-20 | 1 file (construction) | ✅ (no render) | Crashed on our cases |
| code-teaching-video-agent | 2026-06-13 | 0 | ❌ | Claims exceed the code |
| AiManim | 2026-08-08 | 10 small | ❌ | Early |
| manim-shorts | 2025-11-01 | 0 | ❌ | Hobby |
| manim-voiceover | 2026-06-15 | 14 | ✅ | Community-maintained |
| Manim CE | 2026-09-29 | extensive | ✅ | Our dependency |
| Huey | 2026-09-14 | ✅ | ✅ | Mature, one maintainer |

I didn't fetch star counts and contributor numbers for every repository:
the GitHub API rate-limited unauthenticated calls. Those values are
**NOT VERIFIED** and weren't used for any decision.

## 18. Dependency Impact

| Adoption | New dependencies | Runtime | Docker | Memory and startup | Licence |
|---|---|---|---|---|---|
| cs1302-tracer | **No Python dependencies**: a 7.3 MB jar, built or vendored | **JDK 21+** (we use 17; JDK 21 is installed locally) | Base image gains JDK 21; Maven is already required | A JVM per trace (3.5 s on our palindrome, including JVM start and compile; not yet compared with the instrumenter's time) | MIT + Apache/BSD |
| Huey (later) | `huey` (pure Python) | SQLite file on the persistent disk | none | negligible | MIT |
| stdlib bound | none | — | — | — | — |

## 19. Integration Architecture

```
LanguageAdapter (existing seam)
 ├─ JavaInstrumenterBackend   (today; frozen)
 └─ JavaJdiBackend            (ADAPT: cs1302-tracer CLI via run_subprocess)
        snapshots ──► SnapshotDiffer ──► ExecutionTrace (unchanged contract)
                         derives VARIABLE_ASSIGN / ARRAY_WRITE (diff), CONDITION_EVALUATED
                         (next-line inference), METHOD_ENTER/EXIT (frame push/pop),
                         + NEW optional heap facts (needs an ADR: contract change)
Everything downstream of ExecutionTrace is untouched.
```

## 20. Migration Plans

### 20.1 Java tracing: instrumenter → cs1302-tracer backend

| Item | Plan |
|---|---|
| **Current** | `tools/java-instrumenter` (JavaParser rewrite) plus `langadapter/java/trace` |
| **Target** | cs1302-tracer `trace --format modern` jar, behind a `JavaJdiBackend` |
| **Why** | Objects, references, heap and full frames are already implemented and tested. Every future data-structure phase stops requiring instrumenter work |
| **Benefit** | Removes the instrumenter's growth path (and the bug class like Phase 6's `collectionVars.clear()`); unlocks linked lists, trees and recursion |
| **Integration point** | `LanguageAdapter.trace()` |
| **Data contract** | `ExecutionTrace` is unchanged. Snapshot→event derivation is a pure function. Heap facts are an additive optional field and need an ADR |
| **Dependencies** | JDK 21+ and the jar |
| **Licence** | MIT (jar dependencies Apache/BSD) |
| **Complexity / regression risk** | MEDIUM / MEDIUM (condition-result inference is the delicate part) |
| **Tests required** | The 5 golden algorithms traced by both backends, with derived events equal to instrumenter events (or differences explained); trace-limit and timeout tests through `run_subprocess`; a JDI-connector isolation test |
| **Rollback** | Backend selection by config, with the instrumenter kept until parity is proven |

### 20.2 Job concurrency

| Item | Plan |
|---|---|
| **Current** | `JobManager`: one thread per distinct request, unbounded |
| **Target, step 1** | A stdlib bounded executor, cost-weighted so that one 4K render counts as several HD |
| **Target, step 2 (deployment)** | Huey with the SQLite backend, for durable jobs across restarts |
| **Why** | Measured 1.75 GiB per 4K render; restarts kill in-flight jobs (Phase 7 audit) |
| **Integration point** | `JobManager.start` |
| **Data contract** | `Job` is unchanged |
| **Complexity / regression risk** | LOW / LOW for step 1; MEDIUM / MEDIUM for step 2 |
| **Tests** | The existing concurrency tests, plus "N+1 4K jobs queue, not run" |
| **Rollback** | A config flag back to unbounded |

### 20.3 Frame-level visual QA (REUSE of our own Phase 8.3 code, plus REFERENCE ideas)

| Item | Plan |
|---|---|
| **Current** | Ad hoc scripts (`frames_8_3.py`), plus unit-level containment tests |
| **Target** | A `FrameQualityValidator` node after render: per-step mobject bounds against the layout boxes and safe area, overlaps, and the share of empty frame. Optional advisory VLM review on 3 frames |
| **Why** | Bugs like the ones Phase 8.3 found (stroke overrun; one portrait step crossing the safe line) are currently found by hand |
| **Integration point** | Next to `FinalValidationNode` |
| **Data contract** | `ValidationResult` |
| **Complexity / risk** | LOW–MEDIUM / LOW (report-only first) |
| **Rollback** | Disable the node |

## 21. Roadmap Changes

| Planned work | Decision |
|---|---|
| More visualization primitives | **MODIFY.** Only as data from the trace backend (heap → panels); no new instrumenter features |
| AST phase | **CANCEL.** JavaParser is already present; nothing consumes more AST |
| CFG phase | **CANCEL.** The real trace is the executed control flow; a static CFG adds unexecuted paths to a video about one execution |
| Algorithm classification | **CANCEL.** It violates rule 7 (no algorithm-specific branching). The competitor's version doesn't exist in source |
| Self-healing rendering | **CANCEL as code repair; MERGE** into data-level repair (layout parameters and plan) driven by the validator |
| Visual critic | **CONTINUE, redesigned:** deterministic frame QA first, then an advisory VLM; never let an LLM write scene code |
| Richer planner | **MODIFY:** measure first (TeachQuiz-style evaluation), then improve against the metric |
| Multi-language support | **MODIFY:** one trace backend per language using that language's debugger or tracer (JDI; Python `sys.settrace`/Python Tutor), not one source instrumenter per language |
| Job queue | **MODIFY:** stdlib bound now, Huey at deployment; no Celery |
| Distributed rendering | **CANCEL** until measured on the target host; nothing suggests it is needed |

## 22. True Code2Shorts Core

I compared this chain against every candidate:

```
Java → real JVM execution → trace (canonical) → grounded educational interpretation
     → state-aware, closed-vocabulary visualization → repository-owned render
     → measured-speech-fitted narration → validated production video
```

- **No OSS project found implements this chain or a better version of it.**
  The closest projects split into two groups that never meet:
  - **tracers** (cs1302-tracer, Python Tutor) have real execution state
    but no video, plan or narration;
  - **video agents** (Code2Video, TheoremExplainAgent) have video but no
    execution, and they execute AI code instead.
- **Ours stays ours:** grounding against the trace, the closed
  visualization vocabulary, the scene generator, the code panel, the
  timeline fit and media validation.
- **May become someone else's:** the *trace producer*.

## 23. 50% Development-Time Reduction

1. **Stop extending the Java instrumenter.** Spike cs1302-tracer (§20.1).
   Every planned data-structure phase shrinks from "instrument, trace,
   reconstruct, visualise" to "derive, visualise". This is the largest
   saving.
2. **Cancel** the AST, CFG, classifier, distributed-render and
   code-self-healing phases outright.
3. **Don't build queue infrastructure.** That's 20 lines of stdlib now,
   and Huey later.
4. **Turn the Phase 8.3 frame-analysis scripts into one validator.** That
   is cheaper than adding a VLM and catches real layout defects.
5. **Delete the six empty placeholder packages.**
6. **Cut per-phase documentation to decision records.** Benchmarks belong
   in JSON next to the outputs; reports should only state decisions and
   deltas.
7. **Freeze the workflow runner's scope.** No new orchestration features.

**Keep:** everything in §10.

## 24. Proposed Future Architecture

```
                         CODE2SHORTS
                              │
          ┌───────────────────┼─────────────────────────┐
        REUSE               KEEP (our core)            COMPOSE
          │                   │                           │
 Manim CE / Pygments    grounded EducationalPlan     JavaJdiBackend =
 JavaParser             closed VisualizationPlan       cs1302-tracer jar
 litellm                scene generator + code panel   + our SnapshotDiffer
 PyAV / FFmpeg / srt    CompositionLayout + profiles  JobManager =
 Kokoro / onnxruntime   timeline fit + alignment       our API + stdlib bound
 stdlib executor        media + frame validation       (+ Huey at deploy)
 (Huey later)           registry / fingerprints       VisualQA =
                        run_subprocess seam            our validator
                                                       (+ advisory VLM)
```

## 25. Evidence / Confidence

| Claim | Evidence | Confidence |
|---|---|---|
| cs1302-tracer traces our palindrome with frames and heap | Built and run here (24 s build, 3.5 s trace, 29 snapshots) | **High** |
| It would reach parity on all 5 golden algorithms | **Not run.** The §20.1 spike is required | **Medium** |
| CodeVideoRenderer is unfit as our code panel | 3 runs here: crash, out of memory, clipping (screenshot) | **High** |
| code-teaching-video-agent's AST/CFG/classifier don't exist | Source grep: no `classifier.py`, no tree-sitter import | **High** |
| LLM→Manim projects execute AI code | `shell=True` (TheoremExplainAgent), denylist (AiManim), unsandboxed (Code2Video) | **High** |
| Huey covers durable single-host jobs with SQLite | LICENSE read; capability from well-known usage, **not run here** | **Medium** |
| JDI connector isolation | **NOT VERIFIED** | — |
| Star and contributor counts | **NOT VERIFIED** (API rate limit) | not used |

---

## Final Decision Table

| Area | Decision |
|---|---|
| Code rendering | **KEEP** (already REUSEs Manim `Code` and Pygments; CodeVideoRenderer REJECTED on evidence) |
| AST | **KEEP** JavaParser as is; **CANCEL** further AST work |
| CFG | **REJECT / CANCEL** |
| Educational planning | **KEEP** (+ REFERENCE Code2Video's evaluation) |
| Visualization planning | **KEEP** |
| Manim generation | **KEEP** repository-owned generation; **REJECT** LLM codegen |
| Visual critic | **REFERENCE** (Code2Video, TheoremExplainAgent): build deterministic frame QA from our own code; VLM advisory only |
| Self-healing | **REJECT** code self-healing; data-level repair only |
| TTS | **KEEP** (Kokoro via ONNX); **REJECT** manim-voiceover |
| Timeline | **KEEP** |
| Validation | **KEEP** (+ add a frame-QA validator) |
| Job management | **KEEP** the API + **REUSE** a stdlib bound now; **ADAPT** Huey at deployment |
| Registry | **KEEP** |
| 4K rendering | **KEEP** |
| UI format selection | **KEEP** |
| *Java execution tracing* | **ADAPT** cs1302-tracer, then **REPLACE** the instrumenter for new features once parity is proven |

## Final Questions

**1. What are we unnecessarily reinventing?**
Java execution tracing, by source rewriting, for every new language
feature. Also, prospectively: queue infrastructure, AST, CFG and
classifiers, if we built them.

**2. Which existing OSS component is demonstrably better than ours?**
cs1302-tracer, for heap, object and reference-rich Java state. It is not
better for exact condition and array-write events on today's subset.
Nothing else was demonstrably better.

**3. What should we REUSE immediately?**
The stdlib bounded executor for render concurrency, and our own Phase 8.3
frame-analysis code as a validator.

**4. What should we ADAPT?**
cs1302-tracer as a trace backend; Huey at deployment.

**5. What should we REPLACE?**
The Java instrumenter for all new features, once the parity spike passes.
Nothing else.

**6. What should we KEEP?**
Everything in §10: the grounded planner, the closed visualization plan,
the scene generator, the code panel, layout and profiles, the timeline
fit, media validation, the registry, the LLM layer and the sandbox seam.

**7. What should we use only as REFERENCE?**
- Code2Video: critic loop, anchor grid, TeachQuiz.
- TheoremExplainAgent: frame selection, VLM prompts, tracing.
- manim-voiceover: bookmarks.
- Python Tutor: trace format.
- java_visualize: concepts only (AGPL).

**8. What should we REJECT?**
CodeVideoRenderer, code-teaching-video-agent, AiManim and manim-shorts;
Code2Video and TheoremExplainAgent as dependencies; manim-voiceover as
the TTS path; 3b1b/manim; Celery, RQ, Procrastinate and Dramatiq now;
tree-sitter, CFG and classifiers.

**9. What capabilities do we currently lack?**
- heap and object state;
- automated frame QA;
- bounded and durable jobs;
- learning-quality evaluation;
- LLM observability;
- honouring provider `retry-after`.

**10. Which future phases should be cancelled or redesigned?**
- **Cancel:** AST, CFG, classification, distributed rendering, and code
  self-healing.
- **Redesign:**
  - visual critic, as deterministic QA first;
  - multi-language, as a debugger-backed trace per language;
  - data-structure phases, via the JDI backend;
  - job queue, as stdlib then Huey.

**11. What should remain the Code2Shorts core?**
Grounded interpretation of a real trace, a closed visualization
vocabulary rendered by trusted code, speech-fitted timing, and probed
validation (§22).

**12. What would you do first to reduce development effort by 50%?**
The cs1302-tracer parity spike on the 5 golden algorithms. A pass removes
the instrumenter from every future roadmap item.

**13. What should we NOT touch because it is already strong and stable?**
- The code panel and the portrait baseline (hash-pinned).
- `CompositionLayout` and native 4K.
- The timeline fit and alignment.
- Media validation.
- The `run_subprocess` seam.
- The closed `VisualAction` vocabulary.
- The registry and fingerprints.

**14. What is the safest adoption sequence?**
1. **Stdlib render bound.** LOW risk; fixes a measured hazard.
2. **Frame-QA validator,** in report-only mode.
3. **cs1302-tracer spike,** behind a config flag, with no contract change:
   derive the existing events and compare against the instrumenter on the
   5 golden algorithms.
4. **An ADR for optional heap facts** in `ExecutionTrace`, then the first
   object or linked-list visualization through the JDI backend.
5. **Freeze, then retire, the instrumenter.**
6. **Huey,** when the deployment phase needs durable jobs.

*Audit only. Nothing was implemented. Awaiting human review.*

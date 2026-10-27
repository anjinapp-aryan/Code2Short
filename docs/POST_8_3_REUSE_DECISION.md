# Post-Phase 8.3: Reuse Decision Matrix

**Date:** 2026-10-01.

**Baseline:** `docs/OPEN_SOURCE_REUSE_AUDIT.md`, treated as hypotheses and
re-verified here.

**Detail and evidence:** `docs/POST_8_3_REUSE_ADOPTION_REPORT.md`.

**Raw research artifacts:** `output/research/post83/`. This holds both trace
sets, the parity report, the isolation probes and the capability runs.

## Evidence labels

| Label | Meaning |
|---|---|
| **S** | verified in source |
| **T** | verified by test |
| **R** | verified by run (here) |
| **P** | partially verified |
| **RM** | README only |
| **N** | not verified |

## Decision values

REUSE · ADAPT · REPLACE · KEEP · REFERENCE · REJECT · IMPROVE.
**NOW** means implemented in this phase; **LATER** means a named future phase.

## Trace / execution

| Capability | Current | OSS candidate | Evidence | Decision | Why |
|---|---|---|---|---|---|
| Java execution and tracing (arrays, scalars, loops, conditions) | JavaParser source instrumenter, 818 Java + 819 Python lines | cs1302-tracer 3.2.0 (MIT, JDI) | R: 165/166 of our events reproduced on the 5 goldens | **KEEP** for the current subset | Parity isn't complete. Return values aren't serialized (0/5), and conditions, reads and loop events are only inferable from source structure. It needs JDK 21 (we run 17) and an argument harness |
| Heap, objects, references, aliasing | none (instrumenter rejects them) | cs1302-tracer | R: linked-list `Node`, generics `Pair<…>`, `ArrayList<Integer>`, inheritance (its own examples) | **ADAPT, LATER** (Phase 8.4) | Materially more capability than any instrumenter extension; behind `LanguageAdapter`, with no contract break |
| Stack frames, block scoping | `call_depth` + `METHOD_ENTER`/`EXIT` | cs1302-tracer | R (frames with locals; block locals leave scope) | **ADAPT, LATER** | as above |
| Condition and loop reconstruction | exact events from instrumentation | derived from snapshots plus the source AST | R (45/45, 21/21, using a regex prototype) | **KEEP** instrumenter events; the JDI path needs JavaParser-based inference | JDI never re-steps a `for` header; the condition must be inferred from structure (§5.3 of the report) |
| Exceptions | `EXCEPTION_THROWN` event | cs1302-tracer | R: uncaught exception appears as **stderr text only** | **KEEP** | No structured exception event in its output |
| Alternative trace foundations | — | bstee615/jdi-tracer; TucanTrace; java_jail/java_visualize; JVMTI tracers | S/RM (§6 of the report) | **REJECT** | jdi-tracer: no licence, last commit 2022. TucanTrace: 7 commits, 0 tests. java_visualize: AGPL-3.0. JVMTI tracers: AGPL or dormant since 2013–2023 |

## AI / planning

| Capability | Current | OSS candidate | Evidence | Decision | Why |
|---|---|---|---|---|---|
| Explanation, educational plan, grounding, ClaimKind | ours | Code2Video planner, TheoremExplainAgent planner | S (neither grounds against execution) | **KEEP** | Unique: evidence checked against a real trace |
| Structured output and repair | `generate_structured` + `ai/repair.py` | Instructor (rejected in Phase 5) | S | **KEEP** | Unchanged reasoning (ADR-5.4) |
| Provider abstraction and failover | litellm + chain | — | T | **KEEP** | Already reuses litellm |
| LLM observability (tokens, latency) | logged chars only | Langfuse, OpenLLMetry, OpenTelemetry | N (not inspected this pass); litellm `usage` R | **IMPROVE, NOW**: log usage and latency; **REFERENCE, LATER**: OpenLLMetry | The data was already in the litellm response. A platform needs infrastructure and no measured need exists |
| Learning evaluation | none | Code2Video `eval_TQ.py` (TeachQuiz) | S | **REFERENCE** | No evidence yet that a quiz metric moves our planner; prototype only after a measurement plan |

## Visualization

| Capability | Current | OSS candidate | Evidence | Decision | Why |
|---|---|---|---|---|---|
| Scene generation, primitives, layout, code panel, VideoProfile | ours | CodeVideoRenderer, LLM→Manim projects | R (CodeVideoRenderer: crash, out of memory, clipping); S (others execute AI code) | **KEEP** | Nothing matched execution-driven, deterministic, native 4K, fixed viewport and no AI code |
| Syntax highlighting | Manim `Code` / Pygments | — | S | **REUSE** (already) | — |
| Frame QA | Phase 8.3 one-off scripts (numpy + PyAV) | OpenCV, scikit-image, imagehash, pixelmatch | R (our methods, Phase 8.3); N (others not run) | **IMPROVE, LATER** (Phase 8.5): report-only validator on numpy + PyAV | The techniques are proven and their dependencies already installed. It needs a warning-severity `ValidationResult` first, so it isn't done now |
| VLM visual critic | none | Code2Video, TheoremExplainAgent | S | **REFERENCE** | Both rewrite AI code; advisory review only, after deterministic QA |

## Audio / video

| Capability | Current | OSS candidate | Evidence | Decision | Why |
|---|---|---|---|---|---|
| TTS (Kokoro via ONNX) | ours | manim-voiceover | S | **KEEP** | manim-voiceover couples TTS to the render and depends on `pip`/sox |
| Timing, alignment, SRT | ours + `srt` | manim-voiceover bookmarks | S | **KEEP** / **REFERENCE** | — |
| Manim, FFmpeg, PyAV composition and validation | ours on OSS | — | T, R | **KEEP** / **REUSE** | Protected; verified in Phase 8.3 |

## Execution safety

| Capability | Current | OSS candidate | Evidence | Decision | Why |
|---|---|---|---|---|---|
| Sandbox seam (fixed arguments, timeout, tree-kill) | `run_subprocess` | cs1302-tracer's own limits | R: `--timeout-ms` gives exit 3 and the guest is killed; **no sandbox** (README + run) | **KEEP** | Any JDI backend must run *inside* this seam |

## Generation / orchestration

| Capability | Current | OSS candidate | Evidence | Decision | Why |
|---|---|---|---|---|---|
| Fingerprints, registry, versioning, dedupe | ours | MLflow, DVC | S (Phase 8.2D, 8.3) | **KEEP** | Protected; fits |
| Workflow runner, retry, checkpoints | ours | Prefect, Dagster, Temporal | S | **KEEP** (freeze scope) | No measured need |
| Render concurrency | **unbounded** | stdlib `threading.Condition`; Huey, RQ, Procrastinate, Celery, Dramatiq | T (new tests); licences S | **IMPROVE, NOW**: FIFO weighted render gate (HD=1, 4K=4, budget 5) | The measured hazard is solved in-process with zero dependencies |
| Durable jobs across restarts | none | Huey (MIT, SQLite) | S (licence); N (not run) | **ADAPT, LATER** (deployment phase) | Only needed once a server restarts mid-job |
| Celery, RQ, Procrastinate, Dramatiq | — | — | S (licences) | **REJECT** now | They need broker or database infrastructure; Dramatiq is LGPL-3.0 |

## Codebase hygiene

| Capability | Current | OSS candidate | Evidence | Decision | Why |
|---|---|---|---|---|---|
| Six empty Phase-0 placeholder packages | 6 files, comments only, no imports | — | S (grep: zero references) | **IMPROVE, NOW**: deleted | Dead code |
| `codegen.java.JavaCodeGenerator` | stub raising `NotImplementedError`; 1 test | — | S | **LATER** (separate decision) | `codegen.base.GeneratedCode` is load-bearing; the stub isn't |

## Web

| Capability | Current | OSS candidate | Evidence | Decision | Why |
|---|---|---|---|---|---|
| Generation UI, format selection, jobs, details, history | ours | — | T | **KEEP** | — |

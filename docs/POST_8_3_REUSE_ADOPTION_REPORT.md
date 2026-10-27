# Post-Phase 8.3: Reuse Adoption Report

**Date:** 2026-10-01. **Order followed:** research → verify → compare →
decide → implement → test → report.

**Decision matrix:** `docs/POST_8_3_REUSE_DECISION.md`.

**Evidence labels:**

| Label | Meaning |
|---|---|
| **S** | verified in source |
| **T** | verified by test |
| **R** | verified by run |
| **P** | partial |
| **RM** | README only |
| **N** | not verified |

**Research artifacts** (new directories only; nothing existing was
overwritten) live under `output/research/post83/`:

| Path | Contents |
|---|---|
| `traces/ours/*.json` | our instrumenter on the 5 goldens |
| `traces/cs1302_run3/*.{modern,pytutor}.json` | cs1302-tracer on the same 5 |
| `cs1302_ws_run{1,2,3}/` | copied sources and harnesses; runs 1–2 are kept as evidence of the failed attempts |
| `parity_report.json` | per-event parity |
| `isolation/` | Sleeper, Spin and `isolation.txt` |
| `capabilities/` | cs1302-tracer's own examples, traced |

## 1. Executive Summary

- **Hypotheses re-verified:** I tested the previous audit's 14 hypotheses.
  - 12 hold.
  - 1 holds with an amendment: cs1302-tracer, which is **ADAPT later, not
    REPLACE**.
  - 1 is corrected: "stop AST". A JDI backend *still needs* JavaParser,
    to reconstruct conditions and reads from source structure.
- **cs1302-tracer, measured on the five golden algorithms:**
  - It reproduces **165 of our 166 semantic events** (§5).
  - It serializes **no return values** (0/5).
  - Its condition, loop and read events exist only by inference from
    source structure, because JDI never re-steps a `for` header.
  - It requires JDK 21 (we run 17) and cannot pass program arguments.
  - **Parity is therefore not demonstrated, and nothing is replaced.**
  - It is still the best trace foundation found: no newer or better JDI,
    JVMTI or Tutor-style engine exists (§6).
- **Implemented**, as three small changes that each passed their tests:
  1. a FIFO **weighted render gate** (stdlib), which bounds 4K memory;
  2. **LLM token-usage and latency logging**;
  3. deletion of six **dead placeholder packages**.

  No protected component was modified.
- **Tests:**
  - **Before:** 1024 passed, 51 failed, 27 skipped.
  - **After:** 1038 passed, 51 failed, 26 skipped.
  - The **failing set is identical**, and there are no new failures.

## 2. Current Code2Shorts Architecture (source-verified)

The pipeline is unchanged from the previous audit §2 (S):

```
web → TeachingConfig/fingerprint → JobManager (thread per request)
    → workflow nodes: compile → trace (JavaParser instrumenter) → explain → educational plan
      → visualization plan → narration → narration timing (Kokoro, widen-only fit)
      → render (ManimVideoRenderer, profile/layout-driven, native 4K)
      → compose (FFmpeg -c:v copy) → final validation → registry
```

**This phase changes three things:**
- the render node receives `GatedRenderer(ManimVideoRenderer)`;
- providers log usage;
- the placeholders are gone.

**Discrepancies between documentation, source and runtime:**

| Documentation says | Source / runtime says |
|---|---|
| CLAUDE.md: "Phases 0–5 complete; 465 tests" | 1115 collected; Phase 8.3 complete. Stale |
| CLAUDE.md: "turns a Java algorithm into a 1080×1920 short" | Four profiles, including native 4K (S, R). Stale |
| CLAUDE.md placeholder paragraph | The placeholders are now deleted, and the paragraph is updated |
| `Settings.rendering_timeout_seconds = 300` | The web path hard-codes 900 s in `build_renderer` (S). Inconsistent, but harmless |
| `codegen.java.JavaCodeGenerator` presented as a code generator | A `NotImplementedError` stub (S). Dead |
| Previous audit: "cs1302-tracer traced palindrome" | True only with the `package` line removed and the input hard-coded. Its CLI cannot pass arguments (S, R) |

## 3. Previous Audit Findings, Revalidated

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| 1 | cs1302-tracer is the strongest candidate | **Confirmed** | §5, §6 |
| 2 | Stop growing the Java instrumenter | **Confirmed, with a nuance:** freeze it for *new* (heap and object) features; it stays authoritative for today's subset | §5 |
| 3 | Stop AST expansion | **Amended.** No AST *product* phase, but JavaParser stays: the JDI path needs it to infer conditions and reads | §5.3 |
| 4 | Cancel CFG | **Confirmed** | The trace is the executed path |
| 5 | Cancel classification | **Confirmed** | Rule 7; the competitor's classifier doesn't exist (S) |
| 6 | Reject LLM-generated Manim | **Confirmed** | §15 |
| 7 | Keep repository-owned rendering | **Confirmed** | §10 |
| 8 | Add deterministic frame QA | **Confirmed need; deferred** | Needs a warning severity on `ValidationResult` (§22) |
| 9 | Bound job concurrency | **Implemented** at the render stage | §21 |
| 10 | Huey later | **Confirmed later** | §9 |
| 11 | Reject CodeVideoRenderer | **Confirmed** (previous audit's run evidence) | — |
| 12 | Code2Video and TheoremExplainAgent as reference only | **Confirmed** | — |
| 13 | manim-voiceover must not replace timing | **Confirmed** | — |
| 14 | Grounded pipeline is differentiated | **Confirmed** | No candidate grounds explanation in execution |

## 4. Fresh Research Performed

- **GitHub search API, this session**, sorted by update date, with the
  JDI, JVMTI and visualizer queries listed below. The first session (the
  previous audit) used star-sorted queries.

  | Query | Notable hits |
  |---|---|
  | `jdi tracer java` | cs1302-tracer; bstee615/jdi-tracer |
  | `jvmti tracer` | rdbo/java-method-tracer (AGPL, 2023); neuroo/java-runtime-tracer (2013) |
  | `java visualizer execution heap` | JavaFX demos, 0★ |
  | `java tutor visualizer` | daveagp/java_visualize (AGPL); OPT backups |
  | `java execution trace students` | Stellel-One/TucanTrace (MIT, 2026); a concurrency tracer |
  | `program visualization java education`, `sys.settrace visualizer` | toy projects |

- **Cloned and inspected (S):**
  - cs1302-tracer, TucanTrace and java_visualize;
  - all previous-audit repositories;
  - the LICENSE files of Huey, RQ, Procrastinate, Celery and Dramatiq.
- **Run (R):**
  - cs1302-tracer on the 5 goldens;
  - its isolation and limit probes;
  - five of its own capability examples;
  - our instrumenter on the 5 goldens;
  - one live Groq call to verify usage logging.
- **Not run (N):**
  - Langfuse, OpenLLMetry and OpenTelemetry;
  - OpenCV, scikit-image, imagehash and pixelmatch;
  - Huey.

  Their decisions below don't depend on running them.

## 5. cs1302-tracer: Deep Verification (highest-priority hypothesis)

### 5.1 Integration facts

| Item | Finding | Evidence |
|---|---|---|
| JDK | 21+ required; built and ran on 21.0.11. We run 17 | RM, CI S, R |
| CLI | `trace -i FILE -a -f modern\|pytutor`, plus `--timeout-ms`, `--max-*`, `--result-envelope` | R |
| Program arguments | **Not supported.** `GuestLauncher` sets only `main` on the launching connector | S, R |
| Workaround | A generated public harness in the same package calls `Main.main(new String[]{input})` | R (all 5 traced) |
| Input file | Must contain **exactly one public top-level type**; non-public harness → `IllegalArgumentException` | R (run 2 failed) |
| Packages and multi-file | Works when the harness sits in the package directory (`reverse_string` has 2 files) | R |
| stdin | `--stdin` / `--stdin-file` options exist; not exercised | S |
| stdout / stderr | Trace JSON on stdout; guest stdout is embedded per step (`"OLLEH\r\n"`) | R |
| Timeout | `--timeout-ms 3000` on an infinite loop → **exit 3** after 3.7 s with "Trace stopped: timeout". The default deadline is 10 s (10.7 s observed) | R |
| Process-tree termination | The guest JVM was gone after the timeout (Windows) | R |
| Trace limits | 10k snapshots, 1 MiB stream, heap and element caps, 64 MiB trace data (defaults) | S (`docs/BOUNDED_TRACING.md`) |
| Memory | Not measured separately; output 35–116 KB per golden | P |

### 5.2 Isolation (JDI connection)

| Question | Finding | Evidence |
|---|---|---|
| Transport on Windows | `-Xrunjdwp:transport=dt_shmem,address=javadebug43182,suspend=y`: shared memory, **no TCP socket** (none found for either process) | R |
| Transport on Linux | Expected `dt_socket` on loopback | **N** |
| Guest JVM | Plain `java` with `-classpath <temp dir>`; **no sandbox, no security manager**; its README says it does not sandbox | R, RM |
| Guest → debugger / debugger → guest | The debugger controls the guest; no evidence the guest can reach the debugger beyond the shared-memory name | P |

**Consequence:** a JDI backend must run inside `run_subprocess` (fixed
arguments, timeout, tree-kill, temp workspace), as every tool does today.
The Linux socket exposure must be verified before any server use.

### 5.3 Semantic parity on the five goldens (no new goldens)

**Method:**
1. Our trace, from `JavaAdapter.trace`, is the reference.
2. cs1302-tracer's line snapshots are converted to our event vocabulary:
   - frame push and pop give **enter and exit**;
   - local diffs give **assignments**, including the caller's frame after
     a return;
   - heap-array diffs give **writes**;
   - source evaluation of `x[i]` gives **reads**;
   - the next-line branch inside or outside the block, plus `for`-loop
     back-edges, gives **conditions and loop iterations**.
3. Matching uses ordered `difflib` on (type, line, variable, value).

The research differ is a regex prototype (`research_parity.py`); a
production version would use JavaParser.

| Event type | Ours | Reproduced | Class |
|---|---|---|---|
| METHOD_ENTER | 5 | 5 | **DERIVED** (frame push; the line differs: ours is the declaration, theirs the first body line) |
| METHOD_EXIT | 5 | 5 | **DERIVED** (frame pop) |
| METHOD_EXIT `return_value` | 5 | **0** | **MISSING** (`MethodExitEvent` is handled but never serialized, S) |
| VARIABLE_ASSIGN | 50 | 49 | **DERIVED** (the 1 miss is `StringBuilder out`: its object value needs `toString` → PARTIAL) |
| ARRAY_WRITE | 12 | 12 | **DERIVED** (heap diff) |
| ARRAY_READ | 28 | 28 | **PARTIAL** (not in the output; inferred by evaluating the source) |
| CONDITION_EVALUATED | 45 | 45 | **PARTIAL** (inferred from the next line and the block extent) |
| LOOP_ITERATION | 21 | 21 | **PARTIAL** (inferred; our counter is per method, not per loop, so only the line was compared) |
| EXACT (event-for-event) | — | 0 | Snapshot semantics differ from mutation events by design |

**Total:** 165 of 166 events matched; plus 5 return values missing.

**Key behavioural finding (R): JDI line-stepping never revisits a `for`
header after the first entry.** The steps go body end → body start, with
the induction variable already incremented. Per-iteration condition
checks and updates are therefore invisible as steps; they can only be
reconstructed from loop structure. A JDI backend *needs* source
structure, so JavaParser stays.

**Capabilities beyond ours, run on its own examples (R):**
- linked list: `Node` objects with fields;
- generics: `Pair<Integer,Boolean>`;
- `ArrayList<Integer>` with boxed elements;
- inheritance, with stack depth up to 4.

An uncaught exception appears only as **stderr text**; there is no
structured event.

**Performance (R, wall time including JVM start and compile):**

| | Per golden |
|---|---|
| Ours | 2.0–2.4 s |
| cs1302-tracer | 1.7–3.8 s (typically ~3.5 s) |

**Decision: ADAPT, LATER (Phase 8.4), and do NOT replace.** Parity is not
demonstrated because of:
- the missing return values;
- the inference-only conditions, loops and reads;
- JDK 21;
- the argument harness.

The way to close the gap is a `JavaJdiBackend` behind `LanguageAdapter`
with JavaParser-based inference, run beside the instrumenter. Return
values could come from a small upstream contribution, since
`MethodExitEvent` is already handled (S).

## 6. Better Trace Alternatives? (Phase 6)

These are ranked internally by evidence: correctness, security, trace
quality, maintainability, integration cost, performance, licence and fit.

| Candidate | Evidence | Verdict |
|---|---|---|
| **cs1302-tracer** | MIT; JDI; tests and CI (JDK 21 and 25); bounded; maintained in 2026 | **Best found** |
| bstee615/jdi-tracer | No licence; last commit 2022; 1★ (S via search metadata) | REJECT (licence) |
| Stellel-One/TucanTrace | MIT; JDI client + agent; 7 commits; **0 test files**; UML focus (S) | REJECT (maturity) |
| java_jail / java_visualize | AGPL-3.0 (S); last commit 2019 | REFERENCE only (concepts) |
| rdbo/java-method-tracer (JVMTI) | AGPL-3.0; 2023 | REJECT |
| neuroo/java-runtime-tracer (JVMTI) | No licence; 2013 | REJECT |

**Answer:** yes, cs1302-tracer is the best reusable trace foundation
found, but it is not yet a drop-in.

## 7. REUSE Decisions

- **Python stdlib** `threading.Condition` and `collections.deque` for the
  render gate. **NOW, implemented.**
- **litellm's `response.usage`** for observability. **NOW, implemented.**
- **Already reused, unchanged:**
  - Manim CE `Code`/Pygments;
  - JavaParser;
  - litellm;
  - PyAV;
  - FFmpeg;
  - `srt`;
  - Kokoro, onnxruntime and gruut;
  - numpy (frame QA, later).

## 8. ADAPT Decisions

| Candidate | When | Notes |
|---|---|---|
| cs1302-tracer | **LATER**, Phase 8.4 | §5, §26 |
| Huey (SQLite) | **LATER**, deployment phase | Durable jobs |

## 9. REPLACE Decisions

**None.** No candidate has demonstrated parity.

## 10. KEEP Decisions

- **Core:** the instrumenter (current subset), the grounded planner, the
  closed visualization vocabulary and the scene generator.
- **Rendering and media:** the code panel, `CompositionLayout`,
  `VideoProfile` and native 4K; Kokoro TTS, timing and SRT; media
  validation.
- **Infrastructure:** the registry, fingerprints, the workflow runner
  (scope frozen) and the web UI.

## 11. REFERENCE Decisions

| Candidate | What to take |
|---|---|
| Code2Video | TeachQuiz evaluation; critic loop; anchor grid |
| TheoremExplainAgent | VLM review prompts |
| manim-voiceover | Bookmarks |
| java_visualize / java_jail | Architecture (AGPL: no code) |
| OpenLLMetry / Langfuse | Later, if log-based usage isn't enough |

## 12. REJECT Decisions

- jdi-tracer, TucanTrace and the JVMTI tracers (§6).
- Celery, RQ, Procrastinate and Dramatiq, for now.
- OpenCV and other heavy computer-vision dependencies for frame QA.
- Everything rejected in the previous audit: CodeVideoRenderer,
  code-teaching-video-agent, AiManim, manim-shorts, and the LLM→Manim
  generators.

## 13. IMPROVEMENT Decisions

| Improvement | Status |
|---|---|
| Render gate | **NOW, done** |
| Usage and latency logging | **NOW, done** |
| Delete placeholder packages | **NOW, done** |
| Frame-quality validator, report-only (numpy + PyAV, the Phase 8.3 techniques) | **LATER**, Phase 8.5; needs a warning-severity `ValidationResult` first |
| Honour provider `retry-after` inside the failover chain | **LATER** (Groq 8k tokens/minute, observed in Phase 8.3) |

## 14. License Audit

| Component | Licence (LICENSE file read) | Dependencies | Copyleft | Commercial use |
|---|---|---|---|---|
| cs1302-tracer | MIT | asm BSD-3; picocli Apache-2.0; javaparser Apache-2.0/LGPL-3.0 (dual; already ours); gson Apache-2.0 (S, pom) | none if run as a separate jar | ✅; ship its `show-licenses` output |
| Huey | MIT | none mandatory | none | ✅ |
| RQ | BSD-2-style | Redis | none | ✅ |
| Celery | BSD-3 | broker | none | ✅ |
| Procrastinate | MIT | PostgreSQL | none | ✅ |
| Dramatiq | **LGPL-3.0** (COPYING.LESSER) | broker | weak | rejected |
| TucanTrace | MIT | — | — | rejected (maturity) |
| java_visualize | **AGPL-3.0** | — | **network copyleft** | reference only; **no code copied** |
| rdbo/java-method-tracer | **AGPL-3.0** | — | network copyleft | rejected |

**Implemented changes:** they add **no dependency**, and no third-party
code was copied.

## 15. Security Audit

| Item | Status |
|---|---|
| New code | Pure in-process Python: no subprocess, no `exec`/`eval`, no network |
| Usage logging | Logs numbers only. The test asserts that no prompt, response text or key appears, and the existing AST guards (`test_no_provider_module_logs_the_key`, the failover log guard) still pass |
| cs1302-tracer, Windows | Executes guest code **unsandboxed**; JDWP via shared memory; timeout kills the guest |
| cs1302-tracer, Linux | Socket exposure **not verified**. **Blocker for production use** until a test confirms loopback-only binding and the guest can't attach |
| LLM→Manim projects | Execute model-authored code (`shell=True`, `os.system`, denylist filters): rejected for the execution pipeline (previous audit, S) |

## 16. Performance Findings

| Topic | Finding | Evidence |
|---|---|---|
| Trace time | Ours 2.0–2.4 s; cs1302-tracer 1.7–3.8 s per golden | R |
| Render gate overhead | One condition-variable acquire per render, negligible next to a 115–260 s render | T |
| Gate policy | Measured basis (Phase 8.3): 4K + HD ≈ 2.4 GiB is admitted at budget 5; 2× 4K is serialized | T |

## 17. Testing Findings

| Test file | Tests | What it covers |
|---|---|---|
| `tests/test_render_gate.py` | **9** | cost; default budget; never over budget under contention; clamped oversize; FIFO without starvation; delegation; release on failure; real factory wiring with one shared gate; two 4K renders never overlap while 4K + HD do |
| `tests/test_llm_usage_logging.py` | **4** | both providers log tokens and latency; prompt, response text and key are never logged; a missing `usage` is tolerated |

- **Verified live:** a Groq call logged `prompt_tokens=78
  completion_tokens=47 latency_ms=3109`.
- **Weakened or deleted tests:** none.

## 18. Development Work Eliminated

| Planned work | Previously | New approach | Saving (no percentages claimed) |
|---|---|---|---|
| Instrumenter support for objects, references and collections | Extend the JavaParser rewriting per feature | JDI snapshots plus derivation (Phase 8.4) | Each new data-structure feature stops needing instrumentation code. Evidence: objects, generics, lists and inheritance already work (R) |
| AST *product* phase | Planned | Cancelled; JavaParser kept as infrastructure only | The phase |
| CFG phase | Planned | Cancelled (the executed path is in the trace) | The phase |
| Algorithm classifier | Planned | Cancelled (rule 7) | The phase |
| Code-level self-healing | Planned | Cancelled; data-level repair only | The phase |
| Distributed rendering | Planned | Cancelled (single host, gate) | The phase, plus infrastructure |
| Job-queue infrastructure | Redis or Celery considered | A 102-line stdlib gate now (including docs); Huey only for durability | Broker operations |
| Custom QA framework | Considered | A validator on existing numpy + PyAV | Framework adoption and maintenance |

## 19. New Capabilities Unlocked

| OSS / improvement | Capability unlocked | Future phase |
|---|---|---|
| cs1302-tracer | heap, objects, references, aliasing, linked lists, trees, recursion stacks, generics | 8.4 (adapter), then data-structure visuals |
| Render gate | Safe concurrent 4K beside HD | Now |
| Usage logging | Per-request tokens and latency: predict free-tier tokens-per-minute limits, compare providers | Now; cost reporting later |
| Frame QA validator | Automatic visual regression per step | 8.5 |
| TeachQuiz-style evaluation | A measurable learning quality metric | After a measurement plan |

## 20. Architecture Changes

```
RenderVideoNode(GatedRenderer(ManimVideoRenderer(...), shared WeightedGate, cost))
```

Additional changes:
- **Gate:** one per `DefaultPipelineFactory`, so per web app.
- **Budget:** `Settings.render_budget_units`, default 5 and at least 1.
- **Cost:** `ceil(pixels / 1920×1080)`.

Not changed:
- `build_renderer`, `ManimVideoRenderer` and the scene source;
- every contract, the protected components and the accepted scenes. The
  portrait hash test passes.

## 21. Implemented Changes

| File | Change |
|---|---|
| `src/code2shorts/visualization/render_gate.py` | **new**: `render_cost`, `WeightedGate` (FIFO, clamp), `GatedRenderer` |
| `src/code2shorts/webapp/pipeline.py` | shared gate in `DefaultPipelineFactory`; render node wrapped |
| `src/code2shorts/config.py` | `render_budget_units: int = Field(default=5, ge=1)` |
| `src/code2shorts/ai/providers/openai_compatible.py`, `gemini.py` | the existing response log line gains `prompt_tokens`, `completion_tokens`, `latency_ms` |
| `src/code2shorts/{animation,compose,lessonplan,qa,render,tts}/` | **deleted** (comment-only `__init__.py`, zero references) |
| `CLAUDE.md` | placeholder paragraph updated |
| `tests/test_render_gate.py`, `tests/test_llm_usage_logging.py` | **new** (13 tests) |
| `docs/POST_8_3_REUSE_DECISION.md`, this report | **new** |

## 22. Deferred Changes

| Change | Why deferred |
|---|---|
| cs1302-tracer adapter (8.4) | Parity not demonstrated |
| Frame QA validator (8.5) | Needs a warning severity; otherwise it would fail on pre-existing portrait content |
| Huey (deployment) | No restart durability needed yet |
| Provider `retry-after` | Separate provider change |
| `JavaCodeGenerator` stub removal | Has a test; separate decision |
| CLAUDE.md "current state" refresh | Documentation only |

## 23. Cancelled Roadmap Items

- AST product phase.
- CFG.
- Algorithm classification.
- Code-level self-healing and LLM-written Manim.
- Distributed rendering.
- Broker-based job queue (for now).

## 24. Regression Results

| | Passed | Failed | Skipped |
|---|---|---|---|
| Before (end of 8.3) | 1024 | 51 | 27 |
| After | 1038 | 51 | 26 |

- **New failures:** none. The 51 failing tests are byte-for-byte the same
  set, all pre-existing:
  - Maven and JDK not on the suite's PATH;
  - SAPI;
  - the comment-grep rule-7 test (it flags `code_state.py` and
    `primitives.py`, not new code).
- **Where the 14 extra passes come from:**
  - 13 are new tests;
  - **1 is a live Gemini integration test** that was skipped for quota at
    the end of 8.3. The quota has since reset. That pass isn't caused by
    this phase.
- **Protected areas:** portrait scene hash, landscape layout, 4K native and
  format-selection suites all pass.

## 25. Risks

| Risk | Note |
|---|---|
| Gate budget is pixel-based | Not memory-based. It's conservative locally (4K is 2.7× HD measured, 4 assumed); on a smaller host the budget must be lowered by configuration |
| Gate is in-process | A multi-process deployment needs a shared gate. The current deployment is a single instance |
| cs1302-tracer: JDK 21 | Required |
| cs1302-tracer: Linux transport | Unverified |
| cs1302-tracer: upstream dependency | Missing return values need an upstream change or a fork |
| Inference correctness | The research differ is regex-based; production needs JavaParser and tests on all goldens |

## 26. Recommended Roadmap

1. **Phase 8.4 (spike, behind a config flag):** `JavaJdiBackend`.
   - cs1302-tracer jar invoked via `run_subprocess`;
   - generated harness;
   - JavaParser-based inference;
   - `ExecutionTrace` unchanged.
   - **Exit criteria:** 166/166 events on the 5 goldens, return values
     included (via an upstream PR or a minimal fork), a Linux isolation
     test, and the JDK 21 decision.
2. **Phase 8.5:** `FrameQualityValidator`, report-only (a warning
   severity), reusing the Phase 8.3 frame techniques.
3. **Then:** the first heap or object visualization through the JDI
   backend (an ADR for optional heap facts).
4. **Deployment phase:** Huey for durability, gate budget per host, and
   the Linux JDI isolation test.

---

## Final Decision Table

| Area | Decision | Evidence | Confidence |
|---|---|---|---|
| Java tracing, current subset | KEEP | R (parity study) | High |
| Java heap and objects | ADAPT cs1302-tracer, LATER | R | Medium-high |
| Replacing the instrumenter | NOT NOW (no parity) | R: 165/166, returns 0/5 | High |
| JavaParser / AST | KEEP as infrastructure; cancel the AST phase | R (JDI needs structure) | High |
| CFG, classifier | REJECT / CANCEL | S | High |
| Planner, grounding | KEEP | S | High |
| LLM-generated Manim | REJECT | S | High |
| Renderer, code panel, layout, profiles, 4K | KEEP | R, T | High |
| Frame QA | IMPROVE, LATER (numpy + PyAV) | R (8.3) | Medium |
| VLM critic | REFERENCE | S | Medium |
| TTS, timing | KEEP | T | High |
| Render concurrency | IMPROVE, NOW (stdlib gate) | T | High |
| Durable jobs | ADAPT Huey, LATER | S (licence) | Medium |
| Brokers (Celery, RQ, Dramatiq) | REJECT now | S | High |
| LLM observability | IMPROVE, NOW (usage logging); REFERENCE OpenLLMetry | R | High / Low |
| Learning evaluation | REFERENCE (TeachQuiz) | S | Low-medium |
| Multi-language | One debugger or tracer backend per language behind `LanguageAdapter` | P (OPT/java_jail design, RM; cs1302-tracer emits OPT-compatible traces, R) | Medium |
| Placeholders | Deleted | S, T | High |

## Final Architecture

```
                 CODE2SHORTS
                      |
       ┌──────────────┼──────────────────┐
     REUSE           KEEP               ADAPT (later)
       │              │                   │
 stdlib gate     instrumenter (subset)  cs1302-tracer (JDI) behind
 litellm usage   grounded planner         LanguageAdapter + JavaParser
 Manim/Pygments  closed viz plan          inference → ExecutionTrace
 PyAV/FFmpeg     scene gen + code panel  Huey (durable jobs, deploy)
 Kokoro/ONNX     layouts/profiles/4K
 numpy (QA)      timing + validation
       └──────────────┼──────────────────┘
                      ▼
 Execution → Trace → Educational Plan → Visualization Plan → Trusted Renderer
   (GatedRenderer) → Audio/Timing → Media Validation → MP4
```

## Most Important Questions

**1. What are we still reinventing?**
Only potentially: object, heap and collection tracing by source rewriting,
if we extended the instrumenter. Nothing else found qualifies.

**2. What can we stop building?**
Instrumenter features for objects and heap, AST/CFG/classifier phases,
code self-healing, distributed rendering, and broker queues.

**3. What should we reuse immediately?**
The stdlib render gate and litellm usage data. Both are done.

**4. What should we adapt?**
cs1302-tracer, in Phase 8.4 behind a flag. Huey at deployment.

**5. What has earned replacement status?**
Nothing yet. cs1302-tracer reached 165/166 events, but has 0/5 return
values and inference-only conditions.

**6. What should remain untouched?**
The scene generator, code panel, layouts, profiles, native 4K, Kokoro and
timing, media validation, the registry and fingerprints, the plan
contracts, and `run_subprocess`.

**7. What should only be referenced?**
TeachQuiz, VLM critics, manim-voiceover bookmarks, java_jail (AGPL), and
Langfuse/OpenLLMetry.

**8. What should be rejected?**
- jdi-tracer, TucanTrace and the JVMTI tracers;
- the brokers (for now);
- OpenCV for QA;
- the LLM→Manim projects;
- CodeVideoRenderer.

**9. What new capabilities become possible?**
- Heap, objects, linked lists, trees and recursion (JDI).
- Safe 4K concurrency.
- Token and latency visibility.
- Automated frame regression (8.5).

**10. Which roadmap phases should be cancelled?**
AST product, CFG, classifier, self-healing, and distributed rendering.

**11. Which phases should be redesigned?**
- **Data-structure visuals:** via the JDI backend.
- **Visual critic:** deterministic QA first.
- **Multi-language:** a debugger or tracer per language.
- **Jobs:** stdlib gate, then Huey.

**12. What is the safest next phase?**
Phase 8.4: the cs1302-tracer adapter spike behind a flag, with the
instrumenter remaining the default. Exit only on full parity.

**13. What is the smallest implementation that gives the largest reduction
in future effort?**
The Phase 8.4 parity harness. That means turning `research_parity.py`
into a JavaParser-based differ plus a test over the 5 goldens. Once it
passes, every future object or data-structure feature needs no
instrumenter work.

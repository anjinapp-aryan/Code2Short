# Architecture Decisions

This document records why Code2Shorts is built the way it is, and how it relates
to prior open-source work in the "LLM to Manim video" space. Update it whenever
a reuse/build decision is made or reversed.

## Guiding principle

The LLM plans and describes. It never touches pixels directly. The pipeline is:

```
Topic -> LessonSpec -> LanguageAdapter -> generated code -> compile -> test
      -> execute -> ExecutionTrace -> AnimationSpec (DSL) -> Manim renderer
      -> TTS/subtitles -> FFmpeg -> visual QA -> MP4
```

Every stage after "execute" is derived from data validated by the stage before
it. No stage may fabricate state that a prior stage didn't prove.

## Reference projects surveyed

| Project | License | What it does | Decision |
|---|---|---|---|
| [showlab/Code2Video](https://github.com/showlab/Code2Video) | MIT | Tri-agent (Planner / Coder / Critic) pipeline that has an LLM write Manim code directly, refined via visual critique loop. | **Do not copy the core loop.** Its Coder agent writes final animation code directly from a plan, which is exactly the "LLM controls pixels" pattern this project rejects — it has no real execution trace behind the animation, only plan-to-code-to-critique. We take conceptual value only: the idea of a Critic/QA pass after rendering (informs our `qa/` visual QA stage), and their prompt-engineering approach as a reference when writing our LessonSpec-generation prompts. Not a dependency. |
| [ManimCommunity/manim](https://github.com/ManimCommunity/manim) | MIT (dual MIT / MIT-community) | The actual animation rendering engine (mobjects, scenes, camera, LaTeX, etc). | **Reuse directly as a dependency.** This is infrastructure, not a competing architecture. Our `render/` layer builds deterministic Scene subclasses from our own AnimationSpec DSL — Manim just draws what we tell it. |
| [makefinks/manim-generator](https://github.com/makefinks/manim-generator) | MIT | LLM writes Manim code, a second LLM reviews/critiques it, iterate up to N times, uses LiteLLM for provider routing. | **Adapt one idea, reject the core pattern.** Same objection as Code2Video: code-review-as-a-loop is not the same as "did this code actually execute and produce this trace." We do reuse their choice of **LiteLLM as the provider abstraction** (see `lessonplan/llm_provider.py`) since that's exactly the "clean LLM provider abstraction" the stack calls for, and it isn't tied to their generation pattern. |
| [mateolafalce/topic2manim](https://github.com/mateolafalce/topic2manim) | MIT | Multi-agent: script (JSON) -> TTS -> Manim code generation synced to audio durations -> compile per-scene -> concat. | **Adapt the scene-duration-driven-by-audio idea**, reject direct code generation from topic. Its JSON "script" stage is the rough analogue of our `LessonSpec`, which validates the idea of an intermediate structured spec — but topic2manim's Manim code is still LLM-authored per scene with no execution behind it. Their TTS-then-time-scenes ordering informs our `compose/` timing strategy for Phase 2+. |

**Summary decision:** none of the four reference projects has a real
"execute the algorithm and animate the trace" stage — they all let the LLM
author final animation code from a plan or script, optionally reviewed by
another LLM or itself. That is the single architectural risk Code2Shorts is
built to avoid. Manim Community is the only one we take as a direct runtime
dependency; LiteLLM (used by manim-generator) is taken as our provider
abstraction library. Everything else here is original to this project.

## Language abstraction

Code generation and language/runtime operations are two different
responsibilities, split into two separate hierarchies so neither grows
into a god-object:

```
LLMProvider
    v
CodeGenerator
    v
GeneratedCode
    v
LanguageAdapter
    +-- compile()
    +-- test()
    +-- execute()
    +-- trace()
```

- `LLMProvider` (`src/code2shorts/llm/provider.py`) — one method,
  `complete(prompt, system) -> str`. The only seam allowed to know about a
  concrete LLM SDK (LiteLLM-backed in Phase 1, routing to Ollama/Claude/
  Gemini/OpenAI per `config.Settings`).
- `CodeGenerator` (`src/code2shorts/codegen/base.py`) — takes an
  `LLMProvider`, turns an `AlgorithmSpec` into `GeneratedCode`. Knows
  nothing about compiling, testing, executing, or tracing. One
  implementation per language (`JavaCodeGenerator` first).
- `LanguageAdapter` (`src/code2shorts/langadapter/base.py`) — takes
  `GeneratedCode` it did not produce and does language/runtime operations
  on it: `compile`, `test`, `execute` (raw run, captures stdout/exit code),
  `trace` (instrumented run, produces a real `ExecutionTrace`). It has no
  `generate` method and must never call an `LLMProvider`.

This keeps `if language == "java"` out of the pipeline (each language gets
one `CodeGenerator` + one `LanguageAdapter`, nothing else branches on it),
and keeps "the LLM produced this" cleanly separable from "we proved this
runs" — a `LanguageAdapter` can be tested and trusted independent of which
LLM (or human) produced the `GeneratedCode` it's handed.

All four language-independent layers (`LessonSpec`, `AlgorithmSpec`,
`ExecutionTrace`, `AnimationSpec`) live in `core/models.py` and import
nothing from `langadapter/` or `codegen/`. `JavaAdapter` and
`JavaCodeGenerator` are the first (and, for now, only) implementations,
isolated under `langadapter/java/` and `codegen/java/` respectively.

## Execution safety

Generated code is untrusted input. `execution/sandbox.py` runs adapter
`compile`/`test`/`execute` steps in an isolated temporary workspace
(`tempfile.mkdtemp`) via subprocess with an explicit timeout — never
`exec`/`eval`/reflection inside the main process, and never Manim rendering
of anything not backed by a validated `ExecutionTrace`.

## Stack constraints carried into Phase 0

Python 3.12, Pydantic v2, Typer, pytest, Manim Community, FFmpeg (later
phase), JDK 17 + Maven + JUnit 5 (later phase, for `JavaAdapter`), LiteLLM,
Ollama for local dev. No databases, no Kubernetes, no microservices, no
React — modular monolith only, per project constraints.

## Phase 2: execution intelligence / trace layer

`LanguageAdapter.trace()` is now real. The pipeline:

```
GeneratedCode
    v
JavaSourceInstrumenter.instrument()   <- NEW, AST rewrite (JavaParser tool)
    v
instrumented GeneratedCode + Code2ShortsTrace.java runtime helper
    v
JavaCompiler.compile()  /  JavaExecutor.run()   <- Phase 1, REUSED UNCHANGED
    v
TraceStreamParser -> TraceLimiter -> normalizer.build_events()   <- NEW
    v
ExecutionTrace (canonical, language-neutral)
```

### ADR-001 — Trace architecture: compose around Phase 1, don't fork a new execution path

**Context:** `trace()` needed either a new execution mechanism or to reuse
`JavaWorkspace`/`JavaCompiler`/`JavaExecutor`. **Decision:** compose — the
only new subprocess step is running the instrumenter tool, and it goes
through the exact same `execution.sandbox.run_subprocess` as everything
else. `compile()`/`execute()` inside `trace()` call the identical Phase 1
components `execute()`/`compile()` already use. **Alternatives considered:**
a parallel `TraceExecutor`, or JDI-based execution bypassing Maven/`java`
entirely. **Why:** Phase 1's subprocess/timeout/kill-tree code is proven
correct (including a real Windows orphaned-process bug fixed under fire in
Phase 1); duplicating or bypassing it would both waste that validation and
risk reintroducing the same class of bug — which is exactly what happened
mid-Phase-2 anyway, in a new subprocess this design *did* have to add (the
instrumenter), confirming the reuse decision was right for everything else.
**Consequences:** `trace()` pays full recompile cost every call (no
result caching in Phase 2 — deliberately deferred, not a blocker).

### ADR-002 — Java tracing technology: AST source instrumentation, not JDI/bytecode/agent

**Context:** compared JDI/JDWP, bytecode instrumentation (ASM), Java
agents, `jdb` scraping, and AST-based source-to-source rewriting.
**Decision:** AST instrumentation via JavaParser (`tools/java-instrumenter/`).
**Why:** Code2Shorts always controls the traced source (hand-written
fixtures today, `CodeGenerator` output later) — the AST already *is* the
semantic model needed (which line is a loop, which is a condition, which
name is a variable). JDI/bytecode approaches would reverse-engineer that
same structure from a lower-level representation, adding a second
execution/debugging architecture and a second Windows-process-tree risk
surface for no informational gain. Confirmed correct in practice: the
Reverse String fixture's automatically-instrumented trace matched the
Phase 1 hand-instrumented proof exactly (two-pointer swap, correct
call_depth, correct loop iteration numbering) with zero JVM debugging
protocol involved. **Alternatives considered:** JDI (rejected — see
above), ASM bytecode weaving (rejected — no accuracy gain, more
implementation risk), `-javaagent` (rejected — adds dynamic class-loading
attack surface, see `SECURITY_SANDBOX.md`), `jdb` text scraping (rejected
outright — not a real API, output not designed for parsing).
**Consequences:** cannot trace arbitrary/opaque third-party bytecode —
explicitly out of scope, not a target use case. **Reconsider if:**
Code2Shorts ever needs to trace code it did not generate/control (e.g.
"paste your own Java" as a product direction).

### ADR-003 — Canonical trace schema: extend, don't replace, Phase 0's model

**Context:** Phase 0's `TraceEvent`/`ExecutionTrace` were minimal
placeholders (`event_type: str`, no status/limits/exception concepts).
**Decision:** every Phase 2 field is additive with a default — `TraceStatus`,
`TraceEventType`, `ExceptionInfo` are new types; `TraceEvent` gained
`call_depth`/`method`/`variable_name`/`old_value`/`new_value`/
`return_value`/`iteration`/`condition_result` (all optional);
`ExecutionTrace` gained `entry_point`/`status`/`exception`/
`duration_seconds`/`timed_out`/`truncated`/`truncation_reason` (all
defaulted) plus computed properties `event_count`/`max_call_depth_reached`/
`total_loop_iterations` (not stored fields — same pattern as
`AnimationSpec.total_duration_seconds`, avoids a second source of truth
that could drift from `events`). The existing `step_index` sequential
validator is completely untouched. **One deliberate exception to "extend
freely":** `TraceEvent.event_type` stays typed as plain `str`, not the new
`TraceEventType` enum, specifically because Phase 0's own test fixtures
already used illustrative non-canonical values (`"pointer_move"`, `"swap"`)
that predate this schema — Phase 2 producers always populate it from
`TraceEventType.*.value`, but the field itself stays permissive so nothing
upstream breaks. **Consequences:** all 15 Phase 0/1 unit tests plus the two
original `ExecutionTrace` construction tests in `test_core_models.py`
needed zero changes.

### ADR-004 — Trace limits enforced inside the traced JVM, not only post-hoc

**Context:** `max_events`/`max_loop_iterations`/`max_call_depth`/
`max_output_size` all needed enforcement. **Decision:** all four are
enforced *inside* `Code2ShortsTrace.java` (counters + a byte-counting
`PrintStream` wrapper), which throws a dedicated
`TraceLimitExceededException` the moment a limit is crossed — not only
relying on `execution_timeout_seconds` to eventually kill a runaway
program. Limits are threaded in as environment variables
(`C2S_MAX_EVENTS` etc.) via a small additive extension to
`run_subprocess`/`JavaExecutor.run` (`env: dict[str, str] | None = None`,
defaulting to `None` — existing Phase 1 callers unaffected). **Why:** a
program that hits `TRACE_LIMIT_REACHED` should fail fast (milliseconds),
not wait out the full wall-clock timeout; the two are meant to be
distinguishable outcomes, verified by `test_timeout_is_distinct_from_trace_limit`
and `test_infinite_loop_terminates_via_trace_limit_not_wall_clock`.
**Real bug found here:** the output-size counting stream initially let a
`PrintStream`'s internal ~8KB-chunked writes reach the real stream *before*
throwing, leaving a dangling partial line with no trailing newline right
where the limit was crossed — that partial line then silently merged with
the next `TRACE:` line when the Python side split on newlines, corrupting
the program-output/trace-event framing (a `huge_output` test caught this:
the resulting trace showed only `PROGRAM_START`/`PROGRAM_END`, with the
`EXCEPTION_THROWN` event's JSON swallowed into what looked like program
output). Fixed by writing a terminating newline to the underlying stream
before throwing, so the line-based framing protocol (`TRACE:` prefix per
line) stays intact even when a limit fires mid-write.

### ADR-005 — Determinism strategy: restrict, don't normalize

**Context:** timestamps, thread scheduling, `Random`, wall-clock/env
access are all nondeterministic. **Decision:** reject them outright at
instrument-time (`DenylistValidator`, AST-level check for `Thread`,
`java.util.concurrent.*`, `Random`, `Math.random()`,
`System.currentTimeMillis()`/`nanoTime()`/`getenv()`/`getProperty()`, plus
lambdas/method references/the Stream API as separately out-of-scope
constructs) rather than attempting to normalize their output after
execution. `TraceEvent` never carries a timestamp field at all;
`duration_seconds` is the one field explicitly excluded from determinism
comparisons (`tests/trace_helpers.py::assert_traces_equivalent`).
**Verified:** `test_deterministic_repeated_execution` runs the Reverse
String trace 3x and asserts full equality (events, ordering, values, line
numbers, call depth) modulo `duration_seconds`. **Consequences:** a
generator that produces code using these constructs gets a clean
`INSTRUMENTATION_FAILED`, never a silently-flaky trace.

### ADR-006 — Phase 2 scope boundary: facts, not explanations; no LLM in the trace path

**Context:** carried over from the pre-Phase-2 architecture review.
**Decision:** `ExecutionTrace` contains only directly-observed runtime
facts. No natural-language reasoning, no Gemini/Claude/OpenAI calls
anywhere in `langadapter/java/trace/` or `tools/java-instrumenter/`.
**Consequences:** Phase 3's job (turning facts into explanations) stays
fully separate and downstream.

### ADR-007 — max_output_size: in-JVM enforcement, not a new pipe-reading mechanism in run_subprocess

**Context:** `execution.sandbox.run_subprocess` uses
`Popen.communicate(timeout=...)`, an all-or-nothing read — it cannot cap
bytes read mid-stream without a rewrite to manual threaded/non-blocking
pipe reads. **Decision:** do not modify `run_subprocess`'s read semantics
at all. Enforce `max_output_size` the same way `max_events`/
`max_loop_iterations`/`max_call_depth` are enforced — inside the traced
JVM, via a byte-counting `PrintStream` wrapping `System.out` (see ADR-004).
**Why:** avoids introducing a second, riskier subprocess I/O mechanism
into code that Phase 1 already proved solid; keeps all trace-limit
enforcement using one consistent mechanism instead of two. **Consequences:**
this is a resource-exhaustion mitigation *within the traced process*, not
a pipe-level safety net — if a program somehow wrote huge output without
going through `System.out` (not possible for the Phase 2 supported
subset), it would not be caught. Explicitly documented as a boundary in
`SECURITY_SANDBOX.md`, not silently assumed.

## Phase 3: workflow + artifacts + AI contracts

The layer between deterministic execution (Phase 1/2) and future
AI/visualization/video pipelines. Explicitly NOT a full agent system —
see ADR "no multi-agent architecture yet" below.

```
Code2Shorts Workflow API
          |
   Local Workflow Runner        <- Phase 3 implements this
          |
   Domain Contracts (Code2ShortsState, Artifact, AI contracts)
```

A future `LangGraphAdapter` (or similar) would sit alongside
`WorkflowRunner`, both implementing the same node/state contracts — not
built in Phase 3, not needed for Phase 3 to function.

### Workflow state design

`Code2ShortsState` deliberately does NOT contain a field per pipeline
stage (`compilation`, `execution`, `trace`, `explanation`, ...). Instead:
`artifact_ids: dict[str, str]` maps stage name -> `Artifact.id`; the
actual content lives exactly once, in the `ArtifactStore`. This collapses
what the initial sketch called "derived state," "persisted artifacts," and
"AI-generated data" into one mechanism — every stage's output is an
`Artifact`, full stop, so there's no separate "is this derived state or a
persisted artifact" question to answer per field. See `workflow/state.py`
for the full A-G category mapping.

### ADR — Workflow abstraction

**Context:** needed a strongly-typed way to run ordered stages with
state, events, retries, and checkpoints. **Decision:** `WorkflowNode`
(`run(state, context) -> NodeResult`, raises `NodeExecutionError` on
failure), `Workflow` (an ordered node list), `WorkflowRunner` (a plain
Python loop). **Alternatives considered:** a graph-based DAG engine
up front. **Why sequential, not a graph:** Phase 3's actual pipeline
(compile -> trace -> explain -> ...) is linear; the one branching case in
the target pipeline (trace validation pass/fail) is fully expressible as
"a node fails -> the runner stops" without a graph executor — see
"conditional flow" ADR below. **Consequences:** adding real branching
later (e.g. repair-and-retry loops) needs a small, additive change to
`WorkflowRunner`, not a rewrite, since nodes are already independent
units with no positional coupling beyond the list order.

### ADR — Artifact-first architecture

**Context:** every pipeline stage's output needs identity, lineage, and
a checksum, and multiple things (workflow state, future callers, tests)
need to reference stage results without duplicating them.
**Decision:** `Artifact` is a generic envelope (`id`, `type`,
`schema_version`, `artifact_version`, `producer`, `input_artifact_ids`,
`content: dict`, `checksum`, `created_at`, `metadata`), constructed only
via `Artifact.create(...)` so the checksum can never drift from the
content. **Alternatives considered:** typed `Artifact` subclasses per
stage (`SourceArtifact(Artifact)`, etc.) — rejected for Phase 3: the
generic envelope plus typed Request/Response Pydantic models for the
`content` shape (already needed for AI contracts) gives the same
type-safety at the call site without a parallel class hierarchy to
maintain. **Consequences:** `content` is a loosely-typed `dict` at the
storage layer; callers are responsible for validating it back into the
right Pydantic model when reading (as `ExplainNode` does with
`ExecutionTrace.model_validate(trace_artifact.content)`).

### ADR — Artifact lineage/versioning

**Context:** debugging, replay, caching, and partial regeneration all
need to know what produced what, and need to distinguish "this changed"
from "this is a new version of the same thing." **Decision:** three
separate concepts, never conflated: `id` (this instance), `schema_version`
(shape of `content` for this `ArtifactType`), `artifact_version` (which
attempt/regeneration within one lineage), `checksum` (content-addressed,
independent of both version numbers). Lineage is `input_artifact_ids`, a
flat list of parent ids — `ArtifactStore.list_children` walks it in the
child->parent direction reversed. **Real bug caught by this design being
tested, not just designed:** `TraceNode` initially listed both `source`
and `compilation` as its `input_artifact_ids`, which is *technically*
accurate (Phase 2's `trace()` really does re-consume the original source,
not just the compiled output) but breaks the clean linear lineage chain
the rest of the pipeline follows — `source` ended up with two children
instead of one. Fixed to list only `compilation` (full ancestry is still
one hop away). **Consequences:** `duration_seconds` on both
`CompileResult` and `ExecutionTrace` is deliberately excluded from
checksummed `content` (moved to `metadata` instead) — it's wall-clock
timing, not behavior, and Phase 2 already established that
`duration_seconds` is excluded from trace-equality comparisons
(`assert_traces_equivalent`); including it in the checksum would make two
runs of the identical, deterministic program produce different checksums
purely from timing jitter, defeating the whole point of a content-address.
Verified by `test_artifact_checksum_deduplicates_identical_traces`.

### ADR — Local workflow runner instead of a distributed workflow engine

**Context:** needed something that actually runs nodes, today, without
committing to a specific future orchestration platform. **Decision:** a
plain Python loop (`WorkflowRunner.run`) — no queues, no worker
processes, no Kafka/Kubernetes/Temporal. **Why:** Code2Shorts today runs
one pipeline for one request; distributed scheduling solves a problem
this project doesn't have yet. **Consequences:** `WorkflowRunner` is
easy to test (in-process, synchronous, real assertions on real state) and
easy to replace later — `Workflow`/`WorkflowNode`/`Code2ShortsState` are
the actual contracts a distributed engine would need to honor, and none
of them assume synchronous local execution as part of their shape.

### ADR — Framework-independent AI contracts

**Context:** needed request/response types for future AI operations
(explanation, visualization planning, narration) without hard-coding a
provider or agent framework into the domain layer. **Decision:** Pydantic
request/response pairs in `ai/contracts.py`
(`ExplanationRequest`/`Response`, `VisualizationPlanRequest`/`Response`,
`NarrationRequest`/`Response`), all keyed to real domain identifiers —
trace events are referenced by `step_index` (the same integer
`ExecutionTrace.events[i].step_index` and `AnimationStep.trace_event_index`
already use, reused rather than inventing a parallel UUID scheme for the
same events). **AI provider abstraction reuses, not duplicates, Phase
0.1's `LLMProvider`** (`code2shorts.llm.provider`) — `ai/structured.py`'s
`generate_structured(provider: LLMProvider, prompt, response_model)` is a
thin function built on `LLMProvider.complete()`, not a second parallel
`AIProvider` class hierarchy. **Why:** the prompt explicitly asked for an
`AIProvider` interface, but Phase 0.1 had already solved "provider-neutral
LLM seam" for `CodeGenerator` — building a second one would be exactly the
"duplicate abstraction" the process explicitly says to avoid.
**Consequences:** `MockLLMProvider` (`ai/providers/mock.py`) implements the
existing `LLMProvider` interface, so it's usable anywhere an `LLMProvider`
is accepted, not just in `ai/`-specific code.

### ADR — AI output validation

**Context:** "AI says X" must never silently become a trusted fact.
**Decision:** two independent, distinctly-named failure modes:
`SchemaValidationError` (raw text doesn't parse into the response model —
`ai/structured.py`) and semantic validation via `ValidationResult`
(parses fine, but a *claim inside it* is false —
`ai/validation.py::validate_explanation_against_trace`, which checks every
referenced `step_index` against the real `ExecutionTrace`). Both feed the
same `Code2ShortsState.validation: ValidationSummary` accumulator, so
nothing downstream has to know which kind of check ran to ask "did
everything pass." **Verified:**
`test_ai_hallucinated_trace_event_fails_validation_not_silently_accepted`
and `test_ai_schema_validation_rejects_malformed_output` both prove the
corresponding artifact is never created on failure — a rejected AI
response leaves no trace in the `ArtifactStore` at all.

### ADR — LangGraph deferred

**Context:** the prompt explicitly forbids making LangGraph (or any
agent framework) the foundation of Code2Shorts. **Decision:** not
introduced in Phase 3. `WorkflowNode`/`Workflow`/`Code2ShortsState` are
designed so a future `LangGraphWorkflowRunner` (or similar) could
implement the same node contract as an alternative to
`WorkflowRunner` — but none of Phase 3's domain code imports or assumes
LangGraph exists. **Reconsider if:** a real, measured need for
graph-based branching/parallelism emerges that the sequential runner
genuinely can't express cleanly.

### ADR — MCP deferred

**Context:** Model Context Protocol could eventually expose Code2Shorts
artifacts/tools to external agents. **Decision:** not built in Phase 3 —
no requirement exists yet that needs it, and `ArtifactStore`/AI contracts
are already shaped as clean, serializable interfaces an MCP server could
wrap later without redesign. **Reconsider if:** an actual external-agent
integration requirement appears.

### ADR — No multi-agent architecture yet

**Context:** the prompt explicitly forbids multi-agent/swarm patterns in
Phase 3. **Decision:** exactly one AI-backed node (`ExplainNode`) exists,
calling exactly one provider synchronously — no agent-to-agent
communication, no planning loop, no tool-calling loop. **Why:** Phase 3's
job is contracts and plumbing, not intelligence; a multi-agent system
built before the single-agent path is proven and validated would be
speculative infrastructure. **Reconsider if:** a real requirement needs
multiple cooperating AI calls with shared intermediate state beyond what
sequential `WorkflowNode`s already express.

### ADR — Conditional flow: minimal, not a DAG

**Context:** the target pipeline has one real branch (trace validation
pass/fail -> explain or stop). **Decision:** Phase 3 expresses this with
the existing mechanism — a node that fails (`NodeExecutionError`) simply
stops the sequential runner, which is exactly "PASS -> continue, FAIL ->
stop" for the cases Phase 3 actually has. No new `ConditionalNode`/router
abstraction was added. **Consequences:** a future "FAIL -> repair, then
retry" branch (as opposed to "FAIL -> stop") is not yet expressible
without extending the runner — deliberately deferred until a real use
case needs it (see "Known limitations").

## Phase 4: real provider, visualization, narration, trusted rendering

Extends Phase 3's pipeline from `Compile -> Trace -> Mock Explanation`
to the full `Compile -> Trace -> Explain -> Visualize -> Narrate ->
Render -> Compose -> Final Validation` chain. Governing principle,
enforced structurally, not just by convention:

```
AI proposes  ->  Validation decides  ->  Trusted code executes
```

### ADR — Real LLM provider stays behind the existing LLMProvider seam

**Context:** needed a production-oriented Gemini implementation without
leaking Gemini-specific types into workflow/domain code. **Decision:**
`GeminiLLMProvider` (`ai/providers/gemini.py`) implements `LLMProvider`
(Phase 0.1's interface, unchanged) and is built on `litellm` — already a
project dependency, and `llm/provider.py`'s own docstring always intended
a LiteLLM-backed implementation. **Alternatives considered:** the
`google-generativeai` SDK directly — rejected, would add a second
provider-specific dependency when `litellm` already normalizes Gemini
(and every other major provider) behind one call shape.
**Testability:** `completion_fn` is dependency-injected (defaults to
`litellm.completion`), so `test_gemini_provider.py`'s 7 tests run with
zero network access and zero API key, using fakes that raise
litellm-shaped exceptions by class name. **Consequences:** classification
of transient vs. permanent provider errors happens by exception *class
name* (`Timeout`, `RateLimitError` -> transient;
`AuthenticationError`, `BadRequestError` -> permanent), not by importing
litellm's exception hierarchy directly — keeps this file stable across
litellm versions that reorganize those classes, at the cost of a
hand-maintained name list (documented in the file itself).

### ADR — Bounded AI repair, shared across every AI-backed node

**Context:** Explanation, VisualizationPlan, and Narration all need the
same "generate, validate, and if it fails, ask the model to fix it —
bounded" behavior; the risk of building this three times is subtle
divergence in what "bounded" means. **Decision:** one function,
`ai/repair.py::generate_with_repair`, used by `ExplainNode`,
`VisualizationPlanNode`, and `NarrationNode` identically. It builds a
repair prompt from (previous raw output + validation errors) — the model
sees exactly what was wrong, never a bare "try again." `max_repair_attempts`
is a hard ceiling; the loop provably terminates (each iteration is
attempt N of `max_repair_attempts + 1`, no recursion, no while-True).
**Verified:** `test_ai_repair.py` proves both the recovery path (schema
or semantic failure followed by success) and the bounded-failure path
(every attempt fails -> raises after exactly `max_repair_attempts + 1`
calls, never more) via `provider.calls` counts, not just timing/observation.
**Consequences:** repair attempts are recorded into
`Code2ShortsState.validation` even when they ultimately fail — the full
history of what was tried is preserved, not just the final outcome.

### ADR — Visualization vocabulary closed at the schema level, not filtered after

**Context:** the plan explicitly must never carry "arbitrary executable
code" — needed a guarantee stronger than "we scan for bad patterns."
**Decision:** `VisualizationStepPlan.visual_action` is typed as the
`VisualAction` StrEnum (`ai/contracts.py`), not `str`. An AI response
containing `"visual_action": "rm -rf /"` fails **Pydantic schema
validation** — it never reaches semantic validation, business logic, or
the renderer; there is no field anywhere in the schema shaped like
"code"/"command"/"script" for an executable payload to occupy in the
first place. Text-pattern scanning
(`visualization/validation.py::_scan_for_executable_payload`) is
deliberately kept as defense-in-depth on the two remaining free-text
fields (`narration_text`, `lesson_title`) — belt-and-suspenders, not the
primary defense. **Consequences:** extending the visualization vocabulary
(e.g. adding a new animation type) requires a one-line enum addition,
which is the correct amount of friction for widening what a renderer will
ever be asked to interpret.

### ADR — Trusted renderer: AI plan data, never AI-authored source

**Context:** the prompt is explicit — "the AI must NEVER generate
executable Manim/Python source that is executed directly." **Decision:**
`ManimVideoRenderer` (`visualization/manim_renderer.py`) is the only code
that emits Manim scene source, built entirely from *this repository's own
Python code* reading validated plan fields; every dynamic value passes
through `repr()` before insertion — a Python string literal, not spliced
syntax. `test_scene_source_never_splices_narration_as_syntax` proves this
by `compile()`-ing the generated source with a deliberately hostile
narration string (embedded quotes, backslashes, newlines) and asserting
it's still valid Python. Rendering itself happens via the same
trusted-subprocess pattern Phase 1 established for `mvn`/`java`
(`execution.sandbox.run_subprocess`, fixed argument list, no `shell=True`).
**FakeVideoRenderer** (same `VideoRenderer` interface) exists specifically
so tests — including the required end-to-end test — never need Manim
installed; it's the renderer analogue of `MockLLMProvider`.
**Consequences:** Manim isn't installed in this development environment
(it's the pre-existing optional `[render]` extra from Phase 0), so
`ManimVideoRenderer`'s actual subprocess invocation is unit-tested via
dependency injection (a fake `run_subprocess_fn`), not exercised
end-to-end against real Manim — documented in Known Limitations, not
silently assumed working.

### ADR — Artifact lineage extended, not restructured

**Context:** Phase 4 adds five new stages to the lineage chain.
**Decision:** `ArtifactType` gains `AUDIO`, `RENDERED_VIDEO`, and
`FINAL_VIDEO` (additive StrEnum members — `ANIMATION`/`VIDEO` from Phase 3
are left in place, unused, rather than renamed, since nothing depended on
removing them and StrEnum renames are a needless breaking change). The
chain is `source -> compilation -> trace -> explanation ->
visualization_plan -> narration -> rendered_video -> [audio] -> final_video`
— `rendered_video` and `audio` are siblings (both children of `narration`/
`rendered_video` respectively) that `compose_media` merges into
`final_video`, matching the real dependency shape (composition genuinely
needs both). **Verified end-to-end** by
`test_full_phase4_pipeline_no_external_credentials`, which walks the
entire chain via `ArtifactStore.list_children` at every hop, not just
checking that IDs exist. **Consequences (real bug avoided by the Phase 3
precedent):** `RenderResult`/`ComposeResult` content is deliberately just
metadata (paths, checksums, numbers) — never the actual video/audio
bytes — continuing the same principle that caught the `duration_seconds`
checksum bug in Phase 3.

### ADR — TTS and media composition: same trusted-boundary pattern, no new architecture

**Context:** needed vendor-neutral TTS and a way to combine video+audio
without inventing a new pattern. **Decision:** `TTSProvider`
(`narration/tts.py`) mirrors `LLMProvider`/`VideoRenderer` exactly — one
abstract method, `MockTTSProvider` as the only Phase 4 implementation
(no real vendor wired up — see Known Limitations, this is a deliberate,
documented scope cut, not an oversight). `MediaComposer`
(`media/composer.py`) uses the identical fixed-argument-list subprocess
pattern as `ManimVideoRenderer` for `ffmpeg`. **Why no new
"MediaComposer interface + implementations" hierarchy:** there's exactly
one composition strategy (FFmpeg mux); introducing an interface for a
single implementation with no second implementation in sight would be
premature abstraction — unlike `TTSProvider`/`VideoRenderer`, which the
prompt explicitly names multiple future vendors for.

### ADR — LangGraph remains deferred (Phase 4 reassessment)

**Context:** Phase 4 adds real branching-adjacent behavior (repair loops,
multi-stage validation) — exactly the kind of complexity that sometimes
motivates reaching for a graph orchestrator. **Decision:** still no.
**Reasoning:** (1) *current workflow simplicity* — the repair loop
(`generate_with_repair`) is a bounded `for` loop, not graph-shaped
branching; the pipeline itself is still a straight line of 9 nodes with
"fail = stop." Nothing in Phase 4 needed a graph to express. (2)
*framework independence* — `WorkflowNode`/`Workflow`/`WorkflowContext`/
`CheckpointStore` have not changed shape since Phase 3; they remain
LangGraph-agnostic on purpose. (3) *testability* — 108 tests, all
synchronous, all inspectable via plain assertions on real `WorkflowResult`/
`Code2ShortsState` objects; a graph executor would add an abstraction
layer between test assertions and actual node execution for no present
benefit. (4) *avoiding premature coupling* — Phase 4 proved the
single-provider, single-workflow-runner architecture scales to a 9-stage
pipeline with AI/validation/repair/rendering all present; that's strong
evidence against "we'll need a framework eventually" as a Phase 4
justification. (5) *future migration path* — unchanged from Phase 3: a
`LangGraphWorkflowRunner` implementing `WorkflowNode`'s existing contract
remains a clean, additive adapter, not a rewrite, whenever a real need
(true parallel branches, external-system-driven workflow state) appears.

### ADR — MCP remains deferred (Phase 4 reassessment)

**Context:** Phase 4 introduces a real external provider (Gemini) — the
first genuine "external service" in the codebase. **Decision:** still no
MCP. **Reasoning:** MCP solves tool/resource discovery for external
agents; Code2Shorts has exactly one external call type
(`LLMProvider.complete`), reached by exactly one internal caller pattern
(`generate_structured`/`generate_with_repair`), with no external agent or
IDE needing to discover or invoke it. Introducing MCP now would be
protocol overhead with no consumer. **Reconsider if:** a real requirement
appears for external tools/agents to invoke Code2Shorts capabilities, or
for Code2Shorts to consume external MCP tool servers.

### ADR — No multi-agent architecture (Phase 4 reassessment)

**Context:** Phase 4 now has three distinct AI-backed nodes (Explain,
VisualizationPlan, Narration) — a natural point to ask "should these be
agents talking to each other?" **Decision:** no. Each node makes exactly
one bounded `generate_with_repair` call to one `LLMProvider`, synchronously,
with no node-to-node AI communication and no shared "agent memory" beyond
the same `Code2ShortsState`/`ArtifactStore` every node already reads and
writes. **Reasoning:** three sequential, independent AI calls is not a
multi-agent system — it's three ordinary function calls that happen to
hit an LLM. Nothing in Phase 4 needed agents negotiating, planning, or
delegating to each other. **Reconsider if:** a real requirement emerges
for AI-to-AI negotiation or dynamic task delegation that sequential nodes
genuinely cannot express — not yet demonstrated.

### ADR — Open-source visualization reuse strategy (Phase 4.2A)

**Context.** Phase 4.1 shipped a real 1080×1920 MP4, but the renderer draws
sequential text cards — no array tiles, pointer glyphs, or code
highlighting. Before building a visual engine, we ran a reuse
reconnaissance: algorithm-visualizer, tracers.java, algorithm-visualizer's
algorithms corpus, CodeAnimator, code-video-generator, plus independently
found alternatives (arrayviz, ManimSort, Manim Studio, and the academic
Java tracers JavaWiz/JIVE/JaVis/Anteater). Full analysis in
[docs/OPEN_SOURCE_REUSE.md](docs/OPEN_SOURCE_REUSE.md); pipeline placement
in [docs/OPEN_SOURCE_MAPPING.md](docs/OPEN_SOURCE_MAPPING.md).

**Decision.** Reuse **Manim Community's built-in primitives directly**
(`Code`/`code_lines`, `Table`, `Arrow`, `SurroundingRectangle`, `Indicate`,
`Transform`), and build only the Code2Shorts-specific mapping from
`ExecutionTrace` to those primitives. **Add no new production dependency.
Copy no code from any evaluated repository.**

**Why we reuse rather than reinvent — and what that turned out to mean.**
The reuse instinct was correct, but the finding was not the expected one:
we had already installed the right library in Phase 4.1 without noticing
how much of it we were using. Verified empirically rather than from docs —
`Code(code_string=…, language="java", add_line_numbers=True)` renders Java
with individually addressable `.code_lines` (line highlighting), and
`Table(...).get_entries((row, col))` gives addressable cells (array tiles
and pointer anchoring). Every Phase 4.2 primitive already exists, under
MIT, actively maintained, already load-bearing and proven by a real render.
The remaining gap is not a library — it is a ~300–500 line mapping layer
that is inherently ours, because it is defined by our own trace model.

**What is NOT being reused, and why.**

- **tracers.java, algorithm-visualizer/algorithms, algorithm-visualizer/server:
  no license.** GitHub's API reports `"license": null` for all three. Under
  default copyright, no license means *all rights reserved* — a public
  repository is not a grant of rights. We may read them; we may not copy,
  adapt, or vendor them. The parent repo's MIT does not propagate across
  repository boundaries. This disqualifies them regardless of technical
  merit. `tracers.java` is additionally dead since 2019-06-30 and requires
  **manual annotation** of algorithm source — which would let whoever
  writes the algorithm (potentially an LLM) decide what the visualization
  shows, violating "AI proposes, validation decides, trusted code
  executes."
- **arrayviz** — the closest match on paper (Manim, arrays, pointers, MIT):
  **its GitHub repository returns 404 (deleted)**, leaving only an orphaned
  v0.0.1 wheel. Unauditable and unpatchable; a supply-chain red flag, not a
  maintenance inconvenience.
- **code-video-generator** (Apache-2.0, legally fine): last PyPI release
  **v0.5.0, 2021-09-20**, written against Manim ~0.10 while we run 0.21, and
  it pulls **`librosa`** (an audio-ML stack) for code highlighting that
  Manim now provides natively. Superseded.
- **CodeAnimator**: static-code animation only, no execution or state;
  7 stars, bus factor 1. Solves ~15% of the problem — below the brief's own
  "don't introduce it just because it exists" threshold.

**Why Code2Shorts retains its own ExecutionTrace.** Every external "trace"
we examined is a stream of *visualization commands* (`array1DTracer.select(i)`)
— a presentation decision already made by whoever annotated the source.
Ours is a record of *observed program state*, captured automatically. The
distinction is the whole architecture: correctness must originate from real
execution, not from someone's (or some model's) description of it. Adopting
an external model would be lossy in the wrong direction, embedding
presentation choices into the observation layer and breaking the
language-neutral seam that makes future Python/JavaScript adapters
tractable.

**Why the renderer stays trusted and repository-owned.** External code is
called *by* `build_scene_source`, at the last step, and never flows the
other way: no external model enters `ExecutionTrace`, no external code runs
during compile/test/execute/trace, and nothing external decides *what* is
visualized — only *how* our own code draws it. Using more Manim primitives
inside the trusted renderer widens the security surface by exactly nothing:
the generated scene source stays ours, and no `VisualizationPlan` field
becomes executable. `VideoRenderer` already abstracts the engine, so Manim
remains swappable at one seam.

**How licensing was evaluated.** Each candidate's license was read from the
GitHub API / PyPI metadata on 2026-08-24 — not inferred from a README
badge or the phrase "open source." Where no license was detected, the
project is marked 🔴 DO NOT USE rather than assumed permissive. Commercial
use, modification, attribution, and redistribution rights were checked
individually; see the table in OPEN_SOURCE_REUSE.md §B.

**Consequences.** Zero new dependencies; `pyproject.toml` unchanged.
Phase 4.2 becomes a focused, self-contained build of the trace→primitive
mapping rather than an integration project against stale or unlicensed
third-party code. We carry ongoing exposure to Manim API churn — but that
exposure already existed as of Phase 4.1 and is confined to one module.

**Reconsider if.** Manim CE becomes unmaintained (then `VideoRenderer` is
the swap point); or a genuinely maintained, properly licensed
execution-trace-to-visualization library appears that models *observed
state* rather than presentation commands.

### ADR — Generic array/pointer visualization from reconstructed state (Phase 4.2)

**Context.** Phase 4.1 rendered truthful but visually minimal text cards.
The engine had to gain array tiles, pointers, variables and code
highlighting while satisfying a hard constraint: **one implementation, five
algorithms (Reverse String, Palindrome, Two Sum, Move Zeroes, Remove
Duplicates), no algorithm-specific branches.**

**Decision.** A three-part split, all built on Manim primitives already
shipped (per the Phase 4.2A reuse ADR):

1. `visualization/state.py` — replays the trace into a `FrameState` per
   event. The trace records array *mutations*, not snapshots, so full
   contents are reconstructed by seeding from the initial value and
   applying every `ARRAY_WRITE` in order. Pure, deterministic, no I/O.
2. `visualization/primitives.py` — trusted Manim source fragments. Every
   dynamic value goes through `repr()` as a string literal.
3. `build_scene_source` — composes them; degrades to captions when no
   `RenderContext` is supplied, so Phase 4 callers are unaffected.

**The generalization mechanism — one structural rule, no algorithm
knowledge.** A scalar is drawn as a pointer iff its value parses as an
integer *and* falls within the primary array's bounds. That single rule
covers `left`/`right` (Reverse String, Palindrome), `i`/`j` (Two Sum), and
`slow`/`fast` (Move Zeroes, Remove Duplicates) without naming any of them.
Verified two ways: a real-pipeline test across all five algorithms, and a
source scan (`test_no_algorithm_specific_branches_in_visualization_package`)
that fails if any algorithm name appears anywhere in the package.

**Two Phase 2 defects this work exposed, fixed with evidence rather than
assumption.** Both were invisible while only the `char[]` Reverse String
fixture existed:

- **`String.valueOf(int[])` returns an identity hash** (`[I@7ad041f3`), not
  contents — `char[]` only worked because `String.valueOf(char[])` is a
  documented special case. Four of the five required algorithms use
  `int[]`, so array rendering was impossible for them. Fixed by adding
  `Code2ShortsTrace.repr()` overloads (compile-time resolved by static
  type, no reflection) and having the instrumenter emit `repr(x)` instead
  of `String.valueOf(x)`. Identity hashes are also allocation-derived —
  exactly what ADR-005 exists to keep out of a trace.
- **For-loop counters were never traced at all.** A counter declared in the
  for-init is an `Expression` on the `ForStmt`, not an `ExpressionStmt` in
  a block, so `instrumentBlock` never saw it. Those counters *are* the
  pointers in three of the five algorithms. Fixed by reporting each
  counter's value at the top of every iteration.

**Security is unchanged, and now more heavily tested.** The engine embeds
far more trace-derived data into generated source (cells, variable names,
values, Java source), so each is a new place a hostile value could try to
become syntax. 42 tests (`test_visualization_security.py`) push injection
payloads through every one of those channels and assert the output parses
as Python containing **no** import/exec/eval/system call — plus a positive
control confirming the payload survives as inert string data. No
`exec`/`eval`, no `shell=True`, fixed argument lists and timeouts all
unchanged.

**Consequences / known limits.** The pointer rule has a documented false
positive: a non-index integer that happens to be in range (a tally, say)
renders as a pointer. It still displays the variable's true value, so
nothing shown is ever wrong — the annotation is merely more prominent than
it deserves. Chosen over name-based heuristics precisely because
heuristics would smuggle algorithm knowledge back in. Long source files
also shrink to stay inside the 9:16 code band and can become hard to read;
a windowed view around the executing line is the obvious next step.

**Reconsider if.** Non-array structures (HashMap, Stack, linked lists)
enter scope — those need their own template alongside the array one, not
changes to it.

### ADR — Code/execution synchronization (Phase 4.3)

**Context.** Phase 4.2 rendered a code panel but had no principled link
between a trace event and the line it happened on. Worse, `TraceEvent`
carried `line_number` with **no file identity**, and Reverse String spans
two files — so its trace mixes "line 5" from `Main.java` with "line 6"
from `ReverseString.java`. The panel showed one file while highlighting
numbers largely originating in the other.

**Decision.** A derived, language-neutral location model:

```
ExecutionTrace -> SourceLocation -> CodeState -> code_panel() -> Manim Code
```

`SourceLocation` (`core/models.py`) carries file/line/method/class.
`CodeState` (`visualization/code_state.py`) carries a visible window plus
the highlight offset. Neither contains a Manim type; `core` imports nothing
from `visualization`.

**Why AI never picks the line.** The highlighted line is computed solely
from `TraceEvent.line_number` and the method context the event occurred in.
A `VisualizationPlan` selects *which trace event* to display; it has no
field that can name a line, and the renderer never consults one. This is
the same "AI proposes, validation decides, trusted code executes" split as
Phase 4.2, applied to source location.

**File identity is derived, not stored.** `METHOD_ENTER` already carries
`"Class.method"`, so the innermost open method determines the file, and
events before any entry belong to the instrumented entry point. This needs
zero changes to the trace runtime or instrumenter and is exact for the
supported subset — nested/anonymous classes and lambdas, the only
constructs that would break it, are already rejected at instrument time by
`DenylistValidator`. Verified across 166 real events in five algorithms
with zero mismatches against the original source text.

**Manim is reused; the two code-animation projects are not.** Verified by
running them, not by reading READMEs: `code_lines` is per-line addressable,
`set_opacity` works per line, and `line_numbers_from=N` labels a *window*
with the file's true line numbers — which is precisely what windowing
needs. code-video-generator's `HighlightLines` targets `code.code` and
`code.line_no_from`, **both confirmed missing in Manim 0.21**, so it would
fail on import-time use; its dimming *technique* was adopted as a design
idea and re-implemented in a few lines, with no code copied and therefore
no Apache-2.0 NOTICE obligation. CodeAnimator remains reference-only (web
app, no execution model). `manim-code-blocks` was rejected outright — MIT
on PyPI but its repository is deleted, the same supply-chain pattern as
`arrayviz` in Phase 4.2A.

**Long files are windowed, not shrunk.** Scaling a 200-line file into a
9:16 band makes every line unreadable, which defeats showing code at all.
A configurable radius (default 6, giving 13 lines) centres on the executing
line and clamps at both file ends so it stays visible; source text is never
altered.

**Trace schema versioning.** `TRACE_SCHEMA_VERSION = 2` plus
`instrumenter_version`, `language_version`, `source_hash`. Phase 4.2
silently changed array value rendering (`String.valueOf` to `repr`) and
nothing recorded that a trace predated it. A version field and provenance
make such shifts auditable; a migration framework is deliberately not built.

**Consequences.** Render time roughly doubled (~10.8s to ~18.8s) because a
fresh `Code` mobject is built per step now that the window scrolls —
the honest cost of real synchronization, recorded rather than optimized
away. `SourceLocation.column` exists but is always `None`; the instrumenter
emits no columns, so sub-line highlighting is deferred.

**Reconsider if.** Non-Java adapters arrive — they supply `SourceLocation`
themselves and nothing above changes; or nested/anonymous classes enter the
supported subset, which would require explicit per-event file identity.

### ADR - Narration, audio alignment and subtitles (Phase 4.4)

**Context.** The pipeline produced silent video. Narration needed real
speech, deterministic alignment to the visuals, and a subtitle track -
without letting audio dictate animation timing.

**Decision.** Visual timeline stays authoritative. `align_narration()`
computes every segment's start/end from the plan's declared step durations;
measured audio duration is used *only* to detect and report overflow. There
is no code path by which an audio length can move a visual step.

**Reuse (audit: docs/PHASE_4_4_REUSE_AUDIT.md).** Selected: **Windows SAPI**
via subprocess for real speech (offline, key-free, zero cost, and zero new
dependency - it is an OS component); `srt` (MIT) for subtitles and PyAV for
audio probing, both **already installed** as Manim transitive dependencies;
FFmpeg for muxing. **Net new production dependencies: zero.**

**Rejected, with evidence.** Piper is blocked twice over: the original
`rhasspy/piper` is **archived** (2025-08-26), and its successor
`piper1-gpl` is **GPL-3.0** *and* ships **no Windows wheel** (manylinux
only). `kokoro` (Apache-2.0) and `coqui-tts` (MPL-2.0) are good and stay
REFERENCE, but pull a multi-gigabyte torch stack to narrate a 20-second
video. `pyttsx3` declares no license. Cloud vendors (Google/ElevenLabs/
Azure/Polly) require API keys and payment, violating "no key for tests".
`faster-whisper` is excellent but solves a problem we do not have: segment
boundaries are already known exactly, because narration is authored per
visualization step.

**Security.** Narration is untrusted data. TTS text is a **bound PowerShell
parameter**, never interpolated into a script; the script is fixed and
repository-owned. Subtitle text goes into a data file, never a shell.

A **real vulnerability was found by its own test**: SRT cue injection. A
payload shaped like an SRT timing block parsed back as a second cue with
attacker-chosen timestamps. The first sanitizer collapsed a newline pair
but left the injected timing line, and the lenient `srt` parser accepted
it. Fixed by collapsing every newline form, so a timing line can never
begin; `validate_srt` now rejects surviving newlines too.

**Consequences.** SAPI is Windows-only and robotic - bounded by the
`TTSProvider` seam. Plan step durations must be sized to fit narration; the
system reports mismatch rather than auto-resizing, because auto-resizing
would violate the authoritative-timeline rule. Demonstrated live: a 3.0s
plan overflowed against 3.07-3.41s of real speech, so durations were
re-sized from the measurement and the rerun reported overflow=0.

**Reconsider if** narration quality becomes the priority (adopt kokoro or a
cloud voice behind the same interface), or word-level karaoke subtitles are
required (faster-whisper).

### ADR - Production hardening and the media validation layer (Phase 4.5)

**Context.** Phases 0-4.4 built the pipeline; nothing yet proved it was
production-ready as a whole. Before Phase 5 adds LLM nondeterminism, the
deterministic core had to be demonstrated on real artifacts across every
golden-path algorithm, with failure paths exercised rather than assumed.

**Reuse decision (docs/PHASE_4_5_REUSE_AUDIT.md): zero new dependencies.**
Every hardening capability was already present - PyAV and `srt` (installed
via Manim), FFmpeg, pytest markers, stdlib `hashlib`/`tempfile`/
`subprocess` - or already implemented in-house: `workflow/retry.py`
(failure-kind aware, which generic retry libraries do not model) and typed
`WorkflowEvent` observability.

`ffmpeg-python` was rejected on **security** grounds rather than
redundancy: its value is *dynamic* command construction, while our entire
FFmpeg trust model rests on fixed literal argument lists that no
trace-derived or AI-derived string can restructure. `tenacity`, `structlog`
and `psutil` were rejected as already solved.

**The one genuine gap: media validation.** `media/probe.py` reported what a
file *is*; nothing asserted what it *must be*. `media/validation.py` adds
that policy - H.264 1080x1920, AAC audio, frame count, and crucially
**composition drift**: the final video is compared against the rendered
duration and truncation/stretching are each named explicitly in the error.
This is the Phase 4.1 production bug (a bare `-shortest` silently cut an
18.0s render to 5.2s) turned into a permanent guard.

**Governing rule made explicit.** A media mismatch is a validation failure
with an actionable diagnostic. It is never resolved by truncating video,
stretching video, or dropping audio. Silently shipping incorrect
educational content is worse than failing.

**Determinism defined.** "Deterministic" is asserted at the LOGICAL layer:
trace (minus wall-clock duration), FrameState, SourceLocation, CodeState,
VisualizationPlan and SRT bytes must be identical across runs. MP4 bytes,
wall-clock timings and artifact ids are explicitly NOT required to match -
encoders embed timestamps and artifact ids are fresh uuid4 by design.

**A note on test methodology.** Three hardening tests initially failed as
**false positives**: they text-searched for `shell=True`, `exec(` and
`-shortest`, and matched the comments that *document* those very
vulnerabilities. Rewritten to inspect the AST and actual built commands
instead. Grepping source text is not a security control; parsing it is.

**Consequences.** No contract changed - `ExecutionTrace`, `LanguageAdapter`,
`CodeGenerator`, `LLMProvider`, `TTSProvider`, `VisualizationPlan` and
`ArtifactStore` are untouched, so Phase 0-4.4 remains fully backward
compatible. Manim is now measured at ~83% of pipeline time; deliberately
not optimized, since no evidence justifies trading determinism or
isolation for speed yet.

### ADR - Source-line mapping correctness (Phase 4.5.1)

**Context.** Displayed Java line numbers could be offset by one, so the
rendered video highlighted the wrong statement. A correctness defect in
shipped educational content, not a cosmetic one.

**Root cause, measured not assumed.** Manim 0.21's `Code` mobject silently
discards leading and trailing *truly empty* lines while its line-number
column keeps counting from `line_numbers_from`. Interior blanks and
whitespace-only lines survive. A window beginning on a blank line therefore
lost that line inside Manim while the labels did not shift, so every
displayed number read one low and the box landed on the following statement.

Explicitly ruled out: the instrumenter and trace (`SourceLocation.line ==
TraceEvent.line_number` for all 166 events), `CodeState` (offset arithmetic
and window content were always correct), and `line_numbers_from` itself
(correct in isolation). The defect existed only in the interaction.

**Decision.** `code_state.py::_trim_blank_edges` trims leading/trailing
empty lines from the window and advances `start_line` to match, leaving
Manim nothing to strip. It never trims past the highlighted line.

**Why this is architecturally correct.** `ExecutionTrace` is untouched.
`SourceLocation.line` still equals the original Java source line - no
semantic line number is ever rewritten to compensate for a rendering
artifact. `CodeState`'s contract already was "`lines[i]` is file line
`start_line + i`"; the fix *preserves* that invariant through rendering
rather than redefining it. The Manim-specific knowledge lives in one private
helper whose docstring records the measured behaviour; no Manim concept
enters the domain model. Chosen over adjusting `line_numbers_from` inside
`code_panel` because trimming makes the state render-safe for any renderer.

**Verification reached the last link this time.** Earlier checks stopped at
`CodeState`, which is why the defect survived them. `test_line_mapping.py`
adds `decode_displayed_labels`, which reconstructs the labels Manim actually
draws from the line-number Paragraph's concatenated text and per-line glyph
widths - rather than assuming they follow `line_numbers_from`, the exact
assumption the defect hid behind. 166 real-trace events across five
algorithms, 0 mismatches, plus cropped-frame visual confirmation.

**Regression protection.** Proven by reverting: with `_trim_blank_edges`
disabled, `test_window_starting_on_a_blank_line_keeps_labels_correct` and
`test_window_never_starts_or_ends_on_a_blank_line` both fail.
`test_manim_strips_blank_edges_but_our_windows_never_expose_it` pins the
upstream behaviour so a Manim upgrade is noticed.

**Cost.** 0.31 us per call, ~0.02 ms per render - no measurable impact. No
new dependency. Full detail in docs/PHASE_4_5_1_LINE_MAPPING.md.

**Known limitation.** Emoji in Java source makes Manim raise (font renders
fewer glyphs than characters). Mapping is correct; rendering is not.
Documented rather than worked around, since any workaround would alter
displayed source.

## Phase log

- **Phase 0** (this commit): repo scaffold, core Pydantic models
  (`LessonSpec`, `AlgorithmSpec`, `ExecutionTrace`, `AnimationSpec`),
  `LanguageAdapter` interface + empty `JavaAdapter` skeleton, config
  management via `pydantic-settings` (no hardcoded keys), Typer CLI stub,
  pytest scaffold. No generation, execution, or rendering logic yet —
  those are later phases, each gated on the previous phase's tests passing.
- **Phase 0.1** (pre-Phase-1 adjustment): split code generation out of
  `LanguageAdapter` into its own `LLMProvider -> CodeGenerator` hierarchy
  (see Language abstraction above). `LanguageAdapter` lost `generate()` and
  gained `trace()` as a distinct stage from `execute()`. No behavior change
  yet — both hierarchies are still skeletons pending Phase 1.
- **Phase 1** (real Java validation pipeline): `JavaAdapter.compile()`,
  `.test()`, `.execute()` are real — Maven is the Java build boundary
  (`langadapter/java/maven.py` shells out to the real `mvn`), JDK 17
  bytecode target, JUnit 5.10.2. No LLM, no `CodeGenerator` implementation,
  no video, no fabricated `ExecutionTrace` — `.trace()` still raises
  `NotImplementedError` on purpose. See `SECURITY_SANDBOX.md` for exactly
  what the subprocess/timeout/workspace boundary does and does not protect
  against. Found and fixed a real bug along the way: on Windows, a killed
  `mvn.cmd` process left its `java.exe` grandchild (the actual Maven/JVM or
  surefire-forked test run) orphaned and running past the timeout, because
  `Popen.kill()` only signals the direct child. Fixed by killing the whole
  process tree (`taskkill /T /F` on Windows, `os.killpg` on POSIX) and by
  never inheriting stdin (`stdin=DEVNULL`), which also closed off a
  separate hang where a Windows batch-wrapped child blocked waiting on
  input that would never arrive.
- **Phase 2** (execution intelligence / trace layer): `JavaAdapter.trace()`
  is real — AST source instrumentation (`tools/java-instrumenter/`,
  JavaParser), a trusted runtime helper (`Code2ShortsTrace.java`) enforcing
  all four trace limits in-JVM, and a Python-side parse/limit/normalize
  pipeline (`langadapter/java/trace/`), all composed around Phase 1's
  unmodified compile/execute machinery. See ADR-001 through ADR-007 above
  for the individual decisions, `SECURITY_SANDBOX.md` for the updated
  trust-boundary documentation, and `tests/test_java_trace_integration.py`
  (18 tests) for real, non-mocked proof against basic/control-flow/
  recursion/exception/timeout/trace-limit/determinism/JSON-round-trip
  cases. No LLM, no video — `CodeGenerator` remains an unused skeleton.
  Two real bugs were found and fixed via these integration tests, not
  discovered on paper: a definite-assignment compile error in the
  instrumenter for uninitialized-then-conditionally-assigned locals (fixed
  with branch-scoped dataflow tracking in `SourceInstrumenter`), and the
  output-framing corruption described in ADR-004.
- **Phase 3** (workflow + artifacts + AI contracts): new `workflow/`,
  `artifacts/`, `ai/` packages — `WorkflowNode`/`Workflow`/`WorkflowRunner`
  (sequential, local, with retry/checkpoint/structured events),
  `Artifact`/`ArtifactStore` (content-addressed, lineage-tracked), and
  framework-neutral AI contracts (`ExplanationRequest`/`Response` real and
  wired to a node; `VisualizationPlanRequest`/`Response`,
  `NarrationRequest`/`Response` defined but not yet consumed by any node —
  contracts only, per the explicit "does not need to implement every
  stage" scope). Three real nodes (`CompileNode`, `TraceNode`,
  `ExplainNode`) compose around Phase 1/2's `JavaAdapter` completely
  unmodified — proven by `tests/test_phase3_pipeline_integration.py`
  running real Maven/JDK compile+trace end to end with only the AI call
  mocked. See the ADRs above for the individual decisions. No LangGraph,
  no MCP, no multi-agent architecture, no distributed workflow engine — all
  explicitly deferred (see their respective ADRs). Found and fixed two
  real bugs via the test suite, not on paper: a circular import
  (`ai.validation` <-> `workflow` package init <-> `workflow.nodes`,
  resolved by moving the shared leaf type `ValidationResult` into
  `core.models`, which has no dependents that could cycle back), and a
  lineage bug where `TraceNode` gave the `source` artifact two children
  instead of a clean chain (fixed by dropping the redundant `source` edge
  once `compilation` already covers it).
- **Phase 4** (real provider, visualization, narration, trusted
  rendering): `GeminiLLMProvider` (litellm-backed, behind the unchanged
  `LLMProvider` interface), bounded AI repair shared across every
  AI-backed node (`ai/repair.py`), `visualization/` (closed-vocabulary
  `VisualAction` enum, semantic validation, `ManimVideoRenderer` +
  `FakeVideoRenderer` behind one `VideoRenderer` interface),
  `narration/` (validation + `TTSProvider`/`MockTTSProvider`), `media/`
  (`MediaComposer`, fixed-argument FFmpeg invocation). Five new workflow
  nodes (`VisualizationPlanNode`, `NarrationNode`, `RenderVideoNode`,
  `ComposeMediaNode`, `FinalValidationNode`) extend the unchanged
  `WorkflowRunner`/`Workflow` sequential engine — full 9-stage pipeline
  now real: compile -> trace -> explain -> visualize -> narrate -> render
  -> compose -> final-validate. Proven end to end by
  `tests/test_phase4_pipeline_integration.py` (real Java compile+trace,
  mocked LLM/TTS/renderer, zero external credentials) walking the entire
  artifact lineage chain via `ArtifactStore.list_children` at every hop.
  See the ADRs above. No LangGraph, no MCP, no multi-agent architecture —
  reassessed and still deferred, not merely carried over unexamined.

  **Real design decisions made mid-implementation, not purely on paper:**
  `VisualizationStepPlan.visual_action` was upgraded from Phase 3's plain
  `str` to a closed `VisualAction` enum specifically so "no arbitrary
  renderer instructions accepted" is enforced by Pydantic schema
  validation itself, not by a filter that could have a gap. `NarrationResponse`
  was upgraded from Phase 3's placeholder `lines: list[str]` to
  `segments: list[NarrationSegment]` carrying explicit
  `visualization_step_order` sync references, since the unstructured
  version had no way to satisfy "reject narration that references
  nonexistent trace events" (there was nothing to validate against).
  Both changes were safe because no Phase 3 test constructed either model
  with the old shape — verified before changing them, not assumed.

---

## Phase 5 — LLM-driven content generation and multi-provider routing

### ADR-5.1 One `LLMProvider`, many backends, one selection seam

**Status:** Accepted.

**Context.** Phase 5 needed to reach several LLM backends — a local free
routing gateway, direct Gemini, potentially Ollama or OpenAI — without
sprinkling provider knowledge through the workflow.

**Decision.** Keep Phase 0.1's `LLMProvider` (a single `complete()` method)
as the only provider abstraction. Add one `OpenAICompatibleProvider`
parameterised by `base_url`, which reaches every backend speaking the
OpenAI `/v1/chat/completions` format. Provider selection lives in exactly
one file, `ai/providers/factory.py`.

**Consequences.** No second provider abstraction and no per-vendor
subclasses. Workflow and domain code hold an `LLMProvider` and never learn
which backend is behind it; an AST test fails the build if a
provider-specific value appears outside the factory. `KNOWN_PROVIDERS` is
closed, `LLM_PROVIDER` defaults to `mock` so nothing silently requires a
credential, and a missing key fails loudly rather than falling back.
Direct Gemini is unchanged and remains first-class.

**Rejected:** a per-vendor provider class hierarchy (per-vendor code paths
for zero behavioural difference); LangChain's provider layer (excluded by
the Phase 3 gate); a direct `openai` SDK client (a second HTTP client and
a second retry policy alongside litellm's).

### ADR-5.2 OmniRoute is optional infrastructure, never a dependency

**Status:** Accepted.

**Context.** OmniRoute (MIT, verified — `omniroute@3.8.49`) is a local
gateway that routes to free providers with no credentials. It was required
to be evaluated, and required not to become mandatory.

**Decision.** Integrate it as a **URL**, not as a dependency. There is no
`OmniRouteProvider`, nothing imports it, and `pyproject.toml` is unchanged.
The only OmniRoute-specific value in the project is a default base URL
constant inside the factory seam.

**Consequences.** Switching to Ollama is a different `--base-url`; switching
to OpenAI is a different URL and a key. A checkout with no gateway and no
credentials still runs the full unit suite and the mock golden path.
Evidence and the findings that the README does not mention are in
`docs/PHASE_5_OMNIROUTE.md`.

### ADR-5.3 Structured LLM output: schema in the prompt, schema at the gate

**Status:** Accepted.

**Context.** Phase 4's mock provider always emitted valid JSON, so prompts
that never stated a response shape looked fine. The first live model
returned `steps` as an array of strings and exhausted every repair attempt
on a response it had no way to know was misshapen.

**Decision.** Derive the prompt's response-format instruction from the
Pydantic model itself (`json_schema_instruction`), so it cannot drift from
the model that validates the reply. Recover the JSON body from fences and
preamble with a purely lexical `extract_json`. Prompt versions bumped to
`v2`.

**Consequences.** Extraction is not lenient parsing — whatever it finds
must still satisfy the model in full, and non-JSON text is returned
unchanged so the error reports what the model actually said. It is
AST-tested to call no `eval`, `exec`, `compile`, `__import__` or
`literal_eval`. `generate_structured` and `ai/repair.py` now share the one
parse path; previously they differed, and a correct-but-fenced reply
succeeded in one while burning a repair attempt in the other.

**Rejected:** Instructor (a dependency for a retry loop we already have and
a schema instruction that is ten lines, and it would own retry policy,
blurring ADR-5.4); Outlines (constrained decoding needs logit access,
meaningless through a hosted gateway); LangChain `OutputFixingParser` (asks
a second LLM to fix the first, and never validates against the trace).

### ADR-5.4 Provider retry is not workflow repair

**Status:** Accepted.

**Context.** Both look like "try again", and merging them is tempting.

**Decision.** Keep them entirely separate. **Provider retry** handles a
call that did not complete — timeout, 429, 5xx, connection reset — and
resends an identical request, bounded by `max_retries`. **Workflow repair**
handles a call that completed with wrong *content* — schema or semantic
failure — and sends a **different** prompt built from the previous output
plus the specific validation errors, bounded by `max_repair_attempts`.

**Consequences.** Worst case is an explicit `(1 + max_retries) x
(1 + max_repair_attempts)` calls. Merging them would either resend a
semantically wrong answer unchanged, or rebuild a repair prompt after a
network timeout when there was no output to repair.

**Measured defect this separation exposed.** litellm retries three times by
default and the underlying OpenAI client retries again, so `max_retries=2`
produced **nine** upstream HTTP requests — silently tripling load on a
rate-limited free tier while the configured number said three. Fixed by
pinning `num_retries=0, max_retries=0`, making the provider the single
retry authority. Tests assert exactly 3 requests for a transient failure
and exactly 1 for a permanent one. Repair termination is likewise proven
by call count, not inspection.

### ADR-5.5 The LLM may never author executable source

**Status:** Accepted (restates and hardens the Phase 4 rule under a real model).

**Decision.** The model produces explanation, plan, narration and titles.
It never produces Python, Manim, shell, PowerShell, FFmpeg arguments,
subprocess argv, file paths, or Java that will be executed.

**Consequences.** The control is structural rather than procedural: there
is **nowhere to put code**. `VisualizationStepPlan` has no field shaped
like `code`, `command`, `script`, `args`, `path` or `file` — a test fails
the build if one appears — and `visual_action` is a closed `StrEnum`, so
an out-of-vocabulary "instruction" fails Pydantic validation before any
project code inspects it. Invented extra fields are dropped at validation.
Free-text fields reach the renderer only through `repr()` as inert string
literals. Prompt builders are AST-tested to be unable to read `Settings`
or `os.environ`, so no credential can be leaked into a prompt sent to a
third-party gateway.

### ADR-5.6 Superseded validation attempts

**Status:** Accepted. **Contract change:** `ValidationResult` gains
`superseded: bool = False` (backward compatible — optional with a default).

**Context.** A real run reported `validation: FAIL` overall while every
artifact had in fact been built from a passing attempt, because the
superseded first attempt was still counted.

**Decision.** Keep the full repair history — it is audit evidence for how
many attempts a provider needed, and deleting it would hide provider
quality. Mark replaced attempts `superseded`. `ValidationSummary.all_passed`
ignores them; `every_attempt_passed` reports the stricter view.

### ADR-5.7 Timeline fitting: widen the visuals, never cut the audio

**Status:** Accepted.

**Context.** `alignment.py` holds that the visual timeline is authoritative
and audio never stretches it. Correct — but it assumed the plan's durations
were achievable. The live model proposed 1.0 s steps for narration taking
2.2–4.5 s to speak; all 25 segments overflowed and `validate_timeline`
correctly failed rather than truncating.

**Decision.** Fix it upstream of the renderer, not by weakening the
alignment rule. `narration/fitting.py::fit_plan_to_narration` widens each
visual step until its measured speech fits, before Manim runs.

**Consequences.** Widen only, never shrink — nothing is ever cut off, and
an AST test forbids `min()` in that module. No truncation, no `-shortest`,
no silent duration manipulation, no video stretching: Phase 4.4's
prohibitions hold. Durations are measured from real TTS output, never
estimated. The model's proposal is a floor, so deliberate pacing survives
and only the impossible part is corrected. After fitting, real runs report
overflow 0 and `validate_timeline` PASS.

### ADR-5.8 Environment-switched configuration; production reads no dotenv file

**Status:** Accepted.

**Context.** `Settings` read `.env` and `.env.local` relative to the
*working directory*. Two consequences, both observed rather than
theoretical: the unit suite loaded a developer's real keys (pytest runs
with the repo as CWD), and a production process started inside a checkout
would have inherited `.env.local` and run on a personal credential.

**Decision.** `CODE2SHORTS_ENV` selects which files may be read, and
nothing else: `local` (default) reads `.env` then `.env.local`; `test`,
`ci`, `production` and `prod` read **none**. Resolved per instantiation,
not at import, so setting the variable is honoured without reimporting.

**Consequences.** Production is file-free by construction rather than by a
rule nobody can enforce — there is no file for it to find. Precedence
stays `process environment > env files > defaults`, verified in real
subprocesses. `tests/conftest.py` sets `CODE2SHORTS_ENV=test` for every
non-`integration` test, so the unit suite is credential-free on every
machine; integration tests are untouched, because reaching a real provider
is their purpose.

**Rejected:** documenting "do not deploy from a checkout" (unenforceable);
`python-dotenv` directly (pydantic-settings already wraps it); a
configuration framework such as dynaconf or hydra (a dependency for one
class); a cloud secret-manager SDK (binds the app to one platform for
something the platform already does through environment variables).

### ADR-5.9 Credentials are SecretStr; classification is advisory

**Status:** Accepted.

**Context.** A real API key was printed into pytest output. A test asserted
`Settings().gemini_api_key is None` — conflating "the class ships a
default" with "this machine has no key" — and pytest renders the compared
value in its assertion diff. Separately, a `gsk_` credential (a **Groq**
prefix) was configured as `XAI_API_KEY`, and api.x.ai's reply,
"Incorrect API key provided", reads like an expired key rather than the
wrong vendor entirely.

**Decision.** Credential fields are `SecretStr`, so `repr`, `str`,
`model_dump`, `model_dump_json` and tracebacks render a mask;
`config.reveal()` unwraps only at the provider-factory call sites where the
value goes on the wire. `config.classify_credential()` names the vendor a
credential *looks* like from its prefix, returning a label and never a
value.

**Consequences.** Accidental rendering can no longer expose a key, and
tests assert that for provenance, artifacts, workflow state, exceptions,
logs and prompts. Classification is **advisory and deliberately not
enforced by the factory**: prefixes are vendor conventions, not guarantees,
and hard-rejecting on one would break the day a vendor changes its format.
Its job is to stop a confident-but-wrong diagnosis — the integration test
now reports *"credential is not an xAI credential… its prefix identifies it
as 'groq'"* instead of a vague auth failure.

### ADR-5.10 No automatic runtime provider failover

**Status:** Accepted (deferred implementation, deliberately).

**Context.** `LLM_FALLBACK_PROVIDER` exists in the configuration contract,
and free-tier providers fail often enough to make automatic failover
tempting: Gemini's free tier allows 20 requests/day, and the OmniRoute
gateway exhausts its upstreams and answers HTTP 400.

**Decision.** The variable is **read and validated** — declaring a fallback
whose credential is absent fails at startup — but **no runtime failover is
implemented**. A provider that cannot answer raises.

**Consequences.** Artifact lineage stays truthful. Silently switching
provider mid-run would record a `producer` that did not produce the
artifact, and would hide a failing primary behind a quiet success. Given
that lineage is load-bearing in this architecture, that is a decision to
take explicitly, not a side effect of reading a config key.

Locked by tests: configuring a fallback does not change which provider is
built; a failing provider raises rather than substituting; and an AST sweep
fails the build if `build_llm_provider` is ever called outside the factory
seam, which is how a failover path would have to be introduced.

**If it is ever implemented**, it must be explicitly configured, visible in
execution metadata, preserve lineage, record the actual producer, never
hide primary failure, be bounded, and have tests proving deterministic
provider identity.

---

## Phase 5.3 — Educational story planning

### ADR-5.11 Educational Integrity Over Duration

**Status:** Accepted. **This is a product principle, not a tuning knob.**

**The principle.**

> Code2Shorts prioritizes execution correctness, learner comprehension,
> and visual clarity over arbitrary video-duration targets.
>
> The system MUST NOT remove an execution state, explanation, or
> conceptual transition solely to satisfy a duration constraint.
>
> Any future compression must be trace-grounded and must preserve
> pedagogically necessary transitions.
>
> A longer video that teaches correctly is preferable to a shorter video
> that causes the viewer to misunderstand the algorithm.

Duration is **secondary**. Learning quality is **primary**.

**Context.** Real runs produce 109–384 s videos where short-form platforms
want under 60 s. The obvious lever — cap the step count, or drop the
least "interesting" events — is precisely the lever that breaks the
product. The value here is that every frame is derived from a real JVM
execution; a video that omits the transition where the invariant is
established is not a shorter lesson, it is a wrong one.

**Decision.** No duration or step budget exists anywhere in the
educational layer. `EducationalPlanResponse` has **no duration field** —
there is nothing to optimise against. Completeness is judged by concept
coverage, never by length.

**Consequences.**
* `validate_learning_completeness` fails a plan for *missing understanding*
  (a required concept never taught; a moment with neither narration nor
  explanation) and never for being long.
* `LearningCompleteness.estimated_narration_words` is reported for
  diagnostics and is explicitly not a pass/fail input.
* Redundant events may be **identified** and summarised into one moment —
  twenty identical loop checks need not be twenty moments — but the
  conceptual boundaries survive: the first check teaches "we continue
  while…", the last teaches "we stop because…".
* Locked structurally: a test parses the AST of `ai/education.py` and
  `ai/grounding.py` and fails the build if a `MAX_*`/`TARGET_*` budget or a
  `len(...) > limit` comparison appears. Another test asserts a 200-moment
  plan passes.

**The gate question** is not "is this short?" but "does this teach the
algorithm clearly, correctly and visually?" A correct three-minute video
passes; a misleading forty-five-second one fails.

### ADR-5.12 Observed facts and explanations are different kinds of claim

**Status:** Accepted.

**Context.** An LLM asked to explain an execution will happily write
"fast = 3" whether or not any event says so, and a fabricated value is
indistinguishable from a real one in prose. But requiring trace evidence
for *every* sentence is equally wrong: there is no trace event for "why
the pointer moves", so demanding one would force the model to attach
irrelevant evidence to reasoning.

**Decision.** `ClaimKind` splits them at the schema level:
`OBSERVED` (asserts runtime state — **requires** evidence, and stated
values are checked against the cited events), `EXPLANATION` (why the
algorithm behaves this way — grounded in source and semantics),
`COMMENTARY` (motivation, complexity, analogy).

**Consequences.** `ai/grounding.py` enforces evidence only where evidence
is meaningful. Two defects were found by its own tests while building it:
matching English "is" as an assignment rejected the valid explanation
"left is less than right", and a value pattern of `\w+` silently skipped
quoted chars, letting a fabricated `chars[7] = 'Z'` through. Both fixed;
both now regression-locked.

The LLM is an **educational planner**: it may answer "which observed
transitions matter?" and may not answer "what was the value of nums[3]?"
unless the supplied trace says so.

### ADR-5.13 Required concepts are derived from the trace's shape

**Status:** Accepted.

**Context.** Different algorithms must teach different things — a
two-pointer swap and a scalar computation do not share a checklist — but
branching on the algorithm's *name* would reintroduce exactly the
algorithm-specific coupling the visualization layer is forbidden to have.

**Decision.** `detect_algorithm_shapes()` classifies an execution
structurally from the trace alone (`POINTER_TRAVERSAL`, `ARRAY_MUTATION`,
`SCALAR_COMPUTATION`), and required concepts are the union of a universal
set with each detected shape's set. No algorithm name is ever consulted.

**Consequences.** A scalar computation is not failed for never teaching
`POINTER_MOVEMENT`. Adding a new algorithm requires no change here; adding
a genuinely new *shape* is one entry in a table.

---

## Phase 6A — data-structure visualization reuse decision

### ADR-6.1 Observed state, not author-issued visualization commands

**Status:** Accepted. Research-only phase; no implementation.

**Context.** Phase 6 was expected to choose a data-structure
visualization library for HashMap, Stack, Queue and Binary Search. Six
candidates were audited with licences verified from authoritative sources
(docs/PHASE_6A_REUSE_AUDIT.md).

**Finding.** Every candidate that can draw a HashMap is driven by
**author-issued commands** — Algorithm Visualizer's own documentation says
its tracers "extract visualizing commands from code"; `manim-dsa` and
`manim-data-structures` take the contents as constructor arguments. The
only observed-state project, Python Tutor's Java backend, is **AGPL-3.0**
and unusable as code.

**Decision.** Reject all of them as dependencies. Code2Shorts keeps the
observed-state model: a frame is true because the JVM did it.

**Consequences.** Adopting an author-command tracer would invert the
project's central guarantee — state would be true because someone wrote a
call saying so, and an LLM emitting those calls could assert values that
never occurred. That is the failure mode ADR-5.12 exists to prevent, so
no such API may be introduced even internally. `manim-dsa` (stack visual
semantics), `manim-data-structures` (array pointer/window semantics) and
Python Tutor (heap-snapshot-per-step model) are REFERENCE only. VisuAlgo
is proprietary and forbids forks and derivatives; it may be looked at,
never copied. **Zero dependencies added; `pyproject.toml` unchanged.**

### ADR-6.2 The gap is instrumentation, not visualization

**Status:** Accepted.

**Context.** Before judging any renderer, the current tracer was run
against three real Java programs.

**Evidence.** With a real `HashMap`, the variable is captured **once, as
`{}`**; with a real `ArrayDeque`, **once, as `[]`**. Every `put`, `push`
and `pop` produces no event, because the instrumenter emits on assignments
and array subscripts and these are method calls. The programs ran
correctly (`0,1` and `true`) — the execution is fine; the *observation* is
not. Binary search, by contrast, is already fully observed: `low`, `mid`,
`high` as scalars, `nums` as an array, `ARRAY_READ` and
`CONDITION_EVALUATED` for the comparisons.

**Decision.** Phase 6 starts with the **trace**, not the renderer. No
visual work until a real trace shows non-empty, changing collection
contents.

**Consequences.** A renderer fed today's trace would faithfully draw an
empty HashMap while the algorithm solves the problem — worse than drawing
nothing. The minimum extension is to re-`repr` a collection variable after
a statement that may mutate it and emit the **existing**
`VARIABLE_ASSIGN` event: no new `TraceEventType`, no new `TraceEvent`
field, no change to `ExecutionTrace` semantics, because `repr(Object)` and
`assign(...)` already exist.

**Open risk, unresolved by design:** `Map.toString()` iteration order is
not guaranteed by the JLS, and ADR-005 rejects nondeterminism outright.
This must be settled — prefer `LinkedHashMap`, sort keys for display, or
explicitly accept and document per-JDK determinism — before HashMap
visuals ship. Reflecting into bucket internals is deferred: JDK-fragile,
a reflection surface, and unnecessary to teach what the algorithm does.

### ADR-6.3 Two generic snapshots, not four; no SearchState

**Status:** Accepted (design decision; not implemented).

**Context.** The proposed Phase 6 architecture had four leaves:
`ArraySnapshot`, `MapSnapshot`, `StackSnapshot`, `SearchState`.

**Decision.** Add **`MapSnapshot`** and **`SequenceSnapshot`** only.

**Consequences.** `SearchState` is dropped: binary search is already
representable with existing array + scalar + pointer state, so the type
would encode an *algorithm*, not a data structure — the thing this project
forbids. `StackSnapshot` and `QueueSnapshot` are merged: both are ordered
sequences whose interesting property is which end was just touched, that
end is observable, and an `ArrayDeque` is literally the same object used
two ways — two types would force the mapper to guess the author's intent,
and guessing intent is what an observed-state system must not do.

No `HashMapVisualizationEngine` / `StackVisualizationEngine` /
`BinarySearchVisualizationEngine`. The renderer continues to dispatch on
**what state is present**, never on what the algorithm is called, and the
existing no-algorithm-branch test should be extended to fail on any
branch keyed by an algorithm or structure name.

### ADR-6.4 Deterministic collection ordering

**Status:** Accepted. Implemented in Phase 6.

**Context.** `HashMap`'s iteration order is **not guaranteed by the JLS**,
and ADR-005 rejects nondeterminism outright. Emitting `Map.toString()`
would therefore risk two runs of the same program producing two different
videos — and, worse, would present whatever order came out as if it meant
something.

**Decision.** The observed order is either semantic or canonical, and the
trace always says which:

* A map whose class specifies its iteration order (`LinkedHashMap`) keeps
  its **encounter order** and is flagged `collection_ordered=true`.
* Any other map is emitted **sorted canonically by key text** (ties broken
  by value text) and flagged `collection_ordered=false`, meaning "this
  order was chosen for determinism and carries no meaning".
* `Deque` and `List` iteration order **is** specified, so sequences are
  always `ordered=true`.

**Consequences.** Identical logical executions produce identical snapshots
— asserted by test. `MapSnapshot.ordered` carries the distinction to the
renderer, which prints *"order shown is canonical, not insertion order"*
beneath an unordered map, so a viewer is never taught an insertion order
that the JVM never promised. An ordered map carries no such note.

Nothing is silently reinterpreted to look tidier: sorting is applied only
where the order was already meaningless, and it is disclosed where it is
applied.

**Rejected:** reading `HashMap`'s internal table to recover bucket order
(JDK-version fragile, needs reflection, and teaches an implementation
detail rather than the algorithm); and rewriting user programs to use
`LinkedHashMap` (that would change the program being taught).

### ADR-6.5 A sequence's discipline is observed, not inferred

**Status:** Accepted.

**Context.** `SequenceSnapshot` must present a stack vertically (top first)
and a queue horizontally (front first). The first implementation chose
orientation from the last operation's end — and produced a real defect
caught by looking at a rendered frame: a FIFO queue drained with `poll`
was labelled **"top first"**, because `pop` (stack) and `poll` (queue)
both act on the front. One operation cannot distinguish them.

**Decision.** Derive the discipline from what actually differs: whether
**insertions and removals share an end**. The reconstruction carries the
observed `insert_end` and `remove_end` across the run;
`discipline` is `lifo` when they match, `fifo` when they differ, and
`unknown` until both an insertion and a removal have been seen.

**Consequences.** An `ArrayDeque` used as a stack and the same class used
as a queue render differently, and both labels are earned by observation —
`balanced_parens` shows "stack (top first)", `task_queue` shows "queue
(front first)", with no algorithm name consulted anywhere. Before either
kind of operation has been observed the panel says only which end was
touched, rather than asserting a discipline nothing has demonstrated.

This is the same principle as ADR-6.1 applied one level down: state the
thing you observed, not the thing you assume.

### ADR-6.6 A mutation used as a value is still a mutation

**Status:** Accepted.

**Context.** Collection observation initially fired only on bare
statement calls (`stack.push(c);`). Real code frequently mutates a
collection *as an expression*: `int head = queue.poll();` and
`total = total + stack.pop();`.

**Evidence.** The first `task_queue` golden path rendered a queue that
filled to `[1, 2, 3]` and **never drained**, because every `poll` was a
declaration initializer. The video was not merely incomplete — it showed
something untrue about the execution.

**Decision.** Search the whole expression subtree of declarations and
assignments for a mutating call on a tracked collection, and observe the
collection after the statement.

**Consequences.** The queue now shows its full FIFO lifecycle
(`[1,2,3] → [2,3] → [3] → []`). Only direct calls on a simple name are
matched; a chained or computed receiver is left untraced rather than
guessed at, which yields fewer events but never a wrong one — the standing
rule for this instrumenter.

---

## Phase 6.1 — Teaching-first visual layout

### ADR-6.7 The scene frame is derived from the output aspect

**Status:** Accepted.

**Context.** The pipeline produced technically valid 1080x1920 videos that
were unteachable: the Java code was too small to read on a phone and large
black bands filled the top and bottom of every frame. The obvious remedy —
raise font sizes — would have been wrong.

**Measured cause.** A probe scene rendered at the real resolution reported
`frame_width = 14.222`, `frame_height = 8.0`, `pixel_width = 1080`,
`pixel_height = 1920`. Manim keeps `frame_width` at its **16:9** default
even when `--resolution 1080,1920` is passed; only the pixel dimensions
change. The scene was a 16:9 canvas rendered into a 9:16 image, so Manim
letterboxed it: drawing the frame border showed it occupying roughly
**31% of the frame height**, with ~69% structurally unreachable. Nothing
was too small — most of the canvas was being discarded.

**Decision.** The generated scene derives the frame from the real pixel
aspect:

```python
config.frame_height = 8.0
config.frame_width = config.frame_height * (config.pixel_width / config.pixel_height)
```

giving 4.5 x 8 units, 240 px per unit on **both** axes.

**Consequences.** The whole image is addressable and pixels are square, so
layout constants can be stated in units *and* in the pixels they actually
produce — the claim "this is readable" becomes checkable rather than
asserted. Every band constant in `primitives.py` was re-derived for the
4.5-unit width; the previous `CODE_MAX_WIDTH = 6.8` was wider than the
real frame. A regression test asserts the scene emits the correction and
that the derived frame is square-pixelled.

**Rejected:** raising font sizes (treats the symptom, and the letterbox
would still waste 69% of the frame); scaling the whole scene up (Manim
would clip at the letterbox boundary).

### ADR-6.8 Fill the teaching surface; never solve layout by shrinking

**Status:** Accepted.

**Context.** The code panel was only ever *capped* (`if too wide, shrink`),
so a short snippet stayed small and the code — the primary teaching
content — was routinely the least readable element on screen. Captions had
the same defect: a long narration was scaled to one line at roughly a
tenth the height of the code.

**Decision.** Elements fill their band rather than merely fitting inside
it, and overflow is handled by restructuring, not by shrinking below a
readability floor:

* the code panel scales **to** the safe width, not merely under it;
* captions **wrap** (46 chars/line, max 4 lines) at a fixed font size and
  grow downward within a bounded band;
* array cell glyphs are fitted **inside** their box, because font size
  alone cannot guarantee that for multi-character values — the first
  enlarged render showed letters overflowing into neighbouring cells;
* variable-height map/sequence panels are top-aligned in a bounded band,
  because anchoring them at a fixed centre let a three-entry map overrun
  the caption;
* the source window narrowed from 13 lines to 9, because 13 lines could
  only fit by pushing text below the 0.20-unit (48 px) floor.

**Consequences.** Fewer lines shown larger. The window change is
presentational only: `SourceLocation.line` and the highlighted line are
untouched, a test asserts a 9-line and a 13-line window highlight the same
line, and the Phase 4.5.1 mapping regression still passes.

Layout is enforced structurally: every band is asserted inside the safe
area, bands are asserted ordered and separated, the code panel is asserted
to fit its band, and an AST test forbids whole-scene scaling.

**What is NOT claimed.** These tests check geometry, not legibility.
Whether a human can read the result is settled by inspecting real frames,
which stays mandatory — a contact sheet of the palindrome run is legible
at 270x480 thumbnail scale, which is the actual evidence.

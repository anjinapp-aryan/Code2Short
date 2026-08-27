# Code2Shorts

Generate accurate short-form (9:16) educational programming videos from a
topic — e.g. "Reverse String" — by actually planning, generating, compiling,
testing, and *executing* real code, then rendering deterministic Manim
animations from the captured execution trace. The LLM plans; it never
touches pixels.

```
Topic -> LessonSpec -> LanguageAdapter -> generated code -> compile -> test
      -> execute -> ExecutionTrace -> AnimationSpec (DSL) -> Manim renderer
      -> TTS/subtitles -> FFmpeg -> visual QA -> MP4
```

See [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md) for why it's built
this way and what was reused from prior open-source work.

## Status

Phase 1: `JavaAdapter.compile()` / `.test()` / `.execute()` are real —
Maven + JDK 17 + JUnit 5, subprocess-isolated, timeout-bounded, temp
workspace per run. Proven against a hand-written Reverse String fixture
(`HELLO -> OLLEH`) plus deliberately broken variants (bad syntax, wrong
logic, non-zero exit, timeout) to prove the validator actually rejects bad
code, not just accepts good code.

Phase 2: `JavaAdapter.trace()` is real too. `tools/java-instrumenter/`
(JavaParser) source-instruments a small supported Java subset — methods,
variable assignment, if/else, for/while, one-dimensional arrays, simple
recursion — and a trusted runtime helper (`Code2ShortsTrace.java`) emits a
bounded, deterministic event stream, enforcing every trace limit
(`max_events`/`max_loop_iterations`/`max_call_depth`/`max_output_size`)
*inside the traced JVM* so a runaway program terminates fast rather than
waiting out the timeout. Nondeterministic APIs (threads, `Random`,
wall-clock/env access) and out-of-scope constructs (lambdas, streams) are
rejected at instrument-time, never silently traced.

Phase 3: the workflow/artifact/AI layer between deterministic execution and
future AI/visualization/video. `workflow/` (`WorkflowNode`/`Workflow`/
`WorkflowRunner`, local + sequential, with retry/checkpoint/structured
events), `artifacts/` (`Artifact`/`ArtifactStore`, content-addressed,
lineage-tracked), `ai/` (framework-neutral request/response contracts,
schema + semantic validation, `MockLLMProvider`). Three real nodes
(`CompileNode`/`TraceNode`/`ExplainNode`) compose around Phase 1/2's
`JavaAdapter` unmodified. AI output is never trusted merely because it
parsed — every trace-event reference an explanation makes is checked
against the real trace; a reference to an event that doesn't exist fails
validation and is never saved as an artifact. No LangGraph, no MCP, no
multi-agent architecture — see `ARCHITECTURE_DECISIONS.md` for why each is
deferred, not missing by oversight. See `SECURITY_SANDBOX.md` for the
full trust model.

Phase 4: the full pipeline is real, end to end. `GeminiLLMProvider`
(litellm-backed) sits behind the unchanged `LLMProvider` interface — real
provider, zero Gemini-specific types anywhere outside `ai/providers/gemini.py`.
Bounded AI repair (`ai/repair.py`) is shared by every AI-backed node —
generate, validate, and if it fails, ask the model to fix it, capped by
`max_repair_attempts`, never unbounded. `visualization/` (a closed
`VisualAction` vocabulary — Pydantic rejects out-of-vocabulary instructions
before they can reach anything, not just a post-hoc filter — semantic
validation, `ManimVideoRenderer`/`FakeVideoRenderer`), `narration/`
(validation + `TTSProvider`/`MockTTSProvider`), `media/` (`MediaComposer`,
fixed-argument FFmpeg). Nine sequential nodes now run the whole pipeline:
compile -> trace -> explain -> visualize -> narrate -> render -> compose
-> final-validate — proven end to end with real Java compile/trace and
zero external credentials (`tests/test_phase4_pipeline_integration.py`).
AI output is DATA everywhere, never executable code: no AI-authored Python
or shell command is ever executed — see `SECURITY_SANDBOX.md` for the full
seven-boundary trust model.

Phase 4.1: the pipeline produces a **real, playable 1080×1920 MP4** —
real Maven/JUnit/Java execution, real trace, real Manim render, real
FFmpeg composition, verified by probing the actual file and inspecting
extracted frames (not by trusting exit codes). Run it with
`python scripts/run_golden_path.py`; see
[docs/PHASE_4_1_REAL_RENDER.md](docs/PHASE_4_1_REAL_RENDER.md) for setup,
measured metadata, the performance baseline, the four real bugs this
phase caught, and the honest limitation that the current renderer draws
text cards rather than array/pointer visuals.

Phase 4.2: the **rich visual engine**. `visualization/state.py` replays the
trace into per-event `FrameState` (arrays reconstructed from real writes,
scalars, read/write markers), and `visualization/primitives.py` turns that
into array tiles, index labels, pointer arrows and a variables panel — all
from trusted repository-owned code. One generic implementation serves
Reverse String, Palindrome, Two Sum, Move Zeroes and Remove Duplicates with
**no algorithm-specific branches** (enforced by a source-scanning test).

Phase 4.3: **code/execution synchronization**. The highlighted source
line is derived from the real trace — never chosen by an LLM. A new
`SourceLocation` model resolves which *file* each event belongs to (a trace
mixes line numbers across files, so a line number alone is ambiguous), and
`CodeState` windows long files around the executing line instead of
shrinking them into illegibility, preserving true line numbers via Manim's
`line_numbers_from`. Verified across 166 real events in five algorithms
with zero mismatches against the original source. `ExecutionTrace` now
carries `trace_schema_version` plus toolchain/source provenance. See
[docs/PHASE_4_3_CODE_SYNCHRONIZATION.md](docs/PHASE_4_3_CODE_SYNCHRONIZATION.md).

Phase 4.4: **narration, audio alignment and subtitles**. Real speech via
Windows SAPI (offline, no API key, no cost, zero new dependencies),
deterministic alignment where the **visual timeline stays authoritative** -
measured audio duration only detects and reports overflow, it never moves a
visual step - and an SRT subtitle track built with the already-present `srt`
library. Verified end to end with real speech: 1080x1920 h264 20.10s video +
aac 22050Hz audio of matching duration, 4 valid monotonic cues. A real SRT
cue-injection vulnerability was found by its own security test and fixed.
Run `python scripts/run_narrated_golden_path.py`; see
[docs/PHASE_4_4_NARRATION_AUDIO.md](docs/PHASE_4_4_NARRATION_AUDIO.md) and
[docs/PHASE_4_4_REUSE_AUDIT.md](docs/PHASE_4_4_REUSE_AUDIT.md).

Phase 4.5: **production E2E hardening**. All five golden-path algorithms
(Reverse String, Palindrome, Two Sum, Move Zeroes, Remove Duplicates) run
the complete real pipeline - real Maven/JUnit/JVM, real trace, real Manim,
real SAPI speech, real FFmpeg - with **no fake renderer** in the acceptance
gate. A new media validation layer (`media/validation.py`) enforces
1080x1920 H.264 + AAC and detects composition drift, failing loudly rather
than silently truncating or stretching. 24 failure-injection tests, 24
hardening/security-regression tests, 10 repeatability tests. **340 tests
pass; zero new dependencies.** See
[docs/PHASE_4_5_E2E.md](docs/PHASE_4_5_E2E.md),
[docs/PHASE_4_5_REUSE_AUDIT.md](docs/PHASE_4_5_REUSE_AUDIT.md) and
[docs/PHASE_4_5_PERFORMANCE.md](docs/PHASE_4_5_PERFORMANCE.md).

Phase 4.5.1: **source-line mapping correctness**. Fixed a defect where a
code window beginning on a blank line made every displayed Java line number
read one low, so the video highlighted the wrong statement - Manim silently
strips leading/trailing blank lines while its label column keeps counting.
The fix trims those lines at the `CodeState` layer, leaving Manim nothing to
strip; `SourceLocation.line` remains the original Java line and no semantic
number is rewritten. Now verified through to the label Manim *actually
displays*: **166 real-trace events across all five algorithms, 0 mismatches**,
confirmed by cropped-frame inspection. **377 tests pass.** See
[docs/PHASE_4_5_1_LINE_MAPPING.md](docs/PHASE_4_5_1_LINE_MAPPING.md).

Phase 5: **a real LLM writes the content, and the trace still decides what
is true.** Explanation, visualization plan and narration are now generated
by an actual model rather than a mock. One `OpenAICompatibleProvider`
parameterised by `base_url` reaches every OpenAI-compatible backend -
OmniRoute, Ollama, vLLM, OpenAI - behind the unchanged Phase 0.1
`LLMProvider`; there is no per-vendor class, and provider selection lives
in exactly one file (`ai/providers/factory.py`), enforced by an AST test.
**OmniRoute is optional infrastructure, not a dependency**: nothing imports
it, `pyproject.toml` is unchanged, and switching backend is a different URL.
`LLM_PROVIDER` defaults to `mock`, so a fresh checkout needs no credentials.
Direct Gemini still works.

The live model broke three things a cooperative mock never could, and all
three are now fixed and regression-locked: prompts never stated their
response schema (fixed by deriving it from the Pydantic model itself);
`generate_structured` and the repair loop parsed replies differently, so a
correct-but-fenced answer burned a repair attempt; and litellm's own retries
multiplied with ours to make **nine** upstream requests where the config
said three. Real runs also showed the model proposing 1.0s steps for 3.5s of
speech - `narration/fitting.py` now widens visual steps to fit measured
audio **before** rendering, widen-only, so nothing is ever truncated.

Verified with a real free-tier model end to end: Reverse String
(`HELLO -> OLLEH`) and Move Zeroes, real Maven/JVM trace -> real LLM ->
schema + semantic validation -> bounded repair -> real Manim -> real SAPI
speech -> real FFmpeg -> media validation PASS. A hostile model cannot
reach the renderer: 43 security tests cover the closed action vocabulary,
smuggled fields, fabricated trace events, prompt injection carried in the
Java source, secret leakage, and bounded-repair termination proven by call
count. **Zero new dependencies.** See
[docs/PHASE_5_LLM_ARCHITECTURE.md](docs/PHASE_5_LLM_ARCHITECTURE.md),
[docs/PHASE_5_OMNIROUTE.md](docs/PHASE_5_OMNIROUTE.md) and
[docs/PHASE_5_REUSE_AUDIT.md](docs/PHASE_5_REUSE_AUDIT.md).

```bash
python scripts/run_phase5_golden_path.py                       # mock, credential-free
python scripts/run_phase5_golden_path.py --provider omniroute --model auto --timeout 300
```

First golden example in progress: **Reverse String** (`HELLO -> OLLEH`),
target audience Java developers. Once code generation + rendering are
wired up end to end, the same visual primitives must be reused (not
rebuilt) for Palindrome, then Two Sum.

## Educational integrity over duration

Code2Shorts is not a "compress everything into 60 seconds" system. It
prioritizes execution correctness, learner comprehension and visual
clarity over duration targets, and **must not remove an execution state,
explanation or conceptual transition solely to satisfy a duration
constraint** (ADR-5.11).

Phase 5.3 added an educational planning layer between the trace and the
visuals:

```
ExecutionTrace  ->  EducationalPlan  ->  VisualizationPlan
 (what is TRUE)     (what MATTERS)        (how to SHOW it)
```

`EducationalPlanResponse` has no duration field — there is nothing to
optimise against. A plan is judged on concept coverage: every moment is
labelled `observed` / `explanation` / `commentary`, and an `observed`
claim must cite real trace events whose values are checked against the
reconstructed state. Required concepts are derived from the trace's
*shape* (pointer traversal, array mutation, scalar computation), never
from the algorithm's name.

The gate question is "does this teach the algorithm clearly and
correctly?" — a correct three-minute video passes; a misleading
forty-five-second one fails. See
[docs/PHASE_5_3_REUSE_AUDIT.md](docs/PHASE_5_3_REUSE_AUDIT.md).

## Data structures (Phase 6, planned)

A focused audit ([docs/PHASE_6A_REUSE_AUDIT.md](docs/PHASE_6A_REUSE_AUDIT.md))
found that the blocker for HashMap/Stack/Queue is **instrumentation, not
visualization**. Running the current tracer on real Java shows a `HashMap`
captured once as `{}` and an `ArrayDeque` once as `[]` — every `put`,
`push` and `pop` is a method call, and the instrumenter emits events only
on assignments and array subscripts. Binary search, by contrast, is
already fully observable and needs nothing new.

Every candidate library that can draw a HashMap is driven by author-issued
visualization commands, which would invert this project's guarantee that a
frame is true *because the JVM did it*. All were rejected as dependencies;
three are reference-only. Zero dependencies added (ADR-6.1 – ADR-6.3).

## Configuration

`CODE2SHORTS_ENV` decides which files are read, and nothing else:

| Value | Files read | For |
|---|---|---|
| `local` (default) | `.env`, then `.env.local` | your machine |
| `test` / `ci` | none | deterministic pytest |
| `production` / `prod` | none | runtime injection only |

Production reads **no dotenv file** — dotenv paths resolve against the
working directory, so a production process started inside a checkout would
otherwise inherit `.env.local` and run on a personal key.

Copy `.env.example` to `.env.local` and put real keys there (gitignored).
`.env.production.example` is the deployment contract. Precedence is
`process environment > env files > defaults`. Credentials are `SecretStr`,
so printing `Settings` renders a mask.

```bash
python scripts/production_config_check.py   # deployment preflight; exit 0 = usable
```

Providers: `mock` (default, no credentials), `gemini`, `xai`, `omniroute`,
`openai_compatible`. See
[docs/PHASE_5_PROVIDER_CONFIGURATION.md](docs/PHASE_5_PROVIDER_CONFIGURATION.md).

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -e ".[dev]"
```

## Verify

Requires JDK 17+ and Maven on PATH for the integration tests (real
`mvn`/`java` subprocesses; `pytest` alone covers both unit and integration
tests — filter with `-m "not integration"` for a fast unit-only run).
Phase 2's tests also need the instrumenter tool built once:

```bash
cd tools/java-instrumenter && mvn -q package && cd ../..
pytest
```

## Stack

Python 3.12, Pydantic v2, Typer, pytest, Manim Community, FFmpeg, JDK 17 +
Maven + JUnit 5 (Java adapter), LiteLLM (provider abstraction), Ollama for
local LLM dev. Modular monolith — no databases, no Kubernetes, no
microservices, no cloud infrastructure.

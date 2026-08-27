# Execution sandbox: what it does and does not protect against

Generated code is untrusted input, from Phase 1 onward. This document
describes the actual boundary implemented in
`src/code2shorts/execution/sandbox.py` and `langadapter/java/`, and what is
explicitly deferred.

## What Phase 1 implements

- **Isolated temp workspace** — every `compile`/`test`/`execute` call gets
  a fresh `tempfile.mkdtemp()` directory (`Workspace`). Nothing is written
  into the application's own source tree or a shared/reused directory.
- **Subprocess isolation** — generated Java is never `exec`/`eval`'d, or
  otherwise loaded, inside this Python process. Every build/run step is a
  separate OS process (`mvn`, `java`), started with an explicit argument
  list (`subprocess.Popen([...])`, never `shell=True`, never string-built
  shell commands).
- **No inherited stdin** — every subprocess is started with
  `stdin=subprocess.DEVNULL`. A child that probes for interactive input
  cannot hang waiting on a pipe that will never receive data.
- **Explicit timeout, whole-tree kill** — every subprocess call is bounded
  (`config.Settings.build_timeout_seconds` for compile/test,
  `execution_timeout_seconds` for running the algorithm itself). On
  timeout, the entire process tree is killed (`taskkill /T /F` on Windows,
  `os.killpg` on POSIX via `start_new_session=True`) — not just the direct
  child. This matters because Maven's own launcher is frequently a wrapper
  (`mvn.cmd` -> `java.exe`) whose grandchild would otherwise be orphaned
  and keep running after the parent is killed.
- **Controlled working directory** — every subprocess runs with `cwd` set
  to the workspace, never the caller's directory.
- **Captured output** — stdout, stderr, exit code, wall-clock duration,
  and timeout status are captured for every stage
  (`CompileResult`/`TestResult`/`ExecuteResult`).
- **Cleanup** — `Workspace.cleanup()` (`shutil.rmtree`) removes the temp
  directory; `Workspace` is a context manager so callers get this via
  `with Workspace() as workspace: ...` even on exception.

## What Phase 2 adds

Phase 2 (`trace()`) introduces one new trusted component (the instrumenter
tool) and one new untrusted-execution wrinkle (trace limits enforced inside
the traced JVM). The Phase 1 boundary above is unchanged and still applies
to every subprocess Phase 2 spawns — nothing here bypasses `run_subprocess`.

- **Instrumenter trust boundary** — `tools/java-instrumenter/` is *our*
  trusted code (reviewed, versioned in this repo), analogous to
  `pom.xml`'s generated template. It never executes the source it's given;
  it only parses it into an AST and prints transformed text. A hostile or
  malformed input can make it throw a parse error (handled —
  `INSTRUMENTATION_FAILED`) but cannot make it run untrusted code, since it
  performs no reflection, no dynamic class loading, and no bytecode
  execution of any kind.
- **Instrumenter timeout** — the instrumenter runs as its own
  `run_subprocess` call with `instrumenter_timeout_seconds`
  (`config.Settings`), independent of `execution_timeout_seconds`. A
  pathological input that makes JavaParser itself slow (e.g. deeply nested
  expressions) is bounded the same way any other subprocess is — timeout,
  then whole-tree kill, same mechanism as Phase 1.
- **Generated-source handling** — instrumented source is written into the
  same isolated temp `Workspace` and compiled/run through the exact same
  Phase 1 `JavaCompiler`/`JavaExecutor` path. No new file-handling pattern.
- **Runtime helper trust boundary** — `Code2ShortsTrace.java`
  (`langadapter/java/resources/`) is hand-written and materialized
  verbatim into every traced workspace; it is never generated or modified
  per-run. It runs inside the *same* traced JVM as the untrusted program
  (there is no process-level isolation between "our" trace calls and "the
  traced program's" code — they're the same OS process), so it only relies
  on ordinary Java visibility (its fields are private, its public API is a
  fixed set of static methods) rather than any stronger isolation.
- **In-JVM trace limits are a resource-exhaustion mitigation, not a
  security boundary.** `max_events`/`max_loop_iterations`/`max_call_depth`/
  `max_output_size` stop a program from consuming unbounded time/memory/
  output *within this process*, and terminate it via a thrown
  `TraceLimitExceededException` — they do nothing to restrict filesystem,
  network, or process-level access, all of which remain governed entirely
  by the Phase 1 boundary above.
- **No new pipe-reading mechanism.** `max_output_size` is deliberately
  enforced *inside the JVM* (a byte-counting `PrintStream` wrapping
  `System.out`) rather than by adding output-size capping to
  `execution.sandbox.run_subprocess`. `run_subprocess` uses
  `Popen.communicate()`, an all-or-nothing read; capping it mid-stream
  safely would require a threaded/non-blocking rewrite, which was
  explicitly rejected as unnecessary complexity — see ADR-007 in
  `ARCHITECTURE_DECISIONS.md`. `run_subprocess` itself is unmodified except
  for one small additive change: an optional `env` parameter (used to pass
  `C2S_MAX_*` limits to the traced JVM), which existing Phase 1 callers
  don't pass and see no behavior change from.

## What Phase 3 adds — the trust model for AI-generated content

Phase 3 introduces the first AI-generated content in the pipeline
(`ExplanationResponse` and friends). This does **not** change the Phase 1
execution sandbox or the Phase 2 trace boundary in any way — no workflow
node calls `compile()`/`trace()` with anything other than the same
`GeneratedCode` shape Phase 1/2 already validated, and no AI output is
ever compiled, executed, or interpreted as code.

**Three trust boundaries, kept explicitly separate:**

1. **Sandbox boundary** (Phase 1) — user/generated *source code* is
   untrusted; it only ever runs inside `execution.sandbox.run_subprocess`,
   unchanged by Phase 3.
2. **Validation boundary** (Phase 3, new) — AI *output* is untrusted. It
   passes through two independent checks before it can become an Artifact:
   - **Schema validation** (`ai/structured.py::generate_structured`) — the
     raw provider text must parse into the exact Pydantic response model
     requested. Malformed output never reaches a node's business logic.
   - **Semantic validation** (`ai/validation.py`) — the *claims* inside
     validated JSON are cross-checked against real data. Concretely:
     every `referenced_trace_event_indices` value an `ExplanationResponse`
     contains must be a real `TraceEvent.step_index` that actually exists
     in the `ExecutionTrace` it claims to explain. A reference to a
     nonexistent event is treated exactly like a hallucination — the
     `ExplainNode` raises `NodeExecutionError(FailureKind.VALIDATION, ...)`
     and the response is **never** saved as an Artifact
     (`test_ai_hallucinated_trace_event_fails_validation_not_silently_accepted`
     proves this end to end).
3. **Trusted runtime boundary** (Phase 2, unchanged) — `Code2ShortsTrace.java`
   remains the only source of `TraceEvent` data; nothing in Phase 3 can
   inject, edit, or override a trace event. AI can *reference* trace
   events; it cannot *create* them.

**AI-generated instructions are never automatically executed.** There is
no code path anywhere in `workflow/` or `ai/` that takes AI output and
feeds it back into `compile()`/`execute()`/`trace()`, a shell, or
`exec`/`eval`. An `ExplanationResponse`/`VisualizationPlanResponse` is
inert data (Pydantic models) until a future rendering stage (not built in
Phase 3) chooses to consume it — and even then, per the target pipeline,
it only ever drives a *deterministic* renderer (Manim), never arbitrary
code execution.

**New risk surface introduced by Phase 3, assessed honestly:** the
`MockLLMProvider` is deterministic and cannot be attacker-influenced (it
returns whatever the test/caller configured, nothing else) — it carries no
real risk. Once a real network-backed `LLMProvider` implementation is
added in a later phase, that implementation becomes a new external
dependency whose output must still pass the same schema+semantic
validation gates described above before anything downstream can trust it;
Phase 3's validation architecture exists specifically so that adding a
real provider later doesn't require re-deriving this trust boundary.

## What Phase 4 adds — real providers, trusted rendering, seven trust boundaries

Phase 3 predicted this moment: "once a real network-backed `LLMProvider`
implementation is added... that implementation becomes a new external
dependency whose output must still pass the same schema+semantic
validation gates." Phase 4 is that moment (`GeminiLLMProvider`), plus two
new trusted-execution surfaces (the Manim renderer, the FFmpeg composer).
None of it changes the Phase 1/2/3 boundaries above — it extends the same
pattern to new stages.

**The seven trust boundaries, end to end:**

1. **Untrusted Java source** — Phase 1's sandbox, unchanged.
2. **Traced Java runtime** — Phase 2's `Code2ShortsTrace.java`, unchanged;
   still the only source of `TraceEvent` data.
3. **AI output** — Gemini (or any future provider) output is DATA, never
   code. `GeminiLLMProvider.complete()` returns a `str`; nothing in the
   pipeline ever passes that string to `exec`/`eval`/a shell/a subprocess
   argument list. It only ever gets parsed as JSON into a fixed Pydantic
   schema (`ai/structured.py`).
4. **Validation layer** — schema (Pydantic) + semantic
   (`ai/validation.py`, `visualization/validation.py`,
   `narration/validation.py`) + bounded repair (`ai/repair.py`, capped by
   `max_repair_attempts` — never unbounded). An AI response that fails
   every attempt never produces an artifact, full stop — see
   `test_ai_repair.py::test_semantic_failure_exhausts_attempts_and_raises`.
5. **Trusted renderer** — `ManimVideoRenderer`/`FakeVideoRenderer`
   interpret a *validated* `VisualizationPlanResponse`. The scene source
   is Python code **we** wrote (`visualization/manim_renderer.py::build_scene_source`);
   every dynamic value from the plan is embedded via `repr()` (a string
   literal), never spliced in as executable syntax —
   `test_scene_source_never_splices_narration_as_syntax` proves the
   generated source stays syntactically valid Python (`compile()` on it
   succeeds) regardless of what the narration text contains. The renderer
   invokes `manim` via a **fixed argument list** subprocess call
   (`execution.sandbox.run_subprocess`, the exact Phase 1 mechanism — no
   `shell=True`, no string-built commands, no argument the AI supplied
   directly). `MediaComposer` follows the identical pattern for `ffmpeg`.
6. **External providers** — Gemini is the only network call anywhere in
   this codebase, and it goes through exactly one seam
   (`GeminiLLMProvider`, itself hidden behind the pre-existing
   `LLMProvider` interface). The API key is read from environment/config
   only (`Settings.gemini_api_key`, `GEMINI_API_KEY` env var), never
   hard-coded, never logged (`GeminiLLMProvider` logs prompt/response
   *lengths*, model name, and attempt number — never content, never the
   key).
7. **Generated media** — video/audio files are real bytes on disk, tracked
   only by path + checksum in Artifact `content` (never the bytes
   themselves — see "artifact lineage" below). Nothing downstream treats a
   generated media file as executable.

**Explicit, unambiguous statements** (the prompt asked for these spelled
out, not implied):

- AI output is DATA. It is never executable code.
- AI-generated Python is never executed. (`build_scene_source` generates
  Python source from validated data — see point 5 — but the AI never
  authors that source; it authors the plan the trusted generator reads.)
- AI-generated shell commands are never executed — there is no code path
  anywhere that takes AI output and puts it in a subprocess argument list;
  every subprocess command (`manim`, `ffmpeg`, `mvn`, `java`) is a
  hard-coded argument list, with only already-validated *data* (paths,
  numbers, plan content already checked by validation) substituted in.
- AI-generated filesystem operations are never executed — nothing in
  `ai/`, `visualization/validation.py`, or `narration/validation.py`
  touches the filesystem at all; only the trusted renderer/composer/TTS
  provider write files, and only to paths this codebase constructs.
- AI cannot directly invoke subprocesses — `LLMProvider.complete()`
  returns a string; no AI-facing interface exposes `subprocess`, `os`, or
  a shell.
- AI cannot directly access the filesystem — same reasoning.
- AI cannot directly access network resources except through the one
  explicitly implemented provider interface (`GeminiLLMProvider`, itself
  only reachable by application code, never by AI output).

**New risk surface introduced by Phase 4, assessed honestly:**
`GeminiLLMProvider` is a real network client — a compromised or malicious
API endpoint could return arbitrary text, but that text is still just
DATA subject to the same schema+semantic validation as `MockLLMProvider`'s
output; nothing about using a real provider weakens the validation gate.
`ManimVideoRenderer`/`MediaComposer` run real external tools
(`manim`/`ffmpeg`) as subprocesses — same threat model as Phase 1's
`mvn`/`java` (untrusted-input-adjacent, not untrusted-code-adjacent: the
tools themselves are trusted binaries, only their *inputs* — already
validated plan data — come from anywhere near AI output).

## What Phase 4.4 adds - narration, TTS and subtitle channels

Narration text is LLM-authored, therefore untrusted. Phase 4.4 gives it
three new destinations, each held to the same rule: **data, never command**.

1. **TTS subprocess.** Narration reaches Windows SAPI as a **bound
   PowerShell parameter** (`-Text`), inside a fixed argument list. The
   PowerShell script is repository-owned, contains no `Invoke-Expression`,
   and has no format placeholder for text to flow into. A payload
   containing quotes, `$(...)`, backticks, `;`, `&&` or `|` is passed
   through as a single argv element and spoken aloud - it cannot alter the
   command. Verified by
   `test_sapi_passes_text_as_a_bound_argument_never_as_script` and
   `test_sapi_script_never_interpolates_text`.

2. **Subtitle file.** Text is written to a `.srt` data file. FFmpeg reads
   it as data; it never reaches a shell. Quotes, ampersands, Unicode,
   emoji and Java syntax are preserved byte for byte.

3. **FFmpeg mux.** Narration text never appears in an FFmpeg command at
   all - only file paths this codebase constructed, in a fixed argument
   list, with an enforced timeout.

**A real vulnerability found and fixed here, not merely theorised.**
`test_srt_injection_cannot_forge_extra_cues` failed on its first run: a
narration payload shaped like an SRT timing block

```
safe

2
00:00:10,000 --> 00:00:20,000
FORGED CUE
```

parsed back as a **second subtitle cue with attacker-chosen timestamps**.
The initial sanitizer collapsed the blank line but left the injected timing
line intact, and the `srt` parser is lenient enough to start a cue without
one. Fixed by collapsing every newline form (`

`, `
`, `
`,
U+2028, U+2029, ``, ``, ``) to a space, so a timing line can
never begin; `validate_srt` additionally rejects any surviving newline.

**New risk surface, assessed honestly.** SAPI is an OS component invoked as
a subprocess - the same threat model as `mvn`, `java`, `manim` and
`ffmpeg`: a trusted binary whose *inputs* are bounded. No network, no API
key, no credential to leak. The synthetic and mock providers add nothing.

Unchanged: no `exec`, no `eval`, no `shell=True`, fixed argument lists,
enforced timeouts, whole-process-tree kill.

## What Phase 4.5 adds - hardening verification and regression locks

Phase 4.5 adds no new trust boundary. It *verifies* the existing ones and
locks every previously discovered vulnerability behind a permanent test.

**Whole-package AST sweep.** `test_no_exec_or_eval_anywhere_in_the_package`
parses every module in `src/code2shorts` and fails on any `exec()`,
`eval()`, `os.system()` or `shell=True` **call node**. It uses the AST, not
text search - several modules legitimately discuss these constructs in
prose to explain why they are avoided, and three of these tests initially
failed as false positives against their own documentation before being
rewritten. Grepping source text is not a security control; parsing it is.

**Every external tool held to the same contract.** Parameterised over
`run_maven`, `JavaExecutor`, `ManimVideoRenderer`, `MediaComposer`,
`SyntheticTTSProvider`, `SapiTTSProvider` and `JavaSourceInstrumenter`:
fixed executable, fixed argument list, no `shell=True`, explicit timeout.

**Regression locks for every vulnerability found in earlier phases:**

| Phase | Vulnerability | Permanent guard |
|---|---|---|
| 1 | orphaned `java.exe` when only the direct child was killed | asserts `taskkill /T` + `killpg` in the kill path |
| 4.1 | bare `-shortest` silently truncated an 18.0s render to 5.2s | builds the real command and asserts `-shortest` absent, `apad` + explicit `-t` present |
| 4.1 | renderer picked Manim's `partial_movie_files` fragment by alphabetical luck | asserts the exclusion is still in place |
| 4.1 | relative paths resolved twice against subprocess cwd | asserts `.resolve()` in renderer, composer and TTS |
| 4.2 | AI could supply arbitrary renderer instructions | asserts the `VisualAction` enum still rejects out-of-vocabulary values |
| 4.2 | `String.valueOf(int[])` leaked an identity hash instead of contents | asserts `Arrays.toString` / `repr(int[])` in the trace runtime |
| 4.4 | SRT cue injection forged a cue with attacker-chosen timestamps | composes a document from a hostile payload and asserts exactly one cue |

**Secrets.** `Settings.gemini_api_key` must have **no default**, and the
Gemini provider's log lines are scanned to confirm the key is never
formatted into one. No credential appears in any committed file.

**Cleanup.** Temp workspaces are removed even when the body raises,
cleanup is idempotent, workspaces are mutually isolated, and `/output/`
remains gitignored so generated media is never committed.

**Failure loudness as a security property.** 24 failure-injection tests
assert the pipeline stops at the correct stage rather than emitting a
plausible-looking wrong artifact. A truncated or stretched final video is
named as such in the error; it is never silently repaired.

## What Phase 5 adds - a REAL model on the other side of the seam

Phases 3 and 4 defined the AI trust model against a mock provider that was
written to cooperate. Phase 5 is the first phase where an actual language
model - remote, free-tier, and not under our control - produces the
explanation, the visualization plan and the narration. The trust boundary
did not move; it was finally tested.

### The provider boundary

| Property | Control |
|---|---|
| Which backend is in use | One seam, `ai/providers/factory.py`. `KNOWN_PROVIDERS` is closed; an unknown name raises rather than defaulting to something. |
| A missing credential | Fails loudly. `LLM_PROVIDER=gemini` without a key raises `ProviderConfigurationError` - it never silently downgrades to a different provider. |
| A fresh checkout | Defaults to `mock`. Nothing silently requires a credential; the whole unit suite and the mock golden path run with none. |
| Credential exposure | The key is never hardcoded, never logged, never in `describe` (the provenance stored on artifacts), never in an exception message. Asserted by `test_api_key_never_appears_in_provenance`, `..._in_error_messages`, `test_no_provider_module_logs_the_key`. |
| Credential leakage into a prompt | Prompt builders are AST-tested to be unable to read `os.environ`, `getenv`, or any `api_key` attribute. A prompt sent to a third-party gateway cannot carry a secret. |
| Retry load | The provider is the single retry authority (`num_retries=0, max_retries=0` on the client library). Measured before the fix: `max_retries=2` produced NINE upstream requests. |

### What a hostile model cannot do

`tests/test_phase5_llm_security.py` attacks the layer above the renderer:
the response itself. Every attack is stopped by a validator, not by luck.

| Attack | What stops it |
|---|---|
| `"visual_action": "run_shell"` and 13 other executable-looking payloads | `VisualAction` is a closed `StrEnum`; Pydantic rejects the value before any project code inspects it |
| Smuggling a `manim_code` or `post_render_command` field | Extra fields are dropped at validation and never reach the renderer |
| Putting code in a field at all | There is no field shaped like `code`, `command`, `script`, `args`, `path` or `file`. A test fails the build if one appears. |
| Executable-looking narration or titles | Free text by design, but it reaches Manim only through `repr()` as an inert string literal. All 14 payloads compile to valid, inert Python. |
| Referencing a trace event that never happened | `validate_visualization_plan` rejects any index that is not a real `step_index`. Same for fabricated explanation events. |
| Inventing content for an empty trace | Rejected - there is nothing to ground it in |
| Prompt injection carried in the Java source | The injection IS quoted into the prompt as data, and obeying it still fails: the enum is closed. Tested explicitly. |
| A refusal, prose, HTML or `null` reply | Fails schema validation loudly; never becomes content |
| Spinning the repair loop forever | Bounded and proven by call count: `max_repair_attempts=3` makes exactly 4 calls, `=2` makes 3, `=0` makes 1. A schema-valid but always-lying model terminates identically. The loop raises; it never returns a best-effort object. |
| Reaching a subprocess | AST-tested: no module in `ai/` or `narration/` imports `subprocess` or calls `system`/`popen`/`spawn`/`exec*`/`fork`. FFmpeg arguments are built from measured media and fixed flags only - `narration_text` and `lesson_title` do not appear in `media/composer.py`. |

### What Phase 5 does NOT protect against

* **Prompt content is visible to whatever gateway is configured.** Prompts
  carry the algorithm source and its execution trace. If that source is
  sensitive, do not route it through a third-party gateway. No credential
  can be included (see above), but the code itself is sent.
* **A wrong-but-valid plan.** Validation proves a plan is *grounded* - every
  step points at a real trace event with consistent variables. It does not
  prove the plan is *pedagogically good*. A model can produce a
  well-grounded, boring, or badly paced video, and it will pass.
* **Free-tier gateway trustworthiness.** OmniRoute routes to third-party
  providers. Their handling of prompt data is outside this project's
  control and is not audited here.

## Configuration and secrets — local vs production

Phase 5 proved the *provider* boundary. This section covers the boundary
around the credential itself, added after a real key leaked into test
output on this machine.

### The environment switch

`CODE2SHORTS_ENV` decides which dotenv files may be read, and nothing else:

| Value | Files read |
|---|---|
| `local` (default) | `.env`, then `.env.local` |
| `test` / `ci` | none |
| `production` / `prod` | none |

Production reads **no dotenv file**. dotenv paths resolve against the
working directory, so a production process launched inside a developer
checkout would otherwise silently inherit `.env.local` and run on a
personal key. This is a structural fix, not a documented rule: there is no
file for it to find.

Verified by clean-environment subprocess dry run, executed *from inside the
repository while `.env.local` was present on disk*: the process reported
`dotenv files read: (none)` and every credential `MISSING`.

### Precedence

    process environment  >  env files  >  field defaults

In production only the first and last apply.

### Two real leaks, both fixed

1. **`.env.local` was not gitignored.** `.gitignore` covered `.env` only.
   A real `.env.local` existed with live keys; one `git add -A` would have
   published them to a public repository. Now `.env` + `.env.*`, with the
   two committed templates explicitly re-included.
2. **A real key was printed into pytest output.** A test asserted
   `Settings().gemini_api_key is None` — conflating "the class ships a
   default" with "this machine has no key" — and pytest renders the
   compared value in its diff. Two fixes: the test now inspects the field
   definition, and credential fields are `SecretStr`, so `repr`, `str`,
   `model_dump`, `model_dump_json` and tracebacks all render a mask.
   `config.reveal()` unwraps only at the three provider-factory call sites
   where the value goes on the wire.

### The unit suite was loading real credentials

pytest runs with the repository as its working directory, so `Settings()`
read `.env.local` — meaning results depended on which machine ran them,
and any test building a provider could have made a live billed call by
accident. `tests/conftest.py` now sets `CODE2SHORTS_ENV=test` and strips
credential variables for every non-`integration` test. Integration tests
are deliberately untouched: reaching a real provider is their purpose.

### Startup validation

`validate_configuration()` fails before Maven, the JVM, Manim or FFmpeg do
any work. Messages name the **variable**, never the value, so they are safe
to log:

    GEMINI_API_KEY is required when LLM_PROVIDER=gemini

`scripts/production_config_check.py` runs it as a deployment preflight.

### What is asserted by test

A canary credential must not appear in: `Settings` repr/str/`model_dump`/
`model_dump_json`, provider provenance (`describe`, which is stored on
artifacts), artifacts, workflow state, or exception messages. Prompt
builders are AST-checked to be unable to reach `os.environ` or any
`*_api_key` attribute, so a credential cannot travel to a third-party
gateway inside a prompt. No module may pass an `*_api_key` attribute to
`logger`, `logging` or `print`. Both committed templates are scanned for
real key shapes (`AIza…`, `sk-…`, `gsk_…`, `xai-…`) and every `*_API_KEY`
line in them must be empty or a `<PLACEHOLDER>`.

### What this does NOT protect against

* **A key already committed in history.** These controls stop new leaks;
  they cannot un-publish an old one. Rotation is the only remedy, and it is
  a human decision — nothing here rotates or revokes automatically.
* **A developer exporting a key into their shell.** Process environment
  beats everything by design, including in `test`. The conftest strips the
  names it knows; an unusual one would survive.
* **Prompt content reaching the configured provider.** No credential can be
  included, but the algorithm source and its trace are sent.

## What Phase 1 does NOT protect against

This is a development-grade boundary, not a production-grade security
sandbox. It does **not** provide:

- **Filesystem isolation** — generated code's `java`/`mvn` process runs
  with this machine's normal filesystem permissions. It can read/write
  anything the invoking user account can, outside the workspace directory
  too (e.g. nothing stops `java.io.File` from opening `C:\Users\...`).
- **Network isolation** — outbound network access is not blocked. Malicious
  generated code could make HTTP calls, exfiltrate data, etc. (Maven's own
  dependency resolution also needs the network today, which is a separate,
  trusted use of it — see below.)
- **CPU/memory/disk resource limits** — a process can consume unbounded
  CPU or memory until the wall-clock timeout fires; there's no cgroup/Job
  Object-based cap today.
- **Process privilege reduction** — the subprocess runs as the same OS
  user as the application; no sandboxed/low-privilege account, no seccomp
  filter, no syscall allowlist.
- **Kill-tree completeness guarantee** — the whole-tree kill above is a
  strong best-effort (`taskkill /T /F` / `killpg`), not a kernel-enforced
  guarantee. A process that detaches into its own session before the timer
  fires could theoretically escape it.

## Future hardening (not implemented — do not build ahead of need)

- **Docker isolation** — run `mvn`/`java` inside a locked-down container
  (no network by default, read-only root filesystem, dropped capabilities).
- **Resource limits** — cgroups (Linux) / Job Objects (Windows) capping
  CPU, memory, and process count per run.
- **Network isolation** — deny outbound network from the execution
  container entirely, except an explicit allowlist if ever needed.
- **Read-only filesystem** — mount the workspace as the only writable
  path; everything else read-only or absent.
- **seccomp** — restrict the syscall surface available to the sandboxed
  process (Linux-only).

## A note on Maven's network use

`mvn compile`/`mvn test` may need network access to resolve dependencies
(JUnit, compiler/surefire plugins) from Maven Central on a machine whose
local `~/.m2` cache doesn't already have them pinned. That network access
is Maven resolving *our own, trusted* `pom.xml` dependencies — not
generated code doing arbitrary networking — and is out of scope for the
untrusted-code boundary described above. Operationally: a CI or production
environment should pre-warm `~/.m2` or run an internal Maven mirror so
builds don't depend on live internet access at generation time.

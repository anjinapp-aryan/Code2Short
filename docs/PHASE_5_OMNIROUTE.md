# Independent evaluation — OmniRoute

Requested as a Phase 5 gate item: evaluate
<https://github.com/diegosouzapw/OmniRoute> on evidence, not README claims,
and determine whether Code2Shorts should depend on it.

**Verdict: adopted as OPTIONAL infrastructure. It is not a dependency, and
by construction it cannot become one.**

---

## 1. What it claims to be

An LLM routing gateway: install via npm, run locally, point any
OpenAI-compatible client at `http://localhost:20128/v1`, and it routes
requests across providers — including keyless free ones.

## 2. What was verified locally

Everything below was executed on this machine; nothing is taken from the
README.

| Claim | How it was checked | Result |
|---|---|---|
| Installs from npm | `npm i -g omniroute` | **Confirmed** — `omniroute@3.8.49`, 1190 packages, ~1 min |
| MIT licensed | `npm view omniroute license` and the installed `package.json` | **Confirmed — MIT.** (A `license: null` field is what failed tracers.java in Phase 4.2A, so this was checked directly, not assumed.) |
| Runs locally | started the process; polled the port | **Confirmed** — HTTP 200 on `:20128`, WebSocket on `:20131`/`:20132` |
| Exposes an OpenAI-compatible `/v1` | `GET /v1/models` | **Confirmed** — returns a model catalogue (e.g. `auto/best-coding`, 1048576-token context) |
| Serves completions | `POST /v1/chat/completions` | **Confirmed** — real completion returned |
| Works with **no credentials** | ran with no API key configured | **Confirmed** — a keyless free provider served the request (model `big-pickle`) |
| Works through Code2Shorts' own provider | `OpenAICompatibleProvider.complete("ping")` | **Confirmed** — `'pong'` in **7.2 s** |
| Usable for structured output | real `ExplanationResponse` request | **Confirmed** — schema-valid response in **30.1 s**, 2 steps, referencing trace events `[0, 1]` |
| Drives the full pipeline | `scripts/run_phase5_golden_path.py --provider omniroute` | **Confirmed** — real MP4, media validation PASS |

## 3. Findings the README does not tell you

These came out of actually running it:

1. **It returns streaming SSE even when `stream: true` was not requested.**
   Handled transparently by litellm; a hand-rolled `httpx` client would
   have broken here. Worth knowing before writing one.
2. **Cold start is ~55 s.** Measured: `started in 54.7s`. A naive health
   check right after launch reports the gateway as down. Poll, don't assume.
3. **The process does not survive indefinitely.** It died between two
   golden-path runs in this session and had to be restarted — the first
   symptom was a provider `Timeout` classified transient and retried,
   which is exactly the intended behaviour but looks like a model problem.
   Treat it as a service to supervise, not fire-and-forget.
4. **Free-tier latency is far above the 30 s default timeout.** A
   full-trace explanation prompt routinely exceeds it; measured LLM stage
   totals of 277–486 s across three nodes. `Settings.ai_timeout_seconds`
   must be raised for free routing, which is why the golden path exposes
   `--timeout`.
5. **The npm install is not robust to interruption.** A killed install left
   a locked directory that `npm ls` reported as `omniroute@` with no
   version and no files. Recovery: kill the node processes, remove the
   stale directory, reinstall.

## 4. Why it cannot become a mandatory dependency

This is a structural property, not a policy promise:

* OmniRoute speaks the **standard OpenAI wire format**. Code2Shorts
  therefore needs nothing OmniRoute-specific — only a `base_url`.
* There is **no `OmniRouteProvider` class**. There is one
  `OpenAICompatibleProvider`, and OmniRoute is one possible value of one
  constructor argument.
* `pyproject.toml` gains **nothing**. No Python package, no npm package, no
  optional extra. Nothing imports it.
* The only OmniRoute-specific value in the codebase is a **default URL
  constant in `ai/providers/factory.py`**, the single provider-selection
  seam. `tests/test_llm_providers.py::test_provider_selection_lives_only_in_the_factory`
  fails the build if such a value appears anywhere else.
* `LLM_PROVIDER` defaults to **`mock`**, so a checkout with no gateway and
  no credentials runs the full unit suite and the mock golden path.
* Direct Gemini remains a first-class path
  (`test_gemini_remains_supported`), unchanged by Phase 5.

Swapping to Ollama is `--base-url http://localhost:11434/v1`. Swapping to
OpenAI is a different URL and a key. No code changes in any case.

## 5. Risks accepted

| Risk | Mitigation |
|---|---|
| Free-tier models are slow and variable | Configurable timeout; provider retry classifies timeouts as transient |
| Free-tier model quality is low (see the plan-validation failures in `PHASE_5_LLM_ARCHITECTURE.md`) | Every proposal is validated against the real trace; bounded repair; nothing invalid becomes an artifact |
| Third-party gateway sees prompt content | Prompts contain only the algorithm source and its execution trace. Prompt builders are AST-tested to be unable to read `Settings` or `os.environ`, so no credential can be included. |
| The gateway process stops | Provider raises a classified transient error and fails loudly; nothing silently degrades |

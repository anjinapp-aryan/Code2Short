# Phase 5 Reuse Audit — LLM-driven content generation & multi-provider routing

Scope: only what Phase 5 actually needs — multi-provider LLM routing,
structured output, and bounded repair. Following the standing rule
(`CLAUDE.md`, "Conventions"), no dependency is added without an audit
first, and the bar set by Phases 4.2–4.5 is **zero new production
dependencies**.

**Result: zero new production dependencies. Zero new dev dependencies.**

---

## 1. What Phase 5 needs

| Need | Candidate approaches |
|---|---|
| Reach many LLM backends without per-vendor code | LiteLLM · OpenRouter SDK · LangChain · OmniRoute client · plain `httpx` |
| Structured output as validated objects | Instructor · Outlines · LangChain parsers · Pydantic + a parse seam |
| Bounded repair on invalid output | Instructor's retry · LangChain `OutputFixingParser` · existing `ai/repair.py` |
| Local free routing gateway | OmniRoute (see `PHASE_5_OMNIROUTE.md`) |

## 2. Candidates evaluated

| Project | License (verified) | Verdict | Reason |
|---|---|---|---|
| **litellm** | MIT | **REUSE — already present** | Already a dependency since Phase 0 and already used by `GeminiLLMProvider`. Speaks the OpenAI wire format to ~100 providers, and accepts a `base_url`, which is the entire multi-provider requirement. Adopting it costs nothing. |
| **pydantic** | MIT | **REUSE — already present** | Already the contract layer. `model_json_schema()` gives the prompt-side schema instruction for free, derived from the same model that validates the response. |
| **Instructor** | MIT | **REJECT** | Would add a dependency to obtain (a) schema-in-prompt and (b) a retry loop. We already have (b) — `ai/repair.py`, bounded and tested — and (a) is `model_json_schema()`, ~10 lines. Instructor also owns the retry policy, which would blur the provider-retry / workflow-repair separation this phase is required to keep distinct. |
| **Outlines** | Apache-2.0 | **REJECT** | Constrained decoding needs logit access. Meaningless through a hosted OpenAI-compatible gateway, which is the deployment target. |
| **LangChain / LangGraph** | MIT | **REJECT** | Explicitly excluded by the Phase 3 gate ("do NOT make LangGraph the foundation"). Its `OutputFixingParser` also asks a *second* LLM to fix the first one's output — more nondeterminism to fix nondeterminism, and the fix is never validated against the trace. |
| **openai** (official SDK) | Apache-2.0 | **REJECT** | Present transitively under litellm. Using it directly would mean a second HTTP client and a second retry policy alongside litellm's — exactly the amplification defect measured in §4. |
| **OpenRouter SDK** | Apache-2.0 | **REJECT** | Vendor-specific client for one gateway. OpenRouter already exposes an OpenAI-compatible `/v1`, so it is reachable as a `base_url` with no code at all. |
| **omniroute** (npm) | MIT | **ADOPT AS OPTIONAL INFRASTRUCTURE — not a dependency** | A standalone Node process exposing an OpenAI-compatible `/v1`. Nothing in `pyproject.toml` references it; nothing imports it; the whole integration is a URL. Full evaluation in `PHASE_5_OMNIROUTE.md`. |
| **httpx** direct | BSD-3 | **REJECT** | Would mean hand-writing request shaping, error taxonomy and retry that litellm already provides and that Phase 4 already exercised. |

## 3. What was built instead, and why it is small

| Built | Lines | Why not a library |
|---|---|---|
| `ai/providers/openai_compatible.py` | ~120 | The whole multi-provider story is "pass a `base_url`". A library to do that would be larger than the code. |
| `ai/providers/factory.py` | ~60 | The ONE place provider selection exists. Deliberately ours: it is a security boundary (`KNOWN_PROVIDERS` is closed, missing credentials fail loudly). |
| `ai/structured.py::extract_json` | ~25 | Purely lexical fence/preamble stripping. Instructor would be a dependency for this one function. |
| `ai/structured.py::json_schema_instruction` | ~15 | `model_json_schema()` plus a sentence. |
| `narration/fitting.py` | ~70 | Widen-only timeline fitting. No library models this; it encodes a project-specific rule (never truncate). |

## 4. Measured findings that justified writing rather than adopting

* **Retry amplification.** With litellm's defaults, `max_retries=2` produced
  **nine** upstream HTTP requests — litellm retries three times and the
  underlying OpenAI client retries again, both multiplying with ours.
  Against the rate-limited free tier this phase targets, that silently
  triples load while the configured number says three. Fixed by pinning
  `num_retries=0, max_retries=0` and making our class the single retry
  authority. A library that owns its own retry loop would have hidden this.
* **Two divergent parse paths.** `generate_structured` and `repair.py`
  parsed the model's reply differently, so a correct-but-fenced response
  succeeded in one and burned a repair attempt in the other. Found because
  both are ours and both are readable.

## 5. Dependency delta

```
production: +0
dev:        +0
```

`pyproject.toml` is unchanged by Phase 5.

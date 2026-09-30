# Provider configuration — local, test, and production

How Code2Shorts is configured, where secrets live, and why production
cannot accidentally run on a developer's key.

**No new dependencies.** This is the existing `pydantic-settings` loader
with an environment switch, an alias set, and a startup validator.

---

## 1. The three environments

`CODE2SHORTS_ENV` controls **only** which files may be read:

| `CODE2SHORTS_ENV` | Files read | Used for |
|---|---|---|
| `local` (default) | `.env`, then `.env.local` | developer machine |
| `test` / `ci` | none | pytest — deterministic |
| `production` / `prod` | none | deployment — runtime injection only |

Production reads **no dotenv file at all**. This is the central decision.
dotenv paths resolve against the *working directory*, so a production
process started inside a developer checkout would otherwise silently
inherit `.env.local` and run on a personal key. Making production
file-free removes the possibility instead of documenting a rule nobody
can enforce.

## 2. File responsibilities

| File | Committed | Real secrets | Purpose |
|---|---|---|---|
| `.env.example` | **yes** | never | developer contract, placeholders only |
| `.env.production.example` | **yes** | never | production contract, placeholders only |
| `.env.local` | **no** | yes | a developer's own keys |
| `.env`, `.env.production` | **no** | possibly | ignored; prefer runtime injection |

`.gitignore` uses `.env` + `.env.*` with `!.env.example` and
`!.env.production.example` re-included, so a new variant like
`.env.staging.local` is ignored automatically while the two templates stay
committable. Both properties are asserted by tests.

> `.env.local` is **developer-only**. It is never copied to a server, never
> packaged, never referenced by deployment, and never quoted in docs.

## 3. Precedence

Verified in a real subprocess, not assumed:

```
process environment   >   env files   >   field defaults
```

In production only the first and last apply. A production process starts
with nothing but injected variables:

```bash
CODE2SHORTS_ENV=production LLM_PROVIDER=gemini GEMINI_API_KEY=... python -m code2shorts ...
```

## 4. Variable contract

| Variable | Required when | Notes |
|---|---|---|
| `CODE2SHORTS_ENV` | production | disables dotenv loading |
| `LLM_PROVIDER` | always (defaults `mock`) | `mock` · `gemini` · `xai` · `omniroute` · `openai_compatible` |
| `GEMINI_API_KEY` | `LLM_PROVIDER=gemini` | |
| `GEMINI_MODEL` | optional | see §7 — Google retires ids |
| `XAI_API_KEY` | `LLM_PROVIDER=xai` | keys begin `xai-` |
| `GROK_MODEL` | optional | default `grok-4.1-fast` |
| `OMNIROUTE_BASE_URL` | `openai_compatible` | alias of `CODE2SHORTS_LLM_BASE_URL` |
| `OMNIROUTE_API_KEY` | if the gateway needs one | alias of `CODE2SHORTS_LLM_API_KEY` |
| `OMNIROUTE_MODEL` | optional | alias of `CODE2SHORTS_LLM_MODEL` |
| `LLM_FALLBACK_PROVIDER` | optional | **declares intent only** — see §9 |

Every variable also accepts a `CODE2SHORTS_`-prefixed form, which wins.
The unprefixed vendor names exist because that is what people already have
in a `.env.local` and what other tooling sets.

> **Gotcha, observed:** a `.env.local` that still contains
> `CODE2SHORTS_LLM_PROVIDER=mock` copied from `.env.example` will silently
> override a later `LLM_PROVIDER=gemini`, because the prefixed form wins.
> Keep one or the other, not both.

## 5. Startup validation

`config.validate_configuration()` fails immediately rather than at the
first LLM call — otherwise a deployment missing `GEMINI_API_KEY` starts
happily, runs Maven, compiles, executes and traces the algorithm, and only
then discovers it cannot reach a model.

```
GEMINI_API_KEY is required when LLM_PROVIDER=gemini
XAI_API_KEY is required when LLM_PROVIDER=xai
GEMINI_API_KEY is required when LLM_FALLBACK_PROVIDER=gemini
CODE2SHORTS_LLM_BASE_URL is required when LLM_PROVIDER=openai_compatible
```

Messages name the **variable**, never the value, so they are safe to log.

Run it as a deployment preflight or container healthcheck:

```bash
python scripts/production_config_check.py     # exit 0 = usable
```

## 6. Secrets never leak

* Credential fields are `SecretStr`. `repr`, `str`, `model_dump`,
  `model_dump_json` and tracebacks all render a mask. `config.reveal()`
  unwraps only at the three call sites in the provider factory, where the
  value goes on the wire.
* This exists because a real key **did** leak: a test asserted
  `Settings().gemini_api_key is None`, and pytest rendered the compared
  value into the output. That test now inspects the field definition.
* Tests assert a key cannot appear in provenance (`describe`), artifacts,
  workflow state, exceptions, logs, or prompts. Prompt builders are
  AST-checked to be unable to reach `os.environ` or any `*_api_key`.
* Both committed templates are scanned for real key shapes
  (`AIza…`, `sk-…`, `gsk_…`, `xai-…`), and every `*_API_KEY` line in them
  must be empty or a `<PLACEHOLDER>`.

## 7. Gemini

Verified working end to end. **Google retires model ids**: `gemini-2.5-flash`
now returns

```
404 — This model models/gemini-2.5-flash is no longer available to new
users. Please update your code to use models/gemini-3.6-flash
```

Check what your key actually serves rather than assuming:
`GET https://generativelanguage.googleapis.com/v1beta/models`.

## 8. xAI / Grok

Supported through `OpenAICompatibleProvider` with
`base_url=https://api.x.ai/v1` — **one constant and no new class**, because
api.x.ai speaks the OpenAI wire format. This is ADR-5.1 paying off.

Unlike a keyless local gateway, a missing key fails loudly at
configuration time rather than reaching the wire as the `not-needed`
placeholder and returning a confusing 401.

> **NOT VERIFIED — the available credential is not an xAI credential.**
> It begins `gsk_`, which is a **Groq** prefix; xAI keys begin `xai-`.
> api.x.ai rejects it with *"Incorrect API key provided"* on `/v1/models`
> and on every real model (`grok-4-fast`, `grok-3`); api.groq.com returns
> 403. The implementation is correct and was **not** modified to accept it.
> Supply a genuine `xai-` key and the integration test runs for real with
> no code change.

`config.classify_credential()` names the vendor a credential looks like
from its prefix, so this mismatch is reported plainly rather than as a
vague auth failure:

```
XAI_API_KEY  set (looks like: groq)  <-- WARNING: looks like a groq key, expected xai
```

It is **advisory only** and never enforced by the factory — prefixes are
vendor conventions, not guarantees, and hard-rejecting on one would break
the day a vendor changes format. The API remains the authority. It returns
a label, never a value.

### Groq is not supported (deferred)

The supplied credential appears to be a **Groq** key. Groq is *not* added
in this phase — scope is deliberately not expanded. The current
architecture supports Gemini, OmniRoute and xAI/Grok.

Worth noting for later: Groq exposes an OpenAI-compatible endpoint at
`https://api.groq.com/openai/v1`, so supporting it would cost **one URL
constant and no new provider class**, exactly like xAI. It is a future
option, not a commitment, and requires an explicit request.

## 9. Fallback — automatic, but only when you ask for it (Phase 6.5.4)

`LLM_FALLBACK_PROVIDER` is still **declaration only**: setting it does not
change which provider is built. A config key must never silently alter the
`producer` recorded on an artifact.

Automatic failover is a separate, explicit choice:

```
CODE2SHORTS_LLM_PROVIDER=failover
```

That builds an ordered chain and nothing else does.

### Order

```
CODE2SHORTS_LLM_PROVIDER_ORDER=omniroute,openrouter,gemini   # the default
```

    OmniRoute   PRIMARY
        |  transient failure
        v
    OpenRouter  FALLBACK
        |  transient failure
        v
    Gemini      FALLBACK
        |
        v
    answer

Deterministic: read once, never shuffled, never load balanced, never
reordered by latency. A provider with no credential is **skipped**, not
attempted — so a machine with only a Gemini key gets a one-provider chain
rather than a startup failure. A chain with *no* configured member fails
immediately, at startup, naming the variables to set.

### When it falls through, and when it does not

| Outcome | Behaviour |
|---|---|
| 429, 500, 502, 503, 504 | **fall through** to the next provider |
| 408, 425, connection timeout / reset, endpoint not listening | **fall through** |
| 400, 401, 403, 404, 405, 409, 413, 422 | **stop** — report the real error |
| invalid model, malformed request, invalid schema | **stop** |

A wrong API key is not a transient outage. Falling through on it would turn
one clear "your credential is invalid" into three vague failures at three
vendors and leave the real cause unreported.

### Bounded

Each provider is attempted **once**; the chain is the retry. Members are
built with `max_retries=0`, because three providers each retrying three
times would be nine requests against exactly the rate-limited free tiers
this feature exists to route around. There is no loop back to an earlier
provider, no recursion, and no retry of the chain itself.

### Lineage

`provider.describe` reports the provider that **answered**, plus
`fallback_from` when it was not the primary:

```json
{"provider": "openrouter", "chain": "omniroute,openrouter,gemini",
 "fallback_from": "omniroute"}
```

A fallback run therefore can never read as a clean primary run — which was
ADR-5.10's condition for allowing failover at all. See ADR-6.9.

### Credentials

```
CODE2SHORTS_LLM_API_KEY=...            # OmniRoute  (or OMNI_ROUTE_LLM_API_KEY)
CODE2SHORTS_LLM_BASE_URL=...           # OmniRoute  (defaults to localhost:20128/v1)
CODE2SHORTS_OPENROUTER_API_KEY=...     # OpenRouter (or OPENROUTER_API_KEY)
CODE2SHORTS_OPENROUTER_MODEL=...       # defaults to a free-tier model
CODE2SHORTS_GEMINI_API_KEY=...         # Gemini     (or GEMINI_API_KEY)
CODE2SHORTS_GEMINI_MODEL=...           # defaults to gemini-1.5-flash
```

OpenRouter has **no provider class**: `https://openrouter.ai/api/v1` is
OpenAI-compatible, so it cost one URL constant and one key (ADR-5.1).
Keys are `SecretStr`, unwrapped only at the wire, and never logged — the
chain logs provider names, status classes and prompt *lengths* only.

## 10. Provider architecture is unchanged

```
                    build_llm_provider(settings)      <- the ONE seam
                                |
        +---------------+-------+-------+---------------+
        |               |               |               |
  MockLLMProvider  GeminiLLM      OpenAICompatibleProvider
  (default)        Provider        base_url = anything
                                        |
                        +-------+-------+--------+
                     OmniRoute  xAI   Ollama   OpenAI
```

No `GrokProvider`, no `OmniRouteProvider`. Provider selection exists only
in `ai/providers/factory.py`; an AST test fails the build if a
provider-specific value appears outside it. Nothing in `workflow/`,
`visualization/`, `narration/`, `media/` or the domain models learns which
backend is in use.

## 11. OmniRoute

Unchanged and still optional infrastructure, not a dependency — nothing
imports it, `pyproject.toml` references nothing. It remains a `base_url`,
now also reachable via the `OMNIROUTE_*` aliases.

## 12. Testing workflow

| Command | Credentials | Network |
|---|---|---|
| `pytest -m "not integration"` | none | none |
| `pytest` | none required | integration tests skip loudly |
| `pytest -m integration` | opt-in, from `.env.local` | yes |
| `python scripts/production_config_check.py` | none | none |

`tests/conftest.py` sets `CODE2SHORTS_ENV=test` for every non-integration
test and strips credential variables. Before this existed, **the unit
suite loaded a developer's real keys** — pytest runs with the repo as its
working directory, so `.env.local` was in scope. That made results depend
on the machine, and it is how the key reached test output.

Integration tests are untouched by the fixture: reaching a real provider is
their entire purpose. When they skip, the reason states that they did
**not** pass.

## 13. Known gaps

* **Grok is unverified** — invalid credential (§8).
* **Automatic failover is opt-in** — `LLM_PROVIDER=failover` (§9);
  `LLM_FALLBACK_PROVIDER` still declares intent only, by design.
* **No Docker or CI configuration exists** in this repository, so the
  production model is verified by clean-environment subprocess dry runs
  rather than in a container or pipeline.

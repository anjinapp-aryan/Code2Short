# PRODUCTION-SAFE LLM CONFIGURATION REPORT

## 1. Architecture audit

Inspected before changing anything: `.env*`, `.gitignore`, `config.py`,
`ai/providers/` (factory, gemini, openai_compatible, mock), `LLMProvider`,
tests, scripts, README, CLAUDE.md, SECURITY_SANDBOX.md, `.github/`.

Findings, in severity order:

| # | Finding | Severity |
|---|---|---|
| 1 | **The unit suite loaded real credentials.** pytest runs with the repo as CWD, so `Settings()` read `.env.local`. Results depended on the machine, and any test building a provider could make a live billed call. | **High** |
| 2 | **Production could inherit `.env.local`.** dotenv paths resolve against CWD, so a production process started inside a checkout would load a developer's keys. | **High** |
| 3 | No startup validation — a missing key surfaced only at the first LLM call, after Maven/JVM/trace work. | Medium |
| 4 | No production configuration contract existed (`.env.production.example` absent). | Medium |
| 5 | Deployment artefacts: **no Dockerfile, no compose, no CI workflow** exist. `.github/` holds only unrelated java-upgrade hooks. | Informational |

Already correct and preserved: precedence (process env beats files),
`SecretStr` credentials with `reveal()` at the wire, `.env.*` gitignored
with `!.env.example`, one `LLMProvider`, factory as the only selection
seam.

## 2. Reuse audit

**Zero new dependencies.** Everything is the existing `pydantic-settings`
loader plus ~90 lines: an environment switch, alias sets, and a validator.

Rejected: `python-dotenv` (pydantic-settings already wraps it),
`dynaconf`/`hydra` (configuration frameworks — the project needs one class),
any secret-management SDK (production injects env vars; an SDK would bind
the app to one cloud and add a dependency for something the platform
already does).

## 3. Environment loading behaviour

`CODE2SHORTS_ENV` controls **only** which files may be read:

| Value | Files read |
|---|---|
| `local` (default) | `.env`, then `.env.local` |
| `test` / `ci` | none |
| `production` / `prod` | none |

Resolved per instantiation, not at import, so a process or test that sets
the variable is honoured without reimporting.

## 4. Configuration file strategy

| File | Committed | Real secrets | Role |
|---|---|---|---|
| `.env.example` | yes | never | developer contract |
| `.env.production.example` | yes | never | production contract |
| `.env.local` | no | yes | developer keys |
| `.env`, `.env.production` | no | possibly | ignored; injection preferred |

## 5–7. The three templates

`.env.example` rewritten: `LLM_PROVIDER=mock` default, every variable
commented, all credential values empty. `.env.production.example` created:
`CODE2SHORTS_ENV=production`, credentials as
`<INJECT_FROM_SECRET_MANAGER>`. `.env.local` **not renamed, not copied, not
committed**; no key was read, printed, moved, rotated or revoked.

## 8. Production secret injection

Production receives real values as process environment variables from the
platform's secret store. It reads no file:

```bash
CODE2SHORTS_ENV=production LLM_PROVIDER=gemini GEMINI_API_KEY=... <command>
```

## 9. Environment precedence

```
process environment  >  env files  >  field defaults
```

Verified in real subprocesses, not assumed. In production only the first
and last apply.

## 10. Git / security audit

* `git ls-files | grep .env` → **only `.env.example`**. No secret-bearing
  file has ever been tracked; nothing needed history rewriting.
* Tracked-tree scan for `AIza…`, `sk-…`, `gsk_…`, `xai-…`, `AQ.…` →
  **clean** (only a declared test canary).
* Ignore matrix verified: `.env`, `.env.local`, `.env.production`,
  `.env.staging.local` ignored; both templates committable.

## 11. Gemini verification — PASS

Real request `pong` in 18.5 s · structured `ExplanationResponse` in 8.1 s,
4 steps · grounding **PASS** in 140.6 s, **3 calls, 2 repairs** (bounded),
citations `[0,1,2,3]` all real · key absent from provenance.

## 12. Grok verification — **FAIL (invalid credential)**

The `xai` provider builds correctly and reaches
`https://api.x.ai/v1` with the right request shape — the failure is an API
response, not a connection or code error.

The supplied `XAI_API_KEY` begins `gsk_`, which is a **Groq** prefix; xAI
keys begin `xai-`. Evidence:

| Target | Result |
|---|---|
| `api.x.ai/v1/models` | 400 `Incorrect API key provided` |
| `api.x.ai` `grok-4-fast`, `grok-3` | 400 `Incorrect API key provided` |
| `api.groq.com/openai/v1/models` | 403 |

A valid `xai-` key closes this out with no code change.

## 13. OmniRoute verification — PASS

Regression run against a live gateway: **4 passed, 1 skipped** (skip was
free-tier upstream exhaustion, stated loudly). Unchanged and still optional
infrastructure — nothing imports it, `pyproject.toml` untouched. Now also
reachable via `OMNIROUTE_BASE_URL` / `_API_KEY` / `_MODEL` aliases.

## 14. Provider factory verification

Still the only selection seam. `xai` cost **one URL constant and no new
class**, because api.x.ai speaks the OpenAI wire format — ADR-5.1 paying
off. No `GrokProvider`, no `OmniRouteProvider`. The AST leak test was
extended to `api.x.ai` and passes: no provider-specific value appears in
`workflow/`, `visualization/`, `narration/`, `media/` or domain models.

## 15. Fallback behaviour

`LLM_FALLBACK_PROVIDER` is read and **validated** — declaring a fallback
whose credential is absent fails at startup. **No automatic runtime
failover is implemented**, deliberately: switching provider mid-run would
change the `producer` recorded on an artifact while its lineage claims
otherwise, and would mask a failing primary. That is a design decision to
take explicitly, not a side effect of reading a config key.

## 16. Retry behaviour

Unchanged and still bounded: provider retry (transient only) is separate
from workflow repair (invalid content). Worst case
`(1+max_retries)×(1+max_repair_attempts)` = 9 calls per node. Observed
Gemini grounding: 3 calls, 2 repairs, terminated with a valid plan.

## 17. Secret leakage tests

36 tests in `tests/test_production_configuration.py`. A canary credential
must not appear in `Settings` repr/str/`model_dump`/`model_dump_json`,
provider provenance, artifacts, workflow state, or exception messages.
AST checks: prompt builders cannot reach `os.environ` or any `*_api_key`;
no module passes an `*_api_key` to `logger`/`logging`/`print`. Both
templates are scanned for real key shapes and every `*_API_KEY` line must
be empty or a `<PLACEHOLDER>`.

## 18. Test counts

| Run | Result |
|---|---|
| `pytest -m "not integration"` | **419 passed**, 0 failed |
| `pytest` (full) | **498 passed, 4 skipped**, 0 failed, 355 s |
| `tests/test_production_configuration.py` | 36 passed |
| Real-provider integration | 5 total: Gemini **passed**; 4 gateway tests skipped (gateway stopped) |
| Collected | 502 |
| Production dry run | **7/7 scenarios as expected** |

No failure is hidden behind a broad skip: every skip names the exact
missing resource and states it did **not** pass.

## 19. Reverse String — PASS (real Gemini)

25 trace events, `HELLO`→`OLLEH`; 1 repair consumed; fitting widened 25
steps, overflow **0**; 1080×1920 h264 **109.09 s**, aac 22050 Hz matching;
media validation **PASS**, timeline validation **PASS**.

Frame inspected at 60 s: title *"Reversing a String using Two Pointers"*,
state `left = 1, right = 3`, array `O E L L H` (first swap applied), code
panel boxing **line 15 `right--;`** — the exact executing statement.

## 20. Move Zeroes — PASS (pipeline), real-LLM run blocked

Post-config run: 67 trace events, `''`→`1,3,12,0,0` correct, validation
**PASS**, 1080×1920 h264 20.10 s, media and timeline **PASS**.

**Honest qualification:** this post-config run used the **mock** provider.
The real-LLM Move Zeroes run was attempted twice and blocked by
infrastructure, not by code — Gemini's free tier is **20 requests/day**
(exhausted by verification), and the OmniRoute fallback attempt stalled
~35 min with the gateway returning 400s from exhausted upstreams, so it was
stopped. A **real-LLM Move Zeroes run did pass earlier in this session**
(OmniRoute, 67 steps, 384.02 s, media+timeline PASS, frame inspected) on
the same code, before today's configuration commit.

## 21. Performance

| Stage | Gemini (Reverse String) |
|---|---|
| explain | 18.95 s |
| visualization_plan | 86.08 s |
| narration | 8.22 s |
| LLM total | 116.59 s |
| Manim | 126.76 s |
| TTS / FFmpeg | 6.17 s / 1.09 s |

Gemini is ~4× faster than free-tier gateway routing (113–117 s vs
289–570 s). Configuration changes added no measurable overhead.

## 22. Known limitations

* **Grok unverified** — the supplied credential is not an xAI key (§12).
* **Real-LLM Move Zeroes not re-run post-config** — free-tier quota and
  gateway exhaustion (§20).
* **Gemini free tier is 20 requests/day per model**, which a single
  golden path can consume.
* **No automatic failover** (§15).
* **No Docker/CI exists**, so production behaviour is verified by
  clean-environment subprocess dry runs rather than in a container.
* **A shell-exported credential still wins in `test`** — precedence is
  by design; the conftest strips the names it knows.
* `gemini-2.5-flash` is retired for new users; `gemini-3.6-flash` works.

## 23. Technical debt

1. Runtime failover, if wanted, needs explicit provenance recording.
2. `GeminiLLMProvider.describe` is thinner than the OpenAI-compatible one
   (`{'provider': 'gemini'}` — no model), so artifact provenance records
   `gemini:default` when the golden path is run without `--model`.
3. No container or pipeline to assert the production model in CI.
4. Free-tier providers make integration runs slow and flaky; a small billed
   key would make this deterministic.

## 24. Recommended next phase

**Phase 5.1 — content planning**, as already scheduled, plus first:
obtain a valid `xai-` key to close §12, and decide the failover question
in §15 deliberately. The strongest content lever remains constraining plan
length: the model proposes one step per trace event, producing 109–384 s
videos where the short format wants under 60 s.

---

**PHASE GATE: FAIL**

Everything in the configuration objective is implemented and verified —
local/test/production separation, precedence, gitignore, startup
validation, secret-leakage tests, production dry run, Gemini, OmniRoute,
mock, 498 passing tests, nothing committed. The gate fails on one
explicitly required criterion that was **not** actually tested:

> `[ ] Grok works with real credentials`

The available `XAI_API_KEY` is a Groq-format key that xAI rejects. The code
path is implemented and correct; the credential is not. Supply a valid
`xai-` key and this flips to PASS with no code change. Claiming PASS here
would misreport an untested provider as verified.

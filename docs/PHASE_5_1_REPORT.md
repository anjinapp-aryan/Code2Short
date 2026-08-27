# PHASE 5.1 DELIVERABLE REPORT

Closing the Phase 5 configuration/verification gate with the smallest
architecture-preserving changes.

## 1. Files changed

| File | Change |
|---|---|
| `src/code2shorts/config.py` | added `classify_credential()` + `CREDENTIAL_PREFIXES` (advisory vendor detection) |
| `scripts/production_config_check.py` | preflight now reports the vendor a credential looks like and warns on a mismatch |
| `tests/test_llm_providers_integration.py` | real xAI test added; loud, explicit NOT-VERIFIED skip |
| `tests/test_production_configuration.py` | +19 regression locks (classification, provider architecture, provider identity) |
| `tests/test_llm_providers.py` | AST leak test extended to `api.x.ai` |
| `ARCHITECTURE_DECISIONS.md` | ADR-5.8, ADR-5.9, ADR-5.10 |
| `docs/PHASE_5_PROVIDER_CONFIGURATION.md` | xAI NOT-VERIFIED statement; Groq deferred |
| `.env.example`, `.env.production.example`, `.gitignore`, `README.md`, `SECURITY_SANDBOX.md`, `CLAUDE.md` | documentation of the above |

## 2. Files added

`.env.production.example`, `tests/conftest.py`,
`tests/test_production_configuration.py`,
`scripts/production_config_check.py`,
`docs/PHASE_5_PROVIDER_CONFIGURATION.md`,
`docs/PHASE_5_PROVIDER_CONFIGURATION_REPORT.md`, this report.

**No new dependencies.**

## 3. Configuration architecture — VERIFIED STRUCTURALLY

`CODE2SHORTS_ENV` selects which dotenv files may be read, and nothing else:

| Value | Files read |
|---|---|
| `local` (default) | `.env`, then `.env.local` |
| `test` / `ci` | none |
| `production` / `prod` | none |

Not documentation — proven by execution. The production dry run was
deliberately started **from inside the repository with `.env.local` present
on disk**, and reported:

```
environment      : production
dotenv files read: (none — runtime injection only)
  GEMINI_API_KEY         MISSING
  XAI_API_KEY            MISSING
  LLM/OMNIROUTE_API_KEY  MISSING
```

7/7 scenarios behaved as specified: production+gemini with an injected key
passes; without it fails naming `GEMINI_API_KEY`; xai without a key fails
naming `XAI_API_KEY`; mock passes with no credential; a fallback without
its credential fails; `openai_compatible` without a base URL fails; local
retains `.env.local` behaviour.

Precedence, verified in a real subprocess:
`process environment > env files > field defaults`.

## 4. Provider architecture — UNCHANGED, no regression

```
LLMProvider
   ├── GeminiLLMProvider          (direct)
   └── OpenAICompatibleProvider(base_url)
            ├── OmniRoute   http://localhost:20128/v1
            └── xAI / Grok  https://api.x.ai/v1
```

Locked by tests: the provider package contains exactly
`__init__.py, factory.py, gemini.py, mock.py, openai_compatible.py`;
`GrokProvider`, `XAIProvider`, `OmniRouteProvider` must not exist; each
backend differs only by `base_url`; `build_llm_provider` is never called
outside the factory seam (AST sweep). Default remains `mock`.

## 5. Reuse audit (focused)

Scope limited to provider configuration, OpenAI-compatible APIs, Gemini,
xAI, credential loading, environment isolation, retry.

Verified the OpenAI-compatible protocol assumption for xAI **before**
concluding: api.x.ai accepts `POST /v1/chat/completions` with
`Authorization: Bearer`, returns OpenAI-shaped errors, and rejected our key
on authentication rather than on protocol. No incompatibility found, so no
new provider class is justified.

Nothing added. Rejected again: `python-dotenv` (pydantic-settings wraps
it), dynaconf/hydra, any secret-manager SDK, any LLM framework.

## 6. Credential isolation — VERIFIED

`tests/conftest.py` sets `CODE2SHORTS_ENV=test` and strips credential
variables for every non-`integration` test. Before it existed the unit
suite read `.env.local`, so results depended on the machine — that is how a
real key reached pytest output. Integration tests are untouched.

Evidence: a probe test reported `TESTS SEE A REAL KEY` before, and
`tests see no credential` after.

## 7. Gemini verification — PASS

| Check | Result |
|---|---|
| provider initialization | `GeminiLLMProvider` via factory |
| real request | `pong`, 18.5 s |
| structured output | `ExplanationResponse`, 4 steps, 8.1 s |
| grounding validation | PASS, citations `[0,1,2,3]` all real |
| bounded repair | 2 failed attempts repaired, resolved |
| request count | **3 calls**, within the 9-call bound |
| key in logs/provenance | absent (asserted) |
| artifact producer | `gemini:gemini-3.6-flash` |
| media validation | PASS |
| timeline validation | PASS |

**Model correction, reported rather than worked around.** The brief
specified `GEMINI_MODEL=gemini-2.5-flash`. Google returns:

```
404 — This model models/gemini-2.5-flash is no longer available to new
users. Please update your code to use models/gemini-3.6-flash
```

`gemini-3.6-flash` was used. Validation was not weakened.

## 8. xAI / Grok verification — **NOT VERIFIED**

The implementation is correct and was **not** modified to accept the
supplied key.

| Target | Result |
|---|---|
| `api.x.ai/v1/models` | 400 `Incorrect API key provided` |
| `api.x.ai` `grok-4-fast`, `grok-3` | 400 `Incorrect API key provided` |
| `api.groq.com/openai/v1/models` | 403 |

The credential begins `gsk_` — a **Groq** prefix. xAI keys begin `xai-`.

The integration test skips, loudly and unambiguously:

```
XAI/GROK NOT VERIFIED — credential is not an xAI credential. Its prefix
identifies it as 'groq'; xAI keys begin `xai-`. This test did NOT pass and
did NOT fail. The implementation is correct and unchanged: supply a
genuine xAI key to verify it.
```

`classify_credential()` makes this diagnosable rather than mysterious. It
is advisory only and never enforced by the factory — prefixes are vendor
conventions, and hard-rejecting on one would break when a vendor changes
format. A regression test asserts a `gsk_` value classifies as `groq` and
never as `xai`.

**Groq deferred.** The credential appears to be Groq's. Groq support was
**not** added — scope unchanged. Noted for later: Groq exposes
`https://api.groq.com/openai/v1`, so it would cost one URL constant and no
new class. Requires an explicit request.

## 9. OmniRoute verification — PASS (with a loud skip)

Regression against a live gateway: **4 passed, 1 skipped**. The skip is
free-tier upstream exhaustion, reported explicitly. Still optional
infrastructure: nothing imports it, `pyproject.toml` untouched.

## 10. Move Zeroes real-LLM verification — **PASS**

Provider explicit in the run configuration and in artifact metadata; no
silent switching.

```
python scripts/run_phase5_golden_path.py --algorithm move_zeroes \
    --provider gemini --model gemini-3.6-flash --timeout 300
```

| Stage | Result |
|---|---|
| Maven compile / JUnit / JVM | OK |
| ExecutionTrace | 67 events, `''` → `1,3,12,0,0` (expected) |
| plan producer | **`gemini:gemini-3.6-flash`** |
| semantic validation | PASS (1 repair consumed, superseded attempt recorded) |
| timeline fitting | 28 steps widened, e.g. 1.5 s → 4.05 s |
| overflow | **0** |
| subtitles | valid |
| final MP4 | 1080×1920 h264, **142.25 s**, 4268 frames |
| audio | aac 22050 Hz, 142.25 s (matching) |
| media validation | **PASS** |
| timeline validation | **PASS** |

## 11. Security verification

Asserted by test: a canary credential never appears in `Settings`
repr/str/`model_dump`/`model_dump_json`, provider provenance, artifacts,
workflow state, or exception messages. AST checks: prompt builders cannot
reach `os.environ` or any `*_api_key`; no module passes an `*_api_key` to
`logger`/`logging`/`print`. Both committed templates are scanned for real
key shapes and every `*_API_KEY` line must be empty or `<PLACEHOLDER>`.

Git: `git ls-files | grep .env` → only `.env.example`. `.env.local`,
`.env.production`, `.env.staging.local` ignored; both templates
committable. Scan of every changed file found only the declared canary.

Test fixtures use `test-gemini-secret` / `test-xai-secret`. No real
credential was printed, copied, moved, rotated or revoked.

## 12. Tests

| Run | Result |
|---|---|
| `pytest -m "not integration"` | **438 passed**, 0 failed |
| `pytest` (full) | **517 passed, 5 skipped, 0 failed**, 341 s |
| `tests/test_production_configuration.py` | 55 passed |
| Collected | 522 |
| Production dry run | 7/7 as expected |

The 5 skips: 4 gateway tests (gateway stopped) and 1 xAI test (wrong-vendor
credential). Each states it did **not** pass. No false-positive skip: the
xAI skip fires only on a genuinely non-xAI or missing credential, and a
valid key runs the real test.

New Phase 5.1 locks cover: vendor classification (8 cases), Groq≠xAI,
classification never returns the value, classification is advisory,
xAI reuses the shared provider, no per-vendor class exists, each backend is
only a base_url, fallback does not change the built provider, a failing
provider raises rather than switching, no module implements failover,
artifact producer names the real provider.

## 13. Real media verification

Frames extracted from the real-Gemini Move Zeroes MP4 and inspected:

**t = 45 s** — title *"Main.moveZeroes Execution Trace"*; `fast = 1,
slow = 0`; array `0 1 0 3 12` with indices; `slow`→0, `fast`→1; cell 1
highlighted; caption *"Read nums[1], which has value 1."*; code panel boxes
**line 7 `int value = nums[fast];`**.

**t = 95 s** — `fast = 3, slow = 1`; array `1 3 0 0 12` (two swaps applied);
`slow`→1, `fast`→3; cell 3 highlighted; caption *"Swap non-zero value 3 at
index 3 with value at index 1, updating nums to [1, 3, 0, 0, 12]"* —
matching the tiles exactly; code panel boxes **line 11 `nums[fast] = temp;`**
with the window scrolled to lines 5–16.

Every visual fact is reconstructed from `ExecutionTrace`. `slow`/`fast` come
from the same structural pointer rule that yields `left`/`right` for Reverse
String — **no algorithm-specific branch was introduced**.

## 14. Performance

| Stage | Move Zeroes (Gemini) | Reverse String (Gemini) |
|---|---|---|
| explain | 32.47 s | 18.95 s |
| visualization_plan | 64.62 s | 86.08 s |
| narration | 13.85 s | 8.22 s |
| **LLM total** | **115.35 s** | 116.59 s |
| Manim | 130.20 s | 126.76 s |
| TTS / FFmpeg | 8.17 s / 1.82 s | 6.17 s / 1.09 s |

Gemini is ~4× faster than free-tier gateway routing and produced a
28-step plan for Move Zeroes versus the free model's 67 — 142 s instead of
384 s. Configuration changes added no measurable overhead.

## 15. Known limitations

* **xAI/Grok unverified** — the supplied credential is not an xAI key (§8).
* **Gemini free tier is 20 requests/day per model**; a single golden path
  can consume a meaningful share. Quota exhaustion is reported as an
  explicit skip, never bypassed, and retries were not increased.
* **Free-tier gateway routing is unreliable** — it exhausts upstreams and
  answers HTTP 400, which is correctly classified permanent.
* **No automatic failover** (ADR-5.10, deliberate).
* **No Docker or CI configuration exists**, so production behaviour is
  verified by clean-environment subprocess dry runs, not in a container.
* **A shell-exported credential still wins in `test`** — precedence is by
  design; conftest strips the names it knows.
* **Plan length is still model-controlled** — 109–142 s videos where the
  short format wants under 60 s. Not shortened automatically, by rule.
* `gemini-2.5-flash` is retired for new users (§7).

## 16. Deferred work

1. Groq support (one URL constant, no new class) — needs an explicit ask.
2. Runtime failover — only with explicit configuration, visible metadata,
   preserved lineage, bounded attempts, deterministic-identity tests.
3. `GeminiLLMProvider.describe` is thinner than the OpenAI-compatible one.
4. No container/pipeline asserting the production model in CI.
5. Constraining plan length — the strongest remaining content lever.

## 17. Exact verification commands

```bash
# unit, credential-free
pytest -m "not integration"

# full suite
pytest -q -rs

# production configuration
pytest tests/test_production_configuration.py -q
python scripts/production_config_check.py

# real providers (opt-in)
pytest tests/test_llm_providers_integration.py -m integration -v -rs

# real-LLM golden paths
python scripts/run_phase5_golden_path.py --algorithm reverse_string \
    --provider gemini --model gemini-3.6-flash --timeout 300
python scripts/run_phase5_golden_path.py --algorithm move_zeroes \
    --provider gemini --model gemini-3.6-flash --timeout 300

# frame extraction
ffmpeg -y -ss 45 -i output/phase5/move_zeroes/final.mp4 -frames:v 1 mz_t45.png
```

## 18. Final gate decision

| Criterion | Result |
|---|---|
| Production cannot read `.env`/`.env.local` | **PASS** — structurally, proven from inside the repo |
| Tests cannot consume `.env.local` | **PASS** |
| Gemini real request passes, or quota reported without claiming PASS | **PASS** |
| xAI/Grok verified OR clearly NOT VERIFIED | **PASS** (explicitly NOT VERIFIED) |
| `gsk_` not misclassified as xAI | **PASS** |
| Real Move Zeroes verified with a real provider | **PASS** (Gemini) |
| Media validation | **PASS** |
| Timeline validation | **PASS** |
| Frames visually inspected | **PASS** |
| No credential leakage | **PASS** |
| No architecture regression | **PASS** |
| No unnecessary dependencies | **PASS** (zero) |
| Full regression suite passes | **PASS** (517 passed, 0 failed) |
| No false-positive skips | **PASS** |

Nothing was committed. xAI remains unverified by design, which the gate
criteria explicitly permit as long as it is not claimed as passing — it is
not.

### PHASE 5.1 GATE: PASS

# Phase 5 deliverable report — LLM-driven content generation & multi-provider architecture

## 1. Repository inspection findings

Inspected before writing code, as required. What already existed and was
reused rather than rebuilt:

* `llm/provider.py::LLMProvider` — Phase 0.1's framework-neutral seam, a
  single `complete(prompt, system=None) -> str`. **Sufficient as-is**; no
  second provider abstraction was introduced.
* `ai/contracts.py` — `ExplanationResponse`, `VisualizationPlanResponse`
  (with the closed `VisualAction` StrEnum), `NarrationResponse`.
* `ai/repair.py::generate_with_repair` — bounded repair, already shared by
  every AI node.
* `ai/validation.py`, `visualization/validation.py`,
  `narration/validation.py` — semantic validation against the real trace.
* `ai/providers/gemini.py` — a real provider already behind `LLMProvider`,
  with an injectable `completion_fn` (the pattern Phase 5 copied).
* `litellm` — already a dependency since Phase 0.

**Discrepancy found:** `Settings.llm_provider` defaulted to `"ollama"`, a
provider with no implementation behind the factory. Changed to `"mock"` so
nothing silently requires credentials or an absent service.

## 2. Reuse audit

`docs/PHASE_5_REUSE_AUDIT.md`. Evaluated litellm, Pydantic, Instructor,
Outlines, LangChain, the official `openai` SDK, the OpenRouter SDK, raw
`httpx`, and OmniRoute — with licences verified, not assumed.

**Result: zero new production dependencies, zero new dev dependencies.**
`pyproject.toml` is unchanged by Phase 5.

Instructor was the closest call and was rejected on a specific ground: it
would own the retry policy, blurring the provider-retry / workflow-repair
separation this phase was required to keep distinct — and the two things it
provides are a retry loop we already have and a ~15-line schema
instruction.

## 3. OmniRoute evaluation

`docs/PHASE_5_OMNIROUTE.md`. Verified locally, not from the README:
`omniroute@3.8.49`, **MIT licence confirmed directly** (a `license: null`
is what disqualified tracers.java in Phase 4.2A). Installed, started,
served `/v1/models` and `/v1/chat/completions`, and answered **with no
credentials** through a keyless free provider.

Five findings the README does not state, all from actually running it:
returns streaming SSE even when not requested; **~55 s cold start**; the
process does not survive indefinitely (it died mid-session and had to be
restarted); free-tier latency far exceeds the 30 s default timeout; and an
interrupted npm install leaves a locked directory that reports as
`omniroute@` with no version.

## 4. Why OmniRoute cannot become a mandatory dependency

Structural, not a promise. It speaks the standard OpenAI wire format, so
integration is **a URL**. There is no `OmniRouteProvider` class, nothing
imports it, `pyproject.toml` gains nothing, and the only OmniRoute-specific
value in the project is a default URL constant inside the factory seam —
enforced by `test_provider_selection_lives_only_in_the_factory`, which
parses the AST of every module outside that seam.

## 5. Provider architecture

One `LLMProvider`. One `OpenAICompatibleProvider` parameterised by
`base_url`, reaching OmniRoute, Ollama, vLLM, LM Studio, OpenRouter and
OpenAI. `GeminiLLMProvider` unchanged. Selection lives only in
`ai/providers/factory.py`; `KNOWN_PROVIDERS` is closed and unknown names
raise. Workflow and domain code never learn which backend they hold.

## 6. Direct Gemini still works

`test_gemini_remains_supported` (unit) constructs it through the factory;
`test_direct_gemini_still_works` (integration) exercises it for real.
**The integration test SKIPPED — no credential was available. It did not
pass; it did not run.** Its skip reason says exactly that.

## 7. Structured output

Prompts now state their response schema, derived from
`model_json_schema()` so it cannot drift from the model that validates the
reply (`json_schema_instruction`). `extract_json` recovers the JSON body
from markdown fences and preamble — purely lexical, AST-tested to call no
`eval`/`exec`/`compile`/`__import__`/`literal_eval`, and **not** lenient
parsing: what it extracts must still satisfy the model in full. Prompt
versions bumped to `v2`.

## 8. Grounding against the real trace

Every visualization step names a `trace_event_index` checked against the
canonical `ExecutionTrace`. Verified against a live model that actually
failed it:

```
step order must be sequential starting at 0; got [1, 2, ..., 25]
step 9 claims variable_name='chars' but trace event 8 actually concerns 'chars[0]'
```

Repair fixed it; the artifact was built from the passing attempt.

## 9. Bounded repair, termination proven by call count

Not by inspection — by counting provider calls.
`max_repair_attempts=3` → exactly **4** calls; `=2` → **3**; `=0` → **1**.
A schema-valid but always-lying model terminates identically. The loop
raises; it never returns a best-effort object.

## 10. Provider retry vs workflow repair

Kept separate (ADR-5.4). Retry resends an identical request after a
timeout/429/5xx; repair sends a **different** prompt built from the
previous output plus its validation errors. Worst case is an explicit
`(1 + max_retries) × (1 + max_repair_attempts)` = 9 calls per node.

## 11. Security — what a hostile model cannot do

43 tests in `tests/test_phase5_llm_security.py`. The strongest control is
structural: **there is nowhere to put code.** No field shaped like `code`,
`command`, `script`, `args`, `path` or `file`, and `visual_action` is a
closed StrEnum so `"run_shell"` fails Pydantic before any project code
runs. Covered: 14 executable-looking payloads, smuggled extra fields,
fabricated trace events, an empty trace, prompt injection carried in the
Java source (the injection *is* in the prompt, and obeying it still fails),
refusals/prose/HTML replies, path traversal, null bytes, an RTL override,
and repair-loop termination. AST tests confirm no module in `ai/` or
`narration/` can start a process, and that `narration_text`/`lesson_title`
never appear in FFmpeg argument construction.

## 12. Secrets

Never hardcoded, logged, or placed in artifacts, provenance, or exception
messages — each asserted by a test. **Prompt builders are AST-tested to be
unable to read `os.environ`, `getenv`, or any `api_key` attribute**, so a
credential cannot leak to a third-party gateway inside a prompt. Every unit
test is credential-free and network-free; the OpenAI-compatible provider is
exercised against a local `HTTPServer` stub speaking the real wire format.
No key was used from any leaked, shared, scraped, or undocumented source —
the free path is a legitimate keyless provider behind a local gateway.

## 13. Defects found and fixed

Four, all found by running a real model rather than a cooperative mock:

1. **Prompts never stated a response schema.** The model returned `steps`
   as an array of strings and exhausted every repair attempt on a response
   it had no way to know was misshapen.
2. **Two divergent parse paths.** `generate_structured` and `ai/repair.py`
   parsed replies differently, so a correct-but-fenced answer succeeded in
   one and burned a repair attempt in the other.
3. **Retry amplification.** litellm retries 3× and the underlying OpenAI
   client retries again, so `max_retries=2` produced **nine** upstream HTTP
   requests — tripling load on a rate-limited free tier while the config
   said three. Fixed with `num_retries=0, max_retries=0`; asserted at
   exactly 3 (transient) and exactly 1 (permanent).
4. **Impossible step durations.** The model proposed 1.0 s steps for
   2.2–4.5 s of narration; all 25 segments overflowed and
   `validate_timeline` correctly refused to truncate.

## 14. Timeline fitting

The fix for (4) belongs upstream of the renderer, not in the alignment
rule. `narration/fitting.py` widens each visual step to hold its **measured**
speech before Manim runs. Widen-only (`min()` is banned there by test), so
nothing is ever cut; the Phase 4.4 prohibitions — no `-shortest`, no
truncation, no stretching — all still hold. The model's proposal is a
floor, so deliberate pacing survives. After fitting, real runs report
overflow **0** and `validate_timeline` **PASS**.

## 15. Superseded validation attempts

A real run reported `validation: FAIL` overall while every artifact had
been built from a passing attempt. `ValidationResult.superseded` (optional,
default `False` — backward compatible) marks replaced attempts;
`all_passed` ignores them, `every_attempt_passed` gives the stricter view,
and the failed attempt is retained as audit evidence.

## 16. Real-LLM golden path — Reverse String

`scripts/run_phase5_golden_path.py --provider omniroute --model auto --timeout 300`

Real Maven/JVM trace → real LLM → schema + semantic validation → bounded
repair → real Manim → real SAPI speech → real FFmpeg → media validation.

* trace: 25 events, `'HELLO'` → `'OLLEH'` (expected)
* plan producer: `omniroute:auto`; 1 repair attempt consumed
* fitting widened 25 steps (step 0: 1.0 s → 7.32 s); overflow **0**
* video: 1080×1920 h264, **153.68 s**, 4611 frames; audio aac 22050 Hz matching
* media validation **PASS**; timeline validation **PASS**

## 17. Real-LLM golden path — Move Zeroes

* trace: 67 events, `''` → `1,3,12,0,0` (expected)
* fitting widened 67 steps (step 0: 1.0 s → 6.07 s); overflow **0**
* video: 1080×1920 h264, **384.02 s**, 11 522 frames; audio aac 22050 Hz matching
* media validation **PASS**; timeline validation **PASS**

## 18. The MP4s were inspected, not assumed

Frames extracted from both finals and viewed.

*Reverse String @ 40 s* — LLM title "Reversing a String with Two-Pointer
Technique"; state `input = HELLO, left = 0, right = 4`; tiles `H E L L O`
with indices; `left`/`right` arrows on cells 0 and 4; caption "Set right
pointer to 4"; the code panel boxes **line 9**, `int right = chars.length - 1;`
— the statement that step actually executes.

*Move Zeroes @ 90 s* — state `fast = 1, slow = 0`; tiles `0 1 0 3 12`;
`slow`/`fast` arrows on cells 0 and 1; caption "Check if value != 0: true,
non-zero element."; code panel boxes **line 8**, `if (value != 0) {`.

The Phase 4.5.1 line-mapping fix holds under LLM-authored plans.

## 19. No algorithm-specific branching

`left`/`right` and `slow`/`fast` are produced by the same structural
pointer rule (an integer scalar whose value is a valid array index). No
renderer branch names any algorithm; the existing source-scanning test
still enforces this, and Phase 5 added no exception.

## 20. Performance

`docs/PHASE_5_PERFORMANCE.md`. The LLM is now the dominant cost:

| | Mock | Real (Reverse String) | Real (Move Zeroes) |
|---|---|---|---|
| Workflow incl. LLM | 3.33 s | 289–486 s | 569 s |
| TTS | 1.02 s | 6.1 s | 16.2 s |
| Manim | 18.8 s | 135 s | 336 s |
| FFmpeg | 0.19 s | 1.6–2.2 s | 4.1 s |

The LLM is **61–78 %** of wall-clock and varied **68 %** between two
identical runs, while deterministic stages moved under 2 %.

## 21. Test results

| Run | Result |
|---|---|
| `pytest -m "not integration"` | **382 passed**, 0 failed |
| `pytest` (full) | **463 passed, 2 skipped**, 0 failed, 437 s |
| Real-provider integration | **4 passed, 1 skipped** against a live gateway |

Both skips state loudly that they did **not** pass: one is the missing
Gemini credential, one is free-tier capacity exhausted upstream of the
gateway. That second condition is skipped only when the gateway explicitly
reports upstream exhaustion — every other failure still fails the build.
465 tests collected in total.

## 22. Known limitations (nothing here is claimed as working)

* **Free-tier output is too long for the format.** The model proposes one
  step per trace event — 25 and 67 steps — giving 154 s and 384 s videos.
  They are grounded, validated and correctly timed, but longer than a short
  should be. Constraining plan length is content policy, not a correctness
  gate, and is **not implemented**.
* **First-attempt plan quality is poor.** The live model failed grounding
  validation on its first attempt in every observed run and needed repair.
* **Gemini was never exercised for real** — no credential was available.
* **A gateway can report a transient upstream failure as HTTP 400**, which
  is classified permanent and not retried. Correct HTTP semantics; a real
  operational sharp edge. Not special-cased in production code, because
  that would put provider-specific logic above the seam.
* **Layout has large vertical dead space** — content occupies roughly the
  middle third of the 1920 px frame. Cosmetic, pre-existing, untouched.
* **Prompt content is visible to whatever gateway is configured.** No
  credential can be included, but the algorithm source is sent.
* **Validation proves grounding, not pedagogy.** A well-grounded but dull
  or badly paced video passes.

---

**PHASE 5 GATE: PASS**

Real LLM, real trace, real validation, real repair, real MP4s — inspected.
Zero new dependencies. OmniRoute optional by construction. Nothing
committed: all changes are left staged/unstaged for architectural review.

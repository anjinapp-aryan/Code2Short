# Phase 5 — LLM architecture

How a real language model is allowed to shape a Code2Shorts video, and what
stops it from shaping anything else.

The founding rule is unchanged and Phase 5 is the first phase to test it
against a model that was not written to cooperate:

> **ExecutionTrace = canonical truth. LLM = proposer. Validation =
> authority. Renderer = trusted code.**

---

## 1. The provider layer

```
                    build_llm_provider(settings)      <- the ONE seam
                                |
        +-----------------------+-----------------------+
        |                       |                       |
  MockLLMProvider        GeminiLLMProvider     OpenAICompatibleProvider
  (default; no           (direct Gemini,        (base_url = anything)
   credentials)           unchanged)                    |
                                        +---------------+---------------+
                                        |        |        |        |
                                   OmniRoute  Ollama   vLLM    OpenAI
```

All three implement the **same** `LLMProvider` from Phase 0.1 — a single
method, `complete(prompt, system=None) -> str`. Phase 5 introduced no
second provider abstraction; `test_every_provider_implements_the_one_shared_interface`
enforces that.

**Why there is no `OmniRouteProvider`.** Every backend above speaks the
same `/v1/chat/completions` format, so the only thing that differs is a
URL. Writing a class per vendor would create per-vendor code paths for
zero behavioural difference — and would make OmniRoute a dependency rather
than a URL. See `PHASE_5_OMNIROUTE.md`.

**Provider selection exists in exactly one file.** `ai/providers/factory.py`
holds the only `if provider == ...` in the project.
`test_provider_selection_lives_only_in_the_factory` parses the AST of every
module outside that seam and fails if a provider-specific URL appears.
Workflow and domain code never learn which backend is in use; they hold an
`LLMProvider`.

`KNOWN_PROVIDERS` is closed and unknown names raise
`ProviderConfigurationError`. `LLM_PROVIDER` defaults to `mock`, so nothing
silently requires a credential, and `gemini` without a key fails loudly
rather than falling back.

## 2. What the LLM is allowed to produce

| May generate | May NOT generate |
|---|---|
| Explanation prose and learning objectives | Python, Manim, or any executable source |
| A `VisualizationPlanResponse` — ordered steps, each naming a `VisualAction` and a `trace_event_index` | Shell / PowerShell commands |
| Narration text per step | FFmpeg arguments or any subprocess argv |
| Lesson titles | File paths, output locations |
| Proposed step durations (a *floor* — see §5) | Java source that will be executed |

The strongest control is structural rather than procedural: **there is
nowhere to put code.** `VisualizationStepPlan` has no field shaped like
`code`, `command`, `script`, `args`, `path` or `file`, and
`test_the_plan_schema_has_no_field_shaped_like_code_or_a_command` fails the
build if one appears. `visual_action` is a **closed `StrEnum`**, so a
response containing `"visual_action": "run_shell"` fails Pydantic
validation before any project code inspects it. Extra fields a model
invents (`manim_code`, `post_render_command`) are dropped at validation and
never reach the renderer.

Free-text fields — narration, titles — are genuinely free text. Their
control is at the renderer: `build_scene_source` passes every dynamic value
through `repr()` as a Python string literal, so hostile-looking text
renders as inert data. Fourteen executable-looking payloads (`__import__`,
`$(whoami)`, `-shortest`, path traversal, a null byte, an RTL override) are
tested to compile to valid, inert Python.

## 3. Structured output

`ai/structured.py` is the single seam from raw text to a validated model.

* **Prompts state their schema.** `json_schema_instruction(model)` derives
  the instruction from `model_json_schema()`, so it can never drift from
  the model the response is validated against. This was added because the
  first live run returned `steps` as an array of strings and exhausted
  every repair attempt — Phase 4's mock always emitted valid JSON, so the
  omission was invisible.
* **`extract_json` recovers the body.** Real models wrap JSON in ```` ```json ````
  fences or add a sentence of preamble. Extraction is purely lexical — it
  slices text and never interprets it (AST-tested to call no `eval`,
  `exec`, `compile`, `__import__` or `literal_eval`). It is **not** lenient
  parsing: whatever is extracted must still satisfy the Pydantic model in
  full, and when nothing JSON-shaped is found the original text is returned
  so the error reports what the model actually said.
* **One parse path.** `generate_structured` and `ai/repair.py` now share
  `extract_json`. They did not, and a correct-but-fenced reply therefore
  succeeded in one and burned a repair attempt in the other.

## 4. Provider retry vs workflow repair — deliberately separate

These solve different problems and are never merged. See ADR
"provider retry is not workflow repair".

| | Provider retry | Workflow repair |
|---|---|---|
| Lives in | `ai/providers/openai_compatible.py` | `ai/repair.py` |
| Triggered by | Timeout, 429, 5xx, connection reset | Schema failure, semantic failure |
| Meaning | The call did not complete | The call completed and the **content** is wrong |
| Prompt | Identical — resending the same request | **Different** — built from the previous output plus the specific validation errors |
| Bound | `max_retries` (default 2, so 3 attempts) | `max_repair_attempts` |
| Exhausted | `OpenAICompatibleTransientError` | `SchemaValidationError` / `SemanticValidationError` |

Merging them would resend a *semantically wrong* answer unchanged (useless)
or rebuild a prompt after a *network timeout* (misleading — there was no
output to repair). The multiplication is also bounded and explicit:
`(1 + max_retries) × (1 + max_repair_attempts)` calls in the worst case.

**Measured retry amplification.** litellm retries three times by default
and the underlying OpenAI client retries again, so `max_retries=2`
produced **nine** upstream HTTP requests. Against the rate-limited free
tier this phase targets, that triples load while the configured number says
three. Fixed by pinning `num_retries=0, max_retries=0` and making
`OpenAICompatibleProvider` the single retry authority;
`test_server_error_is_classified_transient_and_retried` asserts **exactly
three** requests, and `test_bad_request_is_permanent_and_never_retried`
asserts exactly one.

**Termination is proven by call count**, not by inspection:
`max_repair_attempts=3` against a provider that never complies makes
exactly 4 calls; `=2` makes 3; `=0` makes 1. A model that is schema-valid
but always lies terminates identically. The loop never returns a
best-effort object — it raises.

## 5. Grounding: every step is checked against the real trace

A plan step names a `trace_event_index`. `validate_visualization_plan`
rejects any index that is not a real `step_index` in the `ExecutionTrace`,
rejects non-sequential step ordering, and rejects a step whose claimed
`variable_name` disagrees with the event it points at.

This is not theoretical. The live free-tier model failed exactly these
checks on its first attempt of a real run:

```
step order must be sequential starting at 0; got [1, 2, ..., 25]
step 9 claims variable_name='chars' but trace event 8 actually concerns 'chars[0]'
```

Repair fixed it, and the artifact was built from the passing attempt.

### Superseded attempts

Recording the full repair history is deliberate — it is the audit evidence
for how many attempts a provider needed. But a run whose repair *succeeded*
was reporting `validation: FAIL` overall, because a superseded failure was
still counted. `ValidationResult.superseded` now marks replaced attempts;
`ValidationSummary.all_passed` ignores them, and `every_attempt_passed`
reports the stricter view for judging provider quality. The failed attempt
is never deleted.

## 6. Timeline fitting: the LLM proposes pacing, trusted code makes it possible

`alignment.py` holds that the visual timeline is authoritative and audio
never stretches it. That rule is correct but assumes the plan's durations
were achievable. The live model proposed **1.0 s steps for narration taking
2.2–4.5 s to speak**, and all 25 segments overflowed —
`validate_timeline` correctly failed rather than truncating.

The fix belongs upstream of the renderer, not in the alignment rule.
`narration/fitting.py::fit_plan_to_narration` widens each visual step until
its measured speech fits, before Manim runs:

* **Widen only, never shrink** — nothing is ever cut off.
* **No truncation, no `-shortest`, no silent duration manipulation, no
  video stretching** — Phase 4.4's prohibitions hold.
* **Measured, never estimated** — durations come from the TTS provider's
  real output.
* **The model's proposal is a floor** — a step it deliberately made long
  keeps its pacing; only the impossible part is corrected.

After fitting, overflow is 0 and `validate_timeline` passes on real runs.

## 7. Secrets

* Keys are read from configuration/environment only, never hardcoded.
* `describe` (the provenance recorded on artifacts) deliberately excludes
  the key; `test_api_key_never_appears_in_provenance` asserts it.
* Error messages never carry the key
  (`test_api_key_never_appears_in_error_messages`).
* No provider module logs it (`test_no_provider_module_logs_the_key`).
* **Prompt builders cannot reach configuration.** AST-tested: no
  `build_*_prompt` function may touch `os.environ`, `getenv`, or any
  `api_key` attribute, so a credential cannot be leaked to a third-party
  gateway inside a prompt.
* Every unit test in Phase 5 runs credential-free and network-free; the
  OpenAI-compatible provider is exercised against a local `HTTPServer` stub
  speaking the real wire format.

## 8. Known limitations

* **Free-tier output is long.** The model proposed one step per trace
  event (25 steps), which after fitting produced a ~150 s video. That is
  correct, validated, and grounded — but longer than the short format
  intends. Constraining plan length is content policy, not a correctness
  gate, and is not implemented.
* **Free-tier latency dominates.** The three LLM nodes took 277–486 s
  total, against ~135 s for Manim and under 3 s for FFmpeg.
* **First-attempt plan quality is poor.** The live model failed grounding
  validation on its first attempt in every observed run and needed repair.
  The architecture handles this exactly as designed; a stronger model would
  simply repair less.

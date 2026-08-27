# PHASE 5.3 DELIVERABLE REPORT

Educational story planning & learning quality.

## 1. Files changed

| File | Change |
|---|---|
| `src/code2shorts/ai/contracts.py` | `LearningConcept` (closed enum, 13), `ClaimKind`, `EducationalMoment`, `EducationalPlanRequest/Response` |
| `src/code2shorts/workflow/nodes.py` | `EducationalPlanNode`, `build_educational_prompt`; `build_visualization_prompt` accepts an optional lesson |
| `src/code2shorts/artifacts/models.py` | `ArtifactType.EDUCATIONAL_PLAN` |
| `src/code2shorts/workflow/__init__.py` | exports the node |
| `src/code2shorts/config.py` | blank-entry credential recovery (defect found with a real key, §9) |
| `scripts/run_phase5_golden_path.py` | educational node in the pipeline; lesson + completeness reporting |
| `ARCHITECTURE_DECISIONS.md` | ADR-5.11, 5.12, 5.13 |
| `README.md`, `CLAUDE.md`, `.env.example` | the principle and its rules |

## 2. Files added

`src/code2shorts/ai/education.py`, `src/code2shorts/ai/grounding.py`,
`tests/test_educational_planning.py`, `docs/PHASE_5_3_REUSE_AUDIT.md`,
this report. **Zero new dependencies** — `pyproject.toml` unchanged.

## 3. Focused reuse audit

`docs/PHASE_5_3_REUSE_AUDIT.md`. Twelve candidates assessed on licence,
maintenance, capability, compatibility, security and dependency cost.

**DIRECT REUSE:** Pydantic v2, the existing `ai/repair.py` +
`ai/structured.py` seam, `ExecutionTrace`/`FrameState`/`CodeState`.
**REFERENCE:** DSPy (its assert/suggest split informed our schema/semantic
split), intelligent-tutoring "knowledge component" framing.
**REJECT:** LangChain/LangGraph (excluded by the Phase 3 gate; its
`OutputFixingParser` asks a second LLM to fix the first and never
validates against a trace), Instructor, Outlines, Guidance, `networkx`
(a graph library for one topological check), rubric libraries (built to
grade *learners*, mostly dormant), storyboarding libs, `jsonschema`.

Every rejected candidate fails on the same axis: **none can check a claim
against a real execution trace.** A rubric library can confirm a plan
mentions "termination"; only Code2Shorts can confirm that *"fast = 3"*
matches `TraceEvent[23]`. That check is the product, so BUILD is the
honest answer — and what was built is small.

## 4. Educational architecture

```
ExecutionTrace   ->   EducationalPlan   ->   VisualizationPlan
(what is TRUE)        (what MATTERS)          (how to SHOW it)
 execution truth      LLM reasoning           presentation
```

The three concerns are kept separate. The LLM reasons about educational
importance and never becomes the source of execution truth.

## 5. EducationalPlan model

`EducationalMoment`: `id`, `concept`, `claim_kind`, `explanation`,
`evidence_event_indices`, `source_lines`, `narration`, `importance`,
`prerequisite_ids`. `LearningConcept` is a **closed** 13-value StrEnum, so
an invented category fails Pydantic before any project code runs — the
same control as `VisualAction`.

**There is no duration field anywhere in the model.** Nothing to optimise
against, by design (ADR-5.11), and a test asserts its absence.

## 6. LearningCompleteness

`ai/education.py`. Required concepts are derived from the **trace's
shape**, never the algorithm's name: `detect_algorithm_shapes()` returns
`POINTER_TRAVERSAL` / `ARRAY_MUTATION` / `SCALAR_COMPUTATION` from the
event stream, and required concepts are the union of a universal set with
each shape's set. A scalar computation is not failed for never teaching
`POINTER_MOVEMENT`. No algorithm-specific branch was introduced.

A plan fails for **missing understanding** — a required concept never
taught, or a moment with neither narration nor explanation — and never for
length. `estimated_narration_words` is reported and explicitly excluded
from the verdict.

## 7. LLM prompt / schema changes

`build_educational_prompt` states the contract explicitly rather than
implying it (the Phase 5 bug): the trace is authoritative; do not invent
values; the three claim kinds and when to use each; the required concepts
by name; prerequisite ordering; and

> "There is NO duration limit and no step budget. Do not drop a conceptual
> transition to make the lesson shorter."

The schema is appended via `json_schema_instruction(EducationalPlanResponse)`,
derived from the Pydantic model so it cannot rot. Tests assert every one of
these clauses is present.

## 8. Trace-grounding validation

`ai/grounding.py` enforces evidence **only where evidence is meaningful**:

| Rule | Effect |
|---|---|
| `OBSERVED` without evidence | rejected |
| evidence citing a non-existent event | rejected |
| stated value not shown by the cited events | rejected |
| claim about a variable no cited event concerns | rejected |
| source line outside the real file | rejected |
| prerequisite that is dangling, self-referential, or later | rejected |
| duplicate moment id | rejected |
| `EXPLANATION` / `COMMENTARY` reasoning freely | allowed |

**Two defects found by these tests while building them**, both fixed and
locked: matching English "is" as assignment rejected the valid explanation
*"left is less than right"*; and a `\w+` value pattern silently skipped
quoted chars, letting a fabricated `chars[7] = 'Z'` through unchecked.

## 9. Security changes

All existing controls preserved. New adversarial coverage: 8 hostile
payloads (`__import__`, `eval`, `$(whoami)`, backticks, PowerShell,
traversal, null byte, injection text) confirmed inert through the renderer;
prompt injection carried **inside the Java source** is quoted into the
prompt and still cannot widen the schema; the educational schema has no
field shaped like code.

**One real configuration defect fixed.** After you added
`OMNI_ROUTE_LLM_API_KEY`, it was silently unread: pydantic-settings
resolves `AliasChoices` *inside* the dotenv source, so the blank
`CODE2SHORTS_LLM_API_KEY=` line copied from `.env.example` consumed the
slot and the real key further down the file was never consulted — a
credential that looks configured and fails at the wire. Blank entries are
now dropped and the remaining names are consulted explicitly. Three
regression tests, including one asserting production **still** recovers
nothing from a file (ADR-5.8 unbroken).

## 10. Tests

| Run | Result |
|---|---|
| `pytest -m "not integration"` | **481 passed**, 0 failed |
| `pytest -m integration` | **78 passed, 6 skipped**, 0 failed |
| `tests/test_educational_planning.py` | 40 passed |
| `tests/test_production_configuration.py` | 58 passed |
| Collected | **565** |

Combined: **559 passed, 6 skipped, 0 failed.** The 6 skips are 4 gateway
tests (gateway stopped at the end), the Gemini quota skip, and the xAI
wrong-vendor skip — each states it did **not** pass.

An earlier 47-minute full run reported 1 failure whose identity the
truncated log did not retain; re-running both halves shows 0 failures, so
it did not reproduce. Reported rather than hidden.

Educational coverage includes: closed vocabulary, no duration field,
observed-vs-explanation, fabricated evidence/values/indices/source lines,
prose not mistaken for a claim, prerequisite ordering and cycles,
shape detection, completeness, redundancy collapse, conceptual-boundary
preservation, a 200-moment plan passing, an AST lock against budgets,
security payloads, and backward compatibility.

## 11–13. Real-LLM verification — all three algorithms

| | Reverse String | Move Zeroes | Two Sum |
|---|---|---|---|
| provider | `gemini:gemini-3.6-flash` | `gemini:gemini-3.6-flash` | `omniroute:auto` |
| trace events | 25 | 67 | 17 |
| output | `OLLEH` ✓ | `1,3,12,0,0` ✓ | `0,1` ✓ |
| shapes | pointer_traversal, array_mutation | pointer_traversal, array_mutation | pointer_traversal |
| concepts missing | **none** | **none** | **none** |
| completeness | **PASS** | **PASS** | **PASS** |
| repairs | 1 | 1 | 1 |
| overflow after fitting | 0 | 0 | 0 |
| final MP4 | 1080×1920 h264 **140.52 s** | **97.39 s** | **157.36 s** |
| media validation | PASS | PASS | PASS |
| timeline validation | PASS | PASS | PASS |

Two Sum ran on OmniRoute because Gemini's free tier (20 requests/day) was
exhausted by the first two runs. That was an **explicit** provider choice
in the run configuration and is recorded in artifact metadata as
`omniroute:auto` — not a silent switch. Its first attempt failed on
gateway upstream exhaustion (HTTP 400, classified permanent, not retried);
the retry succeeded. No hack, no extra retries, no quota bypass.

## 14. Real MP4 / frame inspection

**Reverse String** — *introduction* (t=4 s): title, welcome narration,
entry-point source, no array yet (nothing initialised). *Termination /
result* (t=132 s): array `O L L E H`, **`left = 2, right = 2` — the
pointers have met**, which is visually why the loop stopped; caption
"The reversed character array 'OLLEH' is constructed into a new String and
returned."; code panel boxes **line 17 `return new String(chars);`**.

**Move Zeroes** — *invariant / pointer movement* (t=75 s): array
`1 0 0 3 12`, `fast = 1, slow = 1`; caption "slow increments to 1,
expanding the boundary of correctly placed non-zero elements" — an
explicit invariant; code panel boxes **line 12 `slow++;`**.

**Two Sum** — *initialization* (t=60 s): array `2 7 11 15`, `target = 9`,
caption "Enter Main.twoSum method.", code on **line 7** (method entry);
pointers correctly absent because `i`/`j` do not exist yet.
*Termination / result* (t=140 s): `i = 0, j = 1` over values 2 and 7
(summing to the target), caption "Method exits twoSum, returning answer
immediately; loops terminate early without further iterations."; code
boxes **line 14 `return answer;`**.

Every visual fact is reconstructed from `ExecutionTrace`. `left`/`right`,
`slow`/`fast` and `i`/`j` all come from the same structural pointer rule —
**no algorithm-specific branch**.

## 15. Narration / visual consistency

In each inspected frame the spoken text describes what is on screen: the
stated pointer values match the labels, the stated array contents match the
tiles (Move Zeroes' caption names `[1, 3, 0, 0, 12]` and the tiles agree),
and the highlighted source line is the statement the caption describes.

## 16. Duration observations — informational only

| Algorithm | Trace events | Plan steps | Duration |
|---|---|---|---|
| Reverse String | 25 | 21 | 140.52 s |
| Move Zeroes | 67 | 11 | 97.39 s |
| Two Sum | 17 | 18 | 157.36 s |

Move Zeroes is the interesting one: **67 execution events became 11 visual
steps and a shorter video than before (97 s vs 142 s), with zero concepts
missing.** That is the intended behaviour — repetitive events summarised
into conceptual moments — and it happened because the lesson layer asked
"what must be understood?", *not* because anything was truncated to hit a
target. Duration is reported, never enforced.

## 17. Performance

| Node | Reverse String | Move Zeroes | Two Sum (gateway) |
|---|---|---|---|
| explain | 32.3 s | 34.5 s | 77.9 s |
| **educational_plan** | **37.3 s** | **24.8 s** | **119.2 s** |
| visualization_plan | 49.9 s | 65.6 s | 169.7 s |
| narration | 13.5 s | 7.1 s | 42.1 s |
| LLM total | 136.3 s | 135.0 s | 412.1 s |
| Manim | 102.3 s | 65.3 s | 143.5 s |

The educational node adds roughly 25–37 s on Gemini — one extra LLM call.

## 18. Known limitations

* **Gemini free tier is 20 requests/day per model.** Four LLM nodes per run
  means ~5 runs/day. This forced Two Sum onto OmniRoute.
* **The gateway's upstreams exhaust unpredictably** and answer HTTP 400,
  correctly classified permanent; Two Sum needed a retry.
* **xAI/Grok still unverified** — the credential is a Groq key (Phase 5.1).
* **Value checking is lexical.** `_value_errors` compares stated values
  against the cited events' description text. It catches fabricated values
  in the common `name = value` form; it does not deeply parse arbitrary
  prose, so a sufficiently indirect false claim could pass.
* **Presentation profiles are a seam, not an implementation.**
  `SHORT_EXPLANATION` / `FULL_EXPLANATION` / `DEEP_DIVE` are not built —
  the phase brief said design the seam cleanly and not to implement them.
* **Layout still has large vertical dead space** (cosmetic, pre-existing).

## 19. Deferred work

1. Presentation profiles over the same `EducationalPlan`.
2. Structural value grounding (compare against reconstructed `FrameState`
   rather than event text).
3. `importance` is carried but not yet used to steer visual density.
4. Groq support (one URL constant) and runtime failover — both still
   awaiting an explicit request.

## 20. Architecture decisions

* **ADR-5.11 Educational Integrity Over Duration** — the principle,
  recorded verbatim; no duration/step budget anywhere; AST-locked.
* **ADR-5.12 Observed facts and explanations are different claims** —
  `ClaimKind`; evidence required only where it is meaningful.
* **ADR-5.13 Required concepts derive from the trace's shape** — never
  from the algorithm's name.

## 21. Exact verification commands

```bash
pytest -m "not integration"                       # 481 passed
pytest -m integration                             # 78 passed, 6 skipped
pytest tests/test_educational_planning.py         # 40 passed
python scripts/production_config_check.py

python scripts/run_phase5_golden_path.py --algorithm reverse_string \
    --provider gemini --model gemini-3.6-flash --timeout 300
python scripts/run_phase5_golden_path.py --algorithm move_zeroes \
    --provider gemini --model gemini-3.6-flash --timeout 300
python scripts/run_phase5_golden_path.py --algorithm two_sum \
    --provider omniroute --model auto --timeout 300

ffmpeg -y -ss 132 -i output/phase5/reverse_string/final.mp4 -frames:v 1 rs.png
ffmpeg -y -ss 75  -i output/phase5/move_zeroes/final.mp4    -frames:v 1 mz.png
ffmpeg -y -ss 140 -i output/phase5/two_sum/final.mp4        -frames:v 1 ts.png
```

## 22. Final gate decision

| Criterion | Result |
|---|---|
| Focused reuse audit completed | PASS |
| Educational Integrity Over Duration ADR recorded | PASS (ADR-5.11) |
| EducationalPlan introduced without breaking contracts | PASS (additive; optional in the visual prompt) |
| ExecutionTrace remains canonical truth | PASS |
| LLM explicitly acts as educational planner | PASS |
| Execution-dependent claims require trace evidence | PASS |
| Required learning concepts represented | PASS (0 missing, all 3 algorithms) |
| Conceptual transitions preserved | PASS |
| Redundant events identified without blind deletion | PASS (67 events → 11 moments, 0 concepts lost) |
| No arbitrary duration limit introduced | PASS (AST-locked) |
| Real LLM golden paths pass | PASS |
| Reverse String / Move Zeroes / Two Sum verified | PASS / PASS / PASS |
| Actual frames inspected | PASS |
| Narration agrees with visual state | PASS |
| Learning-quality validation passes | PASS |
| Security tests pass | PASS |
| Existing regression suite passes | PASS (559 passed, 0 failed) |
| No unnecessary dependencies | PASS (zero) |
| No architecture regression | PASS |

Nothing was committed. Phase 6 not started.

### PHASE 5.3 GATE: PASS

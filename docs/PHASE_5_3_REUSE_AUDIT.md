# Phase 5.3 focused reuse audit — educational / story planning

Scope: educational & story planning, concept extraction, pedagogical
sequencing, learning objectives, rubric evaluation, LLM structured
planning, graph/state planning, content validation.

Explicitly **not** re-auditing visualization, TTS, subtitles or media —
those were settled in Phases 4.2–4.5.

**Result: BUILD the Code2Shorts-specific layer. Zero new dependencies.**

---

## 1. What we actually need

1. A **typed educational plan** whose every execution-dependent claim
   carries evidence pointing at real `TraceEvent`s.
2. A **deterministic rubric** naming which concepts an algorithm class must
   teach, and reporting which are missing.
3. **Grounding validation** rejecting fabricated values, indices, variables
   and source locations.
4. A **prompt contract** that states its schema.

The distinguishing requirement is (1) and (3): the plan must be verifiable
against a real JVM execution. That is the part no external library can
supply, because no external library knows about `ExecutionTrace`.

## 2. Decision matrix

| Candidate | Capability | License | Maintenance | Compatibility | Security | Decision |
|---|---|---|---|---|---|---|
| **Pydantic v2** | typed schema, JSON-Schema generation, closed enums | MIT | very active | already the contract layer | validates before business logic | **DIRECT REUSE** |
| **`ai/repair.py` + `structured.py`** (in-house) | bounded repair, one parse path, schema-in-prompt | — | ours | exact fit | already hardened | **DIRECT REUSE** |
| **`ExecutionTrace` / `FrameState` / `CodeState`** | the evidence base itself | — | ours | canonical | trusted | **DIRECT REUSE** |
| LangChain / LangGraph | agents, planning graphs, output parsers | MIT | very active | **poor** — excluded by the Phase 3 gate; would become a second orchestration layer beside `workflow/` | its `OutputFixingParser` asks a second LLM to fix the first and never validates against a trace | **REJECT** |
| Instructor | structured LLM output + retries | MIT | active | duplicates `generate_structured`; owns retry policy, blurring ADR-5.4 | — | **REJECT** (unchanged from Phase 5) |
| Outlines / jsonformer | constrained decoding | Apache-2.0 | active | needs logit access; meaningless via a hosted gateway | — | **REJECT** |
| Guidance (Microsoft) | constrained generation templates | MIT | active | same logit-access problem; template DSL is a second authoring language | template execution is a new surface | **REJECT** |
| DSPy | prompt/program optimisation, metric-driven | MIT | active | optimises prompts against a metric; we need *validation against execution truth*, not prompt search. Would add a compiler-like layer over the one seam we deliberately keep small | — | **REFERENCE** — its "assert/suggest" split maps loosely onto our schema/semantic split; concept borrowed, code not |
| `networkx` | DAG, topological sort for prerequisites | BSD-3 | very active | prerequisite ordering is a handful of moments; a full graph library for one topological check is disproportionate | — | **REJECT** (a 15-line cycle check suffices) |
| Bloom's-taxonomy / rubric libraries (`edx-rubric`, `rubric-py`, etc.) | rubric scoring for human assessment | mixed / mostly unmaintained | several dormant >3 yrs | built to grade *learners*, not to check whether a generated plan covers required concepts | unmaintained code in the trust path | **REJECT** |
| Intelligent-tutoring frameworks (CTAT, ASSISTments-style) | curriculum/knowledge-component modelling | mixed, often research | academic cadence | model *learner* knowledge state over time; we plan a single artefact from a single execution | — | **REFERENCE** — "knowledge component" informed `LearningConcept` |
| Storyboarding / screenplay libs (`fountain`-parsers) | scene sequencing | MIT | low | scene sequencing for prose scripts; no notion of evidence or validation | — | **REJECT** |
| `jsonschema` | schema validation | MIT | very active | Pydantic already does this and is already present | — | **REJECT** (redundant) |

## 3. Why BUILD is the honest answer

Every rejected candidate fails on the same axis: **none of them can check a
claim against a real execution trace.** A rubric library can tell you a
plan mentions "termination"; only Code2Shorts can tell you that the plan's
claim *"fast = 3"* matches `TraceEvent[23]` and the reconstructed
`FrameState`. That check is the product.

What is genuinely new is therefore small:

| Built | Purpose |
|---|---|
| `ai/contracts.py` additions | `EducationalMoment`, `EducationalPlanResponse`, closed `LearningConcept` enum, `ClaimKind` |
| `ai/education.py` | required-concept sets per algorithm shape, `LearningCompleteness` evaluation |
| `ai/grounding.py` | evidence validation against the trace |
| `workflow/nodes.py` | `EducationalPlanNode` |

Everything else — provider seam, bounded repair, schema-in-prompt,
`extract_json`, artifact lineage, security controls — is reused unchanged.

## 4. Dependency delta

```
production: +0
dev:        +0
```

`pyproject.toml` is unchanged by Phase 5.3.

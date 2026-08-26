# Open-Source Reuse Reconnaissance (Phase 4.2A)

Investigation gate before building the rich visual engine. The question is
**not** "what looks similar to Code2Shorts" but "what can we reuse without
compromising correctness, determinism, security, or the multi-language
roadmap."

Every license below was verified against the GitHub API / PyPI metadata on
**2026-08-24**, not inferred from the word "open source" in a README.

---

## A. Repository inventory

### A.1 Repositories named in the brief

| Repository | Stars | Last push | What it actually is |
|---|---|---|---|
| [algorithm-visualizer](https://github.com/algorithm-visualizer/algorithm-visualizer) | 48,690 | 2024-06-09 | React **web app**; renders visualizations client-side in the browser |
| [tracers.java](https://github.com/algorithm-visualizer/tracers.java) | 32 | **2019-06-30** | Java library requiring **manual annotation calls** in the algorithm source |
| [algorithms](https://github.com/algorithm-visualizer/algorithms) | 493 | 2024-06-23 | Corpus of annotated algorithm implementations |
| [CodeAnimator](https://github.com/HeyItsJhello/CodeAnimator) | 7 | 2026-02-05 | FastAPI + React web app; animates **static** code, no execution |
| [code-video-generator](https://github.com/sleuth-io/code-video-generator) | 255 | 2024-08-19 | Manim-based code **walkthrough** videos; does not execute code |

### A.2 Alternatives found independently

The brief correctly warned against assuming the suggested list is best, so
I searched for Manim algorithm-visualization packages and Java-tracing
tools.

| Candidate | Finding |
|---|---|
| **`arrayviz`** (PyPI) | Manim array+pointer visualizer — *exactly* our problem domain. **Its GitHub repo returns 404 (deleted).** Single release v0.0.1, 2024-08-01. |
| **ManimSort** | 3 stars, last push 2020-10-05, 8 commits, no license. Per-algorithm scripts, not a library. |
| **Manim Studio / ManimGraphLibrary** | Graph//general animation frameworks; no execution-trace concept. |
| **JavaWiz, JIVE, JaVis, Anteater** | Academic JDI-based Java tracers. Research prototypes/IDE plugins, not consumable libraries. |
| **Manim Community 0.21.0** | **Already our dependency.** See §E — this is the decisive finding. |

---

## B. License analysis

| Project | SPDX | Commercial use | Modification | Attribution | Distribute modified | Status |
|---|---|---|---|---|---|---|
| algorithm-visualizer | **MIT** | ✅ | ✅ | Keep notice | ✅ | ✅ VERIFIED |
| **tracers.java** | **NONE** | ❌ | ❌ | — | ❌ | 🔴 **NO LICENSE** |
| **algorithms** | **NONE** | ❌ | ❌ | — | ❌ | 🔴 **NO LICENSE** |
| algorithm-visualizer/server | **NONE** | ❌ | ❌ | — | ❌ | 🔴 **NO LICENSE** |
| CodeAnimator | **MIT** | ✅ | ✅ | Keep notice | ✅ | ✅ VERIFIED |
| code-video-generator | **Apache-2.0** | ✅ | ✅ | Notice + NOTICE file + state changes | ✅ | ✅ VERIFIED |
| arrayviz | MIT *(PyPI classifier only)* | ✅ | ✅ | Keep notice | ✅ | ⚠️ **source repo deleted** |
| Manim Community | **MIT** | ✅ | ✅ | Keep notice | ✅ | ✅ VERIFIED |

### The critical license finding

`tracers.java`, `algorithms`, and `algorithm-visualizer/server` have **no
license file** — GitHub's API reports `"license": null` for all three.

Under default copyright law, **no license means all rights reserved**. A
public repository is not a grant of rights. We may read this code; we may
**not** copy, adapt, redistribute, or vendor it. This alone disqualifies
`tracers.java` from options A (dependency) and B (adapted component),
independent of any technical assessment.

The parent `algorithm-visualizer` repo being MIT does **not** extend to
these sibling repositories — licenses do not propagate across repository
boundaries within a GitHub organization.

---

## C. Capability matrix

Legend: ✅ provides · ⚠️ partial · ❌ absent · N/A not applicable

| | AlgVis (web) | tracers.java | code-video-gen | CodeAnimator | arrayviz | **Manim CE** | **Code2Shorts today** |
|---|---|---|---|---|---|---|---|
| License | MIT | **NONE** | Apache-2.0 | MIT | MIT* | **MIT** | — |
| Language | JS/React | Java | Python | Python/TS | Python | Python | Python + Java |
| Real code execution | ⚠️ server | ⚠️ in-process | ❌ | ❌ | ❌ | ❌ | ✅ **real JVM** |
| Structured exec. trace | ⚠️ viz commands | ⚠️ viz commands | ❌ | ❌ | ❌ | ❌ | ✅ **canonical** |
| Array visualization | ✅ | ✅ emits | ❌ | ❌ | ✅ | ✅ `Table` | ❌ **missing** |
| Pointer visualization | ✅ | ✅ emits | ❌ | ❌ | ✅ | ✅ `Arrow` | ❌ **missing** |
| Variable state display | ✅ | ✅ emits | ❌ | ❌ | ⚠️ | ✅ `Text`/`Table` | ⚠️ text only |
| Code highlighting | ✅ | N/A | ✅ | ✅ | ❌ | ✅ **`Code`+`code_lines`** | ❌ **missing** |
| Manim support | ❌ | ❌ | ✅ | ✅ | ✅ | **is Manim** | ✅ |
| MP4 output | ❌ browser | ❌ | ✅ | ✅ 1080p60 | ✅ | ✅ | ✅ **1080×1920** |
| 9:16 vertical | ❌ | N/A | ❌ | ❌ | ❌ | ✅ configurable | ✅ |
| Deterministic | ⚠️ | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ **enforced** |
| Multi-language ready | ✅ 6 langs | Java only | N/A | display only | N/A | N/A | ✅ adapter seam |
| Usable as dependency | ❌ app | ❌ **no license** | ⚠️ stale | ❌ app | ❌ **repo gone** | ✅ **already is** | — |
| Safe to adapt code | ✅ MIT | ❌ **no license** | ✅ Apache-2.0 | ✅ MIT | ⚠️ unverifiable | ✅ MIT | — |
| Maintenance | 2024-06 | **2019 (dead)** | **PyPI 2021** | 2026-02 (7★) | **deleted** | active | — |
| **Recommendation** | 🔵 REFERENCE | 🔴 **DO NOT USE** | 🔵 REFERENCE | 🔵 REFERENCE | 🔴 **DO NOT USE** | 🟢 **DIRECT REUSE** | — |

\* MIT per PyPI classifier; source repository deleted, so unverifiable at source.

---

## D. Architecture compatibility

### D.1 algorithm-visualizer + tracers.java

**Problem it solves:** in-browser algorithm visualization from annotated
source.

**What it could replace:** nothing we have. Its "trace" is a stream of
*visualization commands* (`array1DTracer.select(i)`), not a record of
program state.

**Does it execute real Java?** Yes — but the algorithm author must
**manually annotate** the source with tracer calls. That is fundamentally
incompatible with Code2Shorts: our trace comes from *automatic AST
instrumentation* of ordinary Java (Phase 2, ADR-002). Requiring hand-placed
visualization calls would mean the LLM (or a human) authors the
visualization intent, reintroducing precisely the "AI decides what
happened" failure mode the architecture forbids.

**Mappable to our ExecutionTrace?** Backwards. Their model is already a
*presentation* decision; ours is an *observation*. Converting theirs to
ours would be lossy and would embed presentation choices into the trace
layer.

**Coupling risk:** high. Browser-oriented, no license on the tracer, dead
since 2019.

**Verdict:** 🔵 **REFERENCE ONLY** — for its visual vocabulary (what a good
array/pointer visualization *looks* like) and its 6-language tracer lineup
as evidence the pattern generalizes. No code copied (and legally, none may
be).

### D.2 code-video-generator

**Problem it solves:** narrated code-walkthrough videos in Manim.

**What it could replace:** our code-highlighting need — on paper.

**Why not:** last PyPI release **v0.5.0, 2021-09-20**, written against
Manim ~0.10; we run **0.21.0**, eleven minor versions later with breaking
changes across `Code`/`Text` APIs. It also pulls **`librosa`** (a heavy
audio-ML stack with numba/scipy) for features we would never use. Adopting
it means owning a 5-year-old integration against a moving API for
functionality Manim now provides natively.

**Verdict:** 🔵 **REFERENCE ONLY** — its comment-driven walkthrough pacing
is a good idea worth learning from. Apache-2.0 would permit adaptation if
we ever wanted a specific routine; we currently don't need one.

### D.3 CodeAnimator

Web app (FastAPI + React) that animates **static** code — no execution, no
state, no arrays or pointers. 7 stars. Solves ~15% of our problem.

**Verdict:** 🔵 REFERENCE ONLY (per the brief's own rule: "if it solves
only 20%, don't introduce it").

### D.4 arrayviz

The closest match on paper — Manim, arrays, pointers, MIT. **Its GitHub
repository is deleted (404).** A dependency whose upstream source has
vanished cannot be audited, patched, or trusted; the only artifact is an
orphaned v0.0.1 wheel.

**Verdict:** 🔴 **DO NOT USE.** This is a supply-chain red flag, not a
maintenance inconvenience.

### D.5 Manim Community — the decisive finding

Before adding any third-party visualization package, I checked what our
**existing** dependency already provides, and verified it empirically
rather than from documentation:

```
manim 0.21.0
  Code(code_string=..., language="java", add_line_numbers=True)  -> works
    .code_lines      -> individually addressable  => line highlighting
    .line_numbers    -> present
  Table([["O","L","L","E","H"]])                                 -> works
    .get_entries((row, col))  -> addressable cell => array tiles + pointers
  Arrow, SurroundingRectangle, Indicate, Transform, VGroup       -> present
```

Every visual primitive Phase 4.2 needs — **array tiles, index labels,
pointer arrows, cell highlighting, swap transitions, and Java
syntax-highlighted code with per-line addressing** — is already available
in a dependency we ship, under MIT, actively maintained, and already
proven in production by Phase 4.1's real render.

**No external visualization library is required.**

---

## E. Reuse recommendations

| Capability | Decision | Rationale |
|---|---|---|
| Java tracing | **BUILD** *(already built)* | Phase 2 AST instrumentation works, is deterministic, needs no source annotation. Alternatives are unlicensed (tracers.java) or research prototypes (JavaWiz/JIVE). |
| Execution state | **BUILD** *(already built)* | `ExecutionTrace` is canonical and language-neutral by design. No external model is a better fit — theirs are presentation streams, not observations. |
| Array visualization | **DIRECTLY REUSE** Manim `Table`/`VGroup` + **BUILD** thin mapping | Primitives exist; only the trace→tile mapping is Code2Shorts-specific. |
| Pointer visualization | **DIRECTLY REUSE** Manim `Arrow` + **BUILD** thin mapping | Same. |
| Code highlighting | **DIRECTLY REUSE** Manim `Code.code_lines` | Native, Java-capable, per-line addressable. Supersedes code-video-generator entirely. |
| Manim rendering | **DIRECTLY REUSE** Manim CE | Already a proven dependency (Phase 4.1). |
| Video composition | **BUILD** *(already built)* | `MediaComposer` + FFmpeg, working and hardened in Phase 4.1. |
| Narration | **BUILD** *(contracts exist)* | `TTSProvider` seam done; a vendor slots in later. |
| Subtitle generation | **DEFER** | Not required for Phase 4.2. `faster-whisper` is the obvious later candidate. |

**Net new production dependencies: zero.**

---

## F. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Copying unlicensed code (tracers.java/algorithms) | **Critical** — legal | Explicitly forbidden; recorded here and in the ADR. Reference-only means *reading*, not vendoring. |
| Adopting a deleted-upstream package (arrayviz) | **High** — supply chain | Rejected. |
| Manim API churn breaking our renderer | Medium | Already our exposure since Phase 4.1; unchanged by this decision. Mitigated by the trusted-renderer seam — `build_scene_source` is the single point of contact. |
| Building visuals ourselves takes longer than adopting | Low | The mapping layer (~300–500 lines) is Code2Shorts-specific; *no* external project could supply it, since it is defined by our trace model. |
| Visual quality falls short of the reference target | Medium | Explicit Phase 4.2 acceptance criteria + frame inspection, as in Phase 4.1. |

---

## G. Maintenance concerns

- `tracers.java`: **7 years without a commit.** Adopting it would mean
  owning it outright — with no legal right to do so.
- `code-video-generator`: 5 years since the last PyPI release, against a
  fast-moving Manim API.
- `arrayviz`: upstream **gone**.
- `CodeAnimator`: 7 stars, single maintainer, bus factor 1.
- **Manim CE**: large contributor base, active releases, already load-bearing
  for us. The only candidate whose maintenance profile we would willingly
  bet three to five years on.

---

## H. Security concerns

The Phase 4 trust model is non-negotiable and every candidate was assessed
against it:

- **algorithm-visualizer/server** executes user-submitted code server-side.
  We already solved sandboxed execution properly in Phase 1 (isolated
  workspace, fixed-argument subprocesses, timeouts, whole-tree kill). Its
  model is unlicensed *and* weaker than ours.
- **`tracers.java`'s annotation model** would let whoever writes the
  algorithm (potentially an LLM) decide what the visualization shows — a
  direct violation of "AI proposes, validation decides, trusted code
  executes."
- **Manim as a library** introduces no new execution surface: we already
  generate scene source ourselves from validated plan data and run it via
  the trusted subprocess path. Using *more* Manim primitives inside
  `build_scene_source` does not widen that surface at all — the generated
  code stays repository-owned, and no field of `VisualizationPlan` becomes
  executable.

**Conclusion: the recommended path changes the security posture by
exactly nothing.**

---

## I. Dependency concerns

Adopting `code-video-generator` would add `librosa`, `ffmpeg-python`,
`wrapt`, and `pyglet` transitively — an audio-ML stack for a
code-highlighting feature Manim already ships. Adopting `arrayviz` would
add an unauditable orphan.

The recommendation adds **no new dependency**, keeping `pyproject.toml`
unchanged and the `[render]` extra exactly as Phase 4.1 left it.

---

## J. Final recommendation

> **Reuse Manim Community's primitives directly. Build only the
> Code2Shorts-specific mapping from `ExecutionTrace` to those primitives.
> Adopt no new third-party dependency. Copy no code from any unlicensed
> repository.**

The instinct behind this phase was right — don't reinvent the wheel. The
investigation's actual finding is that **we already installed the wheel in
Phase 4.1 and hadn't noticed how much of it we were using.** The gap
between our current text-card output and the reference-quality target is
not a missing library; it is a missing ~300–500 line mapping layer that is
inherently ours to write, because it is defined by our own trace model.

Everything examined either (a) solves a problem we already solved better,
(b) cannot be legally used, (c) has no maintained upstream, or (d) is
superseded by a dependency we already ship.

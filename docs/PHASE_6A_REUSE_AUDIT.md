# Phase 6A — data-structure visualization reuse audit & decision gate

Research and decisions only. **No code, no dependencies, no implementation.**

---

## 1. Executive summary

The audit set out to choose a visualization library for HashMap, Stack,
Queue and Binary Search. It found that **the binding constraint is not
visualization at all — it is instrumentation**, and that finding changes
what Phase 6 should build.

Three results, each backed by running the real tracer (§10):

1. **Binary Search needs nothing new.** `low`/`mid`/`high` are already
   observed as scalars, `nums` as an array, and comparisons as
   `ARRAY_READ` + `CONDITION_EVALUATED`. The existing generic pointer rule
   already renders them. Building a `SearchState` abstraction would add a
   type that carries no information the system lacks.
2. **HashMap and Stack are invisible to the current tracer.** A real
   `Map` is captured **once, as `{}`**, and a real `Deque` **once, as
   `[]`**. Every `put` / `push` / `pop` is a method call, and the
   instrumenter emits events only on assignments and array subscripts.
   The trace does not merely lack detail — it asserts an empty collection
   for the whole run.
3. **Therefore no visualization library can help yet.** A renderer fed
   this trace would faithfully draw an empty HashMap while the algorithm
   solves the problem. The gap is upstream of every candidate evaluated.

Every candidate that can draw a HashMap does so from **author-issued
commands** (`tracer.set(...)`, `MStack(...).push(...)`), which is the
architecture Code2Shorts explicitly rejects. The one project built on the
observed-state model — Python Tutor's Java backend — is **AGPL-3.0** and
therefore unusable as code.

**Recommendation: REJECT all candidates as dependencies. REFERENCE three
for semantics. BUILD a minimal trace-side extension plus two generic
snapshot types — not four.** Zero new dependencies.

## 2. Candidates investigated

| # | Candidate | Why considered |
|---|---|---|
| 1 | Manim CE | already DIRECT REUSE; do its primitives suffice? |
| 2 | `manim-data-structures` (drageelr) | Manim plugin, array/pointer objects |
| 3 | `manim-dsa` (F4bbi) | Manim plugin, has `MStack`, `MGraph` |
| 4 | Algorithm Visualizer | the best-known algorithm visualizer |
| 5 | VisuAlgo | the best-known DS/A teaching visualizer |
| 6 | Python Tutor / `java_visualize` | the only observed-state candidate |

Phase 4.2A/4.3/4.4 decisions were not re-audited (Manim CE, FFmpeg, PyAV,
SRT, SAPI, code-video-generator, CodeAnimator, tracers.java, arrayviz).

## 3. License verification

Verified from the authoritative source (the repository's own licence
display or the project's own terms page), not from README wording.

| Candidate | Licence | Verified via | Consequence |
|---|---|---|---|
| Manim CE | MIT | already verified Phase 4.2A | reuse stands |
| `manim-data-structures` | **MIT** | GitHub repo licence display (drageelr/manim-data-structures) | copying permitted |
| `manim-dsa` | **MIT** | GitHub repo licence display (F4bbi/manim-dsa) | copying permitted |
| Algorithm Visualizer | **MIT** (umbrella repo) | GitHub repo licence display | copying permitted; note `tracers.java` was separately unlicensed — rejected in Phase 4.2A |
| VisuAlgo | **Proprietary** | visualgo.net terms | **no copying, no forks, no derivatives** |
| Python Tutor (core) | MIT | project repo | copying permitted |
| `java_visualize` (Java backend) | **AGPL-3.0** | GitHub repo licence display | **strong copyleft — cannot be used in this project** |

VisuAlgo's own terms state that downloading its client-side files and
hosting them "constitutes plagiarism", and that forking or creating
variants is not permitted. It is usable only as something to *look at*.

## 4. Maintenance analysis

| Candidate | Stars | Activity | Assessment |
|---|---|---|---|
| Manim CE | very large | active | healthy |
| `manim-data-structures` | ~90 | v0.1.7, last release Jan 2023; not archived | **effectively dormant** |
| `manim-dsa` | ~87 | ~24 commits total | young, small, single-maintainer |
| Algorithm Visualizer | ~48.7k | active, not archived | healthy but architecturally wrong (§6) |
| VisuAlgo | n/a | actively hosted | closed |
| `java_visualize` | ~75 | not archived, long dormant | stale |

Popularity was deliberately not treated as a decision input. Algorithm
Visualizer is by far the most starred candidate and is still rejected as a
dependency, for architectural reasons.

## 5. Capability matrix

Percentages are avoided where they would be invented. "Provides?" is a
factual statement about what the documented API contains.

| Capability | Manim CE | manim-data-structures | manim-dsa | Algorithm Visualizer | VisuAlgo |
|---|---|---|---|---|---|
| Array + index labels | via primitives | **yes** (`MArray`) | yes | yes | yes |
| Pointer into array | via primitives (we built it) | **yes** (`MArrayPointer`) | yes | yes | yes |
| Sliding window | via primitives | **yes** (`MArraySlidingWindow`) | — | yes | yes |
| **Stack** | via primitives | **no** | **yes** (`MStack`) | yes | yes |
| **Queue** | via primitives | **no** | not documented | yes | yes |
| **HashMap / buckets** | via primitives | **no** | **no** (graph, not map) | yes | yes |
| Binary-search interval | via primitives | partially (window) | — | yes | yes |
| Consumes observed state | n/a (primitive layer) | **no** | **no** | **no** | **no** |
| Python / Manim native | yes | yes | yes | **no** (JS/web) | **no** (web) |

The decisive row is the last two. The only candidates that provide
HashMap and Queue are **web applications driven by author commands**, and
neither is a Python library that could be called from the existing
renderer.

`manim-data-structures` — the closest architectural fit — provides
`MArray`, `MArrayElement`, `MArrayPointer`, `MArraySlidingWindow`,
`MVariable`. That is essentially the set Code2Shorts has already built,
and it contains **none of the four capabilities Phase 6 is about**.

## 6. Observed-state vs author-command analysis

This was the mandatory question, and it separates the field cleanly.

**Author-command** — the programmer inserts visualization calls into the
algorithm; the visual is a side effect of those calls, not of execution:

* Algorithm Visualizer: its own documentation states the tracer libraries
  "extract visualizing commands from code". The algorithm must be rewritten
  around the visualizer.
* `manim-dsa`: fluent construction and animation calls
  (`MStack([3, 7, 98, 1], style=...).add_label(...).move_to(...)`). The
  author states the contents.
* `manim-data-structures`: `MArray(self, [1, 2, 3], label='Arr')` — the
  author supplies the array literal.
* VisuAlgo: a teaching UI; state comes from user interaction.

Adopting any of these as the tracing architecture would invert the
project's central guarantee. Today a frame is *true because the JVM did
it*. Under an author-command model a frame is true because someone wrote
a call saying so — and an LLM writing those calls could fabricate state
that never occurred. That is precisely the failure mode ADR-5.12 exists to
prevent.

**Observed-state** — state is captured from a running program:

* Python Tutor / `java_visualize` is the only one, and it is the right
  model: it renders whatever the program's memory actually contains,
  including collection contents. Its licence (AGPL-3.0 on the Java
  backend) makes reuse impossible, and Code2Shorts already rejected its
  capture mechanism family (debugger/JDI-based) in Phase 2 in favour of
  AST instrumentation. It remains valuable as **conceptual reference**:
  its heap-snapshot-per-step model is close to what `FrameState` should
  grow into for collections.

## 7. Security analysis

| Concern | Finding |
|---|---|
| Executable code generation | None of the Manim plugins generate code; they build Mobjects. No new `exec`/`eval` surface. |
| Subprocesses / shell | None introduced by the plugins; Manim already runs through the single `run_subprocess` seam. |
| Network access | Algorithm Visualizer and VisuAlgo are **web services**. Using either at runtime would send the user's source code to a third party and add a network dependency to a pipeline that currently has none beyond the LLM. Disqualifying on its own. |
| Dependency chain | `manim-data-structures` and `manim-dsa` depend on Manim CE (already present); footprint would be small. Not the reason they are rejected. |
| Supply chain | `manim-data-structures` is dormant since early 2023 and `manim-dsa` is a young single-maintainer package. Adding either to the trust path for capabilities it does not provide is unjustified risk. |
| Provenance | All licences verified from the repositories themselves; VisuAlgo's proprietary terms verified from its own site. |
| Untrusted input | Unchanged — no candidate is adopted. |

No candidate would breach the security model outright, but the two web
services would add a network trust boundary for no benefit.

## 8. Dependency analysis

**Zero dependencies added.** `pyproject.toml` unmodified.

Had `manim-dsa` been adopted for `MStack`, the cost would have been one
small dependency for a shape (a column of boxes with a top marker) that
the existing Manim primitives already draw — Code2Shorts renders array
cells, labels and pointer arrows today. The saving would be cosmetic; the
cost would be a dormant-or-young package inside the render path.

## 9. Architecture compatibility

The renderer is repository-owned and generates Manim source in which every
dynamic value passes through `repr()` as a string literal. A third-party
Mobject library would sit *inside* that generated source, meaning its API
surface becomes part of the trusted rendering path and its objects must be
constructed from validated data. That is possible but buys little, because
the hard part of Code2Shorts' renderer is not drawing boxes — it is
deciding *which* boxes are true.

## 10. Trace compatibility findings — the decisive evidence

Ran the **current** tracer against three real Java programs (compile →
JUnit → JVM → instrumented execution). Nothing was modified.

| Program | Result | Events | What the trace actually contains |
|---|---|---|---|
| Two Sum with `HashMap` | correct output `0,1` | 18 | `seen` appears **once**: `{}`. `nums` `[2, 7, 11, 15]`. **Zero events for any `put`.** |
| Balanced parens with `ArrayDeque` | correct output `true` | 30 | `stack` appears **once**: `[]`. **Zero events for any `push`/`pop`.** |
| Binary search | correct output `4` | 19 | `low`, `mid`, `high` as scalars; `nums` as array; `nums[2]`, `nums[4]` as `ARRAY_READ`; `CONDITION_EVALUATED` ×5 |

**What already exists**: arrays (reconstructed by replaying
`ARRAY_WRITE`), scalars, pointers (integer scalar that is a valid array
index), conditions, loop iterations, source lines, `repr(Object)` in the
runtime helper — which already renders a `Map` as `{2=0, 7=1}` and a
`Deque` as `[a, b]` when it is asked to.

**What is missing**: *nothing about maps or stacks specifically* — what is
missing is an **event at the moment a collection is mutated**. The
denylist does not block `HashMap`, `ArrayDeque` or `Stack`; the
instrumenter simply has no emit point for a mutating method call.

**Minimum extension proven necessary** (design only, not implemented):
after a statement that may mutate a tracked collection variable, re-`repr`
that variable and emit the **existing** `VARIABLE_ASSIGN` event with
old/new values. This requires:

* no new `TraceEventType`,
* no new `TraceEvent` field,
* no change to `ExecutionTrace` semantics,

because `repr(Object)` and `assign(name, old, new, line)` already exist.
That is the smallest change that closes the gap, and it should be proven
with a real trace before anything visual is built.

**Deliberately *not* proposed**: reflecting into `HashMap`'s internal
table to show buckets and collisions. It is JDK-version-fragile, it is a
security-relevant reflection surface, and bucket structure is not needed
to teach what Two Sum does. Deferred (§17).

## 11. Capability-by-capability decisions

### HashMap — **BUILD (trace first)**
No candidate supplies it in a form compatible with observed state. The
blocker is that mutations are unobserved. Decision: build the trace-side
observation; render key→value pairs from the observed map contents with
existing primitives. Buckets/collisions deferred.

### Stack — **BUILD (trace first); `manim-dsa` as REFERENCE for visual semantics**
`manim-dsa`'s `MStack` is a reasonable visual vocabulary (labelled column,
top marker) and is MIT, so its *appearance* may be imitated. Its API is
author-command, so it is not adopted. Same trace blocker as HashMap.

### Queue — **BUILD (trace first)**
No candidate provides a Python/Manim queue at all. Same trace blocker.
A queue and a stack differ only in which end is operated on, which is
observable — see §12.

### Binary Search — **NOTHING TO BUILD**
Already fully representable (§10). Introducing `SearchState` would be an
abstraction created because the algorithm has a different *name*, which
§8 of the brief explicitly warns against. What remains is a *renderer*
question — optionally shading the `low..high` interval — derivable from
scalars that already exist, with no new state type and no algorithm branch.

## 12. Recommended architecture — and where I disagree with the proposal

The proposed architecture was:

```
FrameState -> State Mapping -> {ArraySnapshot, MapSnapshot,
                                StackSnapshot, SearchState} -> Manim
```

The shape is right; the leaf set is wrong on two counts.

**Drop `SearchState`.** Proven unnecessary (§10, §11). It would encode an
algorithm, not a data structure.

**Merge `StackSnapshot` and `QueueSnapshot` into one `SequenceSnapshot`.**
Both are ordered sequences whose interesting property is *which end was
just touched*. That end is observable from the mutation, so one type with
an "active end" annotation covers stack, queue and deque, and stays honest
about the fact that `ArrayDeque` is literally the same Java object used
two ways. Two types would force the mapper to guess the author's intent —
and guessing intent is what an observed-state system must not do.

Recommended:

```
ExecutionTrace                  (canonical truth)
      |
   FrameState                   (reconstructed, existing)
      |
  state mapping                 (structural rules only, no algorithm names)
      |
  +---+-----------------+
  |                     |
ArraySnapshot     SequenceSnapshot     MapSnapshot
 (exists)          (stack/queue/deque)  (key -> value)
  |                     |                    |
  +---------------------+--------------------+
                        |
             existing Manim primitives
```

Two new snapshot types, not four. No `*VisualizationEngine` classes: the
renderer keeps dispatching on **what state is present**, never on what the
algorithm is called.

## 13. What Code2Shorts must build itself

1. **Collection-mutation observation** in the Java instrumenter (§10) —
   the prerequisite for everything else.
2. **`MapSnapshot` and `SequenceSnapshot`**, reconstructed from observed
   values the way `ArraySnapshot` already is.
3. **Structural mapping rules** deciding which snapshot a variable
   produces, from its observed shape — never from its name.
4. **Grounding validation** extending the Phase 5.3 rules to map/sequence
   claims.
5. **A no-algorithm-branch test extension** (§14).

## 14. What Code2Shorts should NOT build

* No `HashMapVisualizationEngine` / `StackVisualizationEngine` /
  `BinarySearchVisualizationEngine`.
* No `SearchState`.
* No bucket/collision internals in the first increment.
* No author-command tracing API of any kind — not even internally, since
  it would give an LLM a vocabulary for asserting state.

### Enforcing it structurally

The existing test that fails the build when an algorithm name appears in
`visualization/` should be extended to cover the new modules, plus a new
test asserting that the renderer's dispatch reads only *state presence*.
A practical form: parse the AST of the visualization package and fail on
any comparison whose operand is a known algorithm name or a data-structure
name used as a branch key (`if kind == "hashmap"`), the same way
`test_provider_selection_lives_only_in_the_factory` guards the provider
seam.

## 15. Risks

| Risk | Severity | Note |
|---|---|---|
| **`HashMap` iteration order is unspecified** | **High** | `Map.toString()` order is not guaranteed by the JLS. ADR-005 rejects nondeterminism outright, so this must be resolved before HashMap visuals ship — by preferring `LinkedHashMap` in authored programs, or by sorting keys for display, or by explicitly accepting and documenting per-JDK determinism. Not yet decided. |
| Re-`repr`ing a collection each mutation costs trace size | Medium | Bounded by existing `trace_max_events` / `max_output_size`; a large map printed per step could hit them. Needs measurement. |
| `repr(Object)` on a user object gives an identity hash | Medium | Same defect class fixed in Phase 4.2 for `int[]`. Collections of plain objects would render as `[Main$Node@1b6d]`. |
| Manim layout for a growing map/stack | Low | A presentation problem, solvable with existing primitives. |
| Scope creep into general object-graph visualization | Medium | Python Tutor shows how far this can go. Stay with maps and sequences. |

## 16. Deferred capabilities

Bucket/collision visualization; trees and graphs; general object graphs;
linked lists; recursion/call-stack visualization (note: `call_depth`
already exists in `TraceEvent`, so this may be cheaper than it looks);
priority queues.

## 17. Final recommendation

Proceed to Phase 6 in this order, and **not** in the order the capability
list suggests:

1. **Phase 6B — trace first.** Extend the instrumenter to observe
   collection mutations; prove it with a real HashMap and a real Deque
   trace showing non-empty, changing contents. Decide the iteration-order
   question (§15). No visuals.
2. **Phase 6C — snapshots.** `MapSnapshot` + `SequenceSnapshot` with
   structural mapping and grounding validation.
3. **Phase 6D — render** with existing Manim primitives.

Binary Search needs none of this and could be demonstrated today as a
separate, cheap win.

**Decisions:** DIRECT REUSE — Manim CE (unchanged). REFERENCE — `manim-dsa`
(stack visual semantics), `manim-data-structures` (array pointer/window
semantics), Python Tutor/`java_visualize` (observed-state snapshot model,
AGPL: concepts only, no code). REJECT as dependency — Algorithm Visualizer
(author-command; web service), VisuAlgo (proprietary; forks forbidden),
both Manim plugins (do not provide the needed capabilities; adopting them
would add risk for shapes we already draw). BUILD — the trace-side
observation and the two generic snapshots.

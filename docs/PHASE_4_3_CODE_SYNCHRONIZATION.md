# Phase 4.3 — Code / Execution Synchronization

## Objective

Make the relationship between Java source, the execution trace, and the
highlighted code line **deterministic, accurate and language-agnostic**.

The rule this phase locks in:

> The trace determines **what** executed. The renderer determines **how** it
> looks. The LLM may explain the execution but never decides which source
> line ran.

There is no code path by which a `VisualizationPlan` can nominate a line.
A plan chooses *which trace event* to show; the line comes from that
event's own `line_number` plus the method context it occurred in.

## Focused reuse audit

Scope limited to code-synchronization capability, per the brief. All
evidence gathered on 2026-08-24 from the GitHub/PyPI APIs and by running
the libraries, not from README claims.

### Decision matrix

| Candidate | Capability | License | Maintenance | Manim compat | Architecture fit | Security | Integration cost | Exit cost | Decision |
|---|---|---|---|---|---|---|---|---|---|
| **Manim CE** | `Code`, `code_lines`, `line_numbers_from`, per-line `set_opacity` | MIT (verified) | pushed **2026-08-23**, 40,438 stars | native (is our renderer) | exact | no new surface | none (already a dep) | low (behind `VideoRenderer`) | **DIRECT REUSE** |
| code-video-generator | `HighlightLines` — dims non-highlighted lines | Apache-2.0 (verified) | PyPI **v0.5.0, 2021-09-20** | **broken** | walkthrough, not execution-driven | n/a | high | high | **REFERENCE** (technique only) |
| CodeAnimator | static code animation | MIT (verified) | 2026-02-05, 7 stars | web app, not a library | no execution/state model | n/a | high | high | **REFERENCE** |
| manim-code-blocks | animated code blocks | MIT (PyPI only) | repo **404 deleted**, 2022 | unknown | n/a | supply-chain risk | n/a | n/a | **REJECT** |
| Custom | trace to location to window | — | ours | — | exact | controlled | low | none | **BUILD** (the mapping only) |

### Decisive evidence

**Manim provides the primitives** (verified by execution, not docs):

- `code_lines` is a `Paragraph` of per-line `VGroup`s — individually
  addressable; `SurroundingRectangle` and `set_opacity` both work.
- `line_numbers_from=N` labels the first rendered line **N**, which is
  exactly what a *window* over lines 38-46 needs to stay honest.
- There is **no** built-in windowing/scrolling — that is the genuine gap,
  and it is a few dozen lines of slicing, not a library.

**code-video-generator cannot be a dependency.** Its `HighlightLines`
targets `code.code` and `code.line_no_from`. Both verified **missing in
Manim 0.21**:

```
.code             MISSING in Manim 0.21
.line_no_from     MISSING in Manim 0.21
.code_lines       EXISTS
```

It would fail immediately. Its *dimming* idea is good and was adopted as a
**design technique**, re-implemented in a handful of lines against the
current API; no code was copied, so no Apache-2.0 NOTICE obligation is
incurred.

**manim-code-blocks** repeats the `arrayviz` pattern from Phase 4.2A —
MIT on PyPI, **repository deleted**, last release 2022. Rejected on
supply-chain grounds.

## Architecture

```
ExecutionTrace          canonical — what actually happened
      |
      v
SourceLocation          file + line + method + class   (core/models.py)
      |
      v
CodeState               visible window + highlight     (visualization/code_state.py)
      |
      v
code_panel()            trusted Manim source           (visualization/primitives.py)
      |
      v
Manim Code(line_numbers_from=start)
```

Dependency direction is enforced: `core` has no visualization imports, and
no Manim type appears in any domain or state model.

### The correctness bug this phase fixed

`TraceEvent` carried `line_number` but **no file identity**. Reverse String
spans `Main.java` and `ReverseString.java`, so its trace contains "line 5"
and "line 6" *from different files*. Phase 4.2 rendered one file while
highlighting numbers largely originating in the other — the highlight was
against the wrong source.

`SourceLocation` resolves this. File identity is **derived** from method
context rather than stored per event: `METHOD_ENTER` carries
`"Class.method"`, so the innermost open method determines the file, and
events before any entry belong to the instrumented entry point. This needs
**no change to the trace runtime or instrumenter**, and is exact for the
supported Java subset — nested/anonymous classes and lambdas, the only
constructs that would break the assumption, are already rejected at
instrument time by `DenylistValidator`.

### Code window

Long files are **windowed, not shrunk** — scaling a 200-line file into a
9:16 band makes every line unreadable. `window_radius` (default 6, giving
13 lines) is configurable; short files render whole; the window clamps at
both file ends so the executing line stays visible; source text is never
altered.

### Trace schema versioning

`TRACE_SCHEMA_VERSION = 2`, plus `instrumenter_version`, `language_version`
and `source_hash` on `ExecutionTrace`. Phase 4.2 silently changed array
value rendering (`String.valueOf` to `repr`) with nothing recording that a
trace predated the change. Version 1 = pre-4.2, version 2 = current. No
migration framework — a version field and provenance are sufficient now.

## Test evidence

`pytest` — **216/216 passed** (164 unit-only, in 2.6s).

New: 22 code-synchronization tests + 6 hostile-input tests for the code
channel. Coverage includes basic mapping, sequential order, repeated loop
lines, branches, nested loops, cross-file method transitions, window
clamping/determinism, unicode, and schema round-trip.

## Real-render evidence

All five algorithms, real Maven, JUnit, JVM, trace, window:

```
correct            schema v2, 25 events, 2 files, 23 lines checked, 0 mismatches
palindrome         schema v2, 30 events, 1 file,  28 lines checked, 0 mismatches
two_sum            schema v2, 17 events, 1 file,  15 lines checked, 0 mismatches
move_zeroes        schema v2, 67 events, 1 file,  65 lines checked, 0 mismatches
remove_duplicates  schema v2, 37 events, 1 file,  35 lines checked, 0 mismatches
```

**166 events, zero mismatches** — every highlighted line is byte-identical
to the real source line at that file+line.

Semantic spot-checks:

| Algorithm | Event | Highlighted line |
|---|---|---|
| Palindrome | `CONDITION_EVALUATED` x3 | `if (a != b) {` |
| Two Sum | `CONDITION_EVALUATED` | `if (a + b == target) {` |
| Move Zeroes | `LOOP_ITERATION` x5 | `for (int fast = 0; fast < nums.length; fast++) {` |
| Reverse String | `ARRAY_WRITE chars[3]=E` | `chars[right] = tmp;` (line 13, ReverseString.java) |

Rendered MP4s inspected frame-by-frame (1080x1920 h264):

- **Reverse String** — window shows lines 7-19 of *ReverseString.java*
  (correct file), line 13 boxed at full opacity with the rest dimmed;
  array cell 3 highlighted; `right` pointer at 3. All three agree.
- **Palindrome** — window lines 2-14, `char a = chars[left];` highlighted
  while the caption reads `chars[0] -> R` and cell 0 is outlined as a read.

A note on method: a downscaled frame initially *looked* like an off-by-one
in the line labels. That was checked programmatically rather than accepted
or dismissed — `line_numbers_from` and the trace-to-line mapping both
verified correct, and the misread was in the eyeballing, not the code.
Small-image inspection is a weak instrument; the 166-event check is the
authoritative one.

## Performance

| Stage | Seconds |
|---|---|
| Java compile | 1.37 |
| JUnit | 2.07 |
| Execute + trace | 2.04 |
| Manim render | 18.75 |
| Synthetic audio | 0.04 |
| FFmpeg compose | 0.05 |
| **Total** | **24.66** |

Render time rose from ~10.8s to ~18.8s: a fresh `Code` mobject is now built
per step (the window scrolls), where previously one static panel was built
once. That is the cost of real synchronization; not optimized, recorded as
baseline.

## Known limitations

- **Code panel readability** is improved (windowing plus font size 20) but
  remains the tightest constraint in a 9:16 frame.
- **File derivation** relies on method context. Correct for the supported
  subset; nested/anonymous classes would need explicit per-event file
  identity from the instrumenter.
- **No column information.** `SourceLocation.column` exists but is always
  `None` — the instrumenter does not emit columns, so sub-line highlighting
  is not possible yet.
- **Per-step `Code` rebuild** is the main render cost; caching identical
  windows across consecutive steps is the obvious optimization.

## Deferred work

Sub-line/column highlighting; window caching; smooth scroll animation
between windows; non-Java adapters supplying `SourceLocation`.

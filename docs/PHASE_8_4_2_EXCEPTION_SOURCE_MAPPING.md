# Phase 8.4.2: Exception Source-Line Mapping

**Verdict: PASS WITH LIMITATIONS.** Every case the brief requires is
verified. The limitations are in section 14.

## 1. Problem

With the default instrumenter backend, an exception's line in the canonical
trace (`ExceptionInfo.thrown_at_line`, and the `EXCEPTION_THROWN` event's
`line_number`) was a line of the **instrumenter's re-printed source**, not
the user's file. The code panel highlights that line, so it pointed at the
wrong code, or at a line the user's file does not have.

Phase 8.4 found it on the `trace_samples/exception` fixture: user line 10,
reported as 25.

## 2. Reproduction

The `trace_samples/exception` fixture, run through the real path
(instrumenter jar, Maven compile, JVM). Full log:
`output/phase8_4_2/repro_before.txt`.

```
original  10:         int value = tiny[5];
printed   25:         int value = tiny[5];
trace:    thrown_at_line=25 thrown_in_method='boom'   (expected 10)
```

The shift is not constant: in the same file, `main` moves +1 and `boom`
moves +13 to +15.

The new tests found two more wrong cases from the same root cause:

| Case | Reported before | Correct |
|---|---|---|
| Exception raised inside the JDK (`Integer.parseInt`) | line 67 of `Integer.java`, method `parseInt` | the user's call site |
| Trace-limit exception | line 343 of the runtime helper, method `checkLoopIterations` | user line 10 (`while (true)` in `spin`) |

Logs for the trace-limit case: `output/phase8_4_2/trace_limit_before.txt`
and `trace_limit_after.txt`.

## 3. Root cause

1. `Instrumenter.main` prints the rewritten AST with `unit.toString()`.
   JavaParser re-prints the **whole file**: it reformats it and inserts
   trace calls, wrappers and temporaries. Line numbers change by a
   different amount at every point in the file.
2. Ordinary trace events were never affected. Each call carries its
   original line as a literal, taken from the parse of the user's file.
3. `Code2ShortsTrace.exceptionThrown` read `getStackTrace()[0].getLineNumber()`.
   That is the JVM's line for the **compiled instrumented file**, and the
   top frame was not even necessarily user code.
4. `normalizer.py` copies `thrown_at_line` into the event's `line_number`.

## 4. Failing test

`tests/test_exception_source_mapping.py` uses the production path, with no
mocks.
- Each program marks its throwing line with `// THROWS`, so expected lines
  are read from the source, not hard-coded.
- An independent oracle runs the same source **uninstrumented on a plain
  JVM** (`java Main.java`) and checks that the JVM's own stack-trace line
  equals the marker.

Before the fix, 13 tests failed and every failure was a wrong line. Log:
`output/phase8_4_2/tests_before_fix.txt`.

| Case | Reported | Expected |
|---|---|---|
| direct throw | 19 | 9 |
| nested | 66 | 30 |
| instrumentation-heavy | 41 | 20 |
| try/finally | 67 (inside the JDK) | 14 |
| existing sample | 25 | 10 |
| … | | |

All 9 oracle checks passed both before and after the fix, so the markers
agree with javac's line table.

## 5. Mapping design

There is no offset arithmetic anywhere. The mapping is an explicit table
built from the actual printed output:

```
printed (instrumented) line --LineMap table--> original line --> ExceptionInfo / EXCEPTION_THROWN
```

1. **Origins.** `SourceInstrumenter` records the original line of every
   statement:
   - user statements: from the parse, before anything is rewritten;
   - generated statements: where they are built, with the line of the user
     statement they were generated for. That covers trace calls, `var`
     temporaries, return wrappers and the main try/catch wrapper.

   The map is identity-keyed, because JavaParser's `Node.equals` is
   structural.
2. **Pairing.** `LineMap` prints the unit and parses the printed text
   again. Printing then parsing preserves the statement tree, so the n-th
   statement of the reparse is the n-th statement of the unit.
   - Statements are walked in **syntax order**, using JavaParser metamodel
     properties.
   - The first implementation used `findAll`, which follows the internal
     child list. The node-kind check rejected that loudly
     ("WhileStmt vs ExpressionStmt"), because replaced and moved nodes sit
     at a different place in that list than where they print.
   - Statement kinds are compared pair by pair, and any mismatch aborts
     instrumentation (`INSTRUMENTATION_ERROR`). The table is never guessed.
3. **Table.** Each printed statement's line span is filled with its origin,
   outer statements first, so the innermost statement wins. A line with no
   user code maps to 0.
4. **Delivery.** One static initializer per top-level class:
   `static { Code2ShortsTrace.lineMap("pkg.Type", "0,0,3,…"); }`.
   - It is added *before* the table is computed. Its string literal is
     always one printed line, so filling it in moves nothing.
   - The final text is re-checked against the table.
   - It runs before the class executes any code.
5. **Runtime.** `exceptionThrown` picks the **innermost frame of a class
   that registered a map** (the user's own code) and translates that frame's
   line through the table.
   - If the printed line has no user code, `thrown_at_line` is omitted
     rather than reported wrong.
   - With no registered class at all (hand-instrumented sources), behaviour
     is exactly as before: the top frame and its raw line.

Neither the `ExecutionTrace` contract nor the Python normalizer changed. The
fix lives entirely in trusted tooling: the instrumenter and its runtime
helper.

## 6. Implementation

| File | Change |
|---|---|
| `tools/java-instrumenter/.../SourceInstrumenter.java` | `origins` identity map; `generated(line, node)` tags all 16 `parseStatement` sites and the main wrapper |
| `tools/java-instrumenter/.../LineMap.java` | **New.** Builds, registers and verifies the table |
| `tools/java-instrumenter/.../Instrumenter.java` | Prints `LineMap.apply(unit, origins)` instead of `unit.toString()` |
| `src/code2shorts/langadapter/java/resources/Code2ShortsTrace.java` | `lineMap()` registry, `userSite()` and `originalLine()`; `exceptionThrown` uses them |

## 7. Test matrix

Results come from the real path on Windows with JDK 17 and Maven.

| Case (brief) | Program | Result |
|---|---|---|
| A: direct throw | `throw` in main after a loop | PASS |
| B: called method | existing `exception` sample (line 10); recursion 4 frames deep; second file (`Helper.java`) with its own table | PASS |
| C: try/catch | caught exception: no event, later lines correct; uncaught throw inside `try/finally` | PASS |
| D: nested control flow | if, inside for, inside if, in a method called from inside if, inside while | PASS |
| E: instrumentation-heavy | 12 instrumented statements, blank lines and a comment before the throw | PASS |
| No constant offset | two sites shift by different amounts; JUnit `theShiftIsNotConstant` | PASS |
| Boundary: generated wrapper | `return values[at];` (evaluated inside `var __c2s_ret`) | PASS |
| Boundary: inserted read | `dst[0] = src[7];` (the read is evaluated by an inserted statement) | PASS |
| Boundary: condition | `while (v[i] > 0)` (inside `Code2ShortsTrace.condition(...)`) | PASS |
| Boundary: JDK frame, first statement, last line of file | `Integer.parseInt` in a method whose only statement is on the file's last code line | PASS |
| Boundary: trace limit | `infinite_loop`, loop limit 50 | PASS |
| Downstream | `resolve_source_locations` then `build_code_state`: the highlighted line is the `// THROWS` line in `Helper.java` | PASS |

- **New tests:** 25 pytest and 4 JUnit (`LineMapTest`). The JUnit module
  is now 19/19.
- **Video check:** the `exception` sample was rendered through the real,
  unmodified `ManimVideoRenderer` (1080×1920, 6.77 s), using a
  deterministic two-step plan as `run_golden_path.py` does. The frame at
  the exception step shows the code panel highlighting
  **line 10, `int value = tiny[5];`**.
  - Output: `output/phase8_4_2/video/exception_step.png`.
  - The production pipeline only renders completed traces, so this check
    was renderer-level (no TTS or composition).

## 8. Golden regression

For 9 programs, events were compared against the instrumenter events
recorded in Phase 8.4 (`tests/fixtures/jdi/*.json`) on **every field**, with
3 runs each. Log: `output/phase8_4_2/golden_regression.txt`.

| Program | Events | Identical to Phase 8.4 | Deterministic ×3 |
|---|---|---|---|
| palindrome | 30 | yes | yes |
| two_sum | 17 | yes | yes |
| remove_duplicates | 37 | yes | yes |
| reverse_string | 25 | yes | yes |
| move_zeroes | 67 | yes | yes |
| nested_loops / recursion / control_flow / for_loop | 58 / 18 / 7 / 32 | yes | yes |

Nothing changed in ordering, types, values or lines. The E2E golden-path
gate passes in the full suite.

## 9. JDI regression

No JDI code changed. Everything under `tests/test_jdi_backend.py` passed in
the full suite, including the 6 live tests against cs1302-tracer 3.2.0.

The two backends now agree on the exception line: the JDI live test
already asserted `thrown_at_line == 10`, and the instrumenter now reports 10
too.

**PASS.**

## 10. Determinism

`test_exception_mapping_is_deterministic` runs the nested case 3 times:
identical `ExceptionInfo` and `EXCEPTION_THROWN` event each time. The 9
goldens were also byte-identical across 3 runs (section 8).

**PASS.**

## 11. Performance

Instrumentation wall time per file, median of 7, pre-8.4.2 JAR vs the new
one. Log: `output/phase8_4_2/perf_instrumentation.txt`.

| File | Before | After |
|---|---|---|
| palindrome | 232 ms | 279 ms (+47) |
| move_zeroes | 232 ms | 293 ms (+61) |
| nested_loops | 233 ms | 265 ms (+32) |

That is two extra prints and parses per file. It is small next to compile,
JUnit and render, so not a meaningful regression.

## 12. Full suite before/after

Windows, with Maven, JDK 17, FFmpeg and the JDI backend configured.

| | Passed | Failed | Skipped |
|---|---|---|---|
| BEFORE (Phase 8.4.1 result) | 1146 | 4 | 5 |
| AFTER | 1171 | 4 | 5 |

- **+25** are the new tests.
- The failed and skipped sets are **identical**; `Compare-Object` showed no
  difference. They are the 2 comment-grep rule-7 tests, the 2 SAPI timing
  tests, and 5 live-LLM skips, all from before this phase.

Log: `output/phase8_4_2/suite_after.txt`.

## 13. Files changed

| File | Change |
|---|---|
| `tools/java-instrumenter/src/main/java/com/code2shorts/instrumenter/SourceInstrumenter.java` | Modified |
| `tools/java-instrumenter/src/main/java/com/code2shorts/instrumenter/Instrumenter.java` | Modified |
| `tools/java-instrumenter/src/main/java/com/code2shorts/instrumenter/LineMap.java` | New |
| `tools/java-instrumenter/src/test/java/com/code2shorts/instrumenter/LineMapTest.java` | New |
| `src/code2shorts/langadapter/java/resources/Code2ShortsTrace.java` | Modified |
| `tests/test_exception_source_mapping.py` | New |
| `docs/PHASE_8_4_2_EXCEPTION_SOURCE_MAPPING.md` | New |

The rebuilt instrumenter JAR sits in `tools/java-instrumenter/target/`,
which is gitignored. Evidence lives only under `output/phase8_4_2/`.

Not touched:
- the renderer, code panel, JDI, cs1302-tracer, `ExecutionTrace` and the
  Python normalizer;
- fixtures, goldens and earlier-phase output.

`src/code2shorts/execution/sandbox.py` and `tests/test_process_tree.py`
still show as changed. Those are Phase 8.4.1's uncommitted work, not this
phase's.

## 14. Remaining limitations

1. **Multi-line statements map to their first line.** If one statement
   spans several user lines, a throw from its 3rd line reports the 1st.
   Ordinary trace events already use the statement's first line, so the
   code panel stays consistent.
2. **Interfaces cannot hold a static initializer.** A throw inside an
   interface's default or static method is not mapped; it falls back to
   the raw frame line. The supported subset has no such programs.
3. **Hand-instrumented sources keep the old behaviour** (top frame, raw
   line), because they register no table. That applies only to the
   runtime-helper test fixture.
4. **`thrown_in_method` follows the chosen user frame.** A JDK or
   trace-limit exception now names the user's method (`parse`, `spin`)
   instead of `parseInt` or `checkLoopIterations`. That is intended, and
   documented here because the field's value changes for those cases.
5. **Video was verified at renderer level only.** The production pipeline
   does not render failed traces.

## 15. Out-of-scope findings (not fixed)

- **The production pipeline cannot render a failed trace.** An "exception"
  lesson is not a supported video today.
- **The cs1302-tracer JAR still lives in the session scratchpad.** JAR
  provisioning was out of scope, per Step 17.
- **The JDI fixtures' `instrumenter_events`** are still the Phase 8.4
  recordings. They contain no exception events, so they are unaffected.

## 16. Final verdict

**PASS WITH LIMITATIONS**

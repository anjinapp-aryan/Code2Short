# Phase 8.4: JDI Trace Backend (Feasibility, Parity and Security Spike)

**Question:** can cs1302-tracer safely become an **optional second** Java
trace backend behind `LanguageAdapter`?

**Short answer:** yes as an experiment, not for production.

**Labels:**

| Label | Meaning |
|---|---|
| **S** | verified in source |
| **T** | verified by test |
| **R** | verified by run |
| **P** | partial |
| **N** | not verified |

**Artifacts:** everything is new under `output/phase8_4/`, and nothing
existing was overwritten.

| Path | Contents |
|---|---|
| `parity_run1..3/` | instrumenter vs JDI, per golden, 3 JDI runs each |
| `measure_run1/` | per-event-type parity; raw-snapshot determinism; timing |
| `negative_run1..3/` | negative tests |
| `linux_probe/` | Linux isolation probe and its output |
| `windows_probe/` | Windows orphan probe |
| `linux_goldens_run1/` | goldens traced inside Linux |

## 1. Executive Summary

**Parity, behind `CODE2SHORTS_JAVA_TRACE_BACKEND=jdi`:**
- The JDI backend reproduces **164 of the 166 golden events** field for
  field. Descriptions are included, and the two remaining gaps are
  explained (§10).
- Three goldens (palindrome, two_sum, remove_duplicates) are
  **event-for-event identical**, as are five trace samples (nested loops,
  recursion, control flow, for loop, the 20-event budget case).

**The two gaps are genuine information the tracer does not expose:**
1. the return value of a non-pure expression that the caller never stores
   (reverse_string);
2. the `toString()` of a JDK object (move_zeroes' `StringBuilder`).

In strict mode, which is the backend's mode, move_zeroes is therefore
**refused**, not approximated.

**Every derived event is deterministic.** Each transition between two
snapshots is predicted from the JavaParser structure and checked against
the next observed line. A mismatch fails the trace. Three facts about
javac's line tables, found and modeled, make that work (§12).

**Security:**
- **Linux debugger isolation was verified for real** in a Docker
  container:
  - loopback-only JDWP;
  - the listener closes after the guest attaches;
  - third parties and the guest itself are refused;
  - the timeout kills everything.
- **Open limitation, on both Linux and Windows:** if the tracer process
  dies *abnormally*, the guest JVM is **orphaned** and keeps running.
  - Our `run_subprocess` timeout kill prevents this, verified.
  - A tracer crash or self-exit does not.

**Other findings:**
- **Pre-existing defect:** the instrumenter's `thrown_at_line` is a line in
  the *instrumented* source (25, not 10). JDI reports the real line.
- **The JDI path does not reduce code.** It adds about 1,450 lines (Python
  + Java) that re-implement the instrumenter's event semantics on top of
  snapshots. Its value is the heap and object capability (§21), not less
  code.

**Decision: PARITY PASS WITH LIMITATIONS.** The instrumenter stays the
default; JDI stays experimental.

## 2. Current Architecture (source-verified)

```
TraceNode → JavaAdapter.trace()
              ├─ JavaSourceInstrumenter (JavaParser rewrite, tools/java-instrumenter, JDK 17)
              ├─ JavaCompiler (mvn) → JavaExecutor (java Main <input>)  via run_subprocess
              └─ TraceStreamParser → TraceLimiter → build_events / extract_exception → ExecutionTrace
```

Both `TraceNode`s build `JavaAdapter()` with no arguments, and nothing
downstream knows how the trace was made.

## 3. JDI Architecture

```
JavaAdapter.trace()  [backend = "jdi"]
  └─ JdiTraceBackend
       ├─ SourceStructure (NEW main class in the same JavaParser jar, JDK 17)   → structure JSON
       ├─ generic harness  Code2ShortsJdiHarness (reads stdin → Main.main({input}))
       ├─ cs1302-tracer jar (JDK 21+) via run_subprocess, --timeout-ms/--max-snapshots/--max-output-bytes
       ├─ SnapshotDiffer  (snapshots + structure → the runtime's raw events; predict-and-check)
       └─ SAME TraceLimiter → build_events → extract_exception → ExecutionTrace
```

The JDI path feeds the **same normalisation code** the instrumenter uses.
It was not re-implemented.

## 4. Integration Point

**`JavaAdapter.trace()`:**
- the backend is chosen by the constructor argument `trace_backend`;
- otherwise by `Settings.java_trace_backend` (`CODE2SHORTS_JAVA_TRACE_BACKEND`);
- otherwise **`instrumenter`**.

| Case | Behaviour |
|---|---|
| Unknown value | `ValueError` at construction (T) |
| `jdi` without its toolchain | an `INSTRUMENTATION_FAILED` trace with `JDI_BACKEND_UNAVAILABLE: …`. **Never a fallback** to the instrumenter (T) |
| Any JDI failure (structure, tracer, derivation) | `INSTRUMENTATION_FAILED` with a labelled reason, the same status the instrumenter uses when it cannot trace |

**No contract changed:** `ExecutionTrace`, `TraceEvent`, `LanguageAdapter`,
the plans and the renderer are all unchanged.

## 5. JDK 17 / JDK 21 Strategy

- **Coexistence works (R).** Code2Shorts, Maven, the instrumenter and
  SourceStructure keep **JDK 17**. Only the tracer runs on JDK 21, from
  `CODE2SHORTS_JDI_JAVA_HOME`.
- **The global baseline is unchanged.**
- **Discovery:** `JdiToolchain.resolve` reads `<home>/release`
  (`JAVA_VERSION="21.0.11"`) with no process start, and requires
  `bin/java(.exe)` and the tracer jar (`CODE2SHORTS_JDI_TRACER_JAR`).
- **Clear errors (T):** "is not set", "needs JDK 21+" (JDK 17 is refused),
  and the missing-jar message.

| Platform | Result |
|---|---|
| Windows | JDK 21.0.11 (R) |
| Linux | `eclipse-temurin:21-jdk` (21.0.12.1) (R) |

**Deployment implication:** the image needs a second JDK (21) beside 17,
about +200 MB. The tracer jar is built from upstream (pinned commit
`020907c`, version 3.2.0, MIT); it is not vendored.

## 6. Program Argument Strategy

**Native support: none.** `GuestLauncher` sets only `main` on the launching
connector (S).

**Generic harness (`harness_source`):**
- a public class in the entry class's package reads **all of stdin** and
  calls `Main.main(new String[] {input})`;
- that is exactly what `JavaExecutor` does (`java Main <input>` always
  passes one argument, even an empty one).

**The input is never written into source.** It arrives as a file via
`--stdin-file`, so no value can change the harness's code (T: the harness
contains no input or algorithm text).

**Tested inputs:** `HELLO`, `RACECAR`, `9`, `5`, and empty, across all
programs (R). Fixtures are copied, never modified.

## 7. Security Analysis

| Property | Status |
|---|---|
| Only `run_subprocess` (fixed argv, no shell, timeout, tree kill), with the temp `Workspace` | S + AST test |
| No `subprocess`, `os`, `eval`, `exec` or `shell=` in the JDI package | AST test |
| Tracer budgets set explicitly (`--timeout-ms`, `--max-snapshots`, `--max-output-bytes`) | S, R |
| Outer timeout = execution timeout + 60 s, with whole-tree kill | S, R |
| Guest sandboxing | **None.** The tracer states it does not sandbox (README; R: plain `java -classpath <tmp>`). This equals the instrumenter path's trust model: both rely on `run_subprocess` and run code that already passed compile |
| JDWP authentication | **None.** JDWP has only a handshake; isolation rests on loopback, the closed listener and namespaces (§8) |
| **Orphan on abnormal tracer death** | **OPEN (R, both OSes).** SIGKILL / `Process.kill` of the tracer only → the guest is reparented (Linux ppid=1, Windows pid 32388) and spins forever. Our seam's timeout kills the group/tree, so no orphan there (R). A tracer that crashes or exits by itself leaves the guest |

**Required fix before production use** (not done here; it changes the
shared seam): after the tracer returns, *always* kill its process group.
- **Linux:** `killpg` on the session `run_subprocess` already creates.
- **Windows:** a Job Object with kill-on-close.

## 8. Linux Isolation Results

Results are from `eclipse-temurin:21-jdk` under Docker 29.5.3 (R);
`output/phase8_4/linux_probe/probe_output.txt` has the full output.

| # | Check | Result |
|---|---|---|
| 1 | Transport | `-Xrunjdwp:transport=dt_socket,address=localhost:52699,suspend=y`: **socket on loopback, ephemeral port**. The guest connects to the debugger |
| 2 | Bind / listen | After attach: only an ESTABLISHED `127.0.0.1` pair, **no LISTEN socket** |
| 3 | Port | Ephemeral, per run |
| 4 | Authentication | None (JDWP) |
| 5 | Guest → debugger | The guest read its own `-Xrunjdwp` argument (`RuntimeMXBean`), learned the port, connected: **REFUSED** |
| 6 | Another local process attaching | **REFUSED** after attach. The **pre-attach window** (listener open until the guest connects) was not exploited; it is closed by running each trace in its own network namespace, and `--network none` was **verified to work** (R: all 5 goldens traced with `--network none`) |
| 7 | Timeout | `--timeout-ms 3000`: exit 3 after ~4 s, **no java process left** |
| 8 | Tracer crash | **Guest orphaned** (OPEN, §7) |
| 9 | Guest crash | The uncaught exception is reported on stderr, and the tracer exits 0 (R, Windows and Linux) |
| 10 | Process-tree termination | Killing the process group removes tracer and guest (same pgid/sid) (R) |
| 11 | Orphans | Only after abnormal tracer death |
| 12 | Windows | `dt_shmem` (shared memory, no TCP) (R, Phase 8.3 + 8.4) |

The five goldens traced in Linux give the **same 174/176** as Windows (R).

## 9. Five Golden Algorithm Results

| Golden | Instrumenter | JDI | Event Parity | Security | Result |
|---|---|---|---|---|---|
| reverse_string (`HELLO`) | completed, 25 events, `OLLEH` | completed, 25 events, `OLLEH` | 24/25: only `METHOD_EXIT.return_value` absent | §7 | PASS WITH GAP |
| palindrome (`RACECAR`) | completed, 30 | completed, 30 | **30/30 identical** | §7 | PASS |
| two_sum (`9`) | completed, 17 | completed, 17 | **17/17 identical** | §7 | PASS |
| move_zeroes | completed, 67 | **refused** (`JDI_DERIVATION_FAILED: … StringBuilder`) | 66/67 in measurement mode | §7 | GAP (refused, not faked) |
| remove_duplicates | completed, 37 | completed, 37 | **37/37 identical** | §7 | PASS |

All five **execute** under the tracer, on both OSes, with correct program
output.

## 10. Event Parity Matrix

**EXACT** means taken directly from the tracer's output. **DERIVED** means
computed from snapshots plus the AST, deterministically, and checked
against the next observed line.

| Event | Instrumenter | JDI | Classification | Evidence |
|---|---|---|---|---|
| source line | emitted | snapshot `line` | **EXACT** | T, R |
| call depth | runtime counter | user frames − 1 | **DERIVED** | 176/176 |
| local variable state | via events | snapshot locals | **EXACT** | R |
| array state | via events | snapshot heap | **EXACT** | R |
| PROGRAM_START / END | emitted | first / last | **DERIVED** | 10/10 |
| METHOD_ENTER | emitted | frame push + structure `declLine` | **DERIVED** | 5/5 |
| METHOD_EXIT | emitted | `return` statement / frame pop / method brace | **DERIVED** | 5/5 events |
| RETURN value | `repr(expr)` | pure expression on observed state; single-call substitution (`n * f(n-1)`); or the caller's received value | **DERIVED** 4/5 · **MISSING** 1/5 | §11 |
| VARIABLE_ASSIGN | emitted | snapshot diff + structure | **DERIVED** 49/50 · **MISSING** 1 | StringBuilder |
| ARRAY_WRITE | emitted | heap diff at the index evaluated on the pre-state | **DERIVED** | 12/12 |
| ARRAY_READ | emitted | structure access + pure index on the pre-state + observed element | **DERIVED** | 28/28 |
| CONDITION_EVALUATED | emitted | branch taken = observed next line; checked | **DERIVED** | 45/45 |
| loop progression (LOOP_ITERATION + for-counter) | emitted | back-edge / header entry; checked | **DERIVED** | 21/21 |
| EXCEPTION_THROWN | emitted (instrumented line) | the JVM's uncaught-exception report | **DERIVED** (correct line) | §15 |

**Goldens: 164/166 events** excluding start and end, **174/176** including
them. No HEURISTIC counts toward parity: none is used.

## 11. Return Value Analysis

1. **Upstream exposure:** `MethodExitEvent` is handled (S,
   `DebugTraceHelper`), but `returnValue()` is never serialized, and exit
   requests are filtered to the *main class*. With our harness that would
   be the harness, so a patch must also widen the filter.
2. **Existing issue:** none. 9 issues and PRs, none about return values.
3. **Extension mechanism:** none.
4. **Deterministic derivation** covers 4 of 5 goldens and recursion:
   - pure returns (`return answer;`, `return slow + 1;`, `return true;`);
   - single-call returns (`return n * factorial(n - 1);` → 1, 2, 6, 24, 120);
   - the caller's receiving variable.
5. **Minimal patch:** serialize `mee.returnValue()` per frame and remove the
   main-class filter. Roughly tens of lines.
6. **Fork:** required **only** to close the last gap (a value passed
   straight to another call, `println(reverse(input))`).
7. **Maintenance burden of a fork:** tracking upstream (it went 3.2.0 →
   3.2.1 during this spike). Prefer an upstream PR.

**Verdict:** **MINIMAL UPSTREAM CHANGE REQUIRED** for complete return
values. Not done; this phase forbids forking.

## 12. Condition Reconstruction

Conditions are **deterministically derived** (45/45 golden events; all
trace samples).

The taken branch is the one whose first statement the tracer stops on
next. The prediction is **checked**: an unexpected line raises
`DerivationError` (T: a tampered snapshot is refused). Three
javac/line-table facts were found and modeled:

1. **A `for` header is reached once.** The update and later conditions are
   not the line's first location, so there is no snapshot. They are
   derived at the back-edge.
2. **A `while` header is reached every iteration**, since its condition is
   the line's first location.
3. **Closing braces:** when a block that **declares locals** ends, javac
   records its brace line where the scopes close.
   - It is visible only before an instruction without its own position:
     a `while` back-edge `goto`, an `else`-skipping `goto`, or a void
     method's implicit `return` (always).
   - The outermost declaring block wins.
   - Evidence: palindrome and reverse_string stop on the `while` brace;
     nested_loops' inner `while` (no locals) does not.

## 13. Loop Reconstruction

`LOOP_ITERATION` and the per-iteration counter observation are
**deterministically derived**:
- 21/21 on the goldens;
- nested `while` loops (nested_loops, 58/58);
- nested `for` loops (two_sum);
- `for` with early `return` (two_sum).

One line visit is **not** assumed to be one iteration: a `for` iteration
is the back-edge transition, and a `while` iteration is a header visit
followed by body entry.

## 14. Array Read Reconstruction

Array reads are **deterministically derived, without text matching**
(28/28):
- the access (array name and index expression) comes from JavaParser;
- the index is evaluated only when it is side-effect free (names, literals,
  `+ - * / %` with Java int/long semantics, `.length`);
- the element comes from the **observed** pre-statement heap.

Impure indices (`a[i++]`, calls) are refused, never guessed (T).

## 15. Exception Analysis

**Preserved** (from the JVM's own uncaught-exception report, parsed
exactly): class, message (`"null"` when absent, as the runtime prints it),
the throwing method, and the **line** (T, R).

**Lost:**
- caught exceptions, which the instrumenter does not emit either;
- exceptions whose message spans several lines, which are deliberately
  not parsed.

**Which line is right:**
- **JDI:** `thrown_at_line = 10`, the user's source.
- **Instrumenter:** `thrown_at_line = 25`, a line in the **instrumented**
  file.

**This is a pre-existing instrumenter defect.** `normalizer.py` maps that
field into the exception event's `line_number`, so the code panel would
point at a non-existent line. It needs a separate, test-first fix. It was
not changed here.

**Downstream:** `VisualAction.EXCEPTION` exists; structured exceptions are
consumed through the event list.

## 16. Determinism

- **Raw snapshots:** byte-identical across 3 runs per golden (R),
  **including heap object ids**.
- **Derived events:** identical across runs (T, R).
- **Linux vs Windows:** the same derived events (R).

No nondeterminism was found, so nothing was normalised away.

## 17. Performance

Measured, not optimised (R, Windows, Core Ultra 7 265K).

| | Instrumenter | JDI |
|---|---|---|
| Per golden (wall, including JVM start and compile) | 2.0–2.3 s | **1.9–4.0 s** (bimodal, ~2.0 or ~3.8 s) |
| Structure step | — | 0.2–0.4 s per run |
| Output | 17–67 events | 15–48 snapshots, 20–69 KB of snapshot JSON |
| Peak memory | not measured | not measured |

**Verdict: ACCEPTABLE.** The trace stage is seconds, against minutes for
the LLM and render stages.

## 18. Negative Tests

| Case | Instrumenter | JDI |
|---|---|---|
| Malformed Java | INSTRUMENTATION_FAILED | INSTRUMENTATION_FAILED (`JDI_STRUCTURE_FAILED: PARSE_ERROR`) |
| Runtime exception | RUNTIME_ERROR, line 25 (instrumented) | RUNTIME_ERROR, **line 10** (real) |
| Infinite loop | TRACE_LIMIT_REACHED (2,000 events, in-JVM) | **TIMEOUT** (tracer deadline); no guest left |
| Excessive output | TRACE_LIMIT_REACHED (+3 events) | TRACE_LIMIT_REACHED (`output_limit`, 0 events) |
| Excessive trace (budget 20) | 20 events | **20 identical events** |
| Nested methods / recursion (`5`) | 18 events | **18 identical**, including the return chain |
| Nested loops / control flow / for loop | 58 / 7 / 32 | **identical** |

**Process hygiene (R):**
- no Xrunjdwp guest left after any case;
- no `code-tracer*` temp directories left;
- every workspace removed.

**Documented semantic differences** (both fail safely):
- an infinite loop is a TIMEOUT on JDI but a TRACE_LIMIT on the
  instrumenter;
- a limit stop keeps partial events only on the instrumenter.

## 19. Downstream Compatibility

On the three identical goldens, `reconstruct_frames` and
`resolve_source_locations` return **equal** results for the JDI and
instrumenter traces (R).

The only differing `ExecutionTrace` fields are provenance:
- `duration_seconds`;
- `instrumenter_version` = `jdi-cs1302-tracer/derive-1`;
- `stdout`, which has no `TRACE:` lines.

No downstream code reads them, apart from logging the duration.

No downstream test was changed. The full suite runs with JDI configured.

**Required downstream changes: none.**

## 20. Known Limitations

1. **Return values** of non-pure expressions never stored by the caller.
   An upstream change is needed (§11).
2. **JDK object values** (`StringBuilder`, collections' `toString`): the
   trace is refused in strict mode.
3. **Collections** (`COLLECTION_MUTATION`) are not derived: declarations of
   collection types are refused.
4. **Shapes not derived** (all refused with a reason):
   - multi-line statements;
   - several statements on one line;
   - overloaded methods;
   - labelled `break`/`continue`;
   - `do`/`switch`/`try`/for-each.
5. **Orphaned guest** on abnormal tracer death (§7). **Blocks production**
   until the seam kills the group on every exit.
6. **Pre-attach JDWP window** on shared hosts. Mitigation: a network
   namespace per trace (`--network none` verified).
7. **JDK 21** must ship beside JDK 17.
8. **Maintenance:** the derivation (~1,450 lines including the Java tool) is
   a **second implementation** of the instrumenter's event semantics. Any
   new instrumenter event needs a matching derivation rule.

## 21. Heap Capability

The tracer already provides objects, fields, references, aliasing,
generics, inheritance and lists (R; Phase 8.3 capability runs).

None of it was added to the canonical trace (brief §19). The proposal is
`docs/JDI_HEAP_MODEL_PROPOSAL.md`: an optional, observed-only
`heap_snapshots`, with an ADR required.

## 22. Decision

**PARITY PASS WITH LIMITATIONS.** It is a useful experimental backend:
- event-for-event parity on 3/5 goldens and 5 more programs;
- 164/166 golden events;
- determinism proven;
- debugger isolation verified on Linux.

**It is not a PARITY PASS:**
- return values are missing in 1 of 5 goldens;
- 1 of 5 goldens is refused (an object value);
- the orphan-on-crash limitation is open.

**It is not SECURITY BLOCKED:** debugger isolation was demonstrated, and
the remaining risk has a known, testable fix in the existing seam.

**Replacement:** NOT AUTHORIZED. **Default:** instrumenter.

## 23. Future Recommendation

1. **Seam hardening (small, separately approved):** kill the process group
   (POSIX) or Job Object (Windows) after *every* subprocess exit. Add a test
   that kills the tracer and asserts no orphan.
2. **Upstream PR to cs1302-tracer:** serialize `MethodExitEvent.returnValue()`
   per frame and widen the exit filter. Re-run parity and expect 166/166 on
   return values.
3. **Fix the instrumenter's `thrown_at_line` defect**, test first.
4. **Only then decide on heap facts:** an ADR for `heap_snapshots`, with the
   JDI backend still opt-in.

---

```
================================================
PHASE 8.4 RESULT
================================================

Decision:
PARITY PASS WITH LIMITATIONS

Golden algorithms:
3 / 5 event-for-event identical (5 / 5 execute; 4 / 5 produce a trace; move_zeroes refused)

Events:
164 / 166

Return values:
GAP

Condition reconstruction:
PASS

Array-read reconstruction:
PASS

Program arguments:
PASS

JDK strategy:
PASS

Linux isolation:
PASS (debugger endpoint, timeout, group kill verified in Docker) — orphan on abnormal tracer death remains OPEN (§7)

Determinism:
PASS

Performance:
ACCEPTABLE

Regression:
PASS

New tests:
33 (23 offline + 6 live pytest; 4 JUnit)

Existing tests:
1135 passed / 4 failed / 5 skipped (failed and skipped sets identical to the pre-phase baseline: 1106 / 4 / 5)

Replacement:
NOT AUTHORIZED

Default backend:
INSTRUMENTER

Experimental backend:
JDI

Next recommendation:
Harden run_subprocess to kill the process group / Job Object after every exit (closes the orphan risk), then submit the return-value patch upstream to cs1302-tracer and re-run this parity suite.
```

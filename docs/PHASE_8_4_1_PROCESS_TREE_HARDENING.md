# Phase 8.4.1: Process-Tree Hardening

**Verdict: PASS WITH LIMITATIONS.** All required tests were run on both
platforms and pass. The limitations, in section 18, are edge cases outside
the Phase 8.4 finding.

## 1. Objective

Close the Phase 8.4 JDI security finding: *if the JDI tracer process dies
abnormally, the Java program it was tracing can survive as an orphan.*

The fix: when any subprocess run through `run_subprocess` exits, every
process belonging to that execution is terminated and waited for. That
covers normal exit, non-zero exit, timeout, a crashed child, and an
exception in the Python wrapper. It must hold on Windows and Linux.

## 2. Baseline commit

`59ad380`, on branch `feature/phase-8.1-pipeline-parity`. The working tree
was clean before the work started.

## 3. Existing subprocess architecture

There is exactly one shared seam (CLAUDE.md rule 6):
`execution/sandbox.py::run_subprocess`. Every external tool goes through it:

| Caller | Tool |
|---|---|
| `langadapter/java/maven.py` | `mvn` (compile, JUnit) |
| `langadapter/java/executor.py` | `java` (execute) |
| `langadapter/java/trace/instrumenter.py` | instrumenter JAR |
| `langadapter/java/jdi/backend.py` | SourceStructure, cs1302-tracer |
| `visualization/manim_renderer.py` | `manim` |
| `media/composer.py`, `media/audio_forensics.py` | `ffmpeg` |
| `narration/tts.py`, `narration/audio.py` | PowerShell SAPI, Kokoro, ffmpeg |

```
caller
  -> run_subprocess (fixed argv, no shell, stdin=DEVNULL, timeout)
       -> direct child   (e.g. the tracer JVM, mvn.cmd, manim)
            -> descendants (e.g. the guest JVM, java.exe, ffmpeg)
```

So the fix belongs in that one function. No second mechanism was added.

## 4. Root cause of the orphan risk

Before this phase, `run_subprocess` killed the tree **only on timeout**.
- **Windows:** the kill was `taskkill /F /T /PID <child>`. `/T` walks
  parent-pid links, so it cannot find a descendant whose parent has
  already exited.
- **POSIX:** the child led its own process group (`start_new_session`) and
  the timeout called `killpg`. The group was never killed on any other
  exit.

When the tracer crashed, `communicate()` returned normally: the guest JVM
has its own stdio pipes to the tracer, not ours. No timeout fired, so no
kill happened, and the guest kept running. The same was true for any
descendant left behind by a normal or non-zero exit.

Both platforms reproduced this before the fix (section 8):

| Platform | Before the fix |
|---|---|
| Windows | 6 of the new tests failed, including the real-tracer test |
| Linux (Docker) | 8 of 11 failed |

## 5. Windows implementation

The mechanism is a **Job Object** (`_WindowsJob` in `sandbox.py`), built
with standard-library `ctypes`. No new dependency.

1. **Before launch:** create a job with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`.
2. **Launch:** start the child with
   `CREATE_NEW_PROCESS_GROUP | CREATE_SUSPENDED`.
3. **Adopt:** assign the child to the job, then resume it with
   `NtResumeProcess`. Because the child is assigned before it runs a single
   instruction, nothing it spawns can start outside the job. Breakaway is
   not permitted, because the job does not set
   `JOB_OBJECT_LIMIT_BREAKAWAY_OK`.
4. **Terminate:**
   - snapshot the job's process-id list (`JobObjectBasicProcessIdList`);
   - open a `SYNCHRONIZE` handle to each process (this also pins its pid);
   - call `TerminateJobObject`;
   - wait on every handle, bounded by `_REAP_SECONDS`.

   The job's own `ActiveProcesses` count drops to 0 *before* a killed
   process has actually exited. Measured: count 0 while `Popen.poll()`
   still returned `None`. So the code waits on the processes themselves,
   not on the counter.
5. **Close:** close the job handle. Kill-on-close also covers a crash of
   the Python process itself.

**Fallback:** if a job cannot be created, a warning is logged and the
previous `taskkill /F /T /PID` path is used. That path is pid-scoped, never
by name. Adoption failing after the job exists raises an error; it does not
silently degrade.

## 6. Linux implementation

The child still starts with `start_new_session=True`, as before, so it
leads its own process group. Descendants keep that group id after their
parent dies, so `os.killpg` reaches them.

`_kill_process_tree` now:
1. sends `SIGKILL` to the group;
2. reaps the direct child (`process.wait()`), because a zombie still counts
   as a group member;
3. polls `killpg(pgid, 0)` until the group is empty, bounded by
   `_REAP_SECONDS`. Killed descendants are reparented and reaped by init.

`ProcessLookupError` and `PermissionError` end the wait.

There is no `killall` and no `pkill`. An AST test enforces that.

## 7. Cleanup lifecycle

`run_subprocess` now has one `try/finally`, and `_kill_process_tree` runs in
the `finally` on every exit path:

| Path | Sequence |
|---|---|
| Normal / non-zero exit | `communicate()` returns, then the tree is killed and waited for, then the result is returned |
| Timeout | tree kill, drain with `communicate()`, then the timeout result; `finally` kills again (a no-op) |
| Tracer crash | `communicate()` returns when the child dies, then the tree is killed |
| Python exception | the exception propagates; `finally` kills the tree, kills and reaps the direct child if it is still alive, closes the pipes and the job |
| `Popen` itself fails | the job is closed and the exception is re-raised |

The return values, `ProcessResult` fields, timeout result (`returncode=-1`,
`[timed out after …]` suffix), stdout and stderr, `cwd`, `env`,
`stdin=DEVNULL` and the argv contract are all unchanged.

## 8. Tests added

`tests/test_process_tree.py` has 11 tests. They use a tiny Python helper
that builds a real process tree:
- the "tracer" is the direct child;
- its descendants have their own stdio, as a JDI guest does;
- every process writes its pid before the direct child may exit, so the
  checks never race process start-up.

The tests need no JDI, network, LLM, Manim, Kokoro or FFmpeg, except the
one live test. A fixture kills, by pid, every process the test recorded,
even when an assertion fails.

| Test | Brief item | Before fix (Windows / Linux) | After fix |
|---|---|---|---|
| `test_normal_completion_leaves_no_descendant` | A | FAIL / FAIL | PASS |
| `test_non_zero_exit_is_reported_and_leaves_no_descendant` | B | FAIL / FAIL | PASS |
| `test_timeout_kills_the_whole_tree` | C | pass / pass | PASS |
| `test_tracer_crash_does_not_orphan_the_traced_program` | D (key) | FAIL / FAIL | PASS |
| `test_nested_descendants_are_all_terminated` (4 levels) | E | FAIL / FAIL | PASS |
| `test_unrelated_process_with_the_same_executable_survives` | F | FAIL* / FAIL* | PASS |
| `test_repeated_executions_leave_no_orphans` (10 runs) | G | FAIL / FAIL | PASS |
| `test_python_side_exception_still_kills_the_tree` | exception path | FAIL / FAIL | PASS |
| `test_stdout_stderr_cwd_env_and_stdin_semantics_are_unchanged` | semantics | pass / pass | PASS |
| `test_cleanup_never_matches_processes_by_name` (AST) | safety | pass / pass | PASS |
| `test_real_tracer_crash_does_not_orphan_the_guest_jvm` (integration) | JDI | FAIL / FAIL | PASS |

\* Test F failed before the fix only because the execution's own
descendant survived. The unrelated process was never at risk under either
version.

**How the real-tracer test works:** it runs cs1302-tracer 3.2.0 on a guest
program. The guest writes its pid, calls
`ProcessHandle.current().parent().destroyForcibly()`, which kills its own
tracer as a genuine abnormal tracer death, and then spins forever. Before
the fix the guest JVM survived on both platforms. After the fix it is gone
when `run_subprocess` returns.

## 9. Linux results

**Environment:** Docker on a WSL2 kernel, `Linux 6.6.87.2`.
- Image: `python:3.12-slim` plus the Temurin JDK 21.0.12, defined in
  `output/phase8_4_1/linux/Dockerfile`.
- Run options: `--init` and `--network none`.
- Tracer: the real cs1302-tracer JAR.

| Run | Result | Log |
|---|---|---|
| Before the fix | 8 failed, 3 passed | `output/phase8_4_1/linux_before.txt` |
| After the fix, 5 consecutive runs | **11/11 passed each time** | `output/phase8_4_1/linux_after.txt` |

`ps` inside the container after the runs showed no leftover process.

**Linux: PASS.** That includes the real tracer-crash and timeout orphan
scenarios.

## 10. Windows results

**Environment:** Windows 11 Pro 10.0.26200, Python 3.12.10, JDK 21.0.11.

| Run | Result |
|---|---|
| Before the fix | 6 failed. The real-tracer test showed the guest JVM alive after its tracer was killed. |
| After the fix, 5 consecutive runs | **11/11 passed each time** (`output/phase8_4_1/windows_after.txt`) |

Afterwards, a scan of `Win32_Process` for test helpers, tracer JVMs and
guest JVMs found **0** leftovers.

**Windows: PASS.** This was exercised natively, not inferred from code.

## 11. Unrelated-process safety

The test starts an unrelated long-running process with **the same
executable** (`python`), then runs a crash scenario. Afterwards the
execution's processes are dead and the unrelated process is still alive.
This passes on Windows and Linux.

No cleanup path matches processes by name:
- Windows uses the job and, as a fallback, `taskkill /PID`;
- Linux uses the process group;
- the AST test bans `killall`, `pkill` and `/IM`.

**PASS.**

## 12. JDI regression result

With the JDI backend configured, the full suite ran all of these and they
passed:
- every `tests/test_jdi_backend.py` test, including the 6 live ones: golden
  traces, exception, infinite-loop timeout;
- the new real-tracer crash test.

There were no JDI skips. JDI event reconstruction was not touched, so the
recorded-fixture parity tests are unchanged and pass. The Linux tracer path
(JDK 21, `dt_socket` on loopback) was exercised by the real-tracer test in
the container.

**PASS.**

## 13. Instrumenter regression result

The instrumenter is still the default backend. All instrumenter, executor,
Maven, trace and E2E golden-path tests pass, with the same counts as the
baseline.

**PASS.**

## 14. Full-suite before/after

Both runs were on Windows with Maven, JDK 17, FFmpeg and the JDI backend
configured.

| | Passed | Failed | Skipped |
|---|---|---|---|
| BEFORE (`59ad380`, clean) | 1135 | 4 | 5 |
| AFTER | 1146 | 4 | 5 |

The +11 passes are exactly the new tests. The failed and skipped sets are
**identical** (`Compare-Object` found no difference):
- `test_e2e_golden_path::test_no_algorithm_specific_branching_in_the_visualization_engine`
- `test_generalization::test_no_algorithm_specific_branches_in_visualization_package`
- `test_real_narration::test_real_speech_aligns_to_the_visual_timeline`
- `test_real_narration::test_overflow_is_detected_when_speech_exceeds_its_step`
- the 5 skips are live LLM gateway and xAI tests with no credential.

These same 4 failed in Phase 8.4. The first two are comment-grep rule-7
tests; the last two are SAPI timing tests.

Logs: `output/phase8_4_1/suite_before.txt` and `suite_after.txt`.

## 15. Performance impact

Median of 15 runs, with the before and after versions interleaved. Logs:
`output/phase8_4_1/perf_*.txt`.

| Case | Windows before | Windows after | Linux before | Linux after |
|---|---|---|---|---|
| `python -c pass` | 47.3 ms | 46.5 ms | 6.6 ms | 6.5 ms |
| `java -version` | 56.5 ms | 56.2 ms | 24.8 ms | 24.3 ms |
| 1 s timeout, until return | 1168 ms | 1023 ms | 1003 ms | 1004 ms |

- **Startup and cleanup overhead:** negligible, within noise. One first
  Windows run showed +8.6 ms for `java -version`, which did not reproduce
  on the second run.
- **Windows timeout path:** about 150 ms faster, because it no longer
  spawns `taskkill.exe`.
- **Full suite:** 578.7 s after vs 591.5 s before.

## 16. Files changed

| File | Change |
|---|---|
| `src/code2shorts/execution/sandbox.py` | Modified: `_WindowsJob`, `_kill_process_tree(process, job)` with group wait, `run_subprocess` try/finally |
| `tests/test_process_tree.py` | New: 11 tests |
| `docs/PHASE_8_4_1_PROCESS_TREE_HARDENING.md` | New: this report |

Evidence lives only under `output/phase8_4_1/`. No earlier phase's output,
golden trace, video or registry entry was touched. No other source file
changed. CLAUDE.md is unchanged, because rule 6 already describes the
"whole-process-tree kill".

## 17. Out-of-scope findings (not fixed)

- **The Phase 8.4 scratch toolchain had been cleaned up.** The
  cs1302-tracer JAR was rebuilt from upstream commit `020907c`
  (version 3.2.0, the one Phase 8.4 used) into the session scratchpad.
  Live JDI tests need `CODE2SHORTS_JDI_TRACER_JAR` pointing at a durable
  location.
- **The 4 pre-existing suite failures** (section 14) are unchanged.
- **Still open from earlier phases:**
  - the instrumenter exception-line mapping (`thrown_at_line`);
  - the JDI return-value gap and the `StringBuilder` limitation;
  - the 4K E2E, which is blocked by LLM rate limits.

## 18. Remaining limitations

1. **Linux: a descendant that calls `setsid()` or `setpgid()` leaves the
   process group** and would escape `killpg`. Neither the JVM nor the JDI
   guest does this; the Phase 8.4 probe showed the guest in the tracer's
   group. Closing it fully needs cgroups or a subreaper
   (`PR_SET_CHILD_SUBREAPER`), which is beyond this small phase.
2. **Linux: if the Python process itself is SIGKILLed**, nothing runs the
   `finally`. Windows covers this case through `KILL_ON_JOB_CLOSE`; Linux
   would need `PR_SET_PDEATHSIG` or a supervisor.
3. **Linux pid reuse:** after the direct child is reaped, the group id
   could in theory be reused. The kernel does not reuse a pid while it is
   still in use as a process-group id. The residual window needs an empty
   group, a pid wrap-around and a new group leader with that exact id.
4. **Containers without an init:** reaping killed descendants relies on
   init. Without `--init`, the bounded 5 s wait gives up and the zombies
   remain until the container exits. The Linux verification used `--init`.
5. **Windows: the reap wait snapshots at most 1024 process ids.** A larger
   job is still terminated, but not waited on.
6. **Linux was verified in Docker on a WSL2 kernel,** not on bare-metal
   Linux.

## 19. Final verdict

**PASS WITH LIMITATIONS**

The Phase 8.4 orphan finding is closed on Windows and Linux, verified with
the real tracer. The limitations in section 18 are edge cases outside the
Phase 8.4 finding.

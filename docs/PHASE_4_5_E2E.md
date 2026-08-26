# Phase 4.5 — Production E2E Hardening

Proves the Phase 0–4.4 architecture is production-ready **before** Phase 5
introduces LLM nondeterminism.

Reuse analysis: [PHASE_4_5_REUSE_AUDIT.md](PHASE_4_5_REUSE_AUDIT.md).
Timings: [PHASE_4_5_PERFORMANCE.md](PHASE_4_5_PERFORMANCE.md).

## The complete pipeline

```
Java source (deterministic fixture - no LLM in this phase)
   -> real Maven compile
   -> real JUnit validation
   -> real JVM execution + AST instrumentation
   -> ExecutionTrace            CANONICAL
   -> FrameState                arrays, scalars, pointers
   -> SourceLocation/CodeState  file + line + window
   -> VisualizationPlan         validated against the real trace
   -> REAL Manim render         trusted repository-owned source
   -> REAL TTS                  Windows SAPI, measured duration
   -> SRT subtitles             validated by round-trip
   -> REAL FFmpeg composition
   -> Final MP4
   -> Media policy validation   fails loudly, never silently repairs
```

**No fake renderer is used in the acceptance gate.** `FakeVideoRenderer`
and `MockTTSProvider` remain for unit tests only.

## Golden-path algorithms

All five run the full real pipeline in
`tests/test_e2e_golden_path.py::test_full_production_pipeline_per_algorithm`:

| Algorithm | Input | Expected output | Result |
|---|---|---|---|
| Reverse String | `HELLO` | `OLLEH` | pass |
| Palindrome | `RACECAR` | `true` | pass |
| Two Sum | `9` | `0,1` | pass |
| Move Zeroes | (fixture array) | `1,3,12,0,0` | pass |
| Remove Duplicates | (fixture array) | `3` | pass |

Each asserts, on real artifacts: compile succeeds; JUnit passes (where the
fixture ships tests); execution output matches exactly; trace step indexes
are sequential and `trace_schema_version >= 2`; FrameState reconstructs a
real array and derives pointers; the plan validates against the real trace;
**every** traced line's highlighted text is byte-identical to the real
source at that file and line; narration maps to valid steps; TTS produces
real audio with a measured duration; SRT round-trips; the render is真
1080×1920 h264 with frames; and the final MP4 passes media policy plus
timeline validation.

A companion test fails the build if any algorithm name leaks into the
visualization package, so the renderer cannot be special-cased.

## Media validation

New module `media/validation.py` — the policy layer that probing alone did
not provide.

**Video:** MP4 container, H.264, exactly 1080×1920, expected FPS, frame
count > 0, duration above a floor (a fragment instead of the assembled
scene is caught here).

**Audio:** AAC, allowed channel count, sane sample rate, non-zero duration.

**Timeline:** the *video* duration is authoritative — cues must fall inside
it, timestamps non-negative and monotonic, no overlaps, and any narration
overflow is reported rather than absorbed.

**Composition drift:** the final video is compared against the *rendered*
duration. Truncation and stretching are each named explicitly in the error,
because this is exactly the Phase 4.1 production bug (a bare `-shortest`
silently cut an 18.0s render to 5.2s).

Never used to "fix" a mismatch: `-shortest`, arbitrary truncation, silent
duration manipulation, automatic stretching. A mismatch is a validation
failure with an actionable diagnostic.

## Failure behaviour

`tests/test_failure_injection.py` (24 tests) deliberately breaks the
pipeline and asserts it stops correctly:

| Injected failure | Asserted behaviour |
|---|---|
| plan references a nonexistent trace event | rejected, error names the index |
| plan references events out of order | rejected |
| empty plan | rejected |
| narration references a missing step | `AlignmentError` naming the step |
| narration audio exceeds its window | reported; **timeline unchanged** |
| zero-length narration | `TTSFailure` |
| missing / corrupt MP4 | validation failure, not trusted |
| renderer produced no file | `RenderingFailure`, not a success |
| Manim non-zero exit / timeout | `RenderingFailure` |
| FFmpeg failure / timeout | `MediaCompositionFailure` |
| TTS timeout | `TTSFailure` |
| final video truncated | detected, error says "truncated" |
| final video stretched | detected, error says "stretched" |
| wrong resolution | rejected with both values |
| audio outlasts video | reported |
| cue beyond video duration | rejected |
| broken artifact lineage | missing parent detected |
| workspace cleanup after exception | temp directory removed |

## Repeatability — what "deterministic" means here

`tests/test_repeatability.py` (10 tests) runs the pipeline twice and
asserts identity at the **logical** layer:

**Identical:** ExecutionTrace (all fields except wall-clock duration),
FrameState (arrays, scalars, pointers), SourceLocation per event, CodeState
windows, VisualizationPlan, SRT document bytes, video geometry.

**Not required identical:** MP4 bytes (encoders embed timestamps),
wall-clock durations, artifact ids (fresh uuid4 by design).

## Tooling requirements

| Tool | Needed for | Absent behaviour |
|---|---|---|
| JDK 17+ / Maven | compile, JUnit, execute, trace | integration tests skip |
| Manim | real render | `real_render` tests skip |
| FFmpeg | composition, synthetic audio | those tests skip |
| Windows SAPI | real speech | falls back to `SyntheticTTSProvider` |

Skips are narrow and tool-specific. `pytest -m "not integration"` runs
**267 tests in ~0.6s** with no external tooling at all.

## Known limitations

- **Manim is 83% of pipeline time** (~22.6s mean). Inherent to rendering
  1080×1920 vector animation, not a Code2Shorts inefficiency.
- **Only the Reverse String fixture ships JUnit tests**; the other four
  fixtures are validated by exact stdout comparison instead.
- **SAPI is Windows-only**; other platforms fall back to synthetic audio,
  so the *speech* leg of the golden path is not exercised there.
- **Java stages each pay a fresh JVM + Maven startup** (~1.3s compile,
  ~1.8s trace). Reusing one workspace across stages would cut this at the
  cost of the isolation guarantee — not a trade worth making without
  evidence of a real problem.
- **No LLM anywhere in this phase**, by design. Whether the pipeline holds
  up under LLM-generated input is a Phase 5 question.

## Deferred

Workspace reuse for Java stages; render caching; parallel per-algorithm
execution; burned-in subtitles; neural TTS.

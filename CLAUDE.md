# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

Code2Shorts turns a Java algorithm into a 1080×1920 educational short. Its
distinguishing property is that **the video's contents are derived from a real
JVM execution**, not from an LLM's description of one. Most of the design
follows from that single constraint.

## Commands

```bash
# Setup
python -m venv .venv
.venv/Scripts/activate                 # Windows
pip install -e ".[dev,render]"

# Tests
pytest                                 # everything (~5 min; needs all external tools)
pytest -m "not integration"            # unit only, <1s, no external tooling
pytest tests/test_e2e_golden_path.py   # real 5-algorithm production gate (~2.5 min)
pytest tests/test_code_synchronization.py::test_short_file_is_shown_whole   # single test
pytest -k "alignment"                  # by name

# Golden paths (produce real MP4s under output/, which is gitignored)
python scripts/run_golden_path.py            # video only
python scripts/run_narrated_golden_path.py   # + real speech + subtitles
python scripts/run_phase5_golden_path.py     # + a real LLM (mock by default)
python scripts/measure_performance.py        # regenerates docs/PHASE_4_5_PERFORMANCE.md

# The Java instrumenter is a separate Maven module and must be built
# before any trace() call works:
cd tools/java-instrumenter && mvn -q clean package
```

### External tooling

| Tool | Needed for | If missing |
|---|---|---|
| JDK 17+, Maven | compile / JUnit / execute / trace | `integration` tests skip |
| Manim, FFmpeg | real render, composition | `real_render` tests skip |
| Windows SAPI | real speech | falls back to `SyntheticTTSProvider` |

`ffmpeg` must be on PATH by that name. `imageio-ffmpeg` ships the binary but
not under that name — on Windows, copy
`.venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-*.exe` to
`.venv/Scripts/ffmpeg.exe`. There is no `ffprobe`; media inspection uses PyAV
via `media/probe.py`.

**Bash-tool note:** the Bash tool's PATH does not include Maven/FFmpeg on this
machine, so integration tests appear to fail there. Run them through
PowerShell instead.

## Architecture

The pipeline is a straight line, and each arrow is a trust boundary:

```
Java source → compile → JUnit → execute+instrument
  → ExecutionTrace        ← CANONICAL TRUTH
  → FrameState            arrays / scalars / pointers, reconstructed
  → SourceLocation/CodeState   which file, which line, which window
  → VisualizationPlan     validated against the real trace
  → Manim (trusted, repository-owned source generation)
  → TTS → SRT → FFmpeg → final MP4 → media validation
```

### The rules that explain most of the code

1. **`ExecutionTrace` is canonical.** Every visual fact traces back to a real
   `TraceEvent`. `visualization/state.py` *reconstructs* array contents by
   replaying `ARRAY_WRITE` events — the trace stores mutations, not snapshots.
2. **AI output is DATA, never code.** LLMs propose; validators decide; trusted
   code executes. `VisualAction` is a **closed enum**, so an out-of-vocabulary
   "instruction" fails Pydantic validation before any custom code runs.
3. **The renderer is repository-owned.** `visualization/manim_renderer.py::build_scene_source`
   generates Manim source from validated data; every dynamic value goes through
   `repr()` as a string literal. AI never authors executable source.
4. **The visual timeline is authoritative.** `narration/alignment.py` places
   audio on plan-declared step durations. Measured audio duration is used
   *only* to detect overflow — it can never move a visual step.
5. **Never silently truncate or stretch.** `media/validation.py` detects
   composition drift and names it. A mismatch is a loud validation failure.
6. **One subprocess seam.** Every external tool (mvn, java, manim, ffmpeg,
   powershell) goes through `execution/sandbox.py::run_subprocess`: fixed
   argument list, no `shell=True`, `stdin=DEVNULL`, explicit timeout,
   whole-process-tree kill. Do not add a second mechanism.
7. **No algorithm-specific branching in `visualization/`.** Pointers are
   derived by a purely structural rule (an integer scalar whose value is a
   valid array index). A test fails the build if any algorithm name appears in
   that package.

### Package map

Real, load-bearing code: `core/` (domain models — imports nothing else),
`execution/` (sandbox), `langadapter/java/` (compile/test/execute/trace +
`trace/` instrumentation glue), `visualization/`, `narration/`, `media/`,
`workflow/` (nodes, runner, retry, checkpoints), `artifacts/`, `ai/`, `llm/`.

`animation/`, `compose/`, `lessonplan/`, `qa/`, `render/`, `tts/` are empty
Phase-0 placeholders — their real successors are `visualization/`, `media/`
and `narration/`. Don't add to the placeholders.

`tools/java-instrumenter/` is a standalone Maven module (JavaParser) that
rewrites Java source to emit trace events. It is deliberately outside
`src/` because it is trusted, versioned tooling — distinct from the untrusted
generated code that runs in temp workspaces.

### Two non-obvious Java details

- `Code2ShortsTrace.repr()` overloads exist because `String.valueOf(int[])`
  returns an identity hash (`[I@7ad04...`), not contents. `char[]` worked by
  accident; every other array type needed this.
- Trace events carry no file identity. The file is **derived** from method
  context (`METHOD_ENTER` carries `Class.method`), which is exact for the
  supported subset since lambdas/nested classes are rejected at instrument
  time. See `visualization/code_state.py::resolve_source_locations`.

## Conventions

- New dependencies require a documented reuse audit first (see `docs/*REUSE*`).
  Several phases were completed with **zero** new dependencies; that bar is
  intentional.
- Contract changes to `ExecutionTrace`, `LanguageAdapter`, `LLMProvider`,
  `TTSProvider`, `VisualizationPlan` or `ArtifactStore` need an ADR in
  `ARCHITECTURE_DECISIONS.md` and backward compatibility where practical.
- Media claims must be **probed**, never assumed. `probe_video`/`probe_audio`
  exist because a renderer once reported a computed duration that was wrong.
- Security-property tests parse the AST rather than grepping source text —
  modules legitimately document the constructs they forbid, and text search
  flags its own comments.
- **Every changed line traces to the request.** Don't reformat, rename, or
  "improve" adjacent code, and match surrounding style even where you'd
  differ. Mechanical repo-wide edits are the specific hazard here: a
  Phase 6 `collectionVars.clear()` was inserted at every
  `uninitializedVars.clear()` site, including two inside `instrumentIf`,
  which silently stopped every collection mutation inside a branch from
  being observed. Scope such edits by hand and re-verify.
- **A bug fix starts with a failing test when practical.** Reproduce first,
  then fix. If reliable reproduction is impossible, document why and add
  the strongest feasible regression or characterization test. The SRT
  cue-injection and Phase 4.5.1 line-mapping defects were both closed this
  way, and it is why they stay closed.

## Current state

Phases 0–5 complete; 465 tests collected, 354 pass unit-only (`-m "not integration"`). The pipeline runs end to end
with a **real LLM** generating explanation, visualization plan and
narration — verified against a live free-tier gateway, not only a mock.

`ai/providers/factory.py` is the single provider-selection seam; one
`OpenAICompatibleProvider` parameterised by `base_url` reaches OmniRoute,
Ollama, vLLM and OpenAI, and `GeminiLLMProvider` is unchanged.
`LLM_PROVIDER` defaults to `mock`, so nothing requires a credential.
**OmniRoute is optional infrastructure, never a dependency** — nothing
imports it and `pyproject.toml` is unchanged (ADR-5.2).

### Phase 5 rules worth knowing before editing

8. **Provider retry is not workflow repair.** Retry resends an identical
   request after a timeout/429/5xx; repair sends a *different* prompt built
   from the previous output plus its validation errors. Never merge them
   (ADR-5.4). Also: pin `num_retries=0, max_retries=0` on litellm — its own
   retry loop multiplied with ours to make nine requests where the config
   said three.
9. **Prompts state their schema, derived from the Pydantic model.**
   `json_schema_instruction(model)` — never hand-write an example, it rots.
   `extract_json` strips fences/preamble lexically and is *not* lenient
   parsing. `generate_structured` and `ai/repair.py` must share that one
   parse path.
10. **Fit the timeline before rendering, never after.**
    `narration/fitting.py` widens visual steps to hold measured speech.
    Widen-only — `min()` is banned there by test. The Phase 4.4
    prohibitions (no `-shortest`, no truncation, no stretching) still hold.
11. **A superseded repair attempt is not a failed run.** Keep the full
    history as audit evidence; `ValidationResult.superseded` marks replaced
    attempts and `all_passed` ignores them.

12. **Configuration is environment-switched.** `CODE2SHORTS_ENV`
    controls which dotenv files load: `local` reads `.env`/`.env.local`,
    `test` and `production` read none. Production must never be able to
    find `.env.local`. Credentials are `SecretStr`; unwrap only with
    `config.reveal()` at the wire. `tests/conftest.py` forces
    `CODE2SHORTS_ENV=test`, because the unit suite was otherwise loading a
    developer's real keys.

13. **Educational integrity beats duration (ADR-5.11).** Never add a
    `MAX_STEPS`/`MAX_VIDEO_SECONDS` cap, and never drop a conceptual
    transition to shorten a video. `ai/education.py` and `ai/grounding.py`
    are AST-tested to contain no budget. `EducationalPlanResponse` has no
    duration field on purpose. Redundant events may be *summarised* into
    one moment, but conceptual boundaries (first loop check vs last)
    survive.
14. **`ClaimKind` splits truth from reasoning.** `observed` asserts runtime
    state and requires trace evidence whose values are checked;
    `explanation`/`commentary` reason freely. Requiring evidence for "why"
    would force the model to fabricate it.

Free-tier routing is slow (LLM stage 277–486 s vs ~135 s for Manim), so the
Phase 5 golden path takes `--timeout` and defaults to 180 s rather than
`Settings.ai_timeout_seconds`' 30 s.

Detailed history and rationale live in `ARCHITECTURE_DECISIONS.md` (ADRs per
phase), `SECURITY_SANDBOX.md` (trust boundaries + every fixed vulnerability),
and `docs/`.

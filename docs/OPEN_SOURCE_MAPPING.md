# Open-Source Mapping — where reused code sits in the pipeline

Companion to [OPEN_SOURCE_REUSE.md](OPEN_SOURCE_REUSE.md). Shows exactly
where external code touches Code2Shorts, and — just as importantly — where
it does not.

## The one integration point

```
        Java source (untrusted)
                 │
                 ▼
   ┌─────────────────────────────┐
   │ Code2Shorts LanguageAdapter │   OURS — Phase 1/2
   │  compile / test / execute   │   No external code. tracers.java
   │  trace  (AST instrumented)  │   rejected: unlicensed + requires
   └──────────────┬──────────────┘   manual source annotation.
                  ▼
   ┌─────────────────────────────┐
   │  Code2Shorts ExecutionTrace │   OURS — CANONICAL, language-neutral.
   │  observed facts only        │   Never replaced by an external model.
   └──────────────┬──────────────┘
                  ▼
   ┌─────────────────────────────┐
   │     VisualizationPlan       │   OURS — closed VisualAction enum,
   │  schema + semantic validated│   schema-constrained, trace-validated.
   └──────────────┬──────────────┘
                  ▼
   ┌─────────────────────────────┐
   │   Trusted Renderer          │   OURS — build_scene_source().
   │   (repository-owned code)   │   ◄── THE ONLY PLACE external code
   │                             │        is called.
   │   emits Manim source using: │
   │     Code(language="java")   │   🟢 Manim CE (MIT) — DIRECT REUSE
   │     .code_lines  ───────────┼──►  line highlighting
   │     Table / VGroup ─────────┼──►  array tiles + index labels
   │     Arrow ──────────────────┼──►  left/right pointers
   │     SurroundingRectangle ───┼──►  cell highlighting
   │     Indicate / Transform ───┼──►  comparisons, swaps
   └──────────────┬──────────────┘
                  ▼
            Manim (subprocess)          🟢 Manim CE — already a dependency
                  │
                  ▼
            MediaComposer               OURS — fixed-argument FFmpeg
                  │
                  ▼
            MP4 1080×1920
```

## Direction of the dependency

External code is called **by** the trusted renderer, at the very end of the
pipeline. It never flows the other way:

- No external project's data model enters `ExecutionTrace`.
- No external project's code runs during compile/test/execute/trace.
- No external project decides *what* is visualized — only *how* it is drawn,
  and only through primitives our own code invokes.

This keeps the reuse surface to a single, replaceable seam. If Manim were
ever swapped for another engine, `build_scene_source` is the only module
that changes — `VideoRenderer` already abstracts it (Phase 4).

## Reference-only projects — read, never linked

These informed the design and are cited for provenance. **No code from any
of them is present in this repository, and none is linked at runtime.**

| Project | What we took | What we did NOT take |
|---|---|---|
| algorithm-visualizer | Visual vocabulary: how arrays, pointers, and index labels are conventionally drawn; evidence the pattern generalizes across 6 languages | Any code; its command-stream trace model; its browser architecture |
| tracers.java | Confirmation that automatic instrumentation beats manual annotation for our use case | **Nothing — it has no license; copying is not permitted** |
| code-video-generator | Comment-driven walkthrough pacing as a narrative idea | Any code; its 5-year-old Manim 0.10 integration; its `librosa` dependency |
| CodeAnimator | Line-range selection UX as a future consideration | Any code |
| arrayviz | Nothing usable — upstream deleted | Any code or dependency |

## Net effect on `pyproject.toml`

```diff
  (unchanged)
```

Zero new production dependencies. The `[render]` extra remains exactly as
Phase 4.1 left it: `manim>=0.19.0`, `imageio-ffmpeg>=0.5`.

# Phase 7.0 — UI foundation

## What this is

A web UI **above** the existing pipeline. It adds no generation logic:
`GenerationManager` composes the same `workflow/nodes.py` nodes that the
pipeline tests and golden-path scripts compose, and runs them on the same
`WorkflowRunner`.

```
browser
   │
FastAPI routes            src/code2shorts/webapp/
   │
JobManager                background thread, real per-node events
   │
GenerationManager         decide → run → record a NEW version
   │
Workflow + WorkflowRunner ← EXISTING, unchanged
   │
compile · trace · explain · educational plan · visualization plan ·
narration · render · compose · validate      ← EXISTING, unchanged
```

## Running it

```bash
pip install -e ".[web]"      # optional extra; core install is unaffected
code2shorts serve            # http://127.0.0.1:8000
```

The pipeline, the CLI and every test outside `tests/test_webapp.py` run
without the `web` extra installed.

## Generation identity

A video is **not** identified by "does `final.mp4` exist". Two
fingerprints, because they are known at different times:

| | Contains | Used for |
|---|---|---|
| `RequestFingerprint` | algorithm, source hash, teaching config (audience, style, voice, TTS provider, format), LLM provider, **renderer version**, pipeline version | Deciding whether to generate. Known before anything runs. |
| `ContentFingerprint` | the above **plus** trace / explanation / educational-plan / visualization-plan / narration hashes | Auditing, and the groundwork for partial regeneration. Recorded after the run. |

The split is not decoration: a decision taken *before* tracing cannot
depend on the trace's hash, and pretending otherwise would mean running
the expensive half of the pipeline to discover it need not have run.

`RENDERER_VERSION` is a **manual constant** (`6.5.3`). Phase 6.5.3 changed
every frame the renderer produces, so a video made before it is not the
video the current code would make. Deriving the constant from a file hash
would invalidate every video on a comment change, which trains people to
ignore it.

### The decision

```
same request fingerprint, status COMPLETED  →  REUSE, do no work
different fingerprint                        →  offer to generate, say what changed
explicit Re-generate (force=1)               →  always generate a new version
```

Duplicate prevention lives in `GenerationManager.generate`, not in the UI,
so a second caller cannot bypass it.

## Versioning

```
output/library/
  index.json                  every version ever produced
  palindrome/
    v1/  final.mp4  trace.json  visualization_plan.json  narration.wav …
    v2/  …
```

**A new version is always a new directory.** Nothing overwrites a previous
render. `current_version()` returns the newest version that is `COMPLETED`
*and* has a final video — so a failed v3 does not demote a working v2.

## Job progress

Progress is a projection of `WorkflowEvent`s emitted by the runner. A
stage cannot show as complete unless the runner said so, and the percentage
counts **completed** stages only — a running stage contributes nothing,
because a bar that moves while nothing has finished is a lie.

Stage states: `queued · running · completed · failed · skipped`.

Cancellation is cooperative and takes effect at a stage boundary. Killing
a thread mid-render would leave a half-written MP4 and orphaned
Maven/Manim/FFmpeg subprocesses.

## Security

* A program slug is validated against the catalog **before** it becomes a
  path segment.
* Artifact names arrive from URLs and are resolved by
  `GenerationRegistry.artifact_path`, which refuses anything that does not
  resolve inside its own version directory — containment after resolution,
  so symlinks and encoded traversals are caught too.
* Form values are coerced to the offered options; an unexpected voice
  never reaches a TTS engine.
* Jinja autoescaping is on; the job poller writes with `textContent`,
  never `innerHTML`, because stage details carry provider error text.
* The Settings page reports whether a credential is **present**, never its
  value. No absolute filesystem path is rendered.
* No `eval`, no `exec`, no shell strings built from user input. Every
  subprocess still goes through `execution/sandbox.py::run_subprocess`.

## Deployment

The UI/API layer is stateless apart from the registry on disk. The heavy
work is not:

> **`JobManager` runs generation in an in-process background thread. That
> is correct for a single Render service and WRONG for a serverless
> platform that freezes between requests.**

Stated rather than designed around. `JobManager` is the seam to replace
with a queue and a worker; the API talks to it and never to a thread, so
the replacement does not reach the UI. Splitting the heavy pipeline onto a
worker is Phase 7.x, not this phase.

## Deliberately not in Phase 7.0

* Partial regeneration. The architecture supports it —
  `ContentFingerprint.stages_shared_with` reports which stages are
  identical, and `WorkflowRunner.run(start_index=…)` already resumes
  mid-pipeline — but no UI acts on it yet.
* Compare Versions.
* Algorithms other than Palindrome.
* Timeline scrubbing and narration display on the details page.
* Authentication. The app binds to `127.0.0.1` by default and has no user
  model; do not expose it publicly as-is.

# Phase 8.2D — User video format selection

The Create page lets the user choose **9:16 Vertical** (1080 × 1920) or
**16:9 Landscape** (1920 × 1080), and the choice reaches the existing
profile → layout → renderer path. No rendering, pipeline, fingerprint or
registry logic changed.

## 1. User selection

The Create page has a **Video format** fieldset with one native radio per
offered format:

| Label | Pixels | Use | Submitted value |
|---|---|---|---|
| 9:16 Vertical | 1080 × 1920 | Shorts / Reels / TikTok | `vertical_hd` (**checked by default**) |
| 16:9 Landscape | 1920 × 1080 | YouTube / Desktop / TV | `landscape_hd` |

- **The default is explicit.** The 9:16 radio is `checked` in the markup,
  never inferred from the viewer's screen or from earlier generations.
- **Accessibility.** Each radio is wrapped by its label, so the text is
  clickable. The native control stays visible, which gives keyboard
  operation and a selected state that doesn't rely on colour. The card
  border follows `:has(input:checked)` live, and focus shows on
  `:has(input:focus-visible)`.
- **One source for the options.** They come from the profile registry:
  `webapp/app.py::FORMATS` is every profile whose `is_renderable` is true.
  The page adds only wording per *orientation* ("Vertical"/"Landscape" and
  the use line), not a second list of formats. 4K is declared but refused,
  so it is never offered.

## 2. Canonical values

There is one field, `TeachingConfig.video_format`, unchanged since Phase 8.0.

- **Submitted:** the profile id (`vertical_hd` / `landscape_hd`).
- **Stored:** the profile's identity key. This is `youtube_short` for
  VERTICAL_HD (kept since Phase 8.0 so existing fingerprints stay valid)
  and `landscape_hd` for LANDSCAPE_HD. `TeachingConfig`'s validator does
  this normalisation.
- **Read back:** templates turn a stored value into its profile with the
  `video_profile` Jinja filter (`resolve_video_profile`).

## 3. Request propagation

```
<input name="video_format">  ─►  _submitted_video_format (raw form value)
                              ─►  _config_from_form(..., video_format)
                              ─►  TeachingConfig(video_format=profile.id)
                              ─►  GenerationRequest.config
                              ─►  JobManager.start → Job.video_format
                              ─►  GenerationManager.generate (profile.require_renderable)
                              ─►  DefaultPipelineFactory.build
                              ─►  request.config.video_profile → build_renderer(profile)
                              ─►  ManimVideoRenderer(resolution, layout_for(profile))
                              ─►  native MP4 → registry (request_fingerprint.config.video_format)
```

**Where it used to be lost.** Before this phase:
- the Create page had only a disabled text box;
- `/create/review` and `/create/start` read no format;
- `_config_from_form` took none;
- every hidden field (Review, Re-generate on the details page and on the
  cards) dropped it.

So **Re-generate on a 16:9 video would have produced 9:16.**

## 4. Backend validation

The backend is authoritative.

| Submitted | Result |
|---|---|
| field absent | default VERTICAL_HD (every older form and link still works) |
| `vertical_hd`, `landscape_hd` (or the identity key `youtube_short`) | accepted |
| `""`, whitespace, `imax`, `16:9`, `9:16`, `1920x1080` | **422** `unsupported video_format …; offered: vertical_hd, landscape_hd` |
| `vertical_4k`, `landscape_4k` | **422** (declared, not renderable) |

- **Why the raw form value.** It's read through `request.form()` because
  FastAPI's `Form(None)` maps an *empty* value to the default, which would
  have turned `video_format=` into a silent 9:16.
- **No work on rejection.** A rejected request creates no job, no version
  and no directory.
- **The existing API also validates.** `POST /api/videos/{slug}/decide`
  takes a `TeachingConfig` body and already validated `video_format`.

## 5. Job propagation

- **Per job, not global.** Each `Job` records its own `video_format` and
  `request_digest`, and `/api/jobs/{id}` reports `video_format`. Nothing
  about the format is stored globally: the renderer is built per job from
  that job's request.
- **Defect fixed.** `JobManager.start` used to refuse a second job for the
  same *program*, and returned the running job instead. A 16:9 request made
  while a 9:16 palindrome was generating therefore got the 9:16 job back.
  It now dedupes on the **request fingerprint**:
  - the same request is still one job;
  - different formats are different jobs.

  This is safe because Phase 8.1 made version allocation atomic, with one
  directory per version.

## 6. VideoProfile resolution

Unchanged: `TeachingConfig.video_profile`, then `layout_for(profile)` in
`build_renderer`.

## 7. Fingerprint behaviour

Unchanged. `video_format` was already part of `RequestFingerprint.config`:
`youtube_short` ≠ `landscape_hd`, so the two formats can't collide.

## 8. Registry behaviour

Unchanged. Each version records its format in
`request_fingerprint.config.video_format`.

## 9. Review and details pages

- **`/create/review`** shows the **validated request**: format and
  resolution from `config.video_profile`, never from a browser control.
  Its forms carry `video_format` forward.
- **`/videos/{slug}/v{n}`** shows the **generated file**. The final MP4 is
  probed (`media.probe.probe_video`) and its pixel size matched against the
  profile registry. If the file ever disagreed with its request, the page
  shows the file's format plus "Requested: … (does not match the file)".
  Re-generate carries the version's own format.
- **Cards** show the current version's format.

## 10. Concurrent generation isolation

The test runs two jobs submitted at the same moment (9:16 and 16:9), then
again in reverse order. Each gets its own job and version, reports its own
format, and produces a file of its own size: 1080 × 1920 and 1920 × 1080,
probed.

## 11. Tests

`tests/test_format_selection.py`: 28 tests. The routes, `_config_from_form`,
`JobManager`, `GenerationManager`, the **real** `DefaultPipelineFactory` and
the **real** `ManimVideoRenderer` all run. Only the Manim subprocess is
replaced, by real FFmpeg drawing a clip at the `--resolution` the renderer
requested.

- **Create page:** exactly two options, 9:16 checked, 4K absent, the old
  placeholder gone, labels clickable, and the options equal the profile
  registry's renderable profiles.
- **Form to config:** the choice survives for both ids, the legacy identity
  key and an absent field.
- **Review:** it shows the validated request and carries the value forward.
- **Rejection:** 8 invalid, empty or refused values are rejected on both
  routes, with no job, version or build.
- **Propagation:** job, registry and probed pixels are correct for 9:16,
  16:9 and an absent field.
- **Reuse:** each format is its own video, and each is reused on a repeat
  request (exactly two builds).
- **Details page:** it shows the generated format, and Re-generate keeps it.
  It believes the **file** over the request when the two differ (forced).
- **Concurrency:** concurrent 9:16 + 16:9 in both orders show zero
  cross-talk; the same request submitted four times at once is still one
  job.

Full suite: 993 passed, 51 failed, 26 skipped, against 965 / 51 / 26 after
8.2C. The failing set is identical (the pre-existing Maven and SAPI failures
and the comment-grep rule-7 test).

## 12. Known limitations

- **Real E2E: BLOCKED.** On the live server, both concurrent generations
  (9:16 and 16:9) ran real compile and trace with their own formats, then
  failed at the first LLM call with **Gemini HTTP 429**: "Quota exceeded
  for metric: generate_content_free_tier_requests, limit: 20, model:
  gemini-3.6-flash". It was still exhausted on a single probe more than a
  minute later. Earlier runs the same day used up the quota. The brief rules
  out switching provider or adding failover, so the real Gemini → MP4 leg
  for this phase is **not verified**.
  - The 16:9 real pipeline itself was verified in Phase 8.2C, and 9:16 in
    the real palindrome test.
  - The failed attempts are recorded as failed versions in
    `output/real-test/format-selection/library`.
- **Browser E2E: NOT VERIFIED.** No browser tool was available. The live
  HTTP server was driven with the exact fields the Create form submits.
- **Portrait pointer-label overlap** is unchanged (out of scope).
- **4K** stays refused (Phase 8.3).
- **The Home card shows one "current" video per program:** the newest
  completed version, whatever its format.

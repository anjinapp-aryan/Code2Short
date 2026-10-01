"""The Code2Shorts web application.

    browser
       |
    FastAPI routes (this module)
       |
    JobManager  ->  GenerationManager  ->  the EXISTING workflow nodes

No route touches Manim, FFmpeg, Kokoro, the JVM instrumenter, an LLM
client or the trace model. They call `GenerationManager` and read the
registry, which is the whole point of the boundary.

Security posture, since every value below arrives from a browser:

* a program slug is looked up in the catalog before it is used for
  anything - it never becomes a path segment on its own;
* an artifact name is resolved by the registry, which refuses anything
  that escapes its version directory;
* form values are parsed into `TeachingConfig`, so an unexpected field is
  dropped rather than carried;
* Jinja autoescaping is on, so untrusted text cannot become markup;
* no filesystem path and no credential is ever sent to the browser.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from code2shorts.config import Settings
from code2shorts.core.video_profile import (
    DEFAULT_VIDEO_PROFILE,
    PROFILES,
    Orientation,
    UnknownVideoProfileError,
    VideoProfile,
    resolve_video_profile,
)
from code2shorts.generation import (
    GenerationManager,
    GenerationRegistry,
    GenerationRequest,
    GenerationStatus,
    JobManager,
    PipelineFactory,
    TeachingConfig,
)
from code2shorts.webapp import catalog

HERE = Path(__file__).resolve().parent

# Only these are offered in the form, so an arbitrary string cannot reach
# a prompt or a TTS engine through a select box.
AUDIENCES = ("beginner", "intermediate", "advanced")
TEACHING_STYLES = ("step_by_step", "concise", "interview")
VOICES = ("af_heart",)

# The formats the Create page offers: every profile that can actually be
# rendered, read from the profile registry rather than listed again here.
# A profile that is declared but refused is therefore never offered.
FORMATS: tuple[VideoProfile, ...] = tuple(
    profile for profile in PROFILES.values() if profile.is_renderable
)

# User-facing wording only. Keyed by orientation, never by profile id, so
# it is copy for the page and not a second list of formats.
FORMAT_COPY: dict[Orientation, tuple[str, str]] = {
    Orientation.PORTRAIT: ("Vertical", "Shorts / Reels / TikTok"),
    Orientation.LANDSCAPE: ("Landscape", "YouTube / Desktop / TV"),
}


# A profile whose short side is 2160 px is Ultra HD ("4K"). Read from the
# profile's own pixels, so the wording cannot disagree with what renders.
ULTRA_HD_SHORT_SIDE = 2160


def _is_ultra_hd(profile: VideoProfile) -> bool:
    return min(profile.pixel_width, profile.pixel_height) >= ULTRA_HD_SHORT_SIDE


def format_label(profile: VideoProfile) -> str:
    """"9:16 Vertical" / "16:9 Landscape", plus " 4K" for Ultra HD."""
    label = f"{profile.aspect_ratio} {FORMAT_COPY[profile.orientation][0]}"
    return f"{label} 4K" if _is_ultra_hd(profile) else label


def format_use(profile: VideoProfile) -> str:
    """The line under the label: where the format is for."""
    return "Ultra HD" if _is_ultra_hd(profile) else FORMAT_COPY[profile.orientation][1]


def _profile_for(video_format: str | None) -> VideoProfile:
    """The profile a submitted `video_format` names.

    Absent means the default (9:16), which keeps every Phase 7 form and
    link working. Anything SUPPLIED must name an offered format: an
    unknown value, an empty value or a refused profile (4K) is an error,
    never quietly turned into portrait - that would hand the user a video
    in a format they did not ask for.
    """
    if video_format is None:
        return DEFAULT_VIDEO_PROFILE
    try:
        profile = resolve_video_profile(video_format) if video_format.strip() else None
    except UnknownVideoProfileError:
        profile = None
    if profile is None or profile not in FORMATS:
        offered = ", ".join(p.id.value for p in FORMATS)
        raise HTTPException(
            status_code=422,
            detail=f"unsupported video_format {video_format!r}; offered: {offered}",
        )
    return profile

MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".srt": "text/plain; charset=utf-8",
    ".json": "application/json",
}


async def _submitted_video_format(request: Request) -> str | None:
    """The `video_format` exactly as submitted: None when the field is
    absent, the string (possibly empty) when it is present.

    Read from the parsed form rather than declared as `Form(None)`, because
    FastAPI maps an EMPTY form value to the parameter's default - which
    would turn `video_format=` into a silent 9:16. Starlette caches the
    parsed form, so this reads what the route already parsed.
    """
    return (await request.form()).get("video_format")


def _config_from_form(
    audience: str, teaching_style: str, voice: str, video_format: str | None = None
) -> TeachingConfig:
    """Reject anything not offered, rather than passing it through.

    The teaching fields fall back to their first option (unchanged since
    Phase 7). The format does not fall back: see `_profile_for`.
    """
    return TeachingConfig(
        audience=audience if audience in AUDIENCES else AUDIENCES[0],
        teaching_style=(
            teaching_style if teaching_style in TEACHING_STYLES else TEACHING_STYLES[0]
        ),
        voice=voice if voice in VOICES else VOICES[0],
        video_format=_profile_for(video_format).id.value,
    )


def create_app(
    registry: GenerationRegistry | None = None,
    pipeline_factory: PipelineFactory | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Everything injectable, so tests drive the real routes with a fake
    pipeline - no Maven, no Manim, no FFmpeg, no credentials."""
    settings = settings or Settings()
    registry = registry or GenerationRegistry(Path("output") / "library")
    if pipeline_factory is None:
        from code2shorts.webapp.pipeline import DefaultPipelineFactory

        pipeline_factory = DefaultPipelineFactory(settings)

    manager = GenerationManager(registry, pipeline_factory)
    jobs = JobManager(manager)

    app = FastAPI(title="Code2Shorts", docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.autoescape = True
    # A stored `video_format` (a profile id or identity key) -> its profile,
    # so templates render labels and pixels from the one profile registry.
    templates.env.filters["video_profile"] = resolve_video_profile
    templates.env.filters["format_label"] = format_label
    templates.env.filters["format_use"] = format_use

    app.state.registry = registry
    app.state.manager = manager
    app.state.jobs = jobs

    # ---- helpers ------------------------------------------------------

    def program_or_404(slug: str) -> catalog.Program:
        program = catalog.get(slug)
        if program is None:
            raise HTTPException(status_code=404, detail="unknown program")
        return program

    def build_request(slug: str, config: TeachingConfig) -> GenerationRequest:
        program = program_or_404(slug)
        files, entry_point = catalog.source_files(slug)
        return GenerationRequest(
            algorithm=program.slug,
            source_files=files,
            entry_point=entry_point,
            input_value=program.input_value,
            title=program.title,
            config=config,
            llm_provider=(settings.llm_provider or "mock").strip().lower(),
        )

    def library() -> list[dict]:
        """One row per program that has ever been generated, newest
        version first. Shaped here so the page and the JSON API cannot
        disagree about what a library row is."""
        rows = []
        for program in catalog.PROGRAMS.values():
            versions = registry.versions_for(program.slug)
            current = registry.current_version(program.slug)
            rows.append(
                {
                    "program": program,
                    "current": current,
                    "versions": versions,
                    "job": jobs.job_for_algorithm(program.slug),
                }
            )
        return rows

    def page(request: Request, name: str, **context) -> HTMLResponse:
        return templates.TemplateResponse(
            request, name, {"nav": name.split(".")[0], **context}
        )

    # ---- pages --------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return page(
            request,
            "home.html",
            rows=library(),
            jobs=jobs.recent(limit=5),
            programs=list(catalog.PROGRAMS.values()),
        )

    @app.get("/videos", response_class=HTMLResponse)
    def videos(request: Request, status: str = "all"):
        rows = library()
        if status != "all":
            rows = [
                row
                for row in rows
                if row["current"] is not None and row["current"].status.value == status
            ]
        return page(request, "videos.html", rows=rows, status=status)

    @app.get("/create", response_class=HTMLResponse)
    def create(request: Request, program: str = "palindrome"):
        return page(
            request,
            "create.html",
            programs=list(catalog.PROGRAMS.values()),
            selected=program_or_404(program),
            audiences=AUDIENCES,
            styles=TEACHING_STYLES,
            voices=VOICES,
            formats=FORMATS,
            default_format=DEFAULT_VIDEO_PROFILE,
        )

    @app.post("/create/review", response_class=HTMLResponse)
    def review(
        request: Request,
        program: str = Form(...),
        audience: str = Form("beginner"),
        teaching_style: str = Form("step_by_step"),
        voice: str = Form("af_heart"),
        video_format: str | None = Depends(_submitted_video_format),
    ):
        """The gate in front of expensive work.

        This is where a duplicate is caught: the decision is shown, and
        generation only starts if the user then presses the button.
        Nothing is generated by loading a page.
        """
        config = _config_from_form(audience, teaching_style, voice, video_format)
        generation_request = build_request(program, config)
        decision = manager.decide(generation_request)
        return page(
            request,
            "review.html",
            selected=program_or_404(program),
            config=config,
            decision=decision,
            llm_provider=generation_request.llm_provider,
            stages=list(catalog.PROGRAMS) and _stage_labels(),
        )

    @app.post("/create/start")
    def start(
        program: str = Form(...),
        audience: str = Form("beginner"),
        teaching_style: str = Form("step_by_step"),
        voice: str = Form("af_heart"),
        video_format: str | None = Depends(_submitted_video_format),
        force: str = Form(""),
    ):
        """Begin generation. `force` is set ONLY by Re-generate."""
        config = _config_from_form(audience, teaching_style, voice, video_format)
        generation_request = build_request(program, config)
        forced = force == "1"
        if not forced:
            decision = manager.decide(generation_request)
            if not decision.should_generate and decision.existing is not None:
                # Already have it. Go and watch it; do no work.
                return RedirectResponse(
                    f"/videos/{program}/v{decision.existing.version}", status_code=303
                )
        job = jobs.start(generation_request, force=forced)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return page(request, "job.html", job=job)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        return page(request, "jobs.html", jobs=jobs.recent(limit=50))

    @app.get("/videos/{slug}", response_class=HTMLResponse)
    def video_latest(request: Request, slug: str):
        program = program_or_404(slug)
        current = registry.current_version(slug)
        if current is None:
            return RedirectResponse(f"/create?program={slug}", status_code=303)
        return video_version(request, slug, current.version)

    @app.get("/videos/{slug}/v{version}", response_class=HTMLResponse)
    def video_version(request: Request, slug: str, version: int):
        program = program_or_404(slug)
        record = registry.get(slug, version)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown version")
        final_path = registry.artifact_path(record, "final_video")
        return page(
            request,
            "video.html",
            program=program,
            version=record,
            versions=registry.versions_for(slug),
            has_video=final_path is not None,
            requested_profile=resolve_video_profile(
                record.request_fingerprint.config.get("video_format")
            ),
            measured=_measured_format(final_path),
            audiences=AUDIENCES,
            styles=TEACHING_STYLES,
            voices=VOICES,
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        """Reports configuration WITHOUT revealing any of it.

        Only whether a credential is present, never its value - a settings
        screen is the classic place a key leaks into a screenshot.
        """
        return page(
            request,
            "settings.html",
            llm_provider=(settings.llm_provider or "mock"),
            renderer_version=_renderer_version(),
            library_root=registry.root.name,
            credentials={
                "OmniRoute / LLM_API_KEY": bool(settings.llm_api_key),
                "OpenRouter": bool(getattr(settings, "openrouter_api_key", None)),
                "Gemini": bool(settings.gemini_api_key),
            },
        )

    # ---- media and artifacts -------------------------------------------

    @app.get("/media/{slug}/v{version}/{name}")
    def media(slug: str, version: int, name: str):
        """Serve one artifact of one version.

        The registry resolves the name and refuses anything outside the
        version directory, so a traversal attempt returns 404 rather than
        a file.
        """
        program_or_404(slug)
        record = registry.get(slug, version)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown version")
        path = registry.artifact_path(record, name)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown artifact")
        return FileResponse(
            path,
            media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
            filename=path.name,
        )

    # ---- JSON API -------------------------------------------------------

    @app.get("/api/programs")
    def api_programs():
        return [p.__dict__ for p in catalog.PROGRAMS.values()]

    @app.get("/api/videos")
    def api_videos():
        return [
            {
                "algorithm": row["program"].slug,
                "title": row["program"].title,
                "status": row["current"].status if row["current"] else None,
                "version": row["current"].version if row["current"] else None,
                "versions": [v.version for v in row["versions"]],
            }
            for row in library()
        ]

    @app.get("/api/videos/{slug}/versions")
    def api_versions(slug: str):
        program_or_404(slug)
        return [v.model_dump(mode="json") for v in registry.versions_for(slug)]

    @app.post("/api/videos/{slug}/decide")
    def api_decide(slug: str, config: TeachingConfig | None = None):
        """What would happen if you asked to generate this - WITHOUT
        generating it."""
        decision = manager.decide(build_request(slug, config or TeachingConfig()))
        return {
            "should_generate": decision.should_generate,
            "reason": decision.reason,
            "changed": decision.changed,
            "existing_version": decision.existing.version if decision.existing else None,
        }

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        """Polled by the job page. Real stage state, never an estimate."""
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return {
            "id": job.id,
            "algorithm": job.algorithm,
            "video_format": resolve_video_profile(job.video_format).id.value,
            "status": job.status,
            "progress": job.progress,
            "version": job.version,
            "error": job.error,
            "stages": [
                {"name": s.name, "label": s.label, "state": s.state, "detail": s.detail}
                for s in job.stages
            ],
            "logs": job.logs[-40:],
            "finished": job.status
            not in (GenerationStatus.QUEUED, GenerationStatus.RUNNING),
        }

    @app.post("/api/jobs/{job_id}/cancel")
    def api_cancel(job_id: str):
        return {"cancelled": jobs.request_cancel(job_id)}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


def _measured_format(path: Path | None) -> dict | None:
    """What the generated file IS, read from the file itself.

    The details page states the format of the artifact, not of a form
    control: the pixels are probed from the final MP4 and matched against
    the profile registry. None when there is no file to measure.
    """
    if path is None:
        return None
    from code2shorts.media.probe import VideoProbeError, probe_video

    try:
        video = probe_video(path)
    except VideoProbeError:
        return None
    profile = next(
        (p for p in PROFILES.values()
         if (p.pixel_width, p.pixel_height) == (video.width, video.height)),
        None,
    )
    return {"width": video.width, "height": video.height, "profile": profile}


def _stage_labels() -> list[str]:
    from code2shorts.generation.manager import STAGE_LABELS

    return list(STAGE_LABELS.values())


def _renderer_version() -> str:
    from code2shorts.generation.fingerprint import RENDERER_VERSION

    return RENDERER_VERSION


app = None  # built by `serve()`; importing this module must start nothing


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)

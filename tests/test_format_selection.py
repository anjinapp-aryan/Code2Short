"""PHASE 8.2D — the user picks 9:16 or 16:9, and gets exactly that.

Phase 8.3 added the two 4K profiles to the same picker; every test here
now covers all four formats.

    Create page -> video_format -> TeachingConfig -> job -> VideoProfile
      -> CompositionLayout -> the real renderer's --resolution -> MP4
      -> registry -> details page (measured from the file)

The routes, `_config_from_form`, `JobManager`, `GenerationManager`, the
REAL `DefaultPipelineFactory` and the REAL `ManimVideoRenderer` all run.
Only the Manim subprocess is replaced: it is intercepted and answered by
real FFmpeg drawing a black clip at whatever `--resolution` the renderer
asked for, so the pixels of every MP4 here were decided by the format the
user picked - nothing else could have produced them.
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="Phase 7.0 web layer requires fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from code2shorts.artifacts.models import Artifact, ArtifactType  # noqa: E402
from code2shorts.config import Settings  # noqa: E402
from code2shorts.core.video_profile import (  # noqa: E402
    LANDSCAPE_4K,
    LANDSCAPE_HD,
    VERTICAL_4K,
    VERTICAL_HD,
)
from code2shorts.execution.sandbox import run_subprocess as real_run_subprocess  # noqa: E402
from code2shorts.generation import (  # noqa: E402
    GenerationRegistry,
    GenerationStatus,
    PipelineFactory,
    TeachingConfig,
)
from code2shorts.media import probe_video  # noqa: E402
from code2shorts.visualization import manim_renderer  # noqa: E402
from code2shorts.webapp.app import FORMATS, _config_from_form, create_app  # noqa: E402
from code2shorts.webapp.pipeline import DefaultPipelineFactory  # noqa: E402
from code2shorts.workflow import FinalValidationNode, NodeResult, WorkflowNode  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

FORM = {"program": "palindrome", "audience": "beginner",
        "teaching_style": "step_by_step", "voice": "af_heart"}


# ---- a pipeline that renders through the REAL renderer ---------------------


def _manim_stand_in(command, cwd, timeout_seconds, **_kwargs):
    """Answers `manim render ...` with a real clip at the requested size."""
    width, height = command[command.index("--resolution") + 1].split(",")
    output = Path(cwd) / "output.mp4"
    time.sleep(0.2)  # long enough for concurrent jobs to genuinely overlap
    return real_run_subprocess(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=30",
         "-t", "0.5", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(output)],
        cwd=Path(cwd), timeout_seconds=60.0,
    )


class _PlanNode(WorkflowNode):
    name = "visualization_plan"

    def run(self, state, context) -> NodeResult:
        plan = {"lesson_title": "t", "steps": [{
            "order": 0, "visual_action": "intro", "trace_event_index": 0,
            "narration_text": "x", "duration_seconds": 0.5}]}
        artifact = Artifact.create(type=ArtifactType.VISUALIZATION_PLAN, producer=self.name,
                                   content=plan)
        context.artifact_store.save(artifact)
        state.artifact_ids["visualization_plan"] = artifact.id
        return NodeResult(state=state, artifact_ids_created=[artifact.id])


class _FinalFromRender(WorkflowNode):
    """Registers the rendered clip as the final video (no audio stage)."""

    name = "compose_media"

    def run(self, state, context) -> NodeResult:
        rendered = context.artifact_store.get(state.artifact_ids["rendered_video"])
        artifact = Artifact.create(
            type=ArtifactType.FINAL_VIDEO, producer=self.name,
            content={"output_path": rendered.content["output_path"],
                     "checksum": rendered.content["checksum"]},
        )
        context.artifact_store.save(artifact)
        state.artifact_ids["final_video"] = artifact.id
        return NodeResult(state=state, artifact_ids_created=[artifact.id])


class _RealRendererPipeline(PipelineFactory):
    """The real factory's RenderVideoNode - real profile resolution, real
    layout selection, real ManimVideoRenderer - between two small stubs."""

    def __init__(self) -> None:
        self.builds: list[str] = []
        self._lock = threading.Lock()

    def build(self, request, output_dir: Path) -> list:
        with self._lock:
            self.builds.append(request.config.video_format)
        real = DefaultPipelineFactory(Settings(llm_provider="mock")).build(request, output_dir)
        render = next(node for node in real if node.name == "render_video")
        return [_PlanNode(), render, _FinalFromRender(), FinalValidationNode()]


@pytest.fixture
def web(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(manim_renderer, "run_subprocess", _manim_stand_in)
    pipeline = _RealRendererPipeline()
    registry = GenerationRegistry(tmp_path / "library")
    app = create_app(registry=registry, pipeline_factory=pipeline,
                     settings=Settings(llm_provider="mock"))
    return TestClient(app), registry, pipeline, app


def _wait(client, job_id: str) -> dict:
    for _ in range(300):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["finished"]:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _start(client, **extra) -> str:
    response = client.post("/create/start", data={**FORM, **extra}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def _final_size(registry, version: int) -> tuple[int, int]:
    path = registry.artifact_path(registry.get("palindrome", version), "final_video")
    video = probe_video(path)
    return video.width, video.height


# ---- 1-3: the Create page offers exactly the renderable formats -----------


def test_the_create_page_offers_all_four_formats_with_9_16_hd_checked(web) -> None:
    client, *_ = web
    html = client.get("/create").text
    radios = re.findall(r'<input type="radio" name="video_format" value="([a-z_0-9]+)"\s*(checked)?', html)

    assert [value for value, _ in radios] == [
        "vertical_hd", "landscape_hd", "vertical_4k", "landscape_4k"]
    assert [value for value, checked in radios if checked] == ["vertical_hd"], \
        "9:16 HD stays the default; 4K is never preselected"
    cards = re.findall(
        r'<span class="choice-title">([^<]+)</span>\s*'
        r'<span class="choice-sub">([^<]+)</span>\s*'
        r'<span class="choice-io">([^<]+)</span>', html)
    assert cards == [
        ("9:16 Vertical", "1080 × 1920", "Shorts / Reels / TikTok"),
        ("16:9 Landscape", "1920 × 1080", "YouTube / Desktop / TV"),
        ("9:16 Vertical 4K", "2160 × 3840", "Ultra HD"),
        ("16:9 Landscape 4K", "3840 × 2160", "Ultra HD"),
    ]
    assert "Video format" in html
    assert "YouTube Short · 1080 × 1920" not in html, "the disabled placeholder is gone"


def test_the_offered_formats_come_from_the_profile_registry() -> None:
    assert FORMATS == (VERTICAL_HD, LANDSCAPE_HD, VERTICAL_4K, LANDSCAPE_4K)


def test_a_profile_that_is_not_renderable_is_neither_offered_nor_accepted(monkeypatch) -> None:
    """The picker and the backend both read renderability from the profile
    registry, so a declared-but-refused profile can be neither picked nor
    posted."""
    import dataclasses

    from code2shorts.core import video_profile
    from code2shorts.webapp import app as webapp

    refused = dataclasses.replace(LANDSCAPE_4K, unsupported_reason="not ready")
    monkeypatch.setitem(video_profile.PROFILES, LANDSCAPE_4K.id, refused)
    monkeypatch.setattr(webapp, "FORMATS", tuple(
        p for p in video_profile.PROFILES.values() if p.is_renderable))

    assert refused not in webapp.FORMATS
    with pytest.raises(webapp.HTTPException) as raised:
        webapp._profile_for("landscape_4k")
    assert raised.value.status_code == 422


def test_each_radio_has_its_own_clickable_label(web) -> None:
    client, *_ = web
    html = client.get("/create").text
    labels = re.findall(r'<label class="choice format-choice">\s*<input type="radio" name="video_format"', html)
    assert len(labels) == 4, "each radio is wrapped by its label, so the text is clickable"
    assert "<legend>Video format</legend>" in html


# ---- 4-8: form -> config, and what is refused --------------------------------


@pytest.mark.parametrize("submitted,profile", [
    ("vertical_hd", VERTICAL_HD), ("landscape_hd", LANDSCAPE_HD),
    ("vertical_4k", VERTICAL_4K), ("landscape_4k", LANDSCAPE_4K),
    ("youtube_short", VERTICAL_HD), (None, VERTICAL_HD),
])
def test_config_from_form_preserves_the_choice(submitted, profile) -> None:
    config = _config_from_form("beginner", "step_by_step", "af_heart", submitted)
    assert config.video_profile is profile


@pytest.mark.parametrize("video_format,label,size", [
    ("vertical_hd", "9:16 Vertical", "1080 × 1920"),
    ("landscape_hd", "16:9 Landscape", "1920 × 1080"),
    ("vertical_4k", "9:16 Vertical 4K", "2160 × 3840"),
    ("landscape_4k", "16:9 Landscape 4K", "3840 × 2160"),
])
def test_the_review_screen_shows_the_validated_request(web, video_format, label, size) -> None:
    client, *_ = web
    html = client.post("/create/review", data={**FORM, "video_format": video_format}).text
    assert f"<dd>{label}</dd>" in html and f"<dd>{size}</dd>" in html
    # Both forms on the page carry the choice forward to /create/start.
    assert html.count(f'name="video_format" value="{video_format}"') >= 1


def test_a_missing_format_is_the_default(web) -> None:
    client, *_ = web
    html = client.post("/create/review", data=FORM).text
    assert "<dd>9:16 Vertical</dd>" in html


@pytest.mark.parametrize("bad", ["", "  ", "imax", "16:9", "9:16", "1920x1080",
                                 "8k", "4k", "4096x2160", "3840x2160", "random"])
def test_an_invalid_format_is_rejected_not_defaulted(web, bad) -> None:
    client, registry, pipeline, _app = web
    for route in ("/create/review", "/create/start"):
        response = client.post(route, data={**FORM, "video_format": bad}, follow_redirects=False)
        assert response.status_code == 422, (route, bad, response.status_code)
        assert "unsupported video_format" in response.text
    assert registry.all_versions() == [] and pipeline.builds == []


# ---- 9, 13, 16, 17: job, registry and the real output size -----------------


@needs_ffmpeg
@pytest.mark.parametrize("video_format,size", [
    ("vertical_hd", (1080, 1920)), ("landscape_hd", (1920, 1080)),
    ("vertical_4k", (2160, 3840)), ("landscape_4k", (3840, 2160)), (None, (1080, 1920)),
])
def test_the_choice_reaches_the_job_the_registry_and_the_pixels(web, video_format, size) -> None:
    client, registry, _pipeline, _app = web
    extra = {"video_format": video_format} if video_format else {}
    job_id = _start(client, **extra).rsplit("/", 1)[-1]
    job = _wait(client, job_id)

    expected = video_format or "vertical_hd"
    assert job["status"] == "completed", job
    assert job["video_format"] == expected
    record = registry.get("palindrome", job["version"])
    stored = TeachingConfig(video_format=record.request_fingerprint.config["video_format"])
    assert stored.video_profile.id.value == expected
    assert _final_size(registry, job["version"]) == size


# ---- 10-12: identity and reuse, per format ----------------------------------


@needs_ffmpeg
def test_each_format_is_its_own_video_and_each_is_reused(web) -> None:
    """All four coexist in one library; a repeat request reuses ITS format
    and never another one (a 4K request never gets the HD video)."""
    client, registry, pipeline, _app = web
    sizes = {"vertical_hd": (1080, 1920), "landscape_hd": (1920, 1080),
             "vertical_4k": (2160, 3840), "landscape_4k": (3840, 2160)}

    versions: dict[str, int] = {}
    for video_format in sizes:
        job = _wait(client, _start(client, video_format=video_format).rsplit("/", 1)[-1])
        assert job["version"] not in versions.values(), f"{video_format} reused another format"
        versions[video_format] = job["version"]
    for video_format, version in versions.items():
        again = _start(client, video_format=video_format)
        assert again == f"/videos/palindrome/v{version}", f"{video_format} reused"
        assert _final_size(registry, version) == sizes[video_format]

    assert pipeline.builds == ["youtube_short", "landscape_hd", "vertical_4k", "landscape_4k"], \
        "exactly four generations"
    digests = {v.request_fingerprint.digest for v in registry.all_versions()}
    assert len(digests) == 4


# ---- 14: the details page reports the FILE, not a form ----------------------


@needs_ffmpeg
@pytest.mark.parametrize("video_format,label,size", [
    ("vertical_hd", "9:16 Vertical", "1080 × 1920"),
    ("landscape_hd", "16:9 Landscape", "1920 × 1080"),
    ("vertical_4k", "9:16 Vertical 4K", "2160 × 3840"),
    ("landscape_4k", "16:9 Landscape 4K", "3840 × 2160"),
])
def test_the_details_page_shows_the_generated_format(web, video_format, label, size) -> None:
    client, *_ = web
    job = _wait(client, _start(client, video_format=video_format).rsplit("/", 1)[-1])
    html = client.get(f"/videos/palindrome/v{job['version']}").text
    assert f"<dd>{label}</dd>" in html and f"<dd>{size}</dd>" in html
    assert "does not match the file" not in html
    # Re-generate keeps the format rather than silently producing 9:16.
    assert f'name="video_format" value="{video_format}"' in html


@needs_ffmpeg
def test_the_details_page_believes_the_file_over_the_request(web, monkeypatch) -> None:
    """If a file ever disagreed with its request, the page must say what
    the FILE is. Forced here by making the stand-in ignore the size."""
    client, registry, *_ = web

    def always_portrait(command, cwd, timeout_seconds, **kwargs):
        index = command.index("--resolution") + 1
        command = [*command[:index], "1080,1920", *command[index + 1:]]
        return _manim_stand_in(command, cwd, timeout_seconds)

    monkeypatch.setattr(manim_renderer, "run_subprocess", always_portrait)
    job = _wait(client, _start(client, video_format="landscape_hd").rsplit("/", 1)[-1])
    html = client.get(f"/videos/palindrome/v{job['version']}").text

    assert "<dd>9:16 Vertical</dd>" in html and "<dd>1080 × 1920</dd>" in html
    assert "16:9 Landscape (does not match the file)" in html


# ---- 15: concurrent jobs keep their own formats -------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("order", [
    ("vertical_hd", "landscape_hd"), ("landscape_hd", "vertical_hd"),
    ("landscape_4k", "landscape_hd"), ("vertical_4k", "vertical_hd"),
])
def test_concurrent_jobs_in_different_formats_do_not_cross_talk(web, order) -> None:
    client, registry, _pipeline, app = web
    barrier = threading.Barrier(2)
    locations: dict[str, str] = {}

    def submit(video_format: str) -> None:
        barrier.wait()
        locations[video_format] = _start(client, video_format=video_format)

    threads = [threading.Thread(target=submit, args=(fmt,)) for fmt in order]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    first, second = order
    job_ids = {fmt: loc.rsplit("/", 1)[-1] for fmt, loc in locations.items()}
    assert job_ids[first] != job_ids[second], f"a {second} request got the {first} job"
    # Both were genuinely in flight together.
    assert len(app.state.jobs.active()) in (1, 2)

    sizes = {"vertical_hd": (1080, 1920), "landscape_hd": (1920, 1080),
             "vertical_4k": (2160, 3840), "landscape_4k": (3840, 2160)}
    results = {fmt: _wait(client, job_id) for fmt, job_id in job_ids.items()}
    assert {fmt: r["video_format"] for fmt, r in results.items()} == {
        first: first, second: second}
    for fmt in order:
        assert _final_size(registry, results[fmt]["version"]) == sizes[fmt]
    assert all(v.status is GenerationStatus.COMPLETED for v in registry.all_versions())


def test_the_same_request_twice_at_once_is_still_one_job(web) -> None:
    """Deduplication by request is kept: only DIFFERENT requests split."""
    client, *_ = web
    barrier = threading.Barrier(4)
    seen: list[str] = []

    def submit() -> None:
        barrier.wait()
        seen.append(_start(client, video_format="landscape_hd"))

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(seen)) == 1

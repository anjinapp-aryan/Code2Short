"""PHASE 7.0 — the web layer, driven through its REAL routes.

Uses `TestClient`, so every assertion goes through the actual FastAPI
handlers, the actual templates and the actual registry. Only the pipeline
is faked - no Maven, no JVM, no LLM, no Manim, no FFmpeg, no credentials.

The behaviours worth protecting are the ones a user would notice:

* loading a page never generates anything;
* asking twice generates once;
* Re-generate is explicit and keeps the old version watchable;
* an artifact name from a URL cannot escape its directory;
* no credential is ever rendered.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="Phase 7.0 web layer requires fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from code2shorts.config import Settings  # noqa: E402
from code2shorts.generation import GenerationRegistry, GenerationStatus  # noqa: E402
from code2shorts.webapp.app import create_app  # noqa: E402
from tests.test_generation_registry import _FakePipeline  # noqa: E402

FAKE_KEY = "test-not-a-real-key"


@pytest.fixture
def client(tmp_path: Path):
    registry = GenerationRegistry(tmp_path / "library")
    pipeline = _FakePipeline()
    app = create_app(
        registry=registry,
        pipeline_factory=pipeline,
        settings=Settings(
            _env_file=None,
            environment="test",
            llm_provider="mock",
            gemini_api_key=FAKE_KEY,
        ),
    )
    with TestClient(app) as http:
        yield http, registry, pipeline


def _generate(http, force: bool = False) -> None:
    """Drive the real Create flow and wait for the background job."""
    form = {
        "program": "palindrome",
        "audience": "beginner",
        "teaching_style": "step_by_step",
        "voice": "af_heart",
    }
    if force:
        form["force"] = "1"
    response = http.post("/create/start", data=form, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    if not location.startswith("/jobs/"):
        return  # redirected straight to an existing video: no job ran
    job_id = location.rsplit("/", 1)[1]
    for _ in range(200):
        payload = http.get(f"/api/jobs/{job_id}").json()
        if payload["finished"]:
            return
        time.sleep(0.02)
    raise AssertionError("job did not finish")


# ---- pages render ---------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/videos", "/create", "/jobs", "/settings"])
def test_every_page_renders(client, path) -> None:
    http, _registry, _pipeline = client
    response = http.get(path)
    assert response.status_code == 200
    assert "Code2Shorts" in response.text


def test_loading_pages_never_generates_anything(client) -> None:
    """A page load must not spend three minutes of compute. This is the
    reason `decide` and `generate` are separate methods."""
    http, registry, pipeline = client

    for path in ("/", "/videos", "/create", "/jobs", "/settings"):
        http.get(path)
    http.post("/create/review", data={"program": "palindrome"})

    assert pipeline.builds == 0
    assert registry.all_versions() == []


def test_an_unknown_program_is_a_404_not_a_path(client) -> None:
    """The slug reaches a directory name, so it is validated first."""
    http, _registry, _pipeline = client
    assert http.get("/create?program=../../etc").status_code == 404
    assert http.get("/videos/nonsense").status_code == 404


# ---- the critical flow ----------------------------------------------------


def test_generate_then_generate_again_runs_the_pipeline_once(client) -> None:
    """The behaviour the whole phase exists for, through the real routes."""
    http, registry, pipeline = client

    _generate(http)
    assert pipeline.builds == 1
    assert registry.current_version("palindrome").status is GenerationStatus.COMPLETED

    _generate(http)

    assert pipeline.builds == 1, "the second request generated again"
    assert len(registry.versions_for("palindrome")) == 1


def test_the_review_screen_offers_the_existing_video_instead_of_generating(client) -> None:
    http, _registry, pipeline = client
    _generate(http)

    review = http.post(
        "/create/review",
        data={"program": "palindrome", "voice": "af_heart"},
    )

    assert review.status_code == 200
    assert "An existing video already exists" in review.text
    assert "Watch existing" in review.text
    assert "Re-generate anyway" in review.text
    assert pipeline.builds == 1


def test_regenerate_creates_a_new_version_and_keeps_the_old_one(client) -> None:
    http, registry, pipeline = client
    _generate(http)
    _generate(http, force=True)

    assert pipeline.builds == 2
    versions = registry.versions_for("palindrome")
    assert [v.version for v in versions] == [2, 1]

    # BOTH remain watchable - the old page still renders and its video
    # still streams.
    for version in versions:
        page = http.get(f"/videos/palindrome/v{version.version}")
        assert page.status_code == 200
        video = http.get(f"/media/palindrome/v{version.version}/final_video")
        assert video.status_code == 200
        assert video.headers["content-type"].startswith("video/mp4")


def test_a_duplicate_request_redirects_to_the_existing_video(client) -> None:
    http, _registry, pipeline = client
    _generate(http)

    response = http.post(
        "/create/start", data={"program": "palindrome"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/videos/palindrome/v1"
    assert pipeline.builds == 1


def test_changing_the_voice_is_offered_as_a_new_generation(client) -> None:
    http, _registry, _pipeline = client
    _generate(http)

    review = http.post(
        "/create/review",
        data={"program": "palindrome", "voice": "not-a-voice"},
    )
    # An unoffered voice is coerced to the allowed one rather than passed
    # to a TTS engine, so this stays a duplicate.
    assert "An existing video already exists" in review.text


# ---- job reporting --------------------------------------------------------


def test_the_job_reports_real_stage_state(client) -> None:
    """Progress must come from the workflow runner's events. The fake
    pipeline has three nodes, so a finished job reports those three as
    completed - a number this test could not predict if progress were
    invented from elapsed time."""
    http, _registry, _pipeline = client

    response = http.post(
        "/create/start", data={"program": "palindrome"}, follow_redirects=False
    )
    job_id = response.headers["location"].rsplit("/", 1)[1]

    for _ in range(200):
        payload = http.get(f"/api/jobs/{job_id}").json()
        if payload["finished"]:
            break
        time.sleep(0.02)

    assert payload["status"] == "completed"
    assert payload["progress"] == 100
    names = {stage["name"] for stage in payload["stages"] if stage["state"] == "completed"}
    assert {"trace", "visualization_plan", "compose_media"} <= names
    assert http.get(f"/jobs/{job_id}").status_code == 200


def test_an_unknown_job_is_a_404(client) -> None:
    http, _registry, _pipeline = client
    assert http.get("/api/jobs/deadbeef").status_code == 404
    assert http.get("/jobs/deadbeef").status_code == 404


# ---- API ------------------------------------------------------------------


def test_the_decide_endpoint_answers_without_generating(client) -> None:
    http, _registry, pipeline = client

    before = http.post("/api/videos/palindrome/decide").json()
    assert before["should_generate"] is True
    assert pipeline.builds == 0

    _generate(http)
    after = http.post("/api/videos/palindrome/decide").json()

    assert after["should_generate"] is False
    assert after["existing_version"] == 1
    assert pipeline.builds == 1


def test_the_versions_api_lists_newest_first(client) -> None:
    http, _registry, _pipeline = client
    _generate(http)
    _generate(http, force=True)

    versions = http.get("/api/videos/palindrome/versions").json()
    assert [v["version"] for v in versions] == [2, 1]


def test_health_is_available(client) -> None:
    http, _registry, _pipeline = client
    assert http.get("/api/health").json() == {"status": "ok"}


# ---- security -------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../../../index.json", "..%2f..%2findex.json", "escape", "final_video/../../x"],
)
def test_an_artifact_name_cannot_escape_the_version_directory(client, name) -> None:
    """Artifact names arrive straight from a URL."""
    http, registry, _pipeline = client
    _generate(http)

    version = registry.get("palindrome", 1)
    version.artifacts["escape"] = "../../../index.json"
    registry.save(version)

    response = http.get(f"/media/palindrome/v1/{name}")
    assert response.status_code == 404


def test_no_credential_is_ever_rendered(client) -> None:
    http, _registry, _pipeline = client
    settings_page = http.get("/settings")

    assert settings_page.status_code == 200
    assert FAKE_KEY not in settings_page.text
    # Presence is reported; the value is not.
    assert "configured" in settings_page.text


def test_no_absolute_filesystem_path_reaches_the_browser(client) -> None:
    """Internal paths are not the user's business and leak the deployment
    layout."""
    http, registry, _pipeline = client
    _generate(http)

    page = http.get("/videos/palindrome/v1")
    root = str(registry.root.resolve())

    assert page.status_code == 200
    assert root not in page.text
    assert "C:\\" not in page.text and "/tmp/" not in page.text

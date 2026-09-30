"""PHASE 7.1 — production-safety verification of the Phase 7 UI layer.

Scenarios 4, 5, 9 and 10 of the Phase 7.1 brief, as executable evidence:

    4  a FAILED regeneration never destroys a working version
    5  partial or temporary artifacts never become a valid generation
    9  registry safety: atomic writes, containment, no traversal
    10 security: autoescaping, no eval/exec/compile, no shell strings,
       no credential or filesystem path reaching the browser

These run without Maven, a JVM, an LLM, Manim, FFmpeg or credentials -
they test the layer in FRONT of the pipeline, which is the layer Phase 7
added. The real pipeline is verified separately, by a live run, because a
credentialed run does not belong in the unit suite (tests/conftest.py
forces CODE2SHORTS_ENV=test precisely so it cannot happen by accident).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="Phase 7 web layer requires fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from code2shorts.artifacts.store import InMemoryArtifactStore  # noqa: E402
from code2shorts.config import Settings  # noqa: E402
from code2shorts.generation import (  # noqa: E402
    GenerationManager,
    GenerationRegistry,
    GenerationStatus,
    TeachingConfig,
    fingerprint_request,
)
from code2shorts.webapp.app import create_app  # noqa: E402
from tests.test_generation_registry import SOURCE, _FakePipeline, _request  # noqa: E402

CANARY = "phase71-canary-not-a-real-key"


@pytest.fixture
def library(tmp_path: Path) -> GenerationRegistry:
    return GenerationRegistry(tmp_path / "library")


def _fingerprint():
    return fingerprint_request("palindrome", SOURCE, TeachingConfig(), "mock")


# ==========================================================================
# SCENARIO 4 — a failed regeneration never destroys a working version
# ==========================================================================


@pytest.mark.parametrize(
    "fail_at", ["trace", "visualization_plan", "compose_media"]
)
def test_a_failure_at_any_stage_leaves_the_working_version_intact(
    library, fail_at
) -> None:
    """Injected failure at an early, middle and final stage.

    The final stage matters most: `compose_media` is where the MP4 is
    written, so a failure there is the case where a half-written file
    could plausibly be mistaken for a finished one.
    """
    good = GenerationManager(library, _FakePipeline(), InMemoryArtifactStore())
    first = good.generate(_request())
    original_video = library.artifact_path(first, "final_video")
    original_bytes = original_video.read_bytes()

    broken = GenerationManager(
        library, _FakePipeline(fail_at=fail_at), InMemoryArtifactStore()
    )
    second = broken.generate(_request(), force=True)

    # The failure is RECORDED, not hidden...
    assert second.status is GenerationStatus.FAILED
    assert second.version == 2
    assert second.error and fail_at in str(second.error) or second.error

    # ...v1 is untouched in every respect.
    assert library.get("palindrome", 1).status is GenerationStatus.COMPLETED
    assert library.current_version("palindrome").version == 1
    assert original_video.read_bytes() == original_bytes
    assert library.artifact_path(library.get("palindrome", 1), "final_video") is not None


def test_a_failed_version_is_never_offered_for_reuse(library) -> None:
    broken = GenerationManager(
        library, _FakePipeline(fail_at="trace"), InMemoryArtifactStore()
    )
    failed = broken.generate(_request())

    assert not failed.is_reusable
    assert library.find_reusable(_fingerprint()) is None
    assert library.current_version("palindrome") is None


# ==========================================================================
# SCENARIO 5 — partial artifacts never become a valid generation
# ==========================================================================


def test_an_mp4_on_disk_is_not_a_generation(library) -> None:
    """A file in the right place, with no record of what made it."""
    planted = library.version_dir("palindrome", 1)
    planted.mkdir(parents=True)
    (planted / "final.mp4").write_bytes(b"\x00" * 4096)

    assert library.current_version("palindrome") is None
    assert library.find_reusable(_fingerprint()) is None


def test_render_leftovers_are_not_a_generation(library) -> None:
    """Manim partial movie files, a temp FFmpeg output and an orphaned
    narration.wav are all normal debris of a run that did not finish."""
    version = library.create_version("palindrome", _fingerprint())
    directory = library.version_dir("palindrome", 1)
    partials = directory / "render" / "media" / "videos" / "partial_movie_files"
    partials.mkdir(parents=True)
    (partials / "0001.mp4").write_bytes(b"partial")
    (directory / "final.mp4.tmp").write_bytes(b"temp")
    (directory / "narration.wav").write_bytes(b"orphan")

    assert version.status is GenerationStatus.QUEUED
    assert not version.is_reusable
    assert library.current_version("palindrome") is None


def test_a_completed_status_without_a_video_is_still_not_reusable(library) -> None:
    """Both conditions are required. Status alone is a claim; the video is
    the evidence."""
    version = library.create_version("palindrome", _fingerprint())
    version.status = GenerationStatus.COMPLETED
    version.artifacts = {"trace": "trace.json"}      # no final_video
    library.save(version)

    assert not version.is_reusable
    assert library.current_version("palindrome") is None
    assert library.find_reusable(_fingerprint()) is None


def test_a_registered_video_whose_file_vanished_is_not_served(library) -> None:
    """The index is a cache of things on disk; disk is the authority."""
    manager = GenerationManager(library, _FakePipeline(), InMemoryArtifactStore())
    version = manager.generate(_request())
    library.artifact_path(version, "final_video").unlink()

    assert library.artifact_path(version, "final_video") is None


# ==========================================================================
# SCENARIO 9 — registry safety
# ==========================================================================


TRAVERSALS = [
    "../secret.txt",
    "../../secret.txt",
    "../../../secret.txt",
    "..\\secret.txt",
    "..\\..\\secret.txt",
    "../..\\secret.txt",              # mixed separators
    "./../secret.txt",
    "subdir/../../secret.txt",
]


@pytest.mark.parametrize("relative", TRAVERSALS)
def test_no_relative_traversal_escapes_the_version_directory(
    library, tmp_path, relative
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")

    version = library.create_version("palindrome", _fingerprint())
    version.artifacts = {"escape": relative}
    library.save(version)

    assert library.artifact_path(version, "escape") is None


@pytest.mark.parametrize(
    "absolute",
    [
        "C:\\Windows\\System32\\drivers\\etc\\hosts",
        "C:/Windows/win.ini",
        "/etc/passwd",
        "\\\\server\\share\\file.txt",                 # UNC
    ],
)
def test_no_absolute_path_escapes_the_version_directory(library, absolute) -> None:
    version = library.create_version("palindrome", _fingerprint())
    version.artifacts = {"escape": absolute}
    library.save(version)

    assert library.artifact_path(version, "escape") is None


def test_a_real_existing_absolute_path_is_still_refused(library, tmp_path) -> None:
    """Not just "the file is missing" - a file that genuinely exists
    outside the directory must still be refused."""
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")

    version = library.create_version("palindrome", _fingerprint())
    version.artifacts = {"escape": str(outside)}
    library.save(version)

    assert outside.is_file()
    assert library.artifact_path(version, "escape") is None


def test_the_index_write_is_atomic(library, monkeypatch) -> None:
    """A crashed write must leave the PREVIOUS index intact, not a
    truncated file - losing the record of a three-minute render to a
    partial write is not an acceptable failure mode."""
    library.create_version("palindrome", _fingerprint())
    good = library.index_path.read_text(encoding="utf-8")

    original = Path.replace

    def explode(self, target):
        raise OSError("simulated crash during rename")

    monkeypatch.setattr(Path, "replace", explode)
    with pytest.raises(OSError):
        library.create_version("palindrome", _fingerprint())
    monkeypatch.setattr(Path, "replace", original)

    assert library.index_path.read_text(encoding="utf-8") == good
    assert len(library.all_versions()) == 1
    # ...and no temporary file was left behind.
    assert not list(library.root.glob("*.tmp"))


def test_a_corrupt_index_does_not_take_the_app_down(library) -> None:
    library.create_version("palindrome", _fingerprint())
    library.index_path.write_text("{ truncated", encoding="utf-8")

    assert library.all_versions() == []
    assert library.current_version("palindrome") is None


def test_the_registry_records_no_secret(library) -> None:
    """Generation metadata is written to disk and shown in the UI, so a
    credential must never reach it."""
    manager = GenerationManager(library, _FakePipeline(), InMemoryArtifactStore())
    manager.generate(_request())

    index = library.index_path.read_text(encoding="utf-8")
    for field in ("api_key", "apikey", "secret", "token", "password", "authorization"):
        assert field not in index.lower(), f"{field!r} appears in the registry index"


# ==========================================================================
# SCENARIO 10 — security
# ==========================================================================


WEB_PACKAGES = ("webapp", "generation")


def _phase7_sources() -> list[Path]:
    root = Path(__file__).resolve().parents[1] / "src" / "code2shorts"
    return [path for package in WEB_PACKAGES for path in (root / package).rglob("*.py")]


def test_phase7_code_contains_no_dynamic_execution() -> None:
    """AST, not a text search: a module is allowed to name in a comment
    the construct it forbids."""
    offenders = []
    for path in _phase7_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None)
                if name in ("eval", "exec", "compile", "__import__"):
                    offenders.append(f"{path.name}:{node.lineno} {name}")
    assert not offenders, f"dynamic execution in the Phase 7 layer: {offenders}"


def test_phase7_code_never_builds_a_shell_command() -> None:
    offenders = []
    for path in _phase7_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                offenders.append(f"{path.name}:{node.value.lineno} shell=")
            if isinstance(node, ast.Attribute) and node.attr in (
                "system", "popen", "check_output", "call"
            ):
                value = getattr(node.value, "id", "")
                if value in ("os", "subprocess"):
                    offenders.append(f"{path.name}:{node.lineno} {value}.{node.attr}")
    assert not offenders, f"shell execution in the Phase 7 layer: {offenders}"


def test_templates_are_autoescaped(tmp_path: Path) -> None:
    """Jinja autoescaping is the reason a template engine is used rather
    than string formatting - turning it off would silently make every
    untrusted value an injection point."""
    app = create_app(
        registry=GenerationRegistry(tmp_path / "library"),
        pipeline_factory=_FakePipeline(),
        settings=Settings(_env_file=None, environment="test"),
    )
    from code2shorts.webapp import app as app_module

    with TestClient(app):
        pass
    source = (Path(app_module.__file__)).read_text(encoding="utf-8")
    assert "autoescape = True" in source
    assert "autoescape = False" not in source
    assert "| safe" not in _all_template_text()
    assert "|safe" not in _all_template_text()


def _all_template_text() -> str:
    root = Path(__file__).resolve().parents[1] / "src" / "code2shorts" / "webapp" / "templates"
    return "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.html"))


def test_no_template_disables_escaping() -> None:
    text = _all_template_text()
    for unsafe in ("{% autoescape false %}", "|safe", "| safe", "Markup("):
        assert unsafe not in text, f"{unsafe!r} found in a template"


def _strip_js_comments(source: str) -> str:
    """Comments out, code in.

    The first version of this test failed on the poller's own comment
    saying it never uses innerHTML - the exact hazard CLAUDE.md records
    for security-property tests: a module documents what it forbids, and a
    text search then flags its own documentation. Python has no JS parser
    in the stdlib, so comments are removed before matching rather than a
    parser being added as a dependency.
    """
    import re

    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


def test_the_job_poller_never_writes_untrusted_html() -> None:
    """Stage details and log lines carry provider error text."""
    raw = (
        Path(__file__).resolve().parents[1]
        / "src" / "code2shorts" / "webapp" / "static" / "job.js"
    ).read_text(encoding="utf-8")
    script = _strip_js_comments(raw)

    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script
    assert "textContent" in script, "the poller must write text, not markup"


def test_no_credential_reaches_the_page_or_the_javascript(tmp_path: Path) -> None:
    registry = GenerationRegistry(tmp_path / "library")
    app = create_app(
        registry=registry,
        pipeline_factory=_FakePipeline(),
        settings=Settings(
            _env_file=None,
            environment="test",
            llm_provider="mock",
            gemini_api_key=CANARY,
            llm_api_key=CANARY,
            openrouter_api_key=CANARY,
        ),
    )
    with TestClient(app) as http:
        manager = GenerationManager(registry, _FakePipeline(), InMemoryArtifactStore())
        manager.generate(_request())
        for path in ("/", "/videos", "/create", "/settings", "/jobs",
                     "/videos/palindrome/v1", "/api/videos",
                     "/api/videos/palindrome/versions", "/static/job.js"):
            body = http.get(path).text
            assert CANARY not in body, f"credential leaked into {path}"


def test_unoffered_form_values_are_rejected_not_forwarded(tmp_path: Path) -> None:
    """A voice string reaches a TTS engine and an audience string reaches a
    prompt, so neither may be whatever the form posted."""
    app = create_app(
        registry=GenerationRegistry(tmp_path / "library"),
        pipeline_factory=_FakePipeline(),
        settings=Settings(_env_file=None, environment="test"),
    )
    with TestClient(app) as http:
        response = http.post(
            "/create/review",
            data={
                "program": "palindrome",
                "audience": "<script>alert(1)</script>",
                "teaching_style": "../../etc",
                "voice": "rm -rf /",
            },
        )
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "rm -rf /" not in response.text
    assert "Beginner" in response.text          # coerced to the offered value


def test_an_unknown_program_never_becomes_a_path(tmp_path: Path) -> None:
    app = create_app(
        registry=GenerationRegistry(tmp_path / "library"),
        pipeline_factory=_FakePipeline(),
        settings=Settings(_env_file=None, environment="test"),
    )
    with TestClient(app) as http:
        for slug in ("../palindrome", "..%2Fpalindrome", "two_sum", "nonsense"):
            assert http.get(f"/videos/{slug}").status_code == 404
            assert http.post(
                "/create/review", data={"program": slug}
            ).status_code == 404


def test_generation_metadata_is_json_serialisable_and_flat(tmp_path: Path) -> None:
    """The index is read by a browser-facing API, so it must contain only
    plain data - no object that could carry a live handle."""
    registry = GenerationRegistry(tmp_path / "library")
    GenerationManager(registry, _FakePipeline(), InMemoryArtifactStore()).generate(
        _request()
    )
    payload = json.loads(registry.index_path.read_text(encoding="utf-8"))
    assert isinstance(payload["versions"], list)
    json.dumps(payload)          # raises if anything is not plain data

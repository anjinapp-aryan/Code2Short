"""PHASE 8.0 — video profiles: one content model, several output formats.

What this phase promises, and what these tests hold it to:

    * four named profiles, with the numbers each one claims;
    * VERTICAL_HD (9:16, 1080x1920) is the default, and every Phase 7
      request - which never named a format - still resolves to it and
      still hashes to the SAME fingerprint it always did;
    * a different format is a different video, so it can never be served
      from the cache of another;
    * formats that have no composition or benchmark yet refuse to render
      before any work starts;
    * the trace and the teaching contracts know nothing about format.

No Maven, JVM, Manim, FFmpeg or model is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from code2shorts.artifacts.store import InMemoryArtifactStore
from code2shorts.core.video_profile import (
    DEFAULT_VIDEO_PROFILE,
    LANDSCAPE_4K,
    LANDSCAPE_HD,
    PROFILES,
    VERTICAL_4K,
    VERTICAL_HD,
    InvalidVideoProfileError,
    Orientation,
    UnknownVideoProfileError,
    UnsupportedVideoProfileError,
    VideoProfile,
    VideoProfileId,
    resolve_video_profile,
)
from code2shorts.generation import (
    GenerationManager,
    GenerationRegistry,
    RequestFingerprint,
    TeachingConfig,
    fingerprint_request,
)
from tests.test_generation_registry import SOURCE, _FakePipeline, _request

# ---- TESTS 1-4 — the four product profiles -------------------------------


@pytest.mark.parametrize(
    "profile,aspect,orientation,width,height",
    [
        (VERTICAL_HD, "9:16", Orientation.PORTRAIT, 1080, 1920),
        (LANDSCAPE_HD, "16:9", Orientation.LANDSCAPE, 1920, 1080),
        (VERTICAL_4K, "9:16", Orientation.PORTRAIT, 2160, 3840),
        (LANDSCAPE_4K, "16:9", Orientation.LANDSCAPE, 3840, 2160),
    ],
)
def test_each_profile_is_what_it_claims(profile, aspect, orientation, width, height) -> None:
    assert profile.aspect_ratio == aspect
    assert profile.orientation is orientation
    assert (profile.pixel_width, profile.pixel_height) == (width, height)
    assert profile.resolution == f"{width}x{height}"


def test_exactly_the_four_product_profiles_exist() -> None:
    assert set(PROFILES) == set(VideoProfileId)


# ---- TEST 5 — the default --------------------------------------------------


def test_the_default_is_the_accepted_vertical_hd_baseline() -> None:
    assert DEFAULT_VIDEO_PROFILE is VERTICAL_HD
    assert resolve_video_profile(None) is VERTICAL_HD
    assert resolve_video_profile("") is VERTICAL_HD
    assert TeachingConfig().video_profile is VERTICAL_HD


def test_every_product_profile_is_renderable() -> None:
    """Phase 8.3 benchmarked native 4K, so all four render. (Until 8.2C,
    LANDSCAPE_HD was refused; until 8.3, both 4K profiles were.)"""
    for profile in (VERTICAL_HD, LANDSCAPE_HD, VERTICAL_4K, LANDSCAPE_4K):
        assert profile.is_renderable
        profile.require_renderable()


def test_a_profile_with_a_reason_is_refused_and_says_why() -> None:
    """The refusal mechanism stays: a profile that is declared but not
    ready names its size and its reason rather than rendering."""
    import dataclasses

    draft = dataclasses.replace(LANDSCAPE_4K, unsupported_reason="not ready")
    assert not draft.is_renderable
    with pytest.raises(UnsupportedVideoProfileError, match="3840x2160.*not ready"):
        draft.require_renderable()


# ---- TEST 6 — format participates in generation identity ------------------


def _fingerprint(video_format: str | None = None) -> RequestFingerprint:
    config = TeachingConfig() if video_format is None else TeachingConfig(video_format=video_format)
    return fingerprint_request("palindrome", SOURCE, config, "mock")


def test_same_content_and_same_profile_is_the_same_video() -> None:
    assert _fingerprint("landscape_hd").digest == _fingerprint("landscape_hd").digest
    # An id and the identity key it records name ONE profile, so they must
    # not split one video into two cache entries.
    assert _fingerprint("vertical_hd").digest == _fingerprint("youtube_short").digest
    assert _fingerprint("Vertical_HD ").digest == _fingerprint().digest


def test_same_content_in_a_different_profile_never_collides() -> None:
    digests = {profile.id: _fingerprint(profile.id.value).digest for profile in PROFILES.values()}
    assert len(set(digests.values())) == len(PROFILES)
    # The two pairs the brief names explicitly.
    assert digests[VideoProfileId.VERTICAL_HD] != digests[VideoProfileId.LANDSCAPE_HD]
    assert digests[VideoProfileId.VERTICAL_HD] != digests[VideoProfileId.VERTICAL_4K]
    assert digests[VideoProfileId.LANDSCAPE_HD] != digests[VideoProfileId.LANDSCAPE_4K]


def test_identity_keys_are_unique_and_pinned_to_their_pixels() -> None:
    """A key is a promise about pixels. If someone changes a profile's size
    without giving it a new key, old videos of the old size would be served
    as the new one - this pin makes that change fail loudly."""
    pinned = {
        "youtube_short": (1080, 1920),
        "landscape_hd": (1920, 1080),
        "vertical_4k": (2160, 3840),
        "landscape_4k": (3840, 2160),
    }
    actual = {p.identity_key: (p.pixel_width, p.pixel_height) for p in PROFILES.values()}
    assert actual == pinned


def test_a_different_format_is_reported_as_a_config_change(tmp_path: Path) -> None:
    generator = GenerationManager(
        GenerationRegistry(tmp_path / "library"), _FakePipeline(), InMemoryArtifactStore()
    )
    generator.generate(_request())

    decision = generator.decide(_request(config=TeachingConfig(video_format="landscape_hd")))

    assert decision.should_generate, "a 16:9 request must never be served the 9:16 video"
    assert decision.changed == ["config"]


# ---- TEST 7 — invalid input fails clearly ---------------------------------


def _profile(**overrides) -> VideoProfile:
    values = dict(
        id=VideoProfileId.VERTICAL_HD,
        label="test",
        aspect_width=9,
        aspect_height=16,
        pixel_width=1080,
        pixel_height=1920,
        identity_key="test",
    )
    values.update(overrides)
    return VideoProfile(**values)


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"pixel_width": 0}, "positive"),
        ({"pixel_height": -1920}, "positive"),
        ({"pixel_width": 1081}, "odd dimension"),
        ({"pixel_width": 1920, "pixel_height": 1920}, "not 9:16"),
        ({"aspect_width": 18, "aspect_height": 32}, "lowest terms"),
        ({"aspect_width": 1, "aspect_height": 1, "pixel_width": 1080, "pixel_height": 1080},
         "square"),
    ],
)
def test_a_contradictory_profile_cannot_be_constructed(overrides, message) -> None:
    with pytest.raises(InvalidVideoProfileError, match=message):
        _profile(**overrides)


def test_an_unknown_format_name_fails_and_lists_the_real_ones() -> None:
    with pytest.raises(UnknownVideoProfileError, match="vertical_hd"):
        resolve_video_profile("imax")
    with pytest.raises(ValidationError, match="unknown video format"):
        TeachingConfig(video_format="1080x1921")


def test_profiles_are_immutable() -> None:
    with pytest.raises(AttributeError):
        VERTICAL_HD.pixel_width = 720  # type: ignore[misc]


# ---- TEST 8 — backward compatibility with Phase 7 -------------------------


def test_a_phase_7_request_with_no_format_resolves_to_vertical_hd() -> None:
    config = TeachingConfig(audience="beginner", teaching_style="step_by_step", voice="af_heart")
    assert config.video_profile is VERTICAL_HD
    assert config.video_format == "youtube_short"


def test_the_default_fingerprint_is_byte_identical_to_phase_7() -> None:
    """Every version in an existing library was recorded with exactly this
    config. Introducing formats must not move the default's digest, or all
    of them would silently become "needs regenerating". (A PIPELINE change
    does move it, deliberately - Phase 8.1 bumped PIPELINE_VERSION - which
    is why both sides here use the current pipeline version.)"""
    phase_7_fingerprint = RequestFingerprint(
        algorithm="palindrome",
        source_hash=_fingerprint().source_hash,
        config={
            "audience": "beginner",
            "teaching_style": "step_by_step",
            "tts_provider": "kokoro",
            "video_format": "youtube_short",
            "voice": "af_heart",
        },
        llm_provider="mock",
    )
    assert _fingerprint().digest == phase_7_fingerprint.digest


def test_a_stored_phase_7_config_reads_back_as_vertical_hd() -> None:
    stored = {"video_format": "youtube_short"}
    assert resolve_video_profile(stored["video_format"]) is VERTICAL_HD


def test_the_web_form_still_produces_the_default_format() -> None:
    pytest.importorskip("fastapi", reason="Phase 7.0 web layer requires fastapi")
    from code2shorts.webapp.app import _config_from_form

    assert _config_from_form("beginner", "step_by_step", "af_heart").video_profile is VERTICAL_HD


# ---- refusal happens before work, and the renderer gets the pixels --------


def test_an_unrenderable_format_is_refused_before_a_version_exists(
    tmp_path: Path, monkeypatch
) -> None:
    import dataclasses

    from code2shorts.core import video_profile

    monkeypatch.setitem(
        video_profile.PROFILES, VideoProfileId.VERTICAL_4K,
        dataclasses.replace(VERTICAL_4K, unsupported_reason="made unrenderable for this test"),
    )
    pipeline = _FakePipeline()
    registry = GenerationRegistry(tmp_path / "library")
    generator = GenerationManager(registry, pipeline, InMemoryArtifactStore())

    with pytest.raises(UnsupportedVideoProfileError):
        generator.generate(_request(config=TeachingConfig(video_format="vertical_4k")), force=True)

    assert pipeline.builds == 0
    assert registry.all_versions() == [], "a refused format must not leave a running row"


def test_the_profile_reaches_manim_as_its_resolution(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("fastapi", reason="Phase 7.0 web layer requires fastapi")
    from code2shorts.ai.contracts import (
        VisualAction,
        VisualizationPlanResponse,
        VisualizationStepPlan,
    )
    from code2shorts.execution.sandbox import ProcessResult
    from code2shorts.visualization import manim_renderer
    from code2shorts.visualization.renderer import RenderingFailure
    from code2shorts.webapp.pipeline import build_renderer

    commands: list[list[str]] = []

    def fake_run(command, cwd, timeout_seconds, **_kwargs):
        commands.append(command)
        return ProcessResult(returncode=1, stdout="", stderr="stop", timed_out=False,
                             duration_seconds=0.0)

    monkeypatch.setattr(manim_renderer, "run_subprocess", fake_run)
    plan = VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=0, visual_action=VisualAction.SWAP, trace_event_index=0,
                narration_text="x", duration_seconds=1.0,
            )
        ],
    )

    for profile, expected in ((VERTICAL_HD, "1080,1920"), (LANDSCAPE_HD, "1920,1080"),
                              (VERTICAL_4K, "2160,3840"), (LANDSCAPE_4K, "3840,2160")):
        commands.clear()
        with pytest.raises(RenderingFailure):
            build_renderer(profile).render(plan, tmp_path / profile.id.value)

        command = commands[0]
        assert command[command.index("--resolution") + 1] == expected
        assert command[command.index("--fps") + 1] == "30"


# ---- the content model stays format-free ----------------------------------


FORMAT_WORDS = ("format", "resolution", "aspect", "width", "height", "profile", "orientation")


def test_trace_and_teaching_contracts_know_nothing_about_format() -> None:
    from code2shorts.ai.contracts import (
        EducationalPlanResponse,
        NarrationResponse,
        VisualizationPlanResponse,
    )
    from code2shorts.core.models import ExecutionTrace, TraceEvent

    for model in (
        ExecutionTrace,
        TraceEvent,
        EducationalPlanResponse,
        NarrationResponse,
        VisualizationPlanResponse,
    ):
        leaking = [
            field for field in model.model_fields
            if any(word in field.lower() for word in FORMAT_WORDS)
        ]
        assert not leaking, f"{model.__name__} carries format-specific fields: {leaking}"

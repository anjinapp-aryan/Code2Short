"""Phase 4.4 real verification: actual Windows SAPI speech, real FFmpeg,
real probing. Auto-skips where the tooling is unavailable so the normal
developer workflow never depends on it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from code2shorts.ai.contracts import (
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.media import probe_audio
from code2shorts.narration import (
    SapiTTSProvider,
    SyntheticTTSProvider,
    align_narration,
    validate_alignment,
    validate_srt,
)
from code2shorts.narration.subtitles import build_srt

pytestmark = [pytest.mark.integration, pytest.mark.real_render]

sapi_only = pytest.mark.skipif(
    not SapiTTSProvider.is_available(), reason="Windows SAPI unavailable"
)
ffmpeg_only = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH"
)


@sapi_only
def test_sapi_produces_real_probeable_speech(tmp_path: Path) -> None:
    provider = SapiTTSProvider()
    result = provider.synthesize(
        "Two pointers swap characters until they meet. HashMap lookup is order one.",
        tmp_path / "speech",
    )

    path = Path(result.audio_path)
    assert path.is_file() and path.stat().st_size > 1000
    assert result.provider == "windows-sapi"
    assert result.metadata["speech"] is True

    meta = probe_audio(path)
    assert meta.duration_seconds > 1.0
    assert meta.sample_rate > 0
    assert meta.channels >= 1
    # duration must be MEASURED from the file, not estimated
    assert result.duration_seconds == pytest.approx(meta.duration_seconds, abs=0.05)


@sapi_only
def test_sapi_handles_unicode_and_punctuation(tmp_path: Path) -> None:
    provider = SapiTTSProvider()
    result = provider.synthesize(
        "Café — naïve; arr[i] != arr[j] & O(1).", tmp_path / "unicode"
    )
    assert probe_audio(Path(result.audio_path)).duration_seconds > 0.3


@sapi_only
def test_real_speech_aligns_to_the_visual_timeline(tmp_path: Path) -> None:
    """The whole Phase 4.4 contract, with real audio: measured durations
    inform overflow detection but never move a visual step."""
    texts = [
        "We reverse the string using two pointers.",
        "The pointers swap their characters and move inward.",
    ]
    plan = VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.SWAP,
                trace_event_index=i,
                narration_text=texts[i],
                duration_seconds=5.0,
            )
            for i in range(2)
        ],
    )
    narration = NarrationResponse(
        segments=[
            NarrationSegment(order=i, text=texts[i], visualization_step_order=i)
            for i in range(2)
        ]
    )

    provider = SapiTTSProvider()
    audio_by_segment = {}
    for segment in narration.segments:
        result = provider.synthesize(segment.text, tmp_path / f"seg{segment.order}")
        audio_by_segment[segment.order] = (result.audio_path, result.duration_seconds)

    alignment = align_narration(narration, plan, audio_by_segment=audio_by_segment)

    # visual timeline is untouched by real audio durations
    assert alignment.total_duration_seconds == 10.0
    assert [(s.start_seconds, s.end_seconds) for s in alignment.segments] == [
        (0.0, 5.0),
        (5.0, 10.0),
    ]
    assert alignment.overflow_count == 0, "5s steps should fit these lines"
    assert validate_alignment(alignment, plan).passed

    content = build_srt(alignment)
    assert validate_srt(content).passed
    for text in texts:
        assert text in content


@sapi_only
def test_overflow_is_detected_when_speech_exceeds_its_step(tmp_path: Path) -> None:
    """Deliberately under-size the step: the system must report, never
    silently stretch the visuals or truncate without saying so."""
    text = (
        "This narration line is deliberately long so that the synthesized "
        "speech certainly exceeds the very short visual step assigned to it."
    )
    plan = VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=0,
                visual_action=VisualAction.INTRO,
                trace_event_index=0,
                narration_text=text,
                duration_seconds=0.5,
            )
        ],
    )
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text=text, visualization_step_order=0)]
    )
    result = SapiTTSProvider().synthesize(text, tmp_path / "long")
    alignment = align_narration(
        narration, plan, audio_by_segment={0: (result.audio_path, result.duration_seconds)}
    )

    assert alignment.segments[0].overflowed is True
    assert alignment.total_duration_seconds == 0.5  # timeline unchanged
    validation = validate_alignment(alignment, plan)
    assert not validation.passed
    assert any("exceeds its visual step" in e for e in validation.errors)


@ffmpeg_only
def test_synthetic_provider_still_produces_real_decodable_audio(tmp_path: Path) -> None:
    """The key-free, cross-platform fallback must remain genuinely real."""
    result = SyntheticTTSProvider().synthesize("hello world", tmp_path / "syn")
    meta = probe_audio(Path(result.audio_path))
    assert meta.duration_seconds > 0
    assert meta.channels >= 1

"""Phase 4.4: narration alignment + subtitle generation.

The visual timeline is authoritative — these tests pin that rule down.
"""

from __future__ import annotations

import pytest

from code2shorts.ai.contracts import (
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.narration.alignment import (
    AlignmentError,
    align_narration,
    validate_alignment,
)
from code2shorts.narration.subtitles import (
    build_srt,
    sanitize_subtitle_text,
    validate_srt,
    write_srt,
)


def _plan(durations: list[float]) -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.INTRO,
                trace_event_index=i,
                narration_text=f"step {i}",
                duration_seconds=d,
            )
            for i, d in enumerate(durations)
        ],
    )


def _narration(count: int, texts: list[str] | None = None) -> NarrationResponse:
    return NarrationResponse(
        segments=[
            NarrationSegment(
                order=i,
                text=(texts[i] if texts else f"narration {i}"),
                visualization_step_order=i,
            )
            for i in range(count)
        ]
    )


# ---- alignment -----------------------------------------------------------


def test_segments_are_laid_on_the_visual_timeline() -> None:
    result = align_narration(_narration(3), _plan([1.5, 2.0, 1.0]))
    spans = [(s.start_seconds, s.end_seconds) for s in result.segments]
    assert spans == [(0.0, 1.5), (1.5, 3.5), (3.5, 4.5)]
    assert result.total_duration_seconds == 4.5


def test_alignment_is_deterministic() -> None:
    a = align_narration(_narration(3), _plan([1.0, 2.0, 3.0]))
    b = align_narration(_narration(3), _plan([1.0, 2.0, 3.0]))
    assert a == b


def test_no_overlaps_and_no_negative_timestamps() -> None:
    result = align_narration(_narration(4), _plan([1.0, 1.0, 1.0, 1.0]))
    previous_end = 0.0
    for segment in result.segments:
        assert segment.start_seconds >= 0
        assert segment.end_seconds > segment.start_seconds
        assert segment.start_seconds >= previous_end - 1e-9
        previous_end = segment.end_seconds


def test_out_of_order_narration_is_sorted_not_trusted() -> None:
    narration = NarrationResponse(
        segments=[
            NarrationSegment(order=2, text="c", visualization_step_order=2),
            NarrationSegment(order=0, text="a", visualization_step_order=0),
            NarrationSegment(order=1, text="b", visualization_step_order=1),
        ]
    )
    result = align_narration(narration, _plan([1.0, 1.0, 1.0]))
    assert [s.text for s in result.segments] == ["a", "b", "c"]


def test_segment_referencing_a_missing_step_raises() -> None:
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text="x", visualization_step_order=99)]
    )
    with pytest.raises(AlignmentError):
        align_narration(narration, _plan([1.0]))


def test_audio_longer_than_its_step_is_flagged_not_absorbed() -> None:
    """Audio must never stretch the visual timeline."""
    result = align_narration(
        _narration(2),
        _plan([1.0, 1.0]),
        audio_by_segment={0: ("a0.wav", 5.0), 1: ("a1.wav", 0.4)},
    )
    assert result.segments[0].overflowed is True
    assert result.segments[1].overflowed is False
    assert result.overflow_count == 1
    # timeline is unchanged despite the 5s audio
    assert result.total_duration_seconds == 2.0
    assert result.segments[0].end_seconds == 1.0


def test_shorter_audio_leaves_the_step_length_intact() -> None:
    result = align_narration(
        _narration(1), _plan([3.0]), audio_by_segment={0: ("a.wav", 0.5)}
    )
    assert result.segments[0].duration_seconds == 3.0
    assert result.segments[0].audio_duration_seconds == 0.5
    assert result.segments[0].overflowed is False


def test_lead_in_offsets_every_segment() -> None:
    result = align_narration(_narration(2), _plan([1.0, 1.0]), lead_in_seconds=0.5)
    assert result.segments[0].start_seconds == 0.5
    assert result.segments[1].start_seconds == 1.5


def test_validate_alignment_reports_uncovered_steps_and_overflow() -> None:
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text="a", visualization_step_order=0)]
    )
    plan = _plan([1.0, 1.0])
    result = align_narration(narration, plan, audio_by_segment={0: ("a.wav", 9.0)})
    validation = validate_alignment(result, plan)
    assert not validation.passed
    assert any("no narration" in e for e in validation.errors)
    assert any("exceeds its visual step" in e for e in validation.errors)


def test_validate_alignment_passes_for_a_good_alignment() -> None:
    plan = _plan([2.0, 2.0])
    result = align_narration(_narration(2), plan, audio_by_segment={0: ("a", 1.0), 1: ("b", 1.5)})
    assert validate_alignment(result, plan).passed


# ---- subtitles -----------------------------------------------------------


def test_srt_round_trips_and_validates() -> None:
    result = align_narration(_narration(3), _plan([1.0, 2.0, 1.5]))
    content = build_srt(result)
    assert "-->" in content
    assert validate_srt(content).passed


def test_srt_timestamps_match_alignment() -> None:
    result = align_narration(_narration(2), _plan([1.5, 2.5]))
    content = build_srt(result)
    assert "00:00:00,000 --> 00:00:01,500" in content
    assert "00:00:01,500 --> 00:00:04,000" in content


def test_srt_preserves_java_terminology_and_unicode() -> None:
    texts = [
        "HashMap<String, Integer> lookup is O(1)",
        "chars[left] <-> chars[right] — swap & continue",
        "unicode: é 中 ಕನ್ನಡ 😀",
    ]
    result = align_narration(_narration(3, texts), _plan([1.0, 1.0, 1.0]))
    content = build_srt(result)
    for text in texts:
        assert text in content, f"lost: {text}"
    assert validate_srt(content).passed


def test_srt_written_to_disk_is_utf8_and_parses(tmp_path) -> None:
    result = align_narration(_narration(2, ["café 😀", "ಕನ್ನಡ"]), _plan([1.0, 1.0]))
    path = write_srt(result, tmp_path / "subs.srt")
    content = path.read_text(encoding="utf-8")
    assert validate_srt(content).passed
    assert "café 😀" in content


def test_validate_srt_rejects_malformed_documents() -> None:
    assert not validate_srt("not an srt at all").passed
    assert not validate_srt("").passed


def test_sanitize_removes_cue_injection_vectors_and_preserves_the_rest() -> None:
    # Null bytes are dropped; ALL newlines collapse to spaces. The newline
    # rule is a security control, not formatting - see
    # test_srt_injection_cannot_forge_extra_cues, which proved a surviving
    # newline lets a payload forge a second cue with its own timestamps.
    assert sanitize_subtitle_text("a\x00b") == "ab"
    assert sanitize_subtitle_text("a\r\nb") == "a b"
    assert sanitize_subtitle_text("a\u2028b") == "a b"
    # everything else is preserved exactly
    kept = "quotes ' and ampersand & pipe | semi ; O(1) arr[i]"
    assert sanitize_subtitle_text(kept) == kept

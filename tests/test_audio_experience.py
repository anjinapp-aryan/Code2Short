"""PHASE 6.1 — the listener's experience, asserted against measurement.

Every number in this module traces to a forensic reading of the real
Phase 6 MP4 (181.37 s, 30 steps). That file passed media validation and
timeline validation and was still unusable: the audio stopped 22.1 s
before the picture did, and there was over a second of dead air after
every sentence.

The two halves are kept apart on purpose:

* the TIMING model (`visualization.timing`) is pure arithmetic and is
  tested here without touching a media file;
* the AUDIO forensics are tested against fixtures that reproduce what
  FFmpeg reported, so the validator's judgement is exercised without
  requiring a 3-minute render.
"""

from __future__ import annotations

import pytest

from code2shorts.ai.contracts import (
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.media.audio_forensics import (
    AudioForensics,
    SilenceRun,
    validate_audio_experience,
)
from code2shorts.narration.audio import (
    BREATH_SECONDS,
    KEEP_EDGE_SILENCE_SECONDS,
    TARGET_LUFS,
    TRUE_PEAK_CEILING_DBTP,
)
from code2shorts.visualization.timing import (
    FINAL_FADE_OUT_SECONDS,
    STEP_FADE_IN_SECONDS,
    STEP_FADE_OUT_SECONDS,
    TITLE_FADE_IN_SECONDS,
    TITLE_HOLD_SECONDS,
    step_windows,
    total_video_seconds,
)


def _plan(durations: list[float]) -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="T",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.HIGHLIGHT,
                trace_event_index=i,
                narration_text=f"n{i}",
                duration_seconds=d,
            )
            for i, d in enumerate(durations)
        ],
    )


# ---------------------------------------------------------------------------
# The timing model — the root cause of both the drift and the silent tail.
# ---------------------------------------------------------------------------


def test_the_timeline_accounts_for_the_renderers_animation() -> None:
    """Reproduces the exact arithmetic of the failing run.

    30 steps declaring 160.414 s rendered as a 181.37 s video. The 20.95 s
    difference is the title fade, one cross-fade per step and the closing
    fade — none of which the audio timeline modelled.
    """
    plan = _plan([160.414 / 30] * 30)
    predicted = total_video_seconds(plan)
    animation = (
        TITLE_FADE_IN_SECONDS
        + TITLE_HOLD_SECONDS
        + 29 * STEP_FADE_OUT_SECONDS
        + 30 * STEP_FADE_IN_SECONDS
        + FINAL_FADE_OUT_SECONDS
    )
    assert animation == pytest.approx(20.95)
    assert predicted == pytest.approx(160.414 + animation)
    # The number the real render produced, to within a frame at 30 fps.
    assert predicted == pytest.approx(181.37, abs=1 / 30)


def test_a_step_window_is_when_its_frame_is_holding() -> None:
    """Narration is spoken over a static frame, never over a cross-fade —
    during a fade there are two states on screen and neither is the one
    being described."""
    plan = _plan([2.0, 3.0])
    windows = step_windows(plan)

    first_start = TITLE_FADE_IN_SECONDS + TITLE_HOLD_SECONDS + STEP_FADE_IN_SECONDS
    assert windows[0] == pytest.approx((first_start, first_start + 2.0))

    second_start = (
        first_start + 2.0 + STEP_FADE_OUT_SECONDS + STEP_FADE_IN_SECONDS
    )
    assert windows[1] == pytest.approx((second_start, second_start + 3.0))


def test_the_first_step_pays_no_fade_out() -> None:
    """There is nothing to fade out before the first step. Charging for it
    would shift every window and re-introduce the drift in miniature."""
    plan = _plan([1.0])
    start, _ = step_windows(plan)[0]
    assert start == pytest.approx(
        TITLE_FADE_IN_SECONDS + TITLE_HOLD_SECONDS + STEP_FADE_IN_SECONDS
    )


def test_narration_never_starts_before_its_frame_is_drawn() -> None:
    plan = _plan([1.0] * 5)
    for order, (start, _) in step_windows(plan).items():
        assert start >= TITLE_FADE_IN_SECONDS + TITLE_HOLD_SECONDS
        assert start > 0


def test_windows_never_overlap_and_stay_in_order() -> None:
    plan = _plan([1.0, 2.5, 0.5, 4.0])
    windows = [step_windows(plan)[i] for i in range(4)]
    for (_, earlier_end), (later_start, _) in zip(windows, windows[1:]):
        assert later_start > earlier_end


def test_the_video_outlasts_the_narration_by_exactly_the_closing_fade() -> None:
    """The policy, stated as a test: the only silence the end of a video
    may carry is the fade the renderer deliberately draws."""
    plan = _plan([3.0, 3.0])
    last_end = max(end for _, end in step_windows(plan).values())
    assert total_video_seconds(plan) - last_end == pytest.approx(
        FINAL_FADE_OUT_SECONDS
    )


def test_an_empty_plan_has_no_step_windows() -> None:
    assert step_windows(_plan([])) == {}
    assert total_video_seconds(_plan([])) > 0


# ---------------------------------------------------------------------------
# Audio forensics — measured reality, not the pipeline's opinion of itself.
# ---------------------------------------------------------------------------


def _clean(duration: float = 100.0) -> AudioForensics:
    return AudioForensics(
        duration_seconds=duration,
        sample_rate=44100,
        channels=1,
        integrated_lufs=TARGET_LUFS,
        true_peak_dbtp=TRUE_PEAK_CEILING_DBTP,
        silences=[SilenceRun(start_seconds=duration - 0.3, end_seconds=duration)],
    )


def _validate(forensics: AudioForensics, **overrides):
    kwargs = dict(
        video_duration_seconds=forensics.duration_seconds,
        allowed_silent_tail_seconds=FINAL_FADE_OUT_SECONDS,
        target_lufs=TARGET_LUFS,
        expected_sample_rate=44100,
    )
    kwargs.update(overrides)
    return validate_audio_experience(forensics, **kwargs)


def test_a_healthy_track_passes() -> None:
    assert _validate(_clean()).passed


def test_the_22_second_silent_tail_is_caught() -> None:
    """The headline defect, at its real measured values: the file runs to
    181.37 s and the audio goes silent at 159.28 s."""
    forensics = AudioForensics(
        duration_seconds=181.37,
        sample_rate=44100,
        channels=1,
        integrated_lufs=TARGET_LUFS,
        true_peak_dbtp=TRUE_PEAK_CEILING_DBTP,
        silences=[SilenceRun(start_seconds=159.28, end_seconds=181.39)],
    )
    assert forensics.silent_tail_seconds == pytest.approx(22.09, abs=0.01)
    result = _validate(forensics)
    assert not result.passed
    assert any("silent tail" in error for error in result.errors)


def test_the_closing_fade_is_not_reported_as_a_tail() -> None:
    """A deliberate closing fade must not be scored as the same defect, or
    the check would be noise and get ignored."""
    assert _validate(_clean()).passed


def test_the_repeated_one_second_pauses_are_caught() -> None:
    """Measured on the real track: 34 inter-segment gaps averaging 1.10 s,
    worst 1.25 s, 37.5 s of dead air in a 181 s video."""
    silences = [
        SilenceRun(start_seconds=10.0 * i, end_seconds=10.0 * i + 1.22)
        for i in range(1, 6)
    ]
    silences.append(SilenceRun(start_seconds=99.7, end_seconds=100.0))
    forensics = _clean()
    forensics.silences = silences
    result = _validate(forensics)
    assert not result.passed
    assert any("artificial silence" in error for error in result.errors)


def test_a_natural_breath_between_segments_is_allowed() -> None:
    """Section: educational narration needs breathing room. The check must
    distinguish phrasing from concatenation artefacts, not ban silence."""
    forensics = _clean()
    forensics.silences = [
        SilenceRun(start_seconds=10.0 * i, end_seconds=10.0 * i + BREATH_SECONDS)
        for i in range(1, 6)
    ] + [SilenceRun(start_seconds=99.7, end_seconds=100.0)]
    assert _validate(forensics).passed


def test_a_quiet_track_is_caught() -> None:
    """The old track measured -20.0 LUFS."""
    forensics = _clean()
    forensics.integrated_lufs = -20.0
    result = _validate(forensics)
    assert not result.passed
    assert any("loudness" in error for error in result.errors)


def test_no_headroom_for_the_encoder_is_caught() -> None:
    """The old track peaked at -0.0 dBTP, which is where lossy encoding
    turns into audible distortion."""
    forensics = _clean()
    forensics.true_peak_dbtp = -0.0
    result = _validate(forensics)
    assert not result.passed
    assert any("true peak" in error for error in result.errors)


def test_a_sample_rate_change_is_caught() -> None:
    forensics = _clean()
    forensics.sample_rate = 22050
    result = _validate(forensics)
    assert not result.passed
    assert any("sample rate" in error for error in result.errors)


def test_audio_shorter_than_the_video_is_caught() -> None:
    forensics = _clean(duration=100.0)
    result = _validate(forensics, video_duration_seconds=140.0)
    assert not result.passed
    assert any("but video is" in error for error in result.errors)


def test_the_tail_tolerance_cannot_be_stretched_by_accident() -> None:
    """A 22 s tail must fail even against a generous allowance — the fix
    is the pipeline, never a wider tolerance."""
    forensics = AudioForensics(
        duration_seconds=181.37,
        sample_rate=44100,
        channels=1,
        integrated_lufs=TARGET_LUFS,
        true_peak_dbtp=TRUE_PEAK_CEILING_DBTP,
        silences=[SilenceRun(start_seconds=159.28, end_seconds=181.39)],
    )
    result = _validate(forensics, allowed_silent_tail_seconds=2.0)
    assert not result.passed


def test_every_defect_is_reported_together() -> None:
    """One run should surface every problem, so a fix is not a whack-a-mole
    loop of re-rendering three minutes of video per symptom."""
    forensics = AudioForensics(
        duration_seconds=181.37,
        sample_rate=22050,
        channels=1,
        integrated_lufs=-20.0,
        true_peak_dbtp=-0.0,
        silences=[
            SilenceRun(start_seconds=20.0, end_seconds=21.25),
            SilenceRun(start_seconds=159.28, end_seconds=181.39),
        ],
    )
    result = _validate(forensics)
    assert len(result.errors) >= 4


def test_the_breath_and_the_fitting_padding_are_one_number() -> None:
    """Two constants meaning "the pause between sentences" is how the
    pipeline ended up with 1.2 s of it."""
    from code2shorts.narration.fitting import DEFAULT_PADDING_SECONDS

    assert DEFAULT_PADDING_SECONDS == BREATH_SECONDS


# ---------------------------------------------------------------------------
# Silence is classified by CAUSE. Designed silence must not be reported, or
# the check becomes noise and gets ignored; undesigned silence must be.
# ---------------------------------------------------------------------------

DESIGNED_LEAD_IN = (
    TITLE_FADE_IN_SECONDS + TITLE_HOLD_SECONDS + STEP_FADE_IN_SECONDS + 0.1
)
DESIGNED_GAP = (
    2 * KEEP_EDGE_SILENCE_SECONDS
    + BREATH_SECONDS
    + STEP_FADE_OUT_SECONDS
    + STEP_FADE_IN_SECONDS
    + 0.15
)
DESIGNED_TAIL = KEEP_EDGE_SILENCE_SECONDS + BREATH_SECONDS + FINAL_FADE_OUT_SECONDS


def _budgeted(forensics: AudioForensics, **overrides):
    kwargs = dict(
        video_duration_seconds=forensics.duration_seconds,
        allowed_silent_tail_seconds=DESIGNED_TAIL,
        max_internal_gap_seconds=DESIGNED_GAP,
        allowed_lead_in_seconds=DESIGNED_LEAD_IN,
        target_lufs=TARGET_LUFS,
        expected_sample_rate=44100,
    )
    kwargs.update(overrides)
    return validate_audio_experience(forensics, **kwargs)


def test_the_silent_title_card_is_not_reported() -> None:
    """The video opens on a title with nothing to say yet. Measured on the
    real run: 1.84 s of silence starting at 0.00 s."""
    forensics = _clean()
    forensics.silences = [
        SilenceRun(start_seconds=0.0, end_seconds=1.84),
        SilenceRun(start_seconds=99.54, end_seconds=100.0),
    ]
    assert _budgeted(forensics).passed


def test_silence_after_the_title_card_is_still_reported() -> None:
    """The lead-in allowance must not become a blanket amnesty for early
    silence — a gap that merely starts near the beginning is still a gap."""
    forensics = _clean()
    forensics.silences = [
        SilenceRun(start_seconds=DESIGNED_LEAD_IN + 1.0, end_seconds=DESIGNED_LEAD_IN + 4.0),
        SilenceRun(start_seconds=99.54, end_seconds=100.0),
    ]
    result = _budgeted(forensics)
    assert not result.passed
    assert any("artificial silence" in error for error in result.errors)


def test_the_designed_cross_fade_gap_is_not_reported() -> None:
    """Between two steps the renderer cross-fades for 0.65 s and the
    narrator takes a breath. That is the design, not an artefact."""
    forensics = _clean()
    forensics.silences = [
        SilenceRun(start_seconds=10.0 * i, end_seconds=10.0 * i + DESIGNED_GAP - 0.05)
        for i in range(1, 6)
    ] + [SilenceRun(start_seconds=99.54, end_seconds=100.0)]
    assert _budgeted(forensics).passed


def test_the_old_one_and_a_quarter_second_gaps_still_fail() -> None:
    """The measured Phase 6 gap was 1.21-1.24 s. It must fail against the
    designed budget, or this whole exercise proved nothing."""
    forensics = _clean()
    forensics.silences = [
        SilenceRun(start_seconds=10.0 * i, end_seconds=10.0 * i + 1.24)
        for i in range(1, 6)
    ] + [SilenceRun(start_seconds=99.54, end_seconds=100.0)]
    result = _budgeted(forensics)
    assert not result.passed
    assert any("artificial silence" in error for error in result.errors)


def test_the_designed_tail_is_not_reported() -> None:
    forensics = _clean()
    forensics.silences = [
        SilenceRun(start_seconds=100.0 - DESIGNED_TAIL, end_seconds=100.0)
    ]
    assert _budgeted(forensics).passed


def test_the_22_second_tail_fails_against_the_designed_budget() -> None:
    """The headline defect, judged by the same rule that now lets the
    closing fade through."""
    forensics = AudioForensics(
        duration_seconds=181.37,
        sample_rate=44100,
        channels=1,
        integrated_lufs=TARGET_LUFS,
        true_peak_dbtp=TRUE_PEAK_CEILING_DBTP,
        silences=[SilenceRun(start_seconds=159.28, end_seconds=181.39)],
    )
    result = _budgeted(forensics, video_duration_seconds=181.37)
    assert not result.passed
    assert any("silent tail" in error for error in result.errors)


def test_the_designed_budgets_are_smaller_than_the_measured_defect() -> None:
    """A sanity check on the budgets themselves: if the allowance ever
    grew past what the defect measured, the gate would be theatre."""
    assert DESIGNED_TAIL < 1.0 < 22.09
    assert DESIGNED_GAP < 1.21


def test_silence_the_plan_declared_is_not_reported() -> None:
    """ADR-5.11: a step declared longer than its narration is PACING, and
    pacing is content. Measured on a real run, 4 of 29 gaps were longer
    than the cross-fade budget for exactly this reason."""
    forensics = _clean()
    forensics.silences = [
        SilenceRun(start_seconds=40.0, end_seconds=41.34),
        SilenceRun(start_seconds=99.54, end_seconds=100.0),
    ]
    assert _budgeted(
        forensics, designed_silence_intervals=[(40.0, 41.34)]
    ).passed


def test_silence_outside_a_designed_interval_is_still_reported() -> None:
    """Declaring one pause must not excuse a different one."""
    forensics = _clean()
    forensics.silences = [
        SilenceRun(start_seconds=40.0, end_seconds=41.34),
        SilenceRun(start_seconds=60.0, end_seconds=62.0),
        SilenceRun(start_seconds=99.54, end_seconds=100.0),
    ]
    result = _budgeted(forensics, designed_silence_intervals=[(40.0, 41.34)])
    assert not result.passed
    assert any("60.00s" in error for error in result.errors)


def test_a_designed_interval_cannot_excuse_the_silent_tail() -> None:
    """The tail is judged separately, so no amount of declared pacing can
    make a 22 s dead end acceptable."""
    forensics = AudioForensics(
        duration_seconds=181.37,
        sample_rate=44100,
        channels=1,
        integrated_lufs=TARGET_LUFS,
        true_peak_dbtp=TRUE_PEAK_CEILING_DBTP,
        silences=[SilenceRun(start_seconds=159.28, end_seconds=181.39)],
    )
    result = _budgeted(
        forensics,
        video_duration_seconds=181.37,
        designed_silence_intervals=[(159.28, 181.39)],
    )
    assert not result.passed
    assert any("silent tail" in error for error in result.errors)

from __future__ import annotations

from pathlib import Path

import pytest

from code2shorts.ai.contracts import (
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.execution.sandbox import ProcessResult
from code2shorts.media import MediaComposer, MediaCompositionFailure
from code2shorts.narration import MockTTSProvider, TTSFailure, validate_narration


def _plan() -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=0, visual_action=VisualAction.INTRO, trace_event_index=0,
                narration_text="intro", duration_seconds=1.0,
            ),
            VisualizationStepPlan(
                order=1, visual_action=VisualAction.COMPLETION, trace_event_index=1,
                narration_text="done", duration_seconds=1.0,
            ),
        ],
    )


def test_valid_narration_passes() -> None:
    narration = NarrationResponse(
        segments=[
            NarrationSegment(order=0, text="Let's begin", visualization_step_order=0),
            NarrationSegment(order=1, text="All done", visualization_step_order=1),
        ]
    )
    result = validate_narration(narration, _plan())
    assert result.passed


def test_narration_referencing_nonexistent_step_is_rejected() -> None:
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text="x", visualization_step_order=99)]
    )
    result = validate_narration(narration, _plan())
    assert not result.passed
    assert "99" in result.errors[0]


def test_narration_with_no_segments_is_rejected() -> None:
    result = validate_narration(NarrationResponse(segments=[]), _plan())
    assert not result.passed


# ---- TTS --------------------------------------------------------------


def test_mock_tts_produces_real_file_and_checksum(tmp_path: Path) -> None:
    provider = MockTTSProvider()
    result = provider.synthesize("hello world", tmp_path / "audio.bin")
    assert Path(result.audio_path).exists()
    assert result.checksum
    assert result.duration_seconds > 0


def test_mock_tts_rejects_empty_text(tmp_path: Path) -> None:
    provider = MockTTSProvider()
    with pytest.raises(TTSFailure):
        provider.synthesize("   ", tmp_path / "audio.bin")


def test_mock_tts_is_deterministic(tmp_path: Path) -> None:
    provider = MockTTSProvider()
    a = provider.synthesize("same text", tmp_path / "a.bin")
    b = provider.synthesize("same text", tmp_path / "b.bin")
    assert a.checksum == b.checksum


# ---- Media composition --------------------------------------------------


def test_composer_uses_fixed_trusted_command(tmp_path: Path) -> None:
    captured = {}

    def fake_run(command, cwd, timeout_seconds):
        captured["command"] = command
        (tmp_path / "final.mp4").write_bytes(b"composed")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    composer = MediaComposer(run_subprocess_fn=fake_run)
    result = composer.compose(
        tmp_path / "video.mp4", tmp_path / "audio.bin", tmp_path / "final.mp4", video_duration_seconds=5.0
    )

    assert isinstance(captured["command"], list)
    assert captured["command"][0] == "ffmpeg"
    assert result.duration_seconds == 5.0
    assert result.checksum


def test_composer_caps_output_at_video_duration_never_truncating_video(tmp_path: Path) -> None:
    """Regression (Phase 4.1, found by a REAL ffmpeg run): the composer used
    a bare "-shortest", which truncated an 18.0s render down to the 5.2s
    length of its narration audio — silently discarding two thirds of the
    rendered visualization. The command must pad the audio and cap at the
    VIDEO's duration instead."""
    captured = {}

    def fake_run(command, cwd, timeout_seconds):
        captured["command"] = command
        (tmp_path / "final.mp4").write_bytes(b"composed")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    composer = MediaComposer(run_subprocess_fn=fake_run)
    composer.compose(
        tmp_path / "video.mp4", tmp_path / "audio.m4a", tmp_path / "final.mp4",
        video_duration_seconds=18.0,
    )

    command = captured["command"]
    assert "-shortest" not in command, "bare -shortest truncates video to audio length"
    assert "apad" in command, "audio must be padded, not video truncated"
    # output is explicitly capped at the video's own duration
    assert "-t" in command
    assert command[command.index("-t") + 1] == "18.000"


def test_composer_uses_absolute_paths(tmp_path: Path) -> None:
    """Regression (Phase 4.1): the subprocess runs with cwd set to the
    output directory, so relative input paths were resolved a second time
    against it (real failure: manim got .../render/.../render/scene.py)."""
    captured = {}

    def fake_run(command, cwd, timeout_seconds):
        captured["command"] = command
        (tmp_path / "final.mp4").write_bytes(b"composed")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    composer = MediaComposer(run_subprocess_fn=fake_run)
    composer.compose(
        Path("relative/video.mp4"), Path("relative/audio.m4a"), tmp_path / "final.mp4",
        video_duration_seconds=5.0,
    )

    path_args = [arg for arg in captured["command"] if arg.endswith((".mp4", ".m4a"))]
    assert path_args, "expected media path arguments"
    for arg in path_args:
        assert Path(arg).is_absolute(), f"{arg} must be absolute"


def test_composer_raises_on_ffmpeg_failure(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=1, stdout="", stderr="ffmpeg error", timed_out=False, duration_seconds=0.1)

    composer = MediaComposer(run_subprocess_fn=fake_run)
    with pytest.raises(MediaCompositionFailure):
        composer.compose(
            tmp_path / "video.mp4", tmp_path / "audio.bin", tmp_path / "final.mp4", video_duration_seconds=5.0
        )

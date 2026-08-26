"""Phase 4.5: failure paths must fail LOUDLY, at the right stage.

Every test here deliberately breaks something and asserts the pipeline
stops correctly rather than producing a plausible-looking wrong result.
Unit-level (no external tooling) except where marked.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from code2shorts.ai.contracts import (
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.artifacts import Artifact, ArtifactType, InMemoryArtifactStore
from code2shorts.core.models import (
    ExecutionTrace,
    SupportedLanguage,
    TraceEvent,
    TraceEventType,
    TraceStatus,
)
from code2shorts.execution.sandbox import ProcessResult, Workspace
from code2shorts.media import MediaComposer, MediaCompositionFailure, MediaPolicy
from code2shorts.media.validation import (
    validate_final_video,
    validate_timeline,
    validate_video_file,
)
from code2shorts.narration import SyntheticTTSProvider, TTSFailure, align_narration
from code2shorts.narration.alignment import AlignmentError
from code2shorts.visualization import (
    ManimVideoRenderer,
    RenderingFailure,
    validate_visualization_plan,
)


def _trace(status: TraceStatus = TraceStatus.COMPLETED, events: list[TraceEvent] | None = None):
    return ExecutionTrace(
        algorithm_name="t",
        language=SupportedLanguage.JAVA,
        input="",
        output="",
        succeeded=status is TraceStatus.COMPLETED,
        exit_code=0 if status is TraceStatus.COMPLETED else 1,
        status=status,
        events=events
        or [
            TraceEvent(step_index=0, event_type=TraceEventType.PROGRAM_START.value, description="s"),
            TraceEvent(
                step_index=1,
                event_type=TraceEventType.VARIABLE_ASSIGN.value,
                description="arr",
                variable_name="arr",
                new_value="[1, 2]",
            ),
        ],
    )


def _plan(trace_indices: list[int], durations: list[float] | None = None):
    durations = durations or [1.0] * len(trace_indices)
    return VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.INTRO,
                trace_event_index=idx,
                narration_text=f"step {i}",
                duration_seconds=durations[i],
            )
            for i, idx in enumerate(trace_indices)
        ],
    )


# ---- 5/6/7. invalid plan, invalid trace reference ------------------------


def test_plan_referencing_a_nonexistent_trace_event_is_rejected() -> None:
    result = validate_visualization_plan(_plan([0, 999]), _trace())
    assert not result.passed
    assert any("999" in e for e in result.errors)


def test_plan_with_out_of_order_trace_references_is_rejected() -> None:
    result = validate_visualization_plan(_plan([1, 0]), _trace())
    assert not result.passed


def test_empty_plan_is_rejected() -> None:
    empty = VisualizationPlanResponse(lesson_title="t", steps=[])
    assert not validate_visualization_plan(empty, _trace()).passed


# ---- 12. narration overflow ---------------------------------------------


def test_narration_referencing_a_missing_step_raises_with_diagnostics() -> None:
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text="x", visualization_step_order=42)]
    )
    with pytest.raises(AlignmentError, match="42"):
        align_narration(narration, _plan([0]))


def test_narration_overflow_is_reported_and_timeline_unchanged() -> None:
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text="x", visualization_step_order=0)]
    )
    alignment = align_narration(
        narration, _plan([0], durations=[1.0]), audio_by_segment={0: ("a.wav", 30.0)}
    )
    assert alignment.overflow_count == 1
    assert alignment.total_duration_seconds == 1.0  # NOT stretched to 30s
    result = validate_timeline(alignment, video_duration_seconds=1.0)
    assert not result.passed
    assert any("NOT altered" in e for e in result.errors)


def test_zero_length_narration_is_rejected_by_tts() -> None:
    with pytest.raises(TTSFailure):
        SyntheticTTSProvider(run_subprocess_fn=lambda **k: None).synthesize("", Path("x"))


# ---- 9/10. missing / corrupt MP4 ----------------------------------------


def test_missing_video_file_fails_validation_with_a_clear_message(tmp_path: Path) -> None:
    result = validate_video_file(tmp_path / "does_not_exist.mp4")
    assert not result.passed
    assert "unreadable" in result.errors[0]


def test_corrupt_mp4_fails_validation_rather_than_being_trusted(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"this is definitely not an mp4 container")
    result = validate_video_file(corrupt)
    assert not result.passed


def test_renderer_reports_missing_output_instead_of_returning_success(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    renderer = ManimVideoRenderer(run_subprocess_fn=fake_run)
    with pytest.raises(RenderingFailure, match="no .mp4"):
        renderer.render(_plan([0]), tmp_path)


# ---- 8/14/17. manim + ffmpeg failure and timeout ------------------------


def test_manim_nonzero_exit_stops_the_pipeline(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=1, stdout="", stderr="manim exploded", timed_out=False, duration_seconds=0.2)

    with pytest.raises(RenderingFailure, match="manim exploded"):
        ManimVideoRenderer(run_subprocess_fn=fake_run).render(_plan([0]), tmp_path)


def test_manim_timeout_is_surfaced_not_swallowed(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=-1, stdout="", stderr="", timed_out=True, duration_seconds=600.0)

    with pytest.raises(RenderingFailure):
        ManimVideoRenderer(run_subprocess_fn=fake_run).render(_plan([0]), tmp_path)


def test_ffmpeg_failure_stops_composition(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=1, stdout="", stderr="ffmpeg failed", timed_out=False, duration_seconds=0.1)

    composer = MediaComposer(run_subprocess_fn=fake_run)
    with pytest.raises(MediaCompositionFailure, match="ffmpeg failed"):
        composer.compose(tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "f.mp4", 5.0)


def test_ffmpeg_timeout_is_surfaced(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=-1, stdout="", stderr="", timed_out=True, duration_seconds=180.0)

    with pytest.raises(MediaCompositionFailure):
        MediaComposer(run_subprocess_fn=fake_run).compose(
            tmp_path / "v.mp4", tmp_path / "a.m4a", tmp_path / "f.mp4", 5.0
        )


def test_tts_timeout_is_surfaced(tmp_path: Path) -> None:
    def fake_run(command, cwd, timeout_seconds):
        return ProcessResult(returncode=-1, stdout="", stderr="", timed_out=True, duration_seconds=60.0)

    with pytest.raises(TTSFailure):
        SyntheticTTSProvider(run_subprocess_fn=fake_run).synthesize("hello", tmp_path / "a")


# ---- silent truncation / stretching must be caught ----------------------


def test_truncated_final_video_is_detected(tmp_path: Path, monkeypatch) -> None:
    """The exact Phase 4.1 production bug: composition silently cut an
    18.0s render down to 5.2s. Validation must call that out."""
    from code2shorts.media import validation as validation_module
    from code2shorts.media.probe import AudioMetadata, VideoMetadata

    monkeypatch.setattr(
        validation_module,
        "probe_video",
        lambda p: VideoMetadata(
            width=1080, height=1920, fps=30.0, duration_seconds=5.2, frame_count=156, codec="h264"
        ),
    )
    monkeypatch.setattr(
        validation_module,
        "probe_audio",
        lambda p: AudioMetadata(codec="aac", sample_rate=44100, channels=1, duration_seconds=5.2),
    )

    result = validate_final_video(tmp_path / "f.mp4", expected_duration_seconds=18.0)
    assert not result.passed
    assert any("truncated" in e for e in result.errors)


def test_stretched_final_video_is_detected(tmp_path: Path, monkeypatch) -> None:
    from code2shorts.media import validation as validation_module
    from code2shorts.media.probe import AudioMetadata, VideoMetadata

    monkeypatch.setattr(
        validation_module,
        "probe_video",
        lambda p: VideoMetadata(
            width=1080, height=1920, fps=30.0, duration_seconds=30.0, frame_count=900, codec="h264"
        ),
    )
    monkeypatch.setattr(
        validation_module,
        "probe_audio",
        lambda p: AudioMetadata(codec="aac", sample_rate=44100, channels=1, duration_seconds=30.0),
    )

    result = validate_final_video(tmp_path / "f.mp4", expected_duration_seconds=18.0)
    assert not result.passed
    assert any("stretched" in e for e in result.errors)


def test_wrong_resolution_is_rejected(tmp_path: Path, monkeypatch) -> None:
    from code2shorts.media import validation as validation_module
    from code2shorts.media.probe import VideoMetadata

    monkeypatch.setattr(
        validation_module,
        "probe_video",
        lambda p: VideoMetadata(
            width=1920, height=1080, fps=30.0, duration_seconds=10.0, frame_count=300, codec="h264"
        ),
    )
    result = validate_video_file(tmp_path / "f.mp4")
    assert not result.passed
    assert any("1920x1080" in e and "1080x1920" in e for e in result.errors)


def test_audio_outlasting_video_is_reported(tmp_path: Path, monkeypatch) -> None:
    from code2shorts.media import validation as validation_module
    from code2shorts.media.probe import AudioMetadata, VideoMetadata

    monkeypatch.setattr(
        validation_module,
        "probe_video",
        lambda p: VideoMetadata(
            width=1080, height=1920, fps=30.0, duration_seconds=10.0, frame_count=300, codec="h264"
        ),
    )
    monkeypatch.setattr(
        validation_module,
        "probe_audio",
        lambda p: AudioMetadata(codec="aac", sample_rate=44100, channels=1, duration_seconds=15.0),
    )
    result = validate_final_video(tmp_path / "f.mp4")
    assert not result.passed
    assert any("outlasts" in e for e in result.errors)


# ---- subtitle cues outside the video ------------------------------------


def test_cue_beyond_video_duration_is_rejected() -> None:
    narration = NarrationResponse(
        segments=[
            NarrationSegment(order=0, text="a", visualization_step_order=0),
            NarrationSegment(order=1, text="b", visualization_step_order=1),
        ]
    )
    alignment = align_narration(narration, _plan([0, 1], durations=[5.0, 5.0]))
    result = validate_timeline(alignment, video_duration_seconds=6.0)
    assert not result.passed
    assert any("beyond the" in e for e in result.errors)


def test_timeline_inside_video_duration_passes() -> None:
    narration = NarrationResponse(
        segments=[NarrationSegment(order=0, text="a", visualization_step_order=0)]
    )
    alignment = align_narration(narration, _plan([0], durations=[3.0]))
    assert validate_timeline(alignment, video_duration_seconds=10.0).passed


# ---- 16. broken artifact lineage ----------------------------------------


def test_broken_lineage_link_is_detected() -> None:
    """Deliberately point an artifact at a parent that was never stored."""
    store = InMemoryArtifactStore()
    source = Artifact.create(ArtifactType.SOURCE, "t", content={"x": 1})
    store.save(source)
    orphan = Artifact.create(
        ArtifactType.TRACE, "t", content={"y": 2}, input_artifact_ids=["does-not-exist"]
    )
    store.save(orphan)

    missing = [pid for pid in orphan.input_artifact_ids if not store.exists(pid)]
    assert missing == ["does-not-exist"]
    # and the real parent has no children, proving the chain is broken
    assert store.list_children(source.id) == []


def test_intact_lineage_walks_back_to_source() -> None:
    store = InMemoryArtifactStore()
    source = Artifact.create(ArtifactType.SOURCE, "t", content={"x": 1})
    store.save(source)
    trace = Artifact.create(
        ArtifactType.TRACE, "t", content={"y": 2}, input_artifact_ids=[source.id]
    )
    store.save(trace)
    video = Artifact.create(
        ArtifactType.FINAL_VIDEO, "t", content={"z": 3}, input_artifact_ids=[trace.id]
    )
    store.save(video)

    # walk back to the root
    node, hops = video, 0
    while node.input_artifact_ids:
        parent_id = node.input_artifact_ids[0]
        assert store.exists(parent_id), f"broken link at {node.id}"
        node = store.get(parent_id)
        hops += 1
    assert node.id == source.id
    assert hops == 2


# ---- 18. workspace cleanup ----------------------------------------------


def test_workspace_is_removed_even_when_the_body_raises() -> None:
    workspace = Workspace()
    path = workspace.path
    assert path.exists()
    with pytest.raises(RuntimeError):
        with workspace:
            workspace.write_file("a.txt", "x")
            raise RuntimeError("boom")
    assert not path.exists(), "failed job must not leave a temp workspace behind"


def test_workspace_cleanup_is_idempotent() -> None:
    workspace = Workspace()
    path = workspace.path
    workspace.cleanup()
    workspace.cleanup()  # must not raise
    assert not path.exists()

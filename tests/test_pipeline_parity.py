"""PHASE 8.1 — the web generation path must be the verified pipeline.

The Phase 7.1 run and the Phase 8.0 audit found the web path diverging
from the golden path at exactly the stages that make a video trustworthy:

    golden path                         web path (before 8.1)
    ---------------------------------   --------------------------------
    per-segment TTS (spoken form)       one TTS call over all raw text
    fit_plan_to_narration BEFORE render (none)
    align_narration + SRT               (none)
    one track, segments placed          one track, placed nowhere
    validate_final_video + timeline     (none)
                                        ffmpeg -t cut 10.8 s of speech

plus a final gate that failed every run in which an LLM repair worked.

These tests pin each correction. The unit tests need nothing installed.
The `real_render` tests need only FFmpeg (no Maven, JVM, Manim or model):
a real silent MP4 stands in for Manim so that composition, probing and
media validation are the real code, measured on real files.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import time
from pathlib import Path

import pytest

from code2shorts.ai.contracts import (
    NarrationResponse,
    NarrationSegment,
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.artifacts.models import Artifact, ArtifactType
from code2shorts.artifacts.store import InMemoryArtifactStore
from code2shorts.core.models import SupportedLanguage, ValidationResult
from code2shorts.core.video_profile import VERTICAL_HD, UnsupportedVideoProfileError
from code2shorts.execution.sandbox import run_subprocess
from code2shorts.generation import (
    GenerationManager,
    GenerationRegistry,
    GenerationStatus,
    JobManager,
    PipelineFactory,
    TeachingConfig,
    fingerprint_request,
)
from code2shorts.media import MediaComposer, probe_audio, probe_video
from code2shorts.narration import SyntheticTTSProvider
from code2shorts.visualization.renderer import RenderContext, RenderResult, VideoRenderer
from code2shorts.visualization.timing import total_video_seconds
from code2shorts.workflow import (
    Code2ShortsState,
    ComposeMediaNode,
    FinalValidationNode,
    NodeExecutionError,
    NodeResult,
    RenderVideoNode,
    Workflow,
    WorkflowMetadata,
    WorkflowNode,
    WorkflowRequest,
    WorkflowRunner,
)
from tests.test_generation_registry import SOURCE, _request

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg on PATH")


# ---- shared fixtures -------------------------------------------------------


def _plan(seconds_per_step: float = 1.0, steps: int = 3) -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="Palindrome",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.VARIABLE_UPDATE,
                trace_event_index=i,
                narration_text=f"step {i}",
                duration_seconds=seconds_per_step,
            )
            for i in range(steps)
        ],
    )


# Long enough that each segment's speech is several times its 1 s step:
# the 7.1 shape (99 s of speech against an 88 s render), in miniature.
SPOKEN = [
    "We compare the left character with the right character of the string.",
    "They match, so both pointers move one position toward the centre.",
    "The pointers have met in the middle, so the method returns true.",
]


def _narration(plan: VisualizationPlanResponse) -> NarrationResponse:
    return NarrationResponse(
        segments=[
            NarrationSegment(order=s.order, text=SPOKEN[s.order], visualization_step_order=s.order)
            for s in plan.steps
        ]
    )


def _state_with(store: InMemoryArtifactStore, plan, narration) -> Code2ShortsState:
    state = Code2ShortsState(
        request=WorkflowRequest(
            topic="palindrome",
            language=SupportedLanguage.JAVA,
            source_files=SOURCE,
            entry_point="src/main/java/Main.java",
        ),
        metadata=WorkflowMetadata(workflow_id="parity", execution_id="exec"),
    )
    for key, kind, model in (
        ("visualization_plan", ArtifactType.VISUALIZATION_PLAN, plan),
        ("narration", ArtifactType.NARRATION, narration),
    ):
        artifact = Artifact.create(type=kind, producer="test", content=model.model_dump())
        store.save(artifact)
        state.artifact_ids[key] = artifact.id
    return state


class _BlackVideoRenderer(VideoRenderer):
    """A real 1080x1920 H.264 file, exactly as long as Manim would make this
    plan (`total_video_seconds` is the timing model the renderer and the
    aligner share). Records the plan it was handed."""

    def __init__(self) -> None:
        self.rendered: list[VisualizationPlanResponse] = []

    def render(self, plan, output_dir: Path, context: RenderContext | None = None) -> RenderResult:
        self.rendered.append(plan)
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "output.mp4"
        seconds = total_video_seconds(plan)
        result = run_subprocess(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30",
             "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", str(path)],
            cwd=output_dir, timeout_seconds=120.0,
        )
        assert result.returncode == 0, result.stderr[-500:]
        probed = probe_video(path)
        return RenderResult(
            output_path=str(path), checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
            duration_seconds=probed.duration_seconds, resolution=probed.resolution,
            fps=30, renderer_version="test-black",
        )


def _run_web_media_stages(tmp_path: Path):
    """The post-narration half of the web pipeline, with real FFmpeg."""
    from code2shorts.workflow.nodes import NarrationTimingNode

    store = InMemoryArtifactStore()
    plan = _plan()
    narration = _narration(plan)
    state = _state_with(store, plan, narration)
    tts = SyntheticTTSProvider(seconds_per_char=0.06)
    renderer = _BlackVideoRenderer()
    workflow = Workflow(
        "parity",
        [
            NarrationTimingNode(tts_provider=tts, output_dir=tmp_path),
            RenderVideoNode(renderer, output_dir=tmp_path),
            ComposeMediaNode(tts_provider=tts, composer=MediaComposer(), output_dir=tmp_path),
            FinalValidationNode(),
        ],
    )
    result = WorkflowRunner(store).run(workflow, state)
    return result, store, renderer, plan


# ---- A. timeline: the render holds the measured speech ---------------------


def test_the_web_pipeline_times_narration_before_it_renders() -> None:
    """The stage order IS the fix: speech must be measured before the
    visual timeline is committed to pixels."""
    from code2shorts.config import Settings
    from code2shorts.webapp.pipeline import DefaultPipelineFactory

    nodes = DefaultPipelineFactory(Settings(llm_provider="mock")).build(
        _request(), Path("unused")
    )
    names = [node.name for node in nodes]

    assert names.index("narration") < names.index("narration_timing") < names.index("render_video")
    assert names[-2:] == ["compose_media", "final_validation"]


def test_the_web_pipeline_and_the_job_stage_list_agree() -> None:
    from code2shorts.config import Settings
    from code2shorts.generation.manager import STAGE_LABELS
    from code2shorts.webapp.pipeline import DefaultPipelineFactory

    nodes = DefaultPipelineFactory(Settings(llm_provider="mock")).build(_request(), Path("unused"))
    assert [node.name for node in nodes] == list(STAGE_LABELS)


@pytest.mark.real_render
@needs_ffmpeg
def test_the_rendered_plan_is_widened_to_hold_every_segment(tmp_path: Path) -> None:
    result, store, renderer, proposed = _run_web_media_stages(tmp_path)

    assert result.succeeded, [e.message for e in result.state.errors]
    rendered = renderer.rendered[0]
    timing = store.get(result.state.artifact_ids["narration_timing"]).content
    speech = {s["segment_id"]: s["audio_duration_seconds"] for s in timing["alignment"]["segments"]}
    for step in rendered.steps:
        assert step.duration_seconds >= speech[step.order], (
            f"step {step.order} holds {step.duration_seconds}s for {speech[step.order]}s of speech"
        )
    # The proposal was too short for its speech, so the fit must have acted.
    assert sum(s.duration_seconds for s in rendered.steps) > sum(
        s.duration_seconds for s in proposed.steps
    )


@pytest.mark.real_render
@needs_ffmpeg
def test_the_final_video_is_as_long_as_the_fitted_timeline(tmp_path: Path) -> None:
    result, _store, renderer, _plan_ = _run_web_media_stages(tmp_path)

    final = probe_video(Path(_final_path(result, _store)))
    assert final.duration_seconds == pytest.approx(total_video_seconds(renderer.rendered[0]), abs=0.2)


# ---- B. audio: no speech is cut --------------------------------------------


def _final_path(result, store) -> str:
    return store.get(result.state.artifact_ids["final_video"]).content["output_path"]


@pytest.mark.real_render
@needs_ffmpeg
def test_the_final_audio_carries_every_segment_to_its_end(tmp_path: Path) -> None:
    """The 7.1 defect, as an invariant: the muxed audio must reach the end
    of the last placed segment's speech. Before 8.1 the web path muxed one
    unplaced track and `-t` cut whatever outlasted the picture."""
    result, store, _renderer, _plan_ = _run_web_media_stages(tmp_path)
    assert result.succeeded, [e.message for e in result.state.errors]

    timing = store.get(result.state.artifact_ids["narration_timing"]).content["alignment"]
    speech_ends = max(
        s["start_seconds"] + s["audio_duration_seconds"] for s in timing["segments"]
    )
    final_audio = probe_audio(Path(_final_path(result, store)))

    assert final_audio.duration_seconds + 0.05 >= speech_ends


@pytest.mark.real_render
@needs_ffmpeg
def test_media_and_timeline_validation_are_recorded_for_this_run(tmp_path: Path) -> None:
    result, store, _renderer, _plan_ = _run_web_media_stages(tmp_path)

    final_id = result.state.artifact_ids["final_video"]
    media_checks = [r for r in result.state.validation.results if r.subject_artifact_id == final_id]
    assert len(media_checks) >= 2, "final-video AND timeline validation must both run"
    assert all(r.passed for r in media_checks)
    assert "subtitles" in result.state.artifact_ids


# ---- C. the final gate ignores superseded repair attempts ------------------


def _final_gate_state(*results: ValidationResult) -> tuple[Code2ShortsState, InMemoryArtifactStore]:
    store = InMemoryArtifactStore()
    state = _state_with(store, _plan(), _narration(_plan()))
    final = Artifact.create(
        type=ArtifactType.FINAL_VIDEO, producer="test", content={"checksum": "abc"}
    )
    store.save(final)
    state.artifact_ids["final_video"] = final.id
    for result in results:
        state.validation.results.append(result)
    return state, store


def _run_gate(state, store):
    from code2shorts.workflow.context import WorkflowContext

    context = WorkflowContext(
        artifact_store=store, workflow_id="w", execution_id="e", emit=lambda _e: None
    )
    return FinalValidationNode().run(state, context)


def test_a_superseded_repair_attempt_does_not_fail_the_run() -> None:
    """Exactly the Phase 7.1 failure: a semantic attempt failed, repair
    replaced it, the replacement passed - and the final gate still counted
    the replaced attempt."""
    state, store = _final_gate_state(
        ValidationResult(stage="semantic", passed=False, errors=["bad"], superseded=True),
        ValidationResult(stage="semantic", passed=True),
    )

    _run_gate(state, store)

    assert state.validation.all_passed


def test_a_standing_failure_still_fails_the_run() -> None:
    state, store = _final_gate_state(
        ValidationResult(stage="semantic", passed=False, errors=["bad"]),
    )

    with pytest.raises(NodeExecutionError, match="final validation failed"):
        _run_gate(state, store)


# ---- D/E/F. versions, duplicates, force, and validation ownership ----------


class _ValidatedNode(WorkflowNode):
    """Writes a real final video file whose artifact carries a checksum,
    so the REAL FinalValidationNode can judge it."""

    name = "compose_media"

    def __init__(self, output_dir: Path, marker: str, delay: float = 0.0) -> None:
        self._output_dir = output_dir
        self._marker = marker
        self._delay = delay

    def run(self, state, context) -> NodeResult:
        time.sleep(self._delay)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / "final.mp4"
        path.write_bytes(self._marker.encode())
        artifact = Artifact.create(
            type=ArtifactType.FINAL_VIDEO,
            producer=self.name,
            content={"path": str(path), "checksum": hashlib.sha256(path.read_bytes()).hexdigest()},
        )
        context.artifact_store.save(artifact)
        state.artifact_ids["final_video"] = artifact.id
        return NodeResult(state=state, artifact_ids_created=[artifact.id])


class _FailingGateNode(WorkflowNode):
    """Records a standing validation failure for ITS run only."""

    name = "narration_timing"

    def run(self, state, context) -> NodeResult:
        state.validation.record(ValidationResult(stage="domain", passed=False, errors=["no"]))
        return NodeResult(state=state, artifact_ids_created=[])


class _Pipeline(PipelineFactory):
    def __init__(self, fail_versions: tuple[int, ...] = (), delay: float = 0.0) -> None:
        self.builds = 0
        self._fail_versions = fail_versions
        self._delay = delay

    def build(self, request, output_dir: Path) -> list:
        self.builds += 1
        version = int(output_dir.name.removeprefix("v"))
        nodes: list[WorkflowNode] = []
        if version in self._fail_versions:
            nodes.append(_FailingGateNode())
        nodes += [_ValidatedNode(output_dir, f"video-v{version}", self._delay), FinalValidationNode()]
        return nodes


@pytest.fixture
def library(tmp_path: Path):
    registry = GenerationRegistry(tmp_path / "library")
    return registry, InMemoryArtifactStore()


def test_validation_belongs_to_the_version_it_validates(library) -> None:
    """One store, two runs. v2 fails its own validation; that must neither
    touch v1 nor be hidden by v1's success."""
    registry, store = library
    manager = GenerationManager(registry, _Pipeline(fail_versions=(2,)), store)

    v1 = manager.generate(_request())
    v2 = manager.generate(_request(), force=True)

    assert v1.status is GenerationStatus.COMPLETED
    assert v2.status is GenerationStatus.FAILED
    assert registry.current_version("palindrome").version == 1
    assert registry.artifact_path(registry.get("palindrome", 1), "final_video").read_bytes() == b"video-v1"


def test_force_regeneration_adds_v2_and_leaves_v1_intact(library) -> None:
    registry, store = library
    manager = GenerationManager(registry, _Pipeline(), store)

    manager.generate(_request())
    v1_file = registry.artifact_path(registry.get("palindrome", 1), "final_video")
    before = v1_file.read_bytes()
    manager.generate(_request(), force=True)

    v2_file = registry.artifact_path(registry.get("palindrome", 2), "final_video")
    assert v1_file.read_bytes() == before == b"video-v1"
    assert v2_file.read_bytes() == b"video-v2"
    assert v1_file.parent != v2_file.parent


def test_the_same_request_reuses_and_does_no_work(library) -> None:
    registry, store = library
    pipeline = _Pipeline()
    manager = GenerationManager(registry, pipeline, store)

    first = manager.generate(_request())
    # Naming the default format explicitly is still the same video.
    again = manager.generate(_request(config=TeachingConfig(video_format="vertical_hd")))

    assert again.version == first.version == 1
    assert pipeline.builds == 1
    assert len(registry.all_versions()) == 1


# ---- concurrency: two generations of the same content ----------------------


def test_concurrent_forced_generations_get_distinct_versions(library) -> None:
    registry, store = library
    manager = GenerationManager(registry, _Pipeline(delay=0.2), store)
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def generate() -> None:
        barrier.wait()
        try:
            manager.generate(_request(), force=True)
        except BaseException as error:  # noqa: BLE001 - surfaced below
            errors.append(error)

    threads = [threading.Thread(target=generate) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    versions = sorted(v.version for v in registry.all_versions())
    assert versions == [1, 2, 3, 4], "no version may be lost or share a number"
    for number in versions:
        record = registry.get("palindrome", number)
        assert record.status is GenerationStatus.COMPLETED
        assert registry.artifact_path(record, "final_video").read_bytes() == f"video-v{number}".encode()


def test_concurrent_writers_lose_no_rows(tmp_path: Path) -> None:
    registry = GenerationRegistry(tmp_path / "library")
    errors: list[BaseException] = []

    def write(index: int) -> None:
        try:
            registry.create_version(
                f"algo{index}",
                fingerprint_request(f"algo{index}", SOURCE, TeachingConfig(), "mock"),
            )
        except BaseException as error:  # noqa: BLE001 - surfaced below
            errors.append(error)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    assert len(registry.all_versions()) == 20


def test_a_version_directory_is_never_reused(tmp_path: Path) -> None:
    """If the index loses a row, the next version must still get a NEW
    directory - reusing v1/ would write over a video on disk."""
    registry = GenerationRegistry(tmp_path / "library")
    fingerprint = fingerprint_request("palindrome", SOURCE, TeachingConfig(), "mock")
    registry.create_version("palindrome", fingerprint)
    (registry.version_dir("palindrome", 1) / "final.mp4").write_bytes(b"keep me")
    registry.index_path.write_text('{"versions": []}', encoding="utf-8")

    fresh = registry.create_version("palindrome", fingerprint)

    assert fresh.version == 2
    assert (registry.version_dir("palindrome", 1) / "final.mp4").read_bytes() == b"keep me"


def test_two_simultaneous_job_starts_run_one_job(library) -> None:
    registry, store = library
    jobs = JobManager(GenerationManager(registry, _Pipeline(delay=0.3), store))
    barrier = threading.Barrier(8)
    ids: list[str] = []

    def start() -> None:
        barrier.wait()
        ids.append(jobs.start(_request()).id)

    threads = [threading.Thread(target=start) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(ids)) == 1


# ---- G/H. formats ----------------------------------------------------------


def _refuse(monkeypatch, video_format: str) -> None:
    """No product profile is refused since Phase 8.3, so the refusal path
    is exercised against a 4K profile made unrenderable for the test."""
    import dataclasses

    from code2shorts.core import video_profile

    profile = video_profile.resolve_video_profile(video_format)
    monkeypatch.setitem(
        video_profile.PROFILES, profile.id,
        dataclasses.replace(profile, unsupported_reason="made unrenderable for this test"),
    )


@pytest.mark.parametrize("video_format", ["vertical_4k", "landscape_4k"])
def test_an_unsupported_format_leaves_nothing_behind(library, video_format, monkeypatch) -> None:
    _refuse(monkeypatch, video_format)
    registry, store = library
    pipeline = _Pipeline()
    manager = GenerationManager(registry, pipeline, store)

    with pytest.raises(UnsupportedVideoProfileError):
        manager.generate(_request(config=TeachingConfig(video_format=video_format)), force=True)

    assert pipeline.builds == 0
    assert registry.all_versions() == []
    assert not (registry.root / "palindrome").exists(), "no partial artifact directory"


def test_an_unsupported_format_job_fails_cleanly(library, monkeypatch) -> None:
    _refuse(monkeypatch, "vertical_4k")
    registry, store = library
    jobs = JobManager(GenerationManager(registry, _Pipeline(), store))

    job = jobs.start(_request(config=TeachingConfig(video_format="vertical_4k")))
    for _ in range(100):
        current = jobs.get(job.id)
        if current.status not in (GenerationStatus.QUEUED, GenerationStatus.RUNNING):
            break
        time.sleep(0.02)

    assert current.status is GenerationStatus.FAILED
    assert "4K" in (current.error or "")
    assert registry.all_versions() == []


def test_a_request_without_a_format_is_vertical_hd_end_to_end_of_wiring() -> None:
    from code2shorts.config import Settings
    from code2shorts.webapp.pipeline import DefaultPipelineFactory

    request = _request()
    assert request.config.video_profile is VERTICAL_HD
    nodes = DefaultPipelineFactory(Settings(llm_provider="mock")).build(request, Path("unused"))
    compose = next(node for node in nodes if node.name == "compose_media")
    assert (compose.media_policy.width, compose.media_policy.height) == (1080, 1920)

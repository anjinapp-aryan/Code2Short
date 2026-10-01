"""PHASE 8.2B — the layout seam, with the portrait video provably unchanged.

What this phase promises:

    * one layout value per orientation, selected from the VideoProfile;
    * the portrait layout IS the existing constants, and the portrait
      scene the renderer emits through it is byte-identical to the scene
      behind the accepted Phase 6.5.3 video;
    * a layout whose composition is not implemented fails loudly, before
      Manim runs, and a portrait arrangement can never be drawn into a
      16:9 frame (Phase 8.2C then implemented the landscape layout);
    * nothing above the renderer (trace, plans, narration, timeline)
      learns about layout.

No Maven, JVM, Manim, FFmpeg or model is needed.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from code2shorts.ai.contracts import (
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import ExecutionTrace
from code2shorts.core.video_profile import (
    LANDSCAPE_4K,
    LANDSCAPE_HD,
    VERTICAL_4K,
    VERTICAL_HD,
    Orientation,
)
from code2shorts.execution.sandbox import ProcessResult
from code2shorts.visualization import RenderContext, manim_renderer, primitives
import dataclasses

from code2shorts.visualization.layout import LANDSCAPE, CompositionLayout, layout_for
from code2shorts.visualization.manim_renderer import ManimVideoRenderer, build_scene_source
from code2shorts.visualization.primitives import PORTRAIT
from code2shorts.visualization.renderer import RenderingFailure
from tests.java_fixtures import load_algorithm_fixture

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "tests" / "fixtures" / "render_baseline" / "portrait_6_5_3"

ACCEPTED_PORTRAIT_SCENE_SHA256 = (
    "5b0b81e05bc703dfbefc378a7202902482d8a3143d0ffe1d99710c04e497d357"
)
"""sha256 of `output/phase6_5_3/palindrome/render/scene.py`, the scene behind
the accepted Phase 6.5.3 video, regenerated from the plan and trace copied
into BASELINE. If this changes, the portrait video changed."""


def _baseline_scene(**kwargs) -> str:
    plan = VisualizationPlanResponse.model_validate(
        json.loads((BASELINE / "plan.json").read_text(encoding="utf-8"))
    )
    trace = ExecutionTrace.model_validate(
        json.loads((BASELINE / "trace.json").read_text(encoding="utf-8"))
    )
    code = load_algorithm_fixture("palindrome")
    context = RenderContext(
        trace=trace, source_files=dict(code.source_files), entry_point=code.entry_point
    )
    return build_scene_source(plan, "GeneratedScene", context, **kwargs)


def _tiny_plan() -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=0, visual_action=VisualAction.INTRO, trace_event_index=0,
                narration_text="x", duration_seconds=1.0,
            )
        ],
    )


# ---- the portrait baseline is byte-identical -------------------------------


def test_the_portrait_scene_is_byte_identical_to_the_accepted_video() -> None:
    scene = _baseline_scene()
    assert hashlib.sha256(scene.encode("utf-8")).hexdigest() == ACCEPTED_PORTRAIT_SCENE_SHA256


def test_naming_the_portrait_layout_explicitly_changes_nothing() -> None:
    assert _baseline_scene(layout=PORTRAIT) == _baseline_scene()


def test_the_portrait_layout_is_the_existing_constants() -> None:
    """Every field is the constant itself, so the two cannot drift."""
    expected = {
        "frame_width": primitives.FRAME_WIDTH,
        "frame_height": primitives.FRAME_HEIGHT,
        "safe_width": primitives.SAFE_WIDTH,
        "safe_top": primitives.SAFE_TOP,
        "safe_bottom": primitives.SAFE_BOTTOM,
        "title_y": primitives.TITLE_Y,
        "scalars_y": primitives.SCALARS_Y,
        "structure_width": primitives.SAFE_WIDTH,
        "structure_top": primitives.STRUCTURE_TOP,
        "structure_max_height": primitives.STRUCTURE_MAX_HEIGHT,
        "array_y": primitives.ARRAY_Y,
        "caption_y": primitives.CAPTION_Y,
        "caption_width": primitives.SAFE_WIDTH,
        "caption_chars_per_line": primitives.CAPTION_CHARS_PER_LINE,
        "caption_max_lines": primitives.CAPTION_MAX_LINES,
        "caption_max_height": primitives.CAPTION_MAX_HEIGHT,
        "band_gap": primitives.BAND_GAP,
        "code_y": primitives.CODE_Y,
        "code_max_width": primitives.CODE_MAX_WIDTH,
        "code_viewport_width": primitives.CODE_VIEWPORT_WIDTH,
        "code_viewport_center_x": primitives.CODE_VIEWPORT_CENTER_X,
        "code_height_budget": primitives.CODE_HEIGHT_BUDGET,
    }
    for field, value in expected.items():
        assert getattr(PORTRAIT, field) == value, field
    # The x centres were always the literal 0 in the emitted source.
    for field in ("title_x", "scalars_x", "structure_x", "caption_x"):
        assert getattr(PORTRAIT, field) == 0 and isinstance(getattr(PORTRAIT, field), int)
    assert PORTRAIT.composition_implemented
    assert PORTRAIT.orientation is Orientation.PORTRAIT


def test_the_portrait_header_still_states_an_8_unit_frame() -> None:
    assert "config.frame_height = 8.0" in _baseline_scene()


# ---- selection by profile ---------------------------------------------------


def test_vertical_profiles_select_the_portrait_layout() -> None:
    assert VERTICAL_HD.aspect_ratio == "9:16" and VERTICAL_HD.resolution == "1080x1920"
    assert layout_for(VERTICAL_HD) is PORTRAIT
    # Same aspect, same arrangement, higher density.
    assert layout_for(VERTICAL_4K) is PORTRAIT


def test_landscape_profiles_select_the_landscape_layout() -> None:
    assert LANDSCAPE_HD.aspect_ratio == "16:9" and LANDSCAPE_HD.resolution == "1920x1080"
    assert LANDSCAPE_HD.orientation is Orientation.LANDSCAPE
    assert layout_for(LANDSCAPE_HD) is LANDSCAPE
    assert layout_for(LANDSCAPE_4K) is LANDSCAPE


@pytest.mark.parametrize("layout,profile", [(PORTRAIT, VERTICAL_HD), (LANDSCAPE, LANDSCAPE_HD)])
def test_both_layouts_keep_240_pixels_per_unit(layout: CompositionLayout, profile) -> None:
    """Typography, padding and cell sizes are shared constants; they mean
    the same pixels only if the density is the same."""
    assert profile.pixel_height / layout.frame_height == pytest.approx(240.0)
    assert profile.pixel_width / layout.frame_width == pytest.approx(240.0)
    assert layout.aspect == pytest.approx(profile.pixel_width / profile.pixel_height)


def test_the_landscape_layout_describes_the_approved_arrangement() -> None:
    """Data only, from docs/PHASE_8_2A_LANDSCAPE_DESIGN.md: 5% safe area,
    code column at the portrait viewport width, code left of state."""
    assert (LANDSCAPE.frame_width, LANDSCAPE.frame_height) == (8.0, 4.5)
    assert LANDSCAPE.safe_width == pytest.approx(8.0 * 0.90)
    assert LANDSCAPE.safe_top == pytest.approx(4.5 / 2 - 4.5 * 0.05)
    assert LANDSCAPE.code_viewport_width == PORTRAIT.code_viewport_width
    assert LANDSCAPE.code_viewport_center_x < 0 < LANDSCAPE.structure_x
    assert LANDSCAPE.composition_implemented and LANDSCAPE.composer == "columns"


DRAFT = dataclasses.replace(LANDSCAPE, name="draft", composition_implemented=False)
"""A layout that has not been implemented - what LANDSCAPE was until 8.2C."""


# ---- landscape cannot silently render as portrait ---------------------------


def test_an_unimplemented_layout_is_refused_by_scene_generation() -> None:
    with pytest.raises(RenderingFailure, match="draft composition is not implemented"):
        build_scene_source(_tiny_plan(), "GeneratedScene", layout=DRAFT)


def _recording_renderer(**kwargs) -> tuple[ManimVideoRenderer, list]:
    calls: list = []

    def run(command, cwd, timeout_seconds, **_kwargs):
        calls.append(command)
        return ProcessResult(returncode=1, stdout="", stderr="", timed_out=False,
                             duration_seconds=0.0)

    return ManimVideoRenderer(run_subprocess_fn=run, **kwargs), calls


def test_an_unimplemented_layout_is_refused_before_manim_runs(tmp_path: Path) -> None:
    renderer, calls = _recording_renderer(resolution="1920x1080", layout=DRAFT)

    with pytest.raises(RenderingFailure, match="not implemented"):
        renderer.render(_tiny_plan(), tmp_path / "render")

    assert calls == []
    assert not (tmp_path / "render" / "scene.py").exists()


def test_the_portrait_layout_is_refused_for_landscape_pixels(tmp_path: Path) -> None:
    """The exact failure the seam exists to prevent: a 1920x1080 render
    with the portrait arrangement."""
    renderer, calls = _recording_renderer(resolution="1920x1080")

    with pytest.raises(RenderingFailure, match="does not match"):
        renderer.render(_tiny_plan(), tmp_path / "render")

    assert calls == []
    assert not (tmp_path / "render").exists()


def test_the_web_renderer_for_landscape_hd_uses_the_landscape_layout(
    tmp_path: Path, monkeypatch
) -> None:
    """Until 8.2C this asserted a refusal; the landscape composition now
    exists, so the same path must produce a native 16:9 scene."""
    pytest.importorskip("fastapi", reason="Phase 7.0 web layer requires fastapi")
    from code2shorts.webapp.pipeline import build_renderer

    commands: list = []

    def fake_run(command, cwd, timeout_seconds, **_kw):
        commands.append(command)
        return ProcessResult(returncode=1, stdout="", stderr="stop", timed_out=False,
                             duration_seconds=0.0)

    monkeypatch.setattr(manim_renderer, "run_subprocess", fake_run)
    with pytest.raises(RenderingFailure, match="manim render failed"):
        build_renderer(LANDSCAPE_HD).render(_tiny_plan(), tmp_path / "render")

    assert commands[0][commands[0].index("--resolution") + 1] == "1920,1080"
    assert "config.frame_height = 4.5" in (tmp_path / "render" / "scene.py").read_text(
        encoding="utf-8"
    )


def test_the_web_renderer_for_vertical_hd_uses_the_portrait_layout(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("fastapi", reason="Phase 7.0 web layer requires fastapi")
    from code2shorts.webapp.pipeline import build_renderer

    commands: list = []

    def fake_run(command, cwd, timeout_seconds, **_kw):
        commands.append(command)
        return ProcessResult(returncode=1, stdout="", stderr="stop", timed_out=False,
                             duration_seconds=0.0)

    monkeypatch.setattr(manim_renderer, "run_subprocess", fake_run)
    with pytest.raises(RenderingFailure, match="manim render failed"):
        build_renderer(VERTICAL_HD).render(_tiny_plan(), tmp_path / "render")

    assert commands[0][commands[0].index("--resolution") + 1] == "1080,1920"
    assert "config.frame_height = 8.0" in (tmp_path / "render" / "scene.py").read_text(
        encoding="utf-8"
    )


# ---- layout stays below the educational model --------------------------------


LAYOUT_FREE_PACKAGES = ("ai", "narration", "workflow", "generation", "langadapter", "media")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_nothing_above_the_renderer_imports_the_layout() -> None:
    src = REPO / "src" / "code2shorts"
    offenders = []
    for package in LAYOUT_FREE_PACKAGES:
        for path in (src / package).rglob("*.py"):
            if "code2shorts.visualization.layout" in _imports(path):
                offenders.append(str(path.relative_to(src)))
    for path in (src / "core" / "models.py", src / "visualization" / "timing.py"):
        if "code2shorts.visualization.layout" in _imports(path):
            offenders.append(str(path.relative_to(src)))
    assert not offenders, offenders


def test_the_timeline_does_not_take_a_layout() -> None:
    from code2shorts.narration.alignment import align_narration
    from code2shorts.narration.fitting import fit_plan_to_narration
    from code2shorts.visualization.timing import step_windows, total_video_seconds

    for function in (step_windows, total_video_seconds, align_narration, fit_plan_to_narration):
        assert "layout" not in inspect.signature(function).parameters, function.__name__

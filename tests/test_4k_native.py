"""PHASE 8.3 — 4K is a profile, rendered natively, never an upscale.

    VideoProfile (4K) -> layout_for (same layout as HD) -> SAME scene source
      -> Manim --resolution 3840,2160 / 2160,3840 -> native 4K frames

What these tests establish, and how:

* **Same composition.** A 4K profile selects the HD layout of its
  orientation, and the renderer writes a byte-identical scene for HD and
  4K. Geometry lives in scene units, so the only difference is how many
  pixels Manim rasterises each unit into (240 at HD, 480 at 4K).
* **Same geometry, measured.** Every region's bounds are built under the
  HD and the 4K pixel configuration and compared: they are equal in
  units, so every Phase 8.2C / 6.5.3 containment and overlap result holds
  at 4K, at exactly twice the pixels.
* **Native, not upscaled.** A real Manim render at 4K is probed at the
  RENDER stage (not only after composition). Its frame, box-downsampled
  2x, matches the HD render of the same plan - same picture - and it holds
  far more fine detail than the HD frame upscaled to 4K, which is what an
  upscale would have produced.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from code2shorts.ai.contracts import VisualizationPlanResponse
from code2shorts.core.models import ExecutionTrace
from code2shorts.core.video_profile import (
    LANDSCAPE_4K,
    LANDSCAPE_HD,
    VERTICAL_4K,
    VERTICAL_HD,
    Orientation,
)
from code2shorts.execution.sandbox import ProcessResult
from code2shorts.visualization import RenderContext, manim_renderer
from code2shorts.visualization.layout import LANDSCAPE, layout_for
from code2shorts.visualization.primitives import PORTRAIT
from code2shorts.visualization.renderer import RenderingFailure
from tests.java_fixtures import load_algorithm_fixture

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "tests" / "fixtures" / "render_baseline" / "portrait_6_5_3"
PAIRS = [(VERTICAL_HD, VERTICAL_4K), (LANDSCAPE_HD, LANDSCAPE_4K)]


def _context() -> RenderContext:
    trace = ExecutionTrace.model_validate(
        json.loads((BASELINE / "trace.json").read_text(encoding="utf-8"))
    )
    code = load_algorithm_fixture("palindrome")
    return RenderContext(trace=trace, source_files=dict(code.source_files),
                         entry_point=code.entry_point)


def _plan(step_orders: list[int] | None = None, seconds: float | None = None):
    """The accepted real palindrome plan, optionally cut to a few steps."""
    raw = json.loads((BASELINE / "plan.json").read_text(encoding="utf-8"))
    if step_orders is not None:
        picked = [raw["steps"][order] for order in step_orders]
        for new_order, step in enumerate(picked):
            step["order"] = new_order
            if seconds is not None:
                step["duration_seconds"] = seconds
        raw["steps"] = picked
    return VisualizationPlanResponse.model_validate(raw)


# ---- the profile selects the HD layout of its orientation -----------------


@pytest.mark.parametrize("hd,uhd", PAIRS)
def test_4k_uses_the_layout_of_its_orientation(hd, uhd) -> None:
    assert layout_for(uhd) is layout_for(hd)
    assert layout_for(uhd) is (PORTRAIT if uhd.orientation is Orientation.PORTRAIT else LANDSCAPE)
    # The pixel aspect still matches the layout, which the renderer checks.
    assert abs(uhd.pixel_width / uhd.pixel_height - layout_for(uhd).aspect) < 1e-9


@pytest.mark.parametrize("hd,uhd", PAIRS)
def test_hd_and_4k_write_the_same_scene_and_ask_manim_for_their_own_pixels(
    tmp_path: Path, monkeypatch, hd, uhd
) -> None:
    """Resolution reaches Manim as a command-line size and nowhere else: the
    generated scene is byte-identical, so 4K cannot be a different lesson."""
    from code2shorts.webapp.pipeline import build_renderer

    commands: list[list[str]] = []

    def stop(command, cwd, timeout_seconds, **_kwargs):
        commands.append(command)
        return ProcessResult(returncode=1, stdout="", stderr="stop", timed_out=False,
                             duration_seconds=0.0)

    monkeypatch.setattr(manim_renderer, "run_subprocess", stop)
    scenes = {}
    for profile in (hd, uhd):
        with pytest.raises(RenderingFailure):
            build_renderer(profile).render(_plan(), tmp_path / profile.id.value, _context())
        scenes[profile] = (tmp_path / profile.id.value / "scene.py").read_bytes()

    assert scenes[hd] == scenes[uhd]
    sizes = [c[c.index("--resolution") + 1] for c in commands]
    assert sizes == [f"{hd.pixel_width},{hd.pixel_height}", f"{uhd.pixel_width},{uhd.pixel_height}"]
    frame_height = layout_for(uhd).frame_height
    assert f"config.frame_height = {frame_height}".encode() in scenes[uhd]


# ---- geometry is the same in units, so 4K is exactly 2x in pixels ----------


def _compose_bounds(pixel_width: int, pixel_height: int, layout) -> dict[str, tuple]:
    """Build one real palindrome frame's mobjects under a pixel config."""
    pytest.importorskip("manim")
    from manim import tempconfig

    from code2shorts.visualization import primitives as P
    from code2shorts.visualization.manim_renderer import COMPOSERS
    from code2shorts.visualization.state import reconstruct_frames

    context = _context()
    frames = reconstruct_frames(context.trace)
    frame = next(f for f in frames if f.arrays and len(f.scalars) >= 2)
    source = dict(context.source_files)
    from code2shorts.core.models import SourceLocation
    from code2shorts.visualization.code_state import DEFAULT_WINDOW_RADIUS, build_code_state

    location = SourceLocation(file=context.entry_point, line=frame.line_number)
    radius = P.fit_window_radius(location, source, minimum=DEFAULT_WINDOW_RADIUS, layout=layout)
    state = build_code_state(location, source, radius)
    body = (P.title_text("Two Pointers Find A Palindrome", layout=layout)
            + P.array_row(frame, layout=layout) + P.pointer_arrows(frame, layout=layout)
            + P.scalar_panel(frame, layout=layout) + P.code_panel(state, layout=layout)
            + P.caption_text("We compare the two ends and move both pointers inward.",
                             layout=layout))
    compose = COMPOSERS[layout.composer]
    body += compose(["arr_group", "ptr_group", "vars_group"], caption_var="caption",
                    code_var="code_group", layout=layout)
    config = {"pixel_width": pixel_width, "pixel_height": pixel_height,
              "frame_height": layout.frame_height,
              "frame_width": layout.frame_height * pixel_width / pixel_height}
    with tempconfig(config):
        namespace: dict = {}
        exec("from manim import *", namespace)
        exec("\n".join(body), namespace)
    bounds = {}
    for name in ("title", "code_group", "arr_group", "ptr_group", "vars_group", "caption"):
        mob = namespace[name]
        bounds[name] = tuple(round(float(v), 4) for v in (
            mob.get_left()[0], mob.get_right()[0], mob.get_bottom()[1], mob.get_top()[1]))
    return bounds


@pytest.mark.real_render
@pytest.mark.parametrize("hd,uhd", PAIRS)
def test_every_region_has_the_same_unit_bounds_at_hd_and_4k(hd, uhd) -> None:
    layout = layout_for(uhd)
    at_hd = _compose_bounds(hd.pixel_width, hd.pixel_height, layout)
    at_4k = _compose_bounds(uhd.pixel_width, uhd.pixel_height, layout)
    assert at_hd == at_4k


@pytest.mark.parametrize("hd,uhd", PAIRS)
def test_4k_has_twice_the_pixels_per_unit_and_the_same_safe_margins(hd, uhd) -> None:
    layout = layout_for(uhd)
    hd_ppu = hd.pixel_height / layout.frame_height
    uhd_ppu = uhd.pixel_height / layout.frame_height
    assert (hd_ppu, uhd_ppu) == (240.0, 480.0)
    margin_x = (layout.frame_width - layout.safe_width) / 2
    margin_y = layout.frame_height / 2 - layout.safe_top
    # The margins are the layout's, unchanged; at 4K they are twice the pixels.
    assert round(margin_x * uhd_ppu, 6) == round(2 * margin_x * hd_ppu, 6)
    assert round(margin_y * uhd_ppu, 6) == round(2 * margin_y * hd_ppu, 6)
    if layout is LANDSCAPE:
        assert (round(margin_x * uhd_ppu), round(margin_y * uhd_ppu)) == (192, 108), "5% at 4K"


# ---- a real render is native 4K, not an upscale -----------------------------


needs_render_tools = pytest.mark.skipif(
    shutil.which("manim") is None or shutil.which("ffmpeg") is None,
    reason="needs manim and ffmpeg on PATH",
)


def _frame(path: Path, fraction: float):
    """One decoded frame, as greyscale float array, from the render-stage MP4."""
    import av
    import numpy as np

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        frames = [f for f in container.decode(stream)]
    chosen = frames[int(len(frames) * fraction)]
    return np.asarray(chosen.to_ndarray(format="gray"), dtype=np.float64)


def _down2(image):
    h, w = image.shape
    return image.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


def _beyond_hd_nyquist(image) -> float:
    """Share of the image's spectral energy at frequencies an HD frame
    cannot hold (beyond half the 4K band on either axis). A smooth upscale
    (bilinear, bicubic, Lanczos) puts almost nothing there."""
    import numpy as np

    spectrum = np.fft.fftshift(np.abs(np.fft.fft2(image - image.mean())) ** 2)
    h, w = image.shape
    yy, xx = np.ogrid[:h, :w]
    outer = (np.abs(yy - h // 2) > h // 4) | (np.abs(xx - w // 2) > w // 4)
    return float(spectrum[outer].sum() / spectrum.sum())


def _off_grid_detail(image) -> float:
    """How far the image is from 2x2 blocks of one value. A nearest-neighbour
    upscale is exactly such blocks (0.0); a native render is not."""
    import numpy as np

    blocks = np.repeat(np.repeat(image[::2, ::2], 2, axis=0), 2, axis=1)
    return float(np.abs(image - blocks).mean())


@pytest.mark.real_render
@needs_render_tools
@pytest.mark.parametrize("hd,uhd", PAIRS)
def test_a_real_4k_render_is_native_at_the_render_stage(tmp_path: Path, hd, uhd) -> None:
    import numpy as np
    from PIL import Image

    from code2shorts.media import probe_video
    from code2shorts.webapp.pipeline import build_renderer

    # Title, an array step with pointers, and a comparison - real trace.
    plan = _plan([0, 9, 11], seconds=1.0)
    rendered = {}
    for profile in (hd, uhd):
        result = build_renderer(profile).render(plan, tmp_path / profile.id.value, _context())
        path = Path(result.output_path)
        video = probe_video(path)
        # The RENDER stage itself is the profile's size: no composition step
        # has run yet, so nothing downstream could have resized it.
        assert (video.width, video.height) == (profile.pixel_width, profile.pixel_height)
        assert result.resolution == profile.resolution
        assert result.metadata["requested_resolution"] == profile.resolution
        rendered[profile] = (path, video)

    assert rendered[hd][1].frame_count == rendered[uhd][1].frame_count, "same timeline"

    hd_frame = _frame(rendered[hd][0], 0.8)
    uhd_frame = _frame(rendered[uhd][0], 0.8)
    assert uhd_frame.shape == (uhd.pixel_height, uhd.pixel_width)

    # Same picture: the 4K frame averaged down to HD is the HD frame.
    same_picture = float(np.abs(_down2(uhd_frame) - hd_frame).mean())
    assert same_picture < 4.0, f"4K is a different picture (mean |diff| {same_picture:.2f}/255)"

    # Strokes and glyphs scale with the frame. Everything drawn is an area,
    # so at twice the linear density it holds four times the ink. A stroke
    # that stayed N px wide at 4K would add only twice its HD ink and pull
    # this ratio well under 4.
    ink = float(uhd_frame.sum()) / float(hd_frame.sum())
    assert 3.8 < ink < 4.2, f"4K/HD ink ratio {ink:.3f}; strokes or text did not scale"

    # Native, not upscaled: compare with what each kind of upscale of the
    # HD frame would have produced. Measured on the real palindrome bench
    # renders: 0.013-0.015 native vs 0.0014-0.0015 bicubic (about 10x).
    def upscale(method):
        return np.asarray(
            Image.fromarray(hd_frame.astype(np.uint8)).resize(
                (uhd.pixel_width, uhd.pixel_height), method),
            dtype=np.float64)

    native = _beyond_hd_nyquist(uhd_frame)
    for method in (Image.BILINEAR, Image.BICUBIC, Image.LANCZOS):
        fake = _beyond_hd_nyquist(upscale(method))
        assert native > 4 * fake, (
            f"4K frame has no more detail than an upscale (native {native:.5f}, "
            f"upscaled {fake:.5f})")
    assert _off_grid_detail(upscale(Image.NEAREST)) == 0.0
    assert _off_grid_detail(uhd_frame) > 0.2, "4K frame is 2x2 blocks: a nearest upscale"

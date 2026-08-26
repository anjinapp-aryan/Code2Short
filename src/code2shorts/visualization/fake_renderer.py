"""FakeVideoRenderer: a deterministic VideoRenderer test double — same
role as MockLLMProvider (ai/providers/mock.py). Writes a small real file
and computes a real checksum over it (so lineage/checksum tests exercise
real code), but never shells out to Manim. Used by the required Phase 4
end-to-end test, which must not depend on Manim being installed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from code2shorts.ai.contracts import VisualizationPlanResponse
from code2shorts.visualization.renderer import RenderContext, RenderResult, VideoRenderer

FAKE_RENDERER_VERSION = "fake-renderer-1.0"


class FakeVideoRenderer(VideoRenderer):
    def __init__(self, fps: int = 30, resolution: str = "1080x1920") -> None:
        self._fps = fps
        self._resolution = resolution

    def render(
        self,
        plan: VisualizationPlanResponse,
        output_dir: Path,
        context: RenderContext | None = None,
    ) -> RenderResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "output.mp4"
        # A deterministic placeholder payload derived from the plan's own
        # content — same plan -> same bytes -> same checksum, matching the
        # renderer's documented determinism goal without needing a real
        # video encoder.
        payload = f"FAKE_VIDEO:{plan.lesson_title}:{len(plan.steps)}".encode()
        output_path.write_bytes(payload)

        checksum = hashlib.sha256(payload).hexdigest()
        duration_seconds = 2.0 + sum(step.duration_seconds for step in plan.steps)

        return RenderResult(
            output_path=str(output_path),
            checksum=checksum,
            duration_seconds=duration_seconds,
            resolution=self._resolution,
            fps=self._fps,
            renderer_version=FAKE_RENDERER_VERSION,
            metadata={"step_count": len(plan.steps), "fake": True},
        )

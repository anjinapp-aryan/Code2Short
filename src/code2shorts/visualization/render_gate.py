"""Bounded, cost-weighted render concurrency.

Phase 8.3 measured why this exists: one native 4K render peaks at about
1.75 GiB, 4K + HD at about 2.4 GiB, and the web `JobManager` starts one
thread per distinct request with no bound. The renders are the memory
hazard, so only the RENDER stage is gated - the LLM, TTS and JVM stages of
other jobs keep running.

    cost(profile) = ceil(pixels / 1920x1080)   HD = 1, 4K = 4
    budget        = Settings.render_budget_units

The cost is pixel count, not the measured memory ratio (4K is ~2.7x HD),
on purpose: it is a property of the profile, deterministic, and errs on
the safe side. With the default budget of 5, 4K + HD render together (the
measured-safe pair) and two 4K renders do not.

This is a decorator around the existing renderer. `ManimVideoRenderer`
and the scene it writes are untouched, so the accepted portrait and
landscape scenes cannot change. The stdlib is enough here; a queue
library would add infrastructure for one in-process counter (see
docs/POST_8_3_REUSE_DECISION.md).
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from code2shorts.ai.contracts import VisualizationPlanResponse
from code2shorts.core.video_profile import VideoProfile
from code2shorts.visualization.renderer import RenderContext, RenderResult, VideoRenderer

HD_PIXELS = 1920 * 1080


def render_cost(profile: VideoProfile) -> int:
    """Budget units one render of `profile` takes: its pixels in HD frames."""
    return max(1, math.ceil(profile.pixel_width * profile.pixel_height / HD_PIXELS))


class WeightedGate:
    """A first-come-first-served weighted semaphore.

    A request waits until it is at the head of the queue AND its weight
    fits, so a waiting 4K render is never overtaken - and starved - by HD
    renders that arrive after it. A weight above the budget is clamped to
    the budget: that render runs alone rather than deadlocking.
    """

    def __init__(self, budget: int) -> None:
        if budget < 1:
            raise ValueError("render budget must be at least 1")
        self.budget = budget
        self._in_use = 0
        self._queue: deque[object] = deque()
        self._condition = threading.Condition()

    @property
    def in_use(self) -> int:
        with self._condition:
            return self._in_use

    @contextmanager
    def hold(self, weight: int) -> Iterator[None]:
        weight = min(max(1, weight), self.budget)
        ticket = object()
        with self._condition:
            self._queue.append(ticket)
            while self._queue[0] is not ticket or self._in_use + weight > self.budget:
                self._condition.wait()
            self._queue.popleft()
            self._in_use += weight
            # The next ticket may fit too (e.g. HD beside a 4K render).
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._in_use -= weight
                self._condition.notify_all()


class GatedRenderer(VideoRenderer):
    """Holds `cost` units of `gate` for the duration of one render."""

    def __init__(self, inner: VideoRenderer, gate: WeightedGate, cost: int) -> None:
        self.inner = inner
        self.gate = gate
        self.cost = cost

    def render(
        self,
        plan: VisualizationPlanResponse,
        output_dir: Path,
        context: RenderContext | None = None,
    ) -> RenderResult:
        with self.gate.hold(self.cost):
            return self.inner.render(plan, output_dir, context)

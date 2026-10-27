"""POST-8.3 — bounded, cost-weighted render concurrency.

Phase 8.3 measured the hazard: one native 4K render peaks at ~1.75 GiB,
4K + HD at ~2.4 GiB, and `JobManager` starts one thread per distinct
request with no bound. The bound is applied to the RENDER stage only (the
LLM and TTS stages keep running concurrently) by a decorator around the
existing renderer; `ManimVideoRenderer` and `build_renderer` are unchanged.

    cost(profile) = ceil(pixels / 1920x1080)    HD = 1, 4K = 4
    budget        = Settings.render_budget_units (default 5)

so 4K + HD may render together (measured safe) but two 4K renders may
not. Grants are first-come-first-served, so a waiting 4K job is not
starved by HD jobs arriving after it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from code2shorts.config import Settings
from code2shorts.core.video_profile import LANDSCAPE_4K, LANDSCAPE_HD, VERTICAL_4K, VERTICAL_HD
from code2shorts.visualization.render_gate import GatedRenderer, WeightedGate, render_cost
from code2shorts.visualization.renderer import RenderingFailure, RenderResult, VideoRenderer


# ---- cost --------------------------------------------------------------------


def test_cost_is_pixels_in_hd_units() -> None:
    assert [render_cost(p) for p in (VERTICAL_HD, LANDSCAPE_HD)] == [1, 1]
    assert [render_cost(p) for p in (VERTICAL_4K, LANDSCAPE_4K)] == [4, 4]


def test_the_default_budget_admits_4k_plus_hd_but_not_two_4k() -> None:
    budget = Settings(_env_file=None, environment="test").render_budget_units
    assert budget == 5
    assert render_cost(LANDSCAPE_4K) + render_cost(LANDSCAPE_HD) <= budget
    assert 2 * render_cost(LANDSCAPE_4K) > budget


# ---- the gate ------------------------------------------------------------------


def test_the_gate_never_exceeds_its_budget_under_contention() -> None:
    gate = WeightedGate(5)
    in_use, peak, lock = [0], [0], threading.Lock()

    def work(weight: int) -> None:
        with gate.hold(weight):
            with lock:
                in_use[0] += weight
                peak[0] = max(peak[0], in_use[0])
            time.sleep(0.02)
            with lock:
                in_use[0] -= weight

    threads = [threading.Thread(target=work, args=(w,)) for w in [4, 1, 4, 1, 1, 4, 1, 1] * 3]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert 0 < peak[0] <= 5
    assert gate.in_use == 0


def test_a_cost_above_the_budget_runs_alone_instead_of_deadlocking() -> None:
    gate = WeightedGate(2)
    done = threading.Event()

    def big() -> None:
        with gate.hold(4):
            assert gate.in_use == 2, "clamped to the budget: it runs, alone"
        done.set()

    thread = threading.Thread(target=big)
    thread.start()
    thread.join(timeout=2)
    assert done.is_set()


def test_grants_are_first_come_first_served() -> None:
    """A 4K render waiting for room is not overtaken by HD renders that
    arrive after it (no starvation)."""
    gate = WeightedGate(5)
    order: list[str] = []
    first = gate.hold(4)
    first.__enter__()  # a 4K render is running; budget left = 1

    def waiter(name: str, weight: int, started: threading.Event) -> None:
        started.set()
        with gate.hold(weight):
            order.append(name)
            time.sleep(0.01)

    started_4k, started_hd = threading.Event(), threading.Event()
    t4k = threading.Thread(target=waiter, args=("4k", 4, started_4k))
    t4k.start()
    started_4k.wait()
    time.sleep(0.05)  # the 4K request is queued first
    thd = threading.Thread(target=waiter, args=("hd", 1, started_hd))
    thd.start()
    started_hd.wait()
    time.sleep(0.05)
    assert order == [], "the HD request must not jump the queued 4K one"
    first.__exit__(None, None, None)
    t4k.join(timeout=2)
    thd.join(timeout=2)
    assert order == ["4k", "hd"]


# ---- the decorator ---------------------------------------------------------------


class _Recording(VideoRenderer):
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def render(self, plan, output_dir, context=None) -> RenderResult:
        self.calls += 1
        if self.fail:
            raise RenderingFailure("boom")
        return RenderResult(output_path=str(output_dir / "x.mp4"), checksum="c",
                            duration_seconds=1.0, resolution="1920x1080", fps=30,
                            renderer_version="t")


def test_the_gated_renderer_delegates_and_returns_the_inner_result(tmp_path: Path) -> None:
    inner = _Recording()
    gate = WeightedGate(5)
    result = GatedRenderer(inner, gate, cost=4).render(None, tmp_path)
    assert inner.calls == 1 and result.checksum == "c"
    assert gate.in_use == 0


def test_a_failed_render_releases_its_share(tmp_path: Path) -> None:
    gate = WeightedGate(5)
    with pytest.raises(RenderingFailure):
        GatedRenderer(_Recording(fail=True), gate, cost=4).render(None, tmp_path)
    assert gate.in_use == 0, "a failure must not leak budget and stall later jobs"


def test_the_web_pipeline_gates_its_real_renderer() -> None:
    """The factory's RenderVideoNode holds a GatedRenderer around the
    unchanged ManimVideoRenderer, at the profile's cost, on ONE gate shared
    by every job the factory builds."""
    from code2shorts.generation import GenerationRequest, TeachingConfig
    from code2shorts.visualization import ManimVideoRenderer
    from code2shorts.webapp.pipeline import DefaultPipelineFactory
    from tests.test_generation_registry import _request

    factory = DefaultPipelineFactory(Settings(_env_file=None, environment="test",
                                              llm_provider="mock"))
    renderers = []
    for video_format in ("vertical_hd", "landscape_4k"):
        request: GenerationRequest = _request(config=TeachingConfig(video_format=video_format))
        node = next(n for n in factory.build(request, Path("unused")) if n.name == "render_video")
        renderers.append(node._renderer)
    hd, uhd = renderers
    assert isinstance(hd, GatedRenderer) and isinstance(uhd, GatedRenderer)
    assert isinstance(hd.inner, ManimVideoRenderer) and isinstance(uhd.inner, ManimVideoRenderer)
    assert (hd.cost, uhd.cost) == (1, 4)
    assert hd.gate is uhd.gate and hd.gate.budget == 5


def test_two_4k_jobs_never_render_at_once_but_4k_and_hd_do(tmp_path: Path) -> None:
    """End to end through the gate: concurrent renders of the real cost."""
    gate = WeightedGate(5)
    active: list[int] = []
    peak_4k, overlap_mixed, lock = [0], [False], threading.Lock()

    class _Slow(VideoRenderer):
        def __init__(self, cost: int) -> None:
            self.cost = cost

        def render(self, plan, output_dir, context=None) -> RenderResult:
            with lock:
                active.append(self.cost)
                peak_4k[0] = max(peak_4k[0], active.count(4))
                if 4 in active and 1 in active:
                    overlap_mixed[0] = True
            time.sleep(0.1)
            with lock:
                active.remove(self.cost)
            return _Recording().render(plan, output_dir)

    jobs = [GatedRenderer(_Slow(c), gate, cost=c) for c in (4, 4, 1)]
    threads = [threading.Thread(target=j.render, args=(None, tmp_path)) for j in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak_4k[0] == 1, "two 4K renders overlapped"
    assert overlap_mixed[0], "4K + HD fits the budget and should run together"

"""Phase 4.5 repeatability.

"Deterministic" is defined at the LOGICAL layer, precisely:

    Identical for repeated runs:
      - ExecutionTrace events (all fields except wall-clock duration)
      - reconstructed FrameState (arrays, scalars, pointers)
      - SourceLocation resolution (file + line per event)
      - CodeState windows
      - VisualizationPlan
      - subtitle structure (cue count, timings, text)
      - video geometry (resolution, fps, frame count)

    NOT required to be identical:
      - MP4 bytes (encoders embed timestamps/metadata)
      - wall-clock durations
      - artifact ids (fresh uuid4 per instance, by design)
"""

from __future__ import annotations

import shutil

import pytest

from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java import JavaAdapter
from code2shorts.narration.subtitles import build_srt
from code2shorts.visualization import reconstruct_frames
from code2shorts.visualization.code_state import (
    build_code_state,
    resolve_source_locations,
)
from tests.java_fixtures import load_algorithm_fixture, load_java_fixture

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("mvn") is None or shutil.which("java") is None,
        reason="needs mvn and java on PATH",
    ),
]

CASES = [
    ("reverse_string", "correct", "HELLO"),
    ("algorithm", "two_sum", "9"),
    ("algorithm", "move_zeroes", ""),
]


def _load(kind: str, variant: str):
    return load_java_fixture(variant) if kind == "reverse_string" else load_algorithm_fixture(variant)


def _trace_twice(kind: str, variant: str, arg: str):
    adapter = JavaAdapter()
    code = _load(kind, variant)
    traces = []
    for _ in range(2):
        with Workspace() as ws:
            traces.append(adapter.trace(code, ws, arg))
    return code, traces[0], traces[1]


@pytest.mark.parametrize(("kind", "variant", "arg"), CASES)
def test_execution_trace_is_logically_identical(kind: str, variant: str, arg: str) -> None:
    _, a, b = _trace_twice(kind, variant, arg)

    # duration_seconds is explicitly excluded - it is wall-clock, not behaviour
    assert a.model_dump(exclude={"duration_seconds"}) == b.model_dump(
        exclude={"duration_seconds"}
    ), f"{variant}: trace differed between runs"


@pytest.mark.parametrize(("kind", "variant", "arg"), CASES)
def test_frame_state_reconstruction_is_identical(kind: str, variant: str, arg: str) -> None:
    _, a, b = _trace_twice(kind, variant, arg)
    frames_a = reconstruct_frames(a)
    frames_b = reconstruct_frames(b)

    assert len(frames_a) == len(frames_b)
    for fa, fb in zip(frames_a, frames_b):
        assert fa.arrays == fb.arrays
        assert fa.scalars == fb.scalars
        assert [(p.name, p.index) for p in fa.pointers] == [
            (p.name, p.index) for p in fb.pointers
        ]


@pytest.mark.parametrize(("kind", "variant", "arg"), CASES)
def test_code_synchronization_is_identical(kind: str, variant: str, arg: str) -> None:
    code, a, b = _trace_twice(kind, variant, arg)
    loc_a = resolve_source_locations(a, code.source_files, code.entry_point)
    loc_b = resolve_source_locations(b, code.source_files, code.entry_point)

    assert loc_a == loc_b, f"{variant}: source locations differed"

    for step_index, location in loc_a.items():
        state_a = build_code_state(location, code.source_files, 6)
        state_b = build_code_state(loc_b[step_index], code.source_files, 6)
        assert state_a == state_b


def test_subtitle_structure_is_identical_across_runs() -> None:
    """Same alignment inputs must yield a byte-identical SRT document."""
    from code2shorts.ai.contracts import (
        NarrationResponse,
        NarrationSegment,
        VisualAction,
        VisualizationPlanResponse,
        VisualizationStepPlan,
    )
    from code2shorts.narration import align_narration

    plan = VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=i,
                visual_action=VisualAction.INTRO,
                trace_event_index=i,
                narration_text=f"line {i}",
                duration_seconds=2.0,
            )
            for i in range(3)
        ],
    )
    narration = NarrationResponse(
        segments=[
            NarrationSegment(order=i, text=f"line {i}", visualization_step_order=i)
            for i in range(3)
        ]
    )
    first = build_srt(align_narration(narration, plan))
    second = build_srt(align_narration(narration, plan))
    assert first == second

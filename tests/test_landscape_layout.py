"""PHASE 8.2C — the 16:9 column composition, measured under real Manim.

Geometry claims are only worth what the renderer confirms (the Phase 6.1
letterboxing defect looked correct in the emitted source), so every
assertion here is made on real mobjects built in-process - the same
technique as `tests/test_code_layout.py`. Nothing is rendered to video.

    title band     full safe width, top of frame
    code column    left, the portrait viewport width (4.14 u = 994 px)
    state column   right, 2.86 u = 686 px
    explanation    full width, bottom band, <= 3 lines

The portrait composition is covered by the existing layout tests and by
`test_layout_seam.test_the_portrait_scene_is_byte_identical_...`.
"""

from __future__ import annotations

import ast
import itertools
import json
from pathlib import Path

import pytest

from code2shorts.ai.contracts import VisualizationPlanResponse
from code2shorts.core.models import ExecutionTrace, SourceLocation, TraceEventType
from code2shorts.visualization import RenderContext, primitives
from code2shorts.visualization.code_state import DEFAULT_WINDOW_RADIUS, build_code_state
from code2shorts.visualization.layout import LANDSCAPE
from code2shorts.visualization.manim_renderer import build_scene_source
from code2shorts.visualization.state import ArraySnapshot, FrameState
from tests.java_fixtures import load_algorithm_fixture
from tests.test_layout_seam import BASELINE

manim = pytest.importorskip("manim", reason="landscape geometry is measured under real Manim")

EPS = 1e-6
CODE = load_algorithm_fixture("palindrome")
SOURCE = dict(CODE.source_files)
ENTRY = CODE.entry_point

LONG_LINE_SOURCE = {
    "F.java": "\n".join([
        "class F {",
        "    static int veryLongMethodNameForTesting(int firstArgument, int secondArgument, int third) {",
        "        return firstArgument + secondArgument + third + firstArgument * secondArgument;",
        "    }",
        "}",
    ])
}
LONG_FILE_SOURCE = {
    "G.java": "\n".join(
        ["class G {"] + [f"    int value{i} = {i} * {i} + {i};" for i in range(60)] + ["}"]
    )
}
LONG_CAPTION = (
    "We compare the character at the left pointer with the character at the right pointer, "
    "and because they are equal the loop moves both pointers one step toward the middle of "
    "the string, which is exactly what the next line of code does."
)

SAFE_LEFT, SAFE_RIGHT = -LANDSCAPE.safe_width / 2, LANDSCAPE.safe_width / 2
CODE_LEFT = LANDSCAPE.code_viewport_center_x - LANDSCAPE.code_viewport_width / 2
CODE_RIGHT = LANDSCAPE.code_viewport_center_x + LANDSCAPE.code_viewport_width / 2
STATE_LEFT = LANDSCAPE.structure_x - LANDSCAPE.structure_width / 2
STATE_RIGHT = LANDSCAPE.structure_x + LANDSCAPE.structure_width / 2
CAPTION_TOP = LANDSCAPE.caption_y + LANDSCAPE.caption_max_height / 2


def _compose(cells: str, scalars: dict[str, str], source=SOURCE, file=ENTRY, line=8,
             caption=LONG_CAPTION):
    from manim import tempconfig

    frame = FrameState(
        step_index=0, event_type=TraceEventType.VARIABLE_ASSIGN.value, line_number=line,
        description="landscape",
        arrays={"chars": ArraySnapshot(name="chars", cells=list(cells))} if cells else {},
        scalars=scalars,
    )
    location = SourceLocation(file=file, line=line)
    radius = primitives.fit_window_radius(
        location, source, minimum=DEFAULT_WINDOW_RADIUS, layout=LANDSCAPE
    )
    state = build_code_state(location, source, radius)
    anchors = [name for name, present in (
        ("arr_group", bool(cells)), ("ptr_group", bool(cells)), ("vars_group", bool(scalars))
    ) if present]
    body = (
        primitives.title_text("Checking A Sequence With Two Indices Walking Inward", layout=LANDSCAPE)
        + primitives.array_row(frame, layout=LANDSCAPE)
        + primitives.pointer_arrows(frame, layout=LANDSCAPE)
        + primitives.scalar_panel(frame, layout=LANDSCAPE)
        + primitives.code_panel(state, layout=LANDSCAPE)
        + primitives.caption_text(caption, layout=LANDSCAPE)
        + primitives.compose_columns(anchors, "caption", "code_group", layout=LANDSCAPE)
    )
    with tempconfig({"pixel_width": 1920, "pixel_height": 1080,
                     "frame_height": LANDSCAPE.frame_height, "frame_width": LANDSCAPE.frame_width}):
        namespace: dict = {}
        exec("from manim import *", namespace)  # noqa: S102 - repository-owned
        exec("\n".join(body), namespace)  # noqa: S102 - repository-owned
    return namespace, state


def _inside(mob, left, right, bottom, top) -> bool:
    return (mob.get_left()[0] >= left - EPS and mob.get_right()[0] <= right + EPS
            and mob.get_bottom()[1] >= bottom - EPS and mob.get_top()[1] <= top + EPS)


def _overlap(a, b) -> bool:
    return (a.get_left()[0] < b.get_right()[0] - EPS and b.get_left()[0] < a.get_right()[0] - EPS
            and a.get_bottom()[1] < b.get_top()[1] - EPS and b.get_bottom()[1] < a.get_top()[1] - EPS)


def _labels(namespace):
    return [m for m in namespace["ptr_group"] if type(m).__name__ == "Text"]


ARRAY_CASES = [
    pytest.param("RACECAR", {"input": "RACECAR", "left": "0", "right": "6"}, id="racecar-edges"),
    pytest.param("RACECAR", {"input": "RACECAR", "left": "2", "right": "4"}, id="racecar-inner"),
    pytest.param("RACECAR", {"input": "RACECAR", "left": "3", "right": "3"}, id="racecar-converged"),
    pytest.param("ABBA", {"input": "ABBA", "left": "1", "right": "2"}, id="abba-adjacent"),
    pytest.param("A", {"input": "A", "left": "0", "right": "0"}, id="single"),
    pytest.param("AA", {"input": "AA", "left": "0", "right": "1"}, id="aa-adjacent"),
    pytest.param("ABC", {"input": "ABC", "left": "0", "right": "2"}, id="abc"),
    pytest.param("ABCCBA", {"input": "ABCCBA", "left": "2", "right": "3"}, id="abccba-adjacent"),
]


# ---- containment -------------------------------------------------------------


@pytest.mark.real_render
@pytest.mark.parametrize("cells,scalars", ARRAY_CASES)
def test_every_region_stays_inside_its_box(cells, scalars) -> None:
    ns, _state = _compose(cells, scalars)
    top, bottom = LANDSCAPE.safe_top, LANDSCAPE.safe_bottom

    assert _inside(ns["title"], SAFE_LEFT, SAFE_RIGHT, LANDSCAPE.content_top, top), "title"
    assert ns["title"].height <= LANDSCAPE.title_max_height + EPS
    assert _inside(ns["code_group"], CODE_LEFT, CODE_RIGHT,
                   LANDSCAPE.content_bottom, LANDSCAPE.content_top), "code column"
    for name in ("arr_group", "ptr_group", "vars_group"):
        assert _inside(ns[name], STATE_LEFT, STATE_RIGHT,
                       LANDSCAPE.content_bottom, LANDSCAPE.content_top), name
    assert _inside(ns["caption"], SAFE_LEFT, SAFE_RIGHT, bottom, CAPTION_TOP), "explanation"


@pytest.mark.real_render
@pytest.mark.parametrize("cells,scalars", ARRAY_CASES)
def test_no_two_regions_overlap(cells, scalars) -> None:
    ns, _state = _compose(cells, scalars)
    regions = [ns[name] for name in ("title", "code_group", "arr_group", "vars_group", "caption")]
    regions += _labels(ns)
    for a, b in itertools.combinations(regions, 2):
        assert not _overlap(a, b), f"{type(a).__name__} overlaps {type(b).__name__}"


# ---- pointers ----------------------------------------------------------------------


@pytest.mark.real_render
@pytest.mark.parametrize("cells,scalars", ARRAY_CASES)
def test_pointer_labels_never_overlap_and_arrows_reach_their_cells(cells, scalars) -> None:
    ns, _state = _compose(cells, scalars)
    labels = _labels(ns)
    for a, b in itertools.combinations(labels, 2):
        assert not _overlap(a, b), "two pointer labels overlap"

    arrows = [m for m in ns["ptr_group"] if type(m).__name__ == "Arrow"]
    pointed = {int(v) for k, v in scalars.items() if k != "input"}
    assert len(arrows) == len(pointed), "exactly one arrow per addressed cell"
    for index in pointed:
        cell = ns["arr_group"][index][0]
        ends = [a.get_end() for a in arrows]
        assert any(abs(end[0] - cell.get_center()[0]) < 0.05
                   and abs(end[1] - cell.get_top()[1]) < 0.12 for end in ends), (
            f"no arrow ends on cell {index}"
        )


@pytest.mark.real_render
def test_adjacent_pointers_are_lifted_to_separate_levels() -> None:
    ns, _state = _compose("ABBA", {"input": "ABBA", "left": "1", "right": "2"})
    left, right = sorted(_labels(ns), key=lambda m: m.get_center()[0])
    assert right.get_bottom()[1] >= left.get_top()[1] - EPS, "the second label was not lifted"


@pytest.mark.real_render
def test_the_array_does_not_move_when_labels_stack() -> None:
    """Anchored by the cells' top edge: converging pointers stack their
    labels higher, and the array must not jump between those steps."""
    apart, _ = _compose("RACECAR", {"left": "0", "right": "6"})
    stacked, _ = _compose("RACECAR", {"left": "3", "right": "3"})
    assert apart["arr_group"].get_top()[1] == pytest.approx(stacked["arr_group"].get_top()[1])
    assert apart["arr_group"].get_top()[1] == pytest.approx(LANDSCAPE.array_top)


# ---- code ------------------------------------------------------------------------


@pytest.mark.real_render
@pytest.mark.parametrize(
    "source,file,line,floor",
    [
        pytest.param(SOURCE, ENTRY, 8, primitives.MIN_CODE_LINE_HEIGHT_UNITS, id="loop-body"),
        pytest.param(SOURCE, ENTRY, 4, primitives.ABS_MIN_CODE_LINE_HEIGHT_UNITS, id="signature-line"),
        pytest.param(LONG_FILE_SOURCE, "G.java", 30, primitives.ABS_MIN_CODE_LINE_HEIGHT_UNITS,
                     id="long-file"),
    ],
)
def test_code_stays_in_its_panel_at_a_readable_size(source, file, line, floor) -> None:
    ns, state = _compose("RACECAR", {"left": "0", "right": "6"}, source=source, file=file, line=line)
    group = ns["code_group"]
    panel, body = group.submobjects[0], group.submobjects[2]

    assert panel.width == pytest.approx(LANDSCAPE.code_viewport_width), "viewport width is fixed"
    assert _inside(body, panel.get_left()[0], panel.get_right()[0],
                   panel.get_bottom()[1], panel.get_top()[1]), "code escaped its panel"
    assert body.height / len(state.lines) >= floor - EPS, "code shrank below the readable floor"
    numbers = body.submobjects[0]
    assert numbers.get_left()[0] >= panel.get_left()[0] - EPS, "line numbers left the panel"


@pytest.mark.real_render
def test_a_pathologically_long_line_is_contained_not_overflowed() -> None:
    """90+ character lines cannot be large at 994 px - the portrait panel
    renders them at the same size - but they must never leave the panel."""
    ns, _state = _compose("RACECAR", {"left": "0", "right": "6"},
                          source=LONG_LINE_SOURCE, file="F.java", line=3)
    group = ns["code_group"]
    panel, body = group.submobjects[0], group.submobjects[2]
    for line in body.submobjects[1]:
        assert line.get_right()[0] <= panel.get_right()[0] + EPS


@pytest.mark.real_render
def test_code_readability_matches_the_portrait_panel() -> None:
    """Same viewport width, same fitting rules: a window cannot render at a
    SMALLER line height in landscape than the portrait floor."""
    ns, state = _compose("RACECAR", {"left": "0", "right": "6"}, line=8)
    per_line = ns["code_group"].submobjects[2].height / len(state.lines)
    assert per_line * 240 >= primitives.MIN_CODE_LINE_HEIGHT_UNITS * 240 - EPS


# ---- the whole scene ------------------------------------------------------------------


def test_a_landscape_scene_is_native_16_9_and_safe_python() -> None:
    plan = VisualizationPlanResponse.model_validate(
        json.loads((BASELINE / "plan.json").read_text(encoding="utf-8"))
    )
    trace = ExecutionTrace.model_validate(
        json.loads((BASELINE / "trace.json").read_text(encoding="utf-8"))
    )
    source = build_scene_source(
        plan, "GeneratedScene",
        RenderContext(trace=trace, source_files=SOURCE, entry_point=ENTRY),
        layout=LANDSCAPE,
    )
    assert "config.frame_height = 4.5" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Import)
        if isinstance(node, ast.ImportFrom):
            assert node.module == "manim"

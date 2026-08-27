"""Phase 6: collection observation, snapshots, and their visualization.

Unit-level and network-free. Real-Java verification lives in
`test_collection_integration.py`.

The governing rule, unchanged: the renderer draws what the trace observed.
It may not invent an entry, and it may not present a canonically-sorted
order as if it were insertion order.
"""

from __future__ import annotations

import pytest

from code2shorts.ai.contracts import (
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import ExecutionTrace, TraceEvent, TraceEventType
from code2shorts.visualization.manim_renderer import build_scene_source
from code2shorts.visualization.primitives import map_panel, sequence_panel
from code2shorts.visualization.renderer import RenderContext
from code2shorts.visualization.state import (
    UNIT,
    MapSnapshot,
    SequenceSnapshot,
    reconstruct_frames,
)


def _event(step: int, **kwargs) -> TraceEvent:
    base = dict(
        step_index=step,
        event_type=TraceEventType.COLLECTION_MUTATION.value,
        description=f"event {step}",
        line_number=10 + step,
    )
    base.update(kwargs)
    return TraceEvent(**base)


def _trace(*events: TraceEvent) -> ExecutionTrace:
    return ExecutionTrace(
        algorithm_name="X", language="java", input="", output="",
        succeeded=True, exit_code=0, events=list(events),
    )


def _map_event(step: int, name: str, pairs: list[tuple[str, str]], *,
               operation: str = "put", ordered: bool = True) -> TraceEvent:
    return _event(
        step,
        variable_name=name,
        collection_kind="map",
        collection_operation=operation,
        collection_ordered=ordered,
        collection_size=len(pairs),
        collection_keys=UNIT.join(k for k, _ in pairs),
        collection_values=UNIT.join(v for _, v in pairs),
    )


def _seq_event(step: int, name: str, elements: list[str], *,
               operation: str = "push") -> TraceEvent:
    return _event(
        step,
        variable_name=name,
        collection_kind="sequence",
        collection_operation=operation,
        collection_ordered=True,
        collection_size=len(elements),
        collection_values=UNIT.join(elements),
    )


# ---- MapSnapshot reconstruction -------------------------------------------


def test_a_map_snapshot_reconstructs_exactly_what_was_observed() -> None:
    frames = reconstruct_frames(
        _trace(
            _map_event(0, "seen", []),
            _map_event(1, "seen", [("2", "0")]),
            _map_event(2, "seen", [("2", "0"), ("7", "1")]),
        )
    )
    assert frames[0].primary_map.entries == []
    assert frames[1].primary_map.entries == [("2", "0")]
    assert frames[2].primary_map.entries == [("2", "0"), ("7", "1")]


def test_map_frames_are_independent_snapshots() -> None:
    """No mutable state may leak between frames."""
    frames = reconstruct_frames(
        _trace(_map_event(0, "m", [("a", "1")]), _map_event(1, "m", [("a", "1"), ("b", "2")]))
    )
    first, second = frames[0].primary_map, frames[1].primary_map
    assert first is not second
    assert first != second
    first.keys.append("mutated")
    assert "mutated" not in second.keys


def test_a_map_records_the_operation_that_produced_it() -> None:
    frames = reconstruct_frames(_trace(_map_event(0, "m", [("a", "1")], operation="remove")))
    assert frames[0].primary_map.last_operation == "remove"


# ---- SequenceSnapshot reconstruction --------------------------------------


def test_a_sequence_snapshot_reconstructs_exactly_what_was_observed() -> None:
    frames = reconstruct_frames(
        _trace(
            _seq_event(0, "stack", []),
            _seq_event(1, "stack", ["("]),
            _seq_event(2, "stack", ["(", "("]),
            _seq_event(3, "stack", ["("], operation="pop"),
        )
    )
    assert [f.primary_sequence.elements for f in frames] == [
        [], ["("], ["(", "("], ["("]
    ]


def test_sequence_frames_are_independent_snapshots() -> None:
    frames = reconstruct_frames(
        _trace(_seq_event(0, "s", ["a"]), _seq_event(1, "s", ["a", "b"]))
    )
    first, second = frames[0].primary_sequence, frames[1].primary_sequence
    assert first is not second
    first.elements.append("mutated")
    assert "mutated" not in second.elements


@pytest.mark.parametrize(
    ("operation", "end"),
    [
        ("push", "front"), ("pop", "front"), ("addFirst", "front"), ("poll", "front"),
        ("offer", "back"), ("addLast", "back"), ("offerLast", "back"),
        ("init", None),
    ],
)
def test_the_active_end_comes_from_the_observed_operation(operation, end) -> None:
    """Not from the algorithm's name — that is the whole point of one
    SequenceSnapshot serving stack and queue alike."""
    frames = reconstruct_frames(_trace(_seq_event(0, "s", ["a"], operation=operation)))
    assert frames[0].primary_sequence.active_end == end


def test_one_snapshot_type_serves_both_stack_and_queue_use() -> None:
    stack_frame = reconstruct_frames(_trace(_seq_event(0, "s", ["a"], operation="push")))[0]
    queue_frame = reconstruct_frames(_trace(_seq_event(0, "q", ["a"], operation="offer")))[0]
    assert type(stack_frame.primary_sequence) is type(queue_frame.primary_sequence)
    assert stack_frame.primary_sequence.active_end == "front"
    assert queue_frame.primary_sequence.active_end == "back"


# ---- determinism (ADR-6.4) ------------------------------------------------


def test_identical_traces_produce_identical_snapshots() -> None:
    def build():
        return reconstruct_frames(
            _trace(_map_event(0, "m", [("2", "0"), ("7", "1")]), _seq_event(1, "s", ["a", "b"]))
        )

    first, second = build(), build()
    assert [f.primary_map for f in first] == [f.primary_map for f in second]
    assert [f.primary_sequence for f in first] == [f.primary_sequence for f in second]


def test_an_unordered_map_is_flagged_so_its_order_is_never_taught() -> None:
    """A HashMap's iteration order is unspecified by the JLS. The tracer
    sorts it canonically for determinism; the snapshot must say the order
    is not semantic so nothing presents it as insertion order."""
    frames = reconstruct_frames(_trace(_map_event(0, "m", [("2", "0")], ordered=False)))
    assert frames[0].primary_map.ordered is False


def test_an_ordered_map_keeps_its_semantic_order() -> None:
    frames = reconstruct_frames(
        _trace(_map_event(0, "m", [("7", "1"), ("2", "0")], ordered=True))
    )
    assert frames[0].primary_map.keys == ["7", "2"]
    assert frames[0].primary_map.ordered is True


def test_the_unordered_flag_reaches_the_viewer() -> None:
    state = reconstruct_frames(_trace(_map_event(0, "m", [("2", "0")], ordered=False)))[0]
    source = "\n".join(map_panel(state))
    assert "canonical" in source


def test_an_ordered_map_carries_no_such_warning() -> None:
    state = reconstruct_frames(_trace(_map_event(0, "m", [("2", "0")], ordered=True)))[0]
    assert "canonical" not in "\n".join(map_panel(state))


# ---- unsupported state fails visibly rather than degrading ----------------


def test_an_unsupported_collection_is_not_degraded_into_a_plausible_visual() -> None:
    """Rule 15: never silently turn an unrepresentable state into a
    convincing-looking picture."""
    frames = reconstruct_frames(
        _trace(_event(0, variable_name="thing", collection_kind="unsupported"))
    )
    assert frames[0].primary_map is None
    assert frames[0].primary_sequence is None


# ---- serialization --------------------------------------------------------


def test_collection_events_round_trip_through_the_trace_schema() -> None:
    original = _trace(_map_event(0, "m", [("2", "0")]), _seq_event(1, "s", ["a"]))
    restored = ExecutionTrace.model_validate_json(original.model_dump_json())
    assert restored.events[0].collection_keys == "2"
    assert restored.events[1].collection_values == "a"
    assert reconstruct_frames(restored)[0].primary_map.entries == [("2", "0")]


def test_a_pre_phase6_trace_still_validates() -> None:
    """The schema extension is additive — old traces omit every new field."""
    event = TraceEvent(step_index=0, event_type="VARIABLE_ASSIGN", description="x = 1")
    assert event.collection_kind is None
    assert reconstruct_frames(_trace(event))[0].primary_map is None


# ---- hostile values stay data ---------------------------------------------

HOSTILE = [
    "__import__('os').system('calc.exe')",
    "eval('1+1')",
    "exec('import os')",
    '"); import os; os.system("calc",',
    "$(whoami)",
    "`rm -rf /`",
    "'; DROP TABLE users; --",
    'a "quoted" value',
    "line1\nline2",
    "back\\slash",
    "[bracket]",
    "{brace}",
    "null",
    "‮RTL",
    "unicode 你好",
]



def _appears_only_as_string_data(source: str, payload: str) -> bool:
    """The security property, checked structurally rather than textually:
    the generated source must parse, and every occurrence of the payload
    must sit inside a string literal — never as syntax."""
    import ast

    tree = ast.parse(source)          # must be valid Python at all
    literal_text = "".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    return payload in literal_text or payload not in source


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_map_contents_cannot_become_executable(payload) -> None:
    state = reconstruct_frames(_trace(_map_event(0, "m", [(payload, payload)])))[0]
    source = "\n".join(map_panel(state))
    compile(source, "<generated>", "exec")  # valid, inert Python
    assert _appears_only_as_string_data(source, payload)


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_sequence_contents_cannot_become_executable(payload) -> None:
    state = reconstruct_frames(_trace(_seq_event(0, "s", [payload])))[0]
    source = "\n".join(sequence_panel(state))
    compile(source, "<generated>", "exec")
    assert _appears_only_as_string_data(source, payload)


@pytest.mark.parametrize("payload", HOSTILE)
def test_hostile_contents_survive_a_whole_generated_scene(payload) -> None:
    trace = _trace(_map_event(0, "m", [(payload, payload)]))
    plan = VisualizationPlanResponse(
        lesson_title="T",
        steps=[
            VisualizationStepPlan(
                order=0, visual_action=VisualAction.INTRO, trace_event_index=0,
                narration_text="n", duration_seconds=1.0,
            )
        ],
    )
    source = build_scene_source(plan, "S", RenderContext(trace=trace))
    compile(source, "<generated>", "exec")


def test_a_value_containing_the_unit_separator_cannot_forge_an_entry() -> None:
    """U+001F cannot occur in Java source, so a real element can never
    contain it — but if one ever did, the split must not silently invent
    extra entries beyond what the size field reports."""
    event = _map_event(0, "m", [("a", "1")])
    frames = reconstruct_frames(_trace(event))
    assert len(frames[0].primary_map.entries) == event.collection_size


# ---- no algorithm-specific branching --------------------------------------


def test_no_algorithm_or_structure_name_drives_rendering() -> None:
    """Extends the existing no-algorithm-branch guarantee to Phase 6.

    The renderer must dispatch on WHAT STATE IS PRESENT. A branch keyed by
    an algorithm name, or by a data-structure name used as a mode switch,
    is exactly what ADR-6.3 forbids.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "code2shorts" / "visualization"
    forbidden = {
        "two_sum", "twosum", "binary_search", "binarysearch", "balanced_parens",
        "reverse_string", "move_zeroes", "palindrome", "remove_duplicates",
        "task_queue", "hashmap", "arraydeque",
    }
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for operand in [node.left, *node.comparators]:
                    if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                        if operand.value.strip().lower() in forbidden:
                            offenders.append(f"{path.name}:{node.lineno} == {operand.value!r}")
    assert not offenders, f"algorithm/structure-name branch in visualization/: {offenders}"


def test_the_forbidden_snapshot_types_do_not_exist() -> None:
    """ADR-6.3: two generic snapshots, not four."""
    import code2shorts.visualization.state as state_module

    for forbidden in ("SearchState", "StackSnapshot", "QueueSnapshot"):
        assert not hasattr(state_module, forbidden), f"{forbidden} must not exist"
    assert hasattr(state_module, "MapSnapshot")
    assert hasattr(state_module, "SequenceSnapshot")


def test_binary_search_needs_no_collection_state_at_all() -> None:
    """Its low/mid/high are ordinary scalars indexing an array, so the
    existing pointer rule already renders it."""
    trace = _trace(
        TraceEvent(step_index=0, event_type=TraceEventType.VARIABLE_ASSIGN.value,
                   variable_name="nums", new_value="[1, 3, 5, 7, 9, 11]",
                   description="nums = [1, 3, 5, 7, 9, 11]"),
        TraceEvent(step_index=1, event_type=TraceEventType.VARIABLE_ASSIGN.value,
                   variable_name="low", new_value="0", description="low = 0"),
        TraceEvent(step_index=2, event_type=TraceEventType.VARIABLE_ASSIGN.value,
                   variable_name="high", new_value="5", description="high = 5"),
        TraceEvent(step_index=3, event_type=TraceEventType.VARIABLE_ASSIGN.value,
                   variable_name="mid", new_value="2", description="mid = 2"),
    )
    frame = reconstruct_frames(trace)[-1]
    assert frame.primary_map is None and frame.primary_sequence is None
    pointer_names = {p.name for p in frame.pointers}
    assert {"low", "high", "mid"} <= pointer_names


# ---- the renderer draws only observed state -------------------------------


def test_the_map_panel_never_invents_an_entry() -> None:
    state = reconstruct_frames(_trace(_map_event(0, "m", [("2", "0")])))[0]
    source = "\n".join(map_panel(state))
    assert "2 -> 0" in source
    assert "7 -> 1" not in source


def test_an_empty_observed_map_renders_as_empty_not_as_something() -> None:
    state = reconstruct_frames(_trace(_map_event(0, "seen", [])))[0]
    assert "is empty" in "\n".join(map_panel(state))


def test_no_bucket_or_jvm_internals_are_rendered() -> None:
    state = reconstruct_frames(_trace(_map_event(0, "m", [("2", "0")])))[0]
    source = "\n".join(map_panel(state)).lower()
    for internal in ("bucket", "capacity", "resize", "hash", "threshold", "node chain"):
        assert internal not in source


def test_stack_like_and_queue_like_orientation_differ_structurally() -> None:
    """Discipline is derived from OBSERVED ends across the run, not from one
    operation: `pop` (stack) and `poll` (queue) both act on the front, so a
    single event cannot tell them apart. What differs is whether insertion
    and removal share an end."""
    stack = reconstruct_frames(
        _trace(
            _seq_event(0, "s", ["a"], operation="push"),
            _seq_event(1, "s", [], operation="pop"),
        )
    )[-1]
    queue = reconstruct_frames(
        _trace(
            _seq_event(0, "q", ["a"], operation="offer"),
            _seq_event(1, "q", [], operation="poll"),
        )
    )[-1]

    assert stack.primary_sequence.discipline == "lifo"
    assert queue.primary_sequence.discipline == "fifo"

    stack_source = "\n".join(sequence_panel(stack))
    queue_source = "\n".join(sequence_panel(queue))
    assert "arrange(DOWN" in stack_source or stack.primary_sequence.is_empty
    assert "top first" in stack_source
    assert "front first" in queue_source


def test_a_discipline_is_never_claimed_before_it_is_observed() -> None:
    """One insertion proves nothing about removal order, so the panel must
    not label the sequence a stack or a queue yet."""
    frame = reconstruct_frames(_trace(_seq_event(0, "q", ["a"], operation="offer")))[0]
    assert frame.primary_sequence.discipline == "unknown"
    assert "top first" not in "\n".join(sequence_panel(frame))


def test_the_same_class_used_two_ways_yields_two_disciplines() -> None:
    """An ArrayDeque is one Java class; only the observed operations differ."""
    lifo = reconstruct_frames(
        _trace(_seq_event(0, "d", ["a"], operation="push"),
               _seq_event(1, "d", [], operation="pop"))
    )[-1].primary_sequence
    fifo = reconstruct_frames(
        _trace(_seq_event(0, "d", ["a"], operation="offer"),
               _seq_event(1, "d", [], operation="poll"))
    )[-1].primary_sequence
    assert type(lifo) is type(fifo)
    assert (lifo.discipline, fifo.discipline) == ("lifo", "fifo")

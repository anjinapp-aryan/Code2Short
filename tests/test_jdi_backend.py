"""PHASE 8.4 — the EXPERIMENTAL JDI trace backend (cs1302-tracer).

Offline tests replay REAL recorded tracer snapshots (tests/fixtures/jdi/,
captured from cs1302-tracer 3.2.0 on JDK 21) through the SnapshotDiffer and
require the instrumenter's own events for the same run, field by field -
descriptions included. No JDK is needed for them.

Live tests (marked `integration`) run the whole backend through
`JavaAdapter(trace_backend="jdi")`; they skip unless
CODE2SHORTS_JDI_JAVA_HOME (JDK 21+) and CODE2SHORTS_JDI_TRACER_JAR are set.

The instrumenter stays the default; nothing here makes JDI one.
"""

from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path

import pytest

from code2shorts.langadapter.java.jdi.backend import (
    HARNESS_FILE,
    JdiBackendUnavailable,
    JdiToolchain,
    _uncaught_exception,
    harness_source,
    jdk_major_version,
)
from code2shorts.langadapter.java.jdi.derive import DerivationError, SnapshotDiffer
from code2shorts.langadapter.java.jdi.values import (
    Frame,
    ValueUnavailable,
    evaluate,
    render,
)
from code2shorts.langadapter.java.trace.normalizer import build_events

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "jdi"
JDI_PACKAGE = REPO / "src" / "code2shorts" / "langadapter" / "java" / "jdi"
FIELDS = ("event_type", "line_number", "call_depth", "method", "variable_name", "old_value",
          "new_value", "return_value", "iteration", "condition_result", "description")


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _derive(fixture: dict, strict: bool = True) -> tuple[list[dict], SnapshotDiffer]:
    differ = SnapshotDiffer(fixture["structures"], HARNESS_FILE, strict=strict)
    events = build_events(differ.derive(fixture["snapshots"]))
    return [{f: getattr(e, f) for f in FIELDS} for e in events], differ


def _expected(fixture: dict) -> list[dict]:
    return [{f: e.get(f) for f in FIELDS} for e in fixture["instrumenter_events"]]


# ---- exact parity on recorded runs ---------------------------------------


@pytest.mark.parametrize("name", [
    "palindrome", "two_sum", "remove_duplicates",          # goldens
    "nested_loops", "recursion", "control_flow", "for_loop",  # trace samples
])
def test_recorded_snapshots_derive_exactly_the_instrumenter_events(name) -> None:
    fixture = _fixture(name)
    derived, _ = _derive(fixture)
    assert derived == _expected(fixture)


def test_a_non_pure_return_never_stored_by_the_caller_is_the_only_reverse_string_gap() -> None:
    """`System.out.println(ReverseString.reverse(input))`: the value of
    `return new String(chars)` is neither a pure expression nor received by
    a variable, so it is left absent - never inferred from stdout."""
    fixture = _fixture("reverse_string")
    derived, _ = _derive(fixture)
    expected = _expected(fixture)
    differences = [(i, a, b) for i, (a, b) in enumerate(zip(expected, derived)) if a != b]
    assert len(derived) == len(expected) and len(differences) == 1
    _, ours, theirs = differences[0]
    assert ours["event_type"] == "METHOD_EXIT" and ours["return_value"] == "OLLEH"
    assert theirs["return_value"] is None
    assert {k: v for k, v in theirs.items() if k != "return_value"} == \
        {k: v for k, v in ours.items() if k != "return_value"}


def test_an_unrenderable_object_value_fails_the_trace_in_strict_mode() -> None:
    """The tracer exposes no toString() for JDK objects: `StringBuilder out`
    cannot be rendered, so the backend refuses rather than emit a guess."""
    with pytest.raises(DerivationError, match="StringBuilder"):
        _derive(_fixture("move_zeroes"))


def test_measurement_mode_isolates_the_one_missing_value() -> None:
    fixture = _fixture("move_zeroes")
    derived, differ = _derive(fixture, strict=False)
    expected = _expected(fixture)
    assert len(derived) == len(expected)
    differences = [(a, b) for a, b in zip(expected, derived) if a != b]
    assert len(differences) == 1
    ours, theirs = differences[0]
    assert ours["variable_name"] == theirs["variable_name"] == "out"
    assert theirs["new_value"] is None
    assert len(differ.unavailable) == 1 and "line 21" in differ.unavailable[0]


def test_derivation_is_checked_against_the_observed_line_not_assumed() -> None:
    """Change one observed line and the predicted control flow no longer
    matches: the differ refuses instead of emitting plausible events."""
    fixture = copy.deepcopy(_fixture("palindrome"))
    for step in fixture["snapshots"]:
        top = step["callStack"][-1]
        if top["methodName"] == "isPalindrome" and top["line"] == 13:
            top["line"] = 12
            break
    with pytest.raises(DerivationError):
        _derive(fixture)


def test_derivation_is_deterministic() -> None:
    fixture = _fixture("nested_loops")
    assert _derive(fixture)[0] == _derive(fixture)[0]


# ---- values -------------------------------------------------------------------


def test_values_render_exactly_as_the_java_runtime_repr() -> None:
    heap = {
        "1": {"kind": "array", "type": "char[]", "elements": ["H", "I"]},
        "2": {"kind": "array", "type": "int[]", "elements": [1, 3, 12]},
        "3": {"kind": "string", "type": "java.lang.String", "value": "OLLEH"},
        "4": {"kind": "array", "type": "java.lang.String[]", "elements": [{"ref": 3}, None]},
        "5": {"kind": "box", "type": "java.lang.Integer", "value": 7},
        "6": {"kind": "object", "type": "java.lang.StringBuilder", "fields": []},
    }
    assert render({"ref": 1}, "char[]", heap) == "HI"                 # new String(char[])
    assert render({"ref": 2}, "int[]", heap) == "[1, 3, 12]"          # Arrays.toString
    assert render({"ref": 4}, "java.lang.String[]", heap) == "[OLLEH, null]"
    assert render({"ref": 5}, "java.lang.Integer", heap) == "7"
    assert render(True, "boolean", heap) == "true"
    assert render(2.0, "double", heap) == "2.0"
    with pytest.raises(ValueUnavailable):
        render({"ref": 6}, "java.lang.StringBuilder", heap)


def test_evaluation_follows_java_integer_semantics() -> None:
    frame = Frame({"locals": [{"name": "a", "type": "int", "value": -7},
                              {"name": "b", "type": "int", "value": 2},
                              {"name": "big", "type": "int", "value": 2**31 - 1}]}, {})

    def bin_(op, left, right):
        return evaluate({"t": "bin", "op": op, "l": left, "r": right}, frame)

    a, b = {"t": "name", "n": "a"}, {"t": "name", "n": "b"}
    assert bin_("/", a, b) == ("int", -3)        # truncation toward zero
    assert bin_("%", a, b) == ("int", -1)        # sign of the dividend
    assert bin_("+", {"t": "name", "n": "big"}, {"t": "int", "v": 1}) == ("int", -(2**31))
    assert bin_("*", {"t": "name", "n": "b"}, {"t": "long", "v": 3})[0] == "long"
    call = {"t": "bin", "op": "*", "l": a, "r": {"t": "call", "name": "f"}}
    assert evaluate(call, frame, call_result=("long", 6)) == ("long", -42)
    with pytest.raises(ValueUnavailable):
        evaluate(call, frame)                     # the call's value must be supplied
    with pytest.raises(ValueUnavailable):
        evaluate(None, frame)                     # impure expressions are refused


# ---- harness, exceptions, toolchain --------------------------------------------


def test_the_harness_is_generic_and_carries_no_input() -> None:
    """Input reaches the program as stdin, never as generated source, so no
    input value can change the harness's code."""
    source = harness_source("com.example.pkg.Main")
    assert source.startswith("package com.example.pkg;")
    assert "Main.main(new String[] {input});" in source
    assert "System.in.readAllBytes()" in source
    assert harness_source("Main").startswith("public final class")   # default package
    for word in ("palindrome", "two_sum", "move_zeroes", "reverse", "RACECAR", "HELLO"):
        assert word not in source


def test_the_jvm_uncaught_exception_report_is_read_exactly() -> None:
    info = _uncaught_exception(
        'Exception in thread "main" java.lang.NumberFormatException: For input string: ""\n'
        "\tat java.base/java.lang.NumberFormatException.forInputString("
        "NumberFormatException.java:67)\n\tat x.Main.main(Main.java:5)\n")
    assert (info.exception_class, info.message, info.thrown_at_line, info.thrown_in_method) == (
        "java.lang.NumberFormatException", 'For input string: ""', 67, "forInputString")
    no_message = _uncaught_exception('Exception in thread "main" java.lang.IllegalStateException\n'
                                     "\tat a.B.c(B.java:3)\n")
    assert no_message.message == "null"            # String.valueOf(null), as the runtime prints
    assert _uncaught_exception("something else entirely") is None


def test_the_jdk_version_comes_from_the_release_file(tmp_path: Path) -> None:
    (tmp_path / "release").write_text('IMPLEMENTOR="x"\nJAVA_VERSION="21.0.11"\n')
    assert jdk_major_version(tmp_path) == 21
    assert jdk_major_version(tmp_path / "missing") is None


def test_the_toolchain_refuses_jdk_17_and_missing_pieces(tmp_path: Path) -> None:
    jdk17 = tmp_path / "jdk17"
    (jdk17 / "bin").mkdir(parents=True)
    (jdk17 / "bin" / "java").write_text("")
    (jdk17 / "release").write_text('JAVA_VERSION="17.0.11"\n')
    jar = tmp_path / "tracer.jar"
    jar.write_text("")
    with pytest.raises(JdiBackendUnavailable, match="CODE2SHORTS_JDI_JAVA_HOME is not set"):
        JdiToolchain.resolve(None, str(jar), jar)
    with pytest.raises(JdiBackendUnavailable, match="needs JDK 21"):
        JdiToolchain.resolve(str(jdk17), str(jar), jar)
    (jdk17 / "release").write_text('JAVA_VERSION="21.0.2"\n')
    with pytest.raises(JdiBackendUnavailable, match="CODE2SHORTS_JDI_TRACER_JAR"):
        JdiToolchain.resolve(str(jdk17), str(tmp_path / "absent.jar"), jar)
    assert JdiToolchain.resolve(str(jdk17), str(jar), jar).tracer_jar == jar


# ---- backend selection: instrumenter stays the default --------------------------


def test_the_instrumenter_is_the_default_backend() -> None:
    from code2shorts.config import Settings
    from code2shorts.langadapter.java import JavaAdapter

    assert Settings(_env_file=None, environment="test").java_trace_backend == "instrumenter"
    assert JavaAdapter().trace_backend == "instrumenter"


def test_an_unknown_backend_is_refused_at_construction() -> None:
    from code2shorts.langadapter.java import JavaAdapter

    with pytest.raises(ValueError, match="CODE2SHORTS_JAVA_TRACE_BACKEND"):
        JavaAdapter(trace_backend="gdb")


def test_an_unconfigured_jdi_backend_fails_clearly_and_never_falls_back(monkeypatch) -> None:
    from code2shorts.core.models import TraceStatus
    from code2shorts.execution.sandbox import Workspace
    from code2shorts.langadapter.java import JavaAdapter
    from tests.java_fixtures import load_algorithm_fixture

    monkeypatch.delenv("CODE2SHORTS_JDI_JAVA_HOME", raising=False)
    adapter = JavaAdapter(trace_backend="jdi")
    with Workspace() as workspace:
        trace = adapter.trace(load_algorithm_fixture("palindrome"), workspace, "RACECAR")
    assert trace.status is TraceStatus.INSTRUMENTATION_FAILED
    assert trace.events == [] and not trace.succeeded
    assert trace.stderr.startswith("JDI_BACKEND_UNAVAILABLE:")


# ---- security properties (AST, not text search) ------------------------------------


def _modules():
    for path in JDI_PACKAGE.glob("*.py"):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_the_jdi_backend_only_runs_processes_through_run_subprocess() -> None:
    for path, tree in _modules():
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = {a.name for a in node.names} | {getattr(node, "module", "") or ""}
                assert not names & {"subprocess", "os", "shlex"}, f"{path.name} imports {names}"
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                assert name not in ("eval", "exec", "system", "popen", "Popen"), path.name
                for keyword in node.keywords:
                    assert keyword.arg != "shell", f"{path.name} passes shell="


def test_no_algorithm_names_in_the_jdi_package() -> None:
    """Like rule 7 for visualization/: the derivation must be generic. Only
    identifiers and string constants are checked, not comments."""
    banned = {"palindrome", "two_sum", "twosum", "move_zeroes", "movezeroes",
              "remove_duplicates", "reverse_string", "reversestring", "factorial", "binary_search"}
    for path, tree in _modules():
        for node in ast.walk(tree):
            text = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.Name):
                text = node.id
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                text = node.name
            if text is not None:
                lowered = text.lower()
                assert not any(word in lowered for word in banned), f"{path.name}: {text!r}"


# ---- live backend (needs JDK 21 + the tracer jar) ------------------------------------


live = pytest.mark.skipif(
    not (os.environ.get("CODE2SHORTS_JDI_JAVA_HOME") and os.environ.get("CODE2SHORTS_JDI_TRACER_JAR")),
    reason="JDI backend not configured (CODE2SHORTS_JDI_JAVA_HOME + CODE2SHORTS_JDI_TRACER_JAR)",
)


def _live_trace(load, value, backend):
    from code2shorts.execution.sandbox import Workspace
    from code2shorts.langadapter.java import JavaAdapter

    with Workspace() as workspace:
        return JavaAdapter(trace_backend=backend).trace(load(), workspace, value)


@pytest.mark.integration
@live
@pytest.mark.parametrize("variant,value", [
    ("palindrome", "RACECAR"), ("two_sum", "9"), ("remove_duplicates", ""),
])
def test_live_goldens_match_the_instrumenter_event_for_event(variant, value) -> None:
    from tests.java_fixtures import load_algorithm_fixture

    def load():
        return load_algorithm_fixture(variant)

    ours, jdi = _live_trace(load, value, "instrumenter"), _live_trace(load, value, "jdi")
    assert jdi.status == ours.status and jdi.output == ours.output
    assert [e.model_dump(include=set(FIELDS)) for e in jdi.events] == \
        [e.model_dump(include=set(FIELDS)) for e in ours.events]


@pytest.mark.integration
@live
def test_live_move_zeroes_is_refused_not_approximated() -> None:
    from code2shorts.core.models import TraceStatus
    from tests.java_fixtures import load_algorithm_fixture

    trace = _live_trace(lambda: load_algorithm_fixture("move_zeroes"), "", "jdi")
    assert trace.status is TraceStatus.INSTRUMENTATION_FAILED
    assert "JDI_DERIVATION_FAILED" in trace.stderr and "StringBuilder" in trace.stderr


@pytest.mark.integration
@live
def test_live_runtime_exception_reports_the_real_source_line() -> None:
    from code2shorts.core.models import TraceStatus
    from tests.java_fixtures import load_trace_sample

    trace = _live_trace(lambda: load_trace_sample("exception"), "", "jdi")
    assert trace.status is TraceStatus.RUNTIME_ERROR
    assert trace.exception.exception_class == "java.lang.ArrayIndexOutOfBoundsException"
    assert trace.exception.thrown_at_line == 10      # the line in the user's source


@pytest.mark.integration
@live
def test_live_infinite_loop_times_out() -> None:
    from code2shorts.core.models import TraceStatus
    from code2shorts.execution.sandbox import Workspace
    from code2shorts.langadapter.java import JavaAdapter
    from tests.java_fixtures import load_trace_sample

    adapter = JavaAdapter(trace_backend="jdi", execution_timeout_seconds=3.0)
    with Workspace() as workspace:
        trace = adapter.trace(load_trace_sample("infinite_loop"), workspace, "")
    assert trace.status is TraceStatus.TIMEOUT and trace.timed_out and not trace.events

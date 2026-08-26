"""Real Maven + JDK 17 + JUnit 5 integration tests for JavaAdapter.

These are slow (spawn real `mvn`/`java` subprocesses) and require Maven and
a JDK on PATH. No LLM, no fabricated ExecutionTrace, no video — Phase 1
only proves the execution infrastructure is real.
"""

from __future__ import annotations

import pytest

from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java import JavaAdapter
from tests.java_fixtures import load_java_fixture

pytestmark = pytest.mark.integration


@pytest.fixture
def adapter() -> JavaAdapter:
    return JavaAdapter()


def test_compile_succeeds_on_correct_fixture(adapter: JavaAdapter) -> None:
    code = load_java_fixture("correct")
    with Workspace() as workspace:
        result = adapter.compile(code, workspace)
        assert result.succeeded, result.stderr
        assert not result.timed_out
        assert result.duration_seconds > 0


def test_junit_tests_pass_on_correct_fixture(adapter: JavaAdapter) -> None:
    code = load_java_fixture("correct")
    with Workspace() as workspace:
        result = adapter.test(code, workspace)
        assert result.succeeded, result.stdout + result.stderr
        assert result.tests_run == 6
        assert result.tests_passed == 6


def test_execute_reverses_hello(adapter: JavaAdapter) -> None:
    code = load_java_fixture("correct")
    with Workspace() as workspace:
        result = adapter.execute(code, workspace, "HELLO")
        assert result.succeeded
        assert result.exit_code == 0
        assert result.output == "OLLEH"
        assert not result.timed_out


@pytest.mark.parametrize(
    ("input_value", "expected_output"),
    [
        ("HELLO", "OLLEH"),
        ("RACECAR", "RACECAR"),
        ("A", "A"),
        ("MISS", "SSIM"),
    ],
)
def test_execute_various_inputs(
    adapter: JavaAdapter, input_value: str, expected_output: str
) -> None:
    code = load_java_fixture("correct")
    with Workspace() as workspace:
        result = adapter.execute(code, workspace, input_value)
        assert result.succeeded
        assert result.output == expected_output


def test_compile_rejects_broken_syntax(adapter: JavaAdapter) -> None:
    code = load_java_fixture("compile_error")
    with Workspace() as workspace:
        result = adapter.compile(code, workspace)
        assert not result.succeeded
        assert result.stdout + result.stderr


def test_junit_tests_reject_broken_implementation(adapter: JavaAdapter) -> None:
    code = load_java_fixture("broken_logic")
    with Workspace() as workspace:
        result = adapter.test(code, workspace)
        assert not result.succeeded
        assert result.tests_run == 6
        assert result.tests_passed < result.tests_run


def test_execute_surfaces_nonzero_exit(adapter: JavaAdapter) -> None:
    code = load_java_fixture("exit_nonzero")
    with Workspace() as workspace:
        result = adapter.execute(code, workspace, "HELLO")
        assert not result.succeeded
        assert result.exit_code != 0
        assert "RuntimeException" in result.stderr


def test_execute_detects_timeout() -> None:
    short_timeout_adapter = JavaAdapter(execution_timeout_seconds=2.0)
    code = load_java_fixture("timeout")
    with Workspace() as workspace:
        result = short_timeout_adapter.execute(code, workspace, "HELLO")
        assert not result.succeeded
        assert result.timed_out


def test_workspace_is_cleaned_up_after_use(adapter: JavaAdapter) -> None:
    code = load_java_fixture("correct")
    workspace = Workspace()
    workspace_path = workspace.path
    with workspace:
        adapter.compile(code, workspace)
        assert workspace_path.exists()
    assert not workspace_path.exists()


def test_trace_produces_a_real_execution_trace(adapter: JavaAdapter) -> None:
    # Phase 1 asserted trace() stayed NotImplementedError (no fabrication
    # before real infrastructure existed). Phase 2 implements it for real —
    # see tests/test_java_trace_integration.py for the full event-by-event
    # proof; this just confirms the Phase 1 contract point (JavaAdapter
    # exposes a working trace() backed by real execution, not a stub) still
    # holds from this file's perspective.
    code = load_java_fixture("correct")
    with Workspace() as workspace:
        trace = adapter.trace(code, workspace, "HELLO")
        assert trace.succeeded
        assert trace.output == "OLLEH"
        assert len(trace.events) > 0

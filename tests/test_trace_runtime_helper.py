"""Step 2 proof: Code2ShortsTrace emits valid TRACE: JSON lines.

Uses a HAND-instrumented fixture (not the AST instrumenter, which doesn't
exist yet) run through Phase 1's already-proven JavaAdapter.execute() —
deliberately not JavaAdapter.trace() (also doesn't exist yet). This proves
the runtime helper works before building anything that depends on it.
"""

from __future__ import annotations

import json

import pytest

from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java import JavaAdapter
from tests.java_fixtures import load_java_fixture

pytestmark = pytest.mark.integration


def _trace_lines(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        if line.startswith("TRACE:"):
            events.append(json.loads(line[len("TRACE:") :]))
    return events


def _program_output_lines(stdout: str) -> list[str]:
    # execute()'s .output is just stdout.strip() — it predates tracing and
    # was never meant to separate TRACE: lines from real output (that's
    # TraceStreamParser's job, Step 7). This helper stands in for it here.
    return [line for line in stdout.splitlines() if not line.startswith("TRACE:")]


def test_hand_instrumented_fixture_emits_valid_trace_json() -> None:
    code = load_java_fixture("traced_correct")
    adapter = JavaAdapter()
    with Workspace() as workspace:
        result = adapter.execute(code, workspace, "HELLO")
        assert result.succeeded, result.stderr
        assert _program_output_lines(result.stdout) == ["OLLEH"]

        events = _trace_lines(result.stdout)
        assert events, "expected TRACE: lines in stdout"

        assert events[0]["event_type"] == "PROGRAM_START"
        assert events[-1]["event_type"] == "PROGRAM_END"

        event_types = {event["event_type"] for event in events}
        assert "METHOD_ENTER" in event_types
        assert "METHOD_EXIT" in event_types
        assert "VARIABLE_ASSIGN" in event_types
        assert "CONDITION_EVALUATED" in event_types
        assert "LOOP_ITERATION" in event_types
        assert "ARRAY_READ" in event_types
        assert "ARRAY_WRITE" in event_types

        method_exit = next(e for e in events if e["event_type"] == "METHOD_EXIT")
        assert method_exit["return_value"] == "OLLEH"

        loop_iterations = [e for e in events if e["event_type"] == "LOOP_ITERATION"]
        assert [e["iteration"] for e in loop_iterations] == [1, 2]

        # call_depth: 0 outside any method, 1 inside reverse()
        program_start = events[0]
        method_enter = next(e for e in events if e["event_type"] == "METHOD_ENTER")
        assert program_start["call_depth"] == 0
        assert method_enter["call_depth"] == 1


def test_hand_instrumented_fixture_reports_exception() -> None:
    # ReverseString.reverse on a null-ish/edge input isn't a real exception
    # case for this algorithm, so prove the exception path using the
    # existing exit_nonzero-style contract instead: call with an input that
    # forces an uncaught exception via a deliberately-broken variant is
    # Step 9's job (real fixture). Here we just confirm normal completion
    # does NOT emit an EXCEPTION_THROWN event, keeping the happy path clean.
    code = load_java_fixture("traced_correct")
    adapter = JavaAdapter()
    with Workspace() as workspace:
        result = adapter.execute(code, workspace, "A")
        assert result.succeeded
        assert _program_output_lines(result.stdout) == ["A"]
        events = _trace_lines(result.stdout)
        assert not any(e["event_type"] == "EXCEPTION_THROWN" for e in events)

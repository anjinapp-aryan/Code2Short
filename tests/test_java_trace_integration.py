"""Phase 2 integration tests: JavaAdapter.trace() end to end, real Maven +
JDK compile/execute, real AST instrumentation (JavaParser tool), no mocks.

Requires the instrumenter jar built: cd tools/java-instrumenter && mvn -q package
"""

from __future__ import annotations

from code2shorts.codegen.base import GeneratedCode
from code2shorts.core.models import SupportedLanguage, TraceEventType, TraceStatus
from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.java import JavaAdapter
from tests.java_fixtures import load_java_fixture, load_trace_sample
from tests.trace_helpers import assert_traces_equivalent

import pytest

pytestmark = pytest.mark.integration


def _trace(adapter: JavaAdapter, code: GeneratedCode, input_value: str = ""):
    with Workspace() as workspace:
        return adapter.trace(code, workspace, input_value)


# ---- basic method tracing / variable assignment / arrays / while loop ----
# (all exercised together by the Reverse String fixture, same one Phase 1
# already proved compiles/executes correctly)


def test_basic_method_and_variable_and_array_and_while_loop_tracing() -> None:
    adapter = JavaAdapter()
    trace = _trace(adapter, load_java_fixture("correct"), "HELLO")

    assert trace.status == TraceStatus.COMPLETED
    assert trace.succeeded
    assert trace.output == "OLLEH"

    event_types = {e.event_type for e in trace.events}
    assert TraceEventType.METHOD_ENTER.value in event_types
    assert TraceEventType.METHOD_EXIT.value in event_types
    assert TraceEventType.VARIABLE_ASSIGN.value in event_types
    assert TraceEventType.CONDITION_EVALUATED.value in event_types
    assert TraceEventType.LOOP_ITERATION.value in event_types
    assert TraceEventType.ARRAY_READ.value in event_types
    assert TraceEventType.ARRAY_WRITE.value in event_types

    method_exit = next(e for e in trace.events if e.event_type == TraceEventType.METHOD_EXIT.value)
    assert method_exit.return_value == "OLLEH"

    loop_events = [e for e in trace.events if e.event_type == TraceEventType.LOOP_ITERATION.value]
    assert [e.iteration for e in loop_events] == [1, 2]

    assert trace.max_call_depth_reached == 1
    assert trace.total_loop_iterations == 2

    # step_index must be sequential 0..N-1 — the model validator already
    # guarantees this at construction, but assert explicitly for clarity.
    assert [e.step_index for e in trace.events] == list(range(len(trace.events)))


def test_json_round_trip() -> None:
    adapter = JavaAdapter()
    trace = _trace(adapter, load_java_fixture("correct"), "HELLO")
    round_tripped = trace.__class__.model_validate_json(trace.model_dump_json())
    assert round_tripped == trace


def test_deterministic_repeated_execution() -> None:
    adapter = JavaAdapter()
    code = load_java_fixture("correct")
    traces = [_trace(adapter, code, "HELLO") for _ in range(3)]
    assert_traces_equivalent(traces[0], traces[1])
    assert_traces_equivalent(traces[1], traces[2])


# ---- if / else, helper method ----


def test_if_else_and_helper_method() -> None:
    adapter = JavaAdapter()
    trace = _trace(adapter, load_trace_sample("control_flow"), "5")

    assert trace.status == TraceStatus.COMPLETED
    assert trace.output == "positive"

    conditions = [e for e in trace.events if e.event_type == TraceEventType.CONDITION_EVALUATED.value]
    assert any(e.condition_result is True for e in conditions)

    method_enter = next(e for e in trace.events if e.event_type == TraceEventType.METHOD_ENTER.value)
    assert method_enter.method == "Main.classify"


# ---- for loop ----


def test_for_loop_and_array_sum() -> None:
    adapter = JavaAdapter()
    trace = _trace(adapter, load_trace_sample("for_loop"), "")

    assert trace.status == TraceStatus.COMPLETED
    assert trace.output == "15"  # 1+2+3+4+5
    assert trace.total_loop_iterations == 5

    array_reads = [e for e in trace.events if e.event_type == TraceEventType.ARRAY_READ.value]
    assert len(array_reads) == 5


# ---- nested loops ----


def test_nested_loops() -> None:
    adapter = JavaAdapter()
    trace = _trace(adapter, load_trace_sample("nested_loops"), "")

    assert trace.status == TraceStatus.COMPLETED
    assert trace.output == "9"  # 3 outer * 3 inner
    assert trace.total_loop_iterations == 12  # 3 outer + 9 inner


# ---- recursion ----


def test_recursion_call_depth() -> None:
    adapter = JavaAdapter()
    trace = _trace(adapter, load_trace_sample("recursion"), "5")

    assert trace.status == TraceStatus.COMPLETED
    assert trace.output == "120"  # 5!
    assert trace.max_call_depth_reached == 5

    method_enters = [e for e in trace.events if e.event_type == TraceEventType.METHOD_ENTER.value]
    assert len(method_enters) == 5
    assert [e.call_depth for e in method_enters] == [1, 2, 3, 4, 5]


# ---- runtime exception ----


def test_runtime_exception_is_captured_and_prior_events_preserved() -> None:
    adapter = JavaAdapter()
    trace = _trace(adapter, load_trace_sample("exception"), "")

    assert trace.status == TraceStatus.RUNTIME_ERROR
    assert not trace.succeeded
    assert trace.exit_code != 0
    assert trace.exception is not None
    assert trace.exception.exception_class.endswith("ArrayIndexOutOfBoundsException")

    # events collected before the crash are preserved, not discarded
    event_types = [e.event_type for e in trace.events]
    assert TraceEventType.METHOD_ENTER.value in event_types
    assert TraceEventType.PROGRAM_END.value in event_types  # finally block still ran


# ---- timeout vs. trace limit (must be distinguishable) ----


def test_timeout_is_distinct_from_trace_limit() -> None:
    # Generous JVM-side limits, tiny wall-clock timeout — isolates TIMEOUT.
    adapter = JavaAdapter(
        execution_timeout_seconds=1.0,
        trace_max_events=100_000_000,
        trace_max_loop_iterations=100_000_000,
        trace_max_output_bytes=100_000_000,
    )
    trace = _trace(adapter, load_trace_sample("infinite_loop"), "")
    assert trace.status == TraceStatus.TIMEOUT
    assert trace.timed_out
    assert not trace.succeeded


def test_infinite_loop_terminates_via_trace_limit_not_wall_clock() -> None:
    # Tiny JVM-side loop-iteration limit, generous wall-clock timeout —
    # proves the limit (not the timeout) is what stops it, and that it
    # stops FAST rather than waiting out the wall clock.
    adapter = JavaAdapter(
        execution_timeout_seconds=60.0,
        trace_max_loop_iterations=50,
    )
    trace = _trace(adapter, load_trace_sample("infinite_loop"), "")
    assert trace.status == TraceStatus.TRACE_LIMIT_REACHED
    assert not trace.timed_out  # must NOT have hit the wall-clock timeout
    assert not trace.succeeded
    assert trace.duration_seconds < 30.0  # terminated fast, not near the 60s wall clock


# ---- deep recursion -> max_call_depth ----


def test_deep_recursion_terminates_via_max_call_depth() -> None:
    adapter = JavaAdapter(trace_max_call_depth=50)
    trace = _trace(adapter, load_trace_sample("recursion"), "100000")
    assert trace.status == TraceStatus.TRACE_LIMIT_REACHED
    assert not trace.succeeded
    assert trace.max_call_depth_reached <= 51  # stopped at/just past the limit


# ---- huge output -> max_output_size ----


def test_huge_output_is_bounded() -> None:
    adapter = JavaAdapter(trace_max_output_bytes=10_000)
    trace = _trace(adapter, load_trace_sample("huge_output"), "")
    assert trace.status == TraceStatus.TRACE_LIMIT_REACHED
    assert not trace.succeeded
    # never buffered the full 5MB string into the trace/output
    assert len(trace.stdout) < 1_000_000


# ---- max_trace_size (Python-side belt-and-suspenders truncation) ----


def test_trace_size_is_bounded_independently_of_jvm_limits() -> None:
    adapter = JavaAdapter(trace_max_trace_size_events=5)
    trace = _trace(adapter, load_trace_sample("nested_loops"), "")
    assert trace.truncated
    assert trace.truncation_reason == "max_trace_size"
    assert len(trace.events) == 5
    assert [e.step_index for e in trace.events] == [0, 1, 2, 3, 4]


# ---- nondeterministic API rejection ----


def _inline_code(source: str) -> GeneratedCode:
    return GeneratedCode(
        language=SupportedLanguage.JAVA,
        source_files={"src/main/java/com/code2shorts/tracesamples/Main.java": source},
        entry_point="src/main/java/com/code2shorts/tracesamples/Main.java",
    )


def test_random_usage_is_rejected() -> None:
    source = """
package com.code2shorts.tracesamples;
import java.util.Random;
public final class Main {
    public static void main(String[] args) {
        Random r = new Random();
        System.out.println(r.nextInt());
    }
}
"""
    adapter = JavaAdapter()
    trace = _trace(adapter, _inline_code(source), "")
    assert trace.status == TraceStatus.INSTRUMENTATION_FAILED
    assert not trace.succeeded
    assert trace.events == []


def test_thread_usage_is_rejected() -> None:
    source = """
package com.code2shorts.tracesamples;
public final class Main {
    public static void main(String[] args) {
        Thread t = new Thread();
    }
}
"""
    adapter = JavaAdapter()
    trace = _trace(adapter, _inline_code(source), "")
    assert trace.status == TraceStatus.INSTRUMENTATION_FAILED


# ---- unsupported construct rejection (lambda) ----


def test_lambda_is_rejected() -> None:
    source = """
package com.code2shorts.tracesamples;
public final class Main {
    public static void main(String[] args) {
        Runnable r = () -> System.out.println("hi");
        r.run();
    }
}
"""
    adapter = JavaAdapter()
    trace = _trace(adapter, _inline_code(source), "")
    assert trace.status == TraceStatus.INSTRUMENTATION_FAILED
    assert not trace.succeeded

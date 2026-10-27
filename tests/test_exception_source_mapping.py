"""Phase 8.4.2: an exception's line must be the line in the USER's source.

The instrumenter re-prints the whole file (JavaParser `toString()`), so the
JVM's stack-trace line for the instrumented class is a line of the
REWRITTEN source. Before this phase that line went straight into the
canonical trace: the `exception` trace sample threw at user line 10 and the
trace said 25.

Every program here marks its throwing line with `// THROWS`, so the
expected line is read from the source, never hard-coded. Where the program
is a single file, the same source is also run uninstrumented on a plain JVM
and its own stack trace must agree: an independent oracle.

Real path, no mocks: JavaParser instrumenter -> Maven compile -> JVM.
"""

from __future__ import annotations

import re

import pytest

from code2shorts.codegen.base import GeneratedCode
from code2shorts.core.models import SupportedLanguage, TraceEventType, TraceStatus
from code2shorts.execution.sandbox import Workspace, run_subprocess
from code2shorts.langadapter.java import JavaAdapter
from code2shorts.visualization.code_state import build_code_state, resolve_source_locations
from tests.java_fixtures import TRACE_SAMPLE_ENTRY_POINT, load_trace_sample

pytestmark = pytest.mark.integration

ENTRY = TRACE_SAMPLE_ENTRY_POINT
HELPER = "src/main/java/com/code2shorts/tracesamples/Helper.java"
MARK = "// THROWS"

DIRECT_THROW = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int total = 0;
        for (int i = 0; i < 3; i++) {
            total = total + i;
        }
        throw new IllegalStateException("total=" + total); // THROWS
    }
}
"""

# Exception thrown two calls deep, inside if-inside-while-inside-method,
# after enough instrumented statements that the shift differs per method.
NESTED = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int[] data = {4, 0, 7, 1};
        int answer = scan(data);
        System.out.println(answer);
    }

    static int scan(int[] data) {
        int i = 0;
        int sum = 0;
        while (i < data.length) {
            if (data[i] == 0) {
                sum = sum + check(data, i);
            }
            sum = sum + data[i];
            i++;
        }
        return sum;
    }

    static int check(int[] data, int at) {
        int left = data[at];
        int right = 0;
        if (left == right) {
            int width = data.length;
            for (int k = 0; k < width; k++) {
                if (k == at) {
                    int bad = data[k + 10]; // THROWS
                    right = bad;
                }
            }
        }
        return right;
    }
}
"""

# Many instrumented statements (assignments, array writes, inc/dec) before
# the throw: each emits extra lines in the rewritten source.
HEAVY = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int[] a = new int[4];
        int x = 1;
        x = x + 1;
        x++;
        a[0] = x;
        a[1] = a[0];
        x = x * 3;
        a[2] = x;
        x--;
        a[3] = a[2];
        int y = a[3];
        y = y + x;


        // a comment and blank lines between statements
        a[y] = 9; // THROWS
    }
}
"""

# A caught exception produces no EXCEPTION_THROWN; execution continues and a
# later, uncaught exception maps to its own line.
TRY_CATCH = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int recovered = 0;
        try {
            int[] small = new int[1];
            small[3] = 1;
        } catch (ArrayIndexOutOfBoundsException e) {
            recovered = 1;
        }
        int after = recovered + 1;
        try {
            Integer.parseInt("x" + after); // THROWS
        } finally {
            recovered = 2;
        }
    }
}
"""

# The throwing expression is inside a generated wrapper: the return
# statement is rewritten into `{ var __c2s_ret = <expr>; ... }`.
RETURN_EXPRESSION = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int[] values = {1, 2};
        System.out.println(pick(values, 5));
    }

    static int pick(int[] values, int at) {
        return values[at]; // THROWS
    }
}
"""

# The out-of-bounds READ on the right of an array write is evaluated by an
# instrumentation statement inserted BEFORE the user's statement.
ARRAY_COPY = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int[] src = {1, 2};
        int[] dst = new int[2];
        dst[0] = src[7]; // THROWS
    }
}
"""

# Exception inside a loop/if CONDITION, which the instrumenter wraps in
# Code2ShortsTrace.condition(...).
CONDITION = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int[] v = {3, 1};
        int i = 0;
        while (v[i] > 0) { // THROWS
            i++;
        }
    }
}
"""

# A throw on the first statement of a method and the last code line of
# the file, plus the exception coming from inside the JDK.
EDGES = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        System.out.println(parse("12") + parse("oops"));
    }
    static int parse(String text) {
        return Integer.parseInt(text); // THROWS
    }
}"""

RECURSION = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        System.out.println(down(3));
    }

    static int down(int n) {
        if (n == 0) {
            int[] none = new int[0];
            return none[n]; // THROWS
        }
        return down(n - 1) + 1;
    }
}
"""

# Two files. The helper's rewritten source shifts differently from Main's.
MULTI_FILE_MAIN = """package com.code2shorts.tracesamples;

public final class Main {
    public static void main(String[] args) {
        int start = 2;
        start = start + 1;
        System.out.println(Helper.divide(start, 0));
    }
}
"""
MULTI_FILE_HELPER = """package com.code2shorts.tracesamples;

public final class Helper {
    static int divide(int a, int b) {
        int total = a;
        total = total * 2;
        int result = total / b; // THROWS
        return result;
    }
}
"""


def _code(main: str, **extra: str) -> GeneratedCode:
    files = {ENTRY: main}
    files.update({path: text for path, text in extra.items()})
    return GeneratedCode(language=SupportedLanguage.JAVA, source_files=files, entry_point=ENTRY)


def _marked(source: str) -> int:
    lines = [n for n, text in enumerate(source.splitlines(), 1) if MARK in text]
    assert len(lines) == 1, "each program marks exactly one throwing line"
    return lines[0]


def _trace(code: GeneratedCode):
    with Workspace() as workspace:
        return JavaAdapter(trace_backend="instrumenter").trace(code, workspace, "")


def _plain_jvm_line(source: str) -> int:
    """The line a plain, uninstrumented JVM reports for the first frame in
    Main.java: independent of everything this phase changes."""
    with Workspace() as workspace:
        path = workspace.write_file("Main.java", source)
        result = run_subprocess(["java", str(path)], cwd=workspace.path, timeout_seconds=60)
    frames = re.findall(r"\(Main\.java:(\d+)\)", result.stderr)
    assert frames, result.stderr
    return int(frames[0])


def _exception_event(trace):
    events = [e for e in trace.events if e.event_type == TraceEventType.EXCEPTION_THROWN.value]
    assert len(events) == 1
    return events[0]


def _assert_maps_to_marked_line(code: GeneratedCode, marked_file: str, method: str):
    trace = _trace(code)
    assert trace.status is TraceStatus.RUNTIME_ERROR, trace.stderr
    expected = _marked(code.source_files[marked_file])
    assert trace.exception.thrown_at_line == expected
    assert trace.exception.thrown_in_method == method
    assert _exception_event(trace).line_number == expected
    return trace


@pytest.mark.parametrize(
    ("name", "source", "method"),
    [
        ("direct_throw", DIRECT_THROW, "main"),
        ("nested_control_flow", NESTED, "check"),
        ("instrumentation_heavy", HEAVY, "main"),
        ("try_finally", TRY_CATCH, "main"),
        ("return_expression", RETURN_EXPRESSION, "pick"),
        ("array_copy", ARRAY_COPY, "main"),
        ("loop_condition", CONDITION, "main"),
        ("jdk_frame_first_line_last_line", EDGES, "parse"),
        ("recursion", RECURSION, "down"),
    ],
    ids=["direct_throw", "nested_control_flow", "instrumentation_heavy", "try_finally",
         "return_expression", "array_copy", "loop_condition", "jdk_frame_first_line_last_line",
         "recursion"],
)
def test_exception_line_is_the_original_source_line(name: str, source: str, method: str) -> None:
    _assert_maps_to_marked_line(_code(source), ENTRY, method)


@pytest.mark.parametrize(
    "source",
    [DIRECT_THROW, NESTED, HEAVY, TRY_CATCH, RETURN_EXPRESSION, ARRAY_COPY, CONDITION, EDGES, RECURSION],
    ids=["direct_throw", "nested_control_flow", "instrumentation_heavy", "try_finally",
         "return_expression", "array_copy", "loop_condition", "jdk_frame_first_line_last_line",
         "recursion"],
)
def test_the_marked_line_is_what_a_plain_jvm_reports(source: str) -> None:
    """The oracle check: the `// THROWS` markers agree with javac's own
    line table for the uninstrumented program."""
    assert _plain_jvm_line(source) == _marked(source)


def test_existing_exception_sample_reports_user_line_10() -> None:
    """The defect as found in Phase 8.4: user line 10, trace said 25."""
    code = load_trace_sample("exception")
    trace = _trace(code)
    assert trace.exception.exception_class == "java.lang.ArrayIndexOutOfBoundsException"
    assert trace.exception.thrown_at_line == 10
    assert trace.exception.thrown_in_method == "boom"
    assert _exception_event(trace).line_number == 10
    assert _plain_jvm_line(code.source_files[ENTRY]) == 10


def test_trace_limit_exception_points_at_the_users_loop_not_the_runtime() -> None:
    """The limit is raised inside Code2ShortsTrace; before 8.4.2 the trace
    said line 343 of THAT file, in `checkLoopIterations`."""
    code = load_trace_sample("infinite_loop")
    with Workspace() as workspace:
        trace = JavaAdapter(trace_backend="instrumenter", trace_max_loop_iterations=50).trace(
            code, workspace, ""
        )
    assert trace.status is TraceStatus.TRACE_LIMIT_REACHED
    assert trace.exception.thrown_in_method == "spin"
    line = trace.exception.thrown_at_line
    assert "while (true)" in code.source_files[ENTRY].splitlines()[line - 1]


def test_exception_in_a_second_file_maps_through_that_files_own_table() -> None:
    code = _code(MULTI_FILE_MAIN, **{HELPER: MULTI_FILE_HELPER})
    _assert_maps_to_marked_line(code, HELPER, "divide")


def test_caught_exception_emits_no_event_and_later_lines_stay_correct() -> None:
    trace = _trace(_code(TRY_CATCH))
    lines = TRY_CATCH.splitlines()
    assert len([e for e in trace.events if e.event_type == TraceEventType.EXCEPTION_THROWN.value]) == 1
    after = next(e for e in trace.events if e.variable_name == "after")
    assert "int after" in lines[after.line_number - 1]


def test_no_constant_offset_two_sites_in_one_file_shift_differently() -> None:
    """A fixed offset cannot be right for both main (shifted by the main
    wrapper) and a deep method (shifted by every instrumented statement
    above it). Both must land on their own marked line."""
    first = DIRECT_THROW
    second = NESTED
    assert _trace(_code(first)).exception.thrown_at_line == _marked(first)
    assert _trace(_code(second)).exception.thrown_at_line == _marked(second)


def test_exception_mapping_is_deterministic() -> None:
    results = {
        repr((t.exception, _exception_event(t)))
        for t in (_trace(_code(NESTED)) for _ in range(3))
    }
    assert len(results) == 1


def test_code_panel_highlights_the_users_throwing_line() -> None:
    """Downstream: SourceLocation -> CodeState, unchanged code, now fed the
    corrected line."""
    code = _code(MULTI_FILE_MAIN, **{HELPER: MULTI_FILE_HELPER})
    trace = _trace(code)
    event = _exception_event(trace)
    location = resolve_source_locations(trace, code.source_files, entry_point=ENTRY)[event.step_index]
    state = build_code_state(location, code.source_files)
    assert location.file == HELPER
    assert MARK in state.lines[state.highlight_offset]

"""Java implementation of LanguageAdapter.

Phase 1: compile/test/execute are real (Maven + JDK 17 + JUnit 5, subprocess
isolated).

Phase 2: trace() is real too — instrument (AST rewrite, JavaParser-based
tool) -> compile (Phase 1, reused) -> execute (Phase 1, reused, with trace
limits passed as env vars) -> parse/limit/normalize (Phase 2) ->
ExecutionTrace. Every TraceEvent comes from real, captured execution
output; nothing here fabricates trace data.
"""

from __future__ import annotations

import os

from code2shorts.codegen.base import GeneratedCode
from code2shorts.core.models import ExecutionTrace, SupportedLanguage, TraceStatus
from code2shorts.execution.sandbox import Workspace
from code2shorts.langadapter.base import (
    CompileResult,
    ExecuteResult,
    LanguageAdapter,
    TestResult,
)
from code2shorts.langadapter.java.compiler import JavaCompiler
from code2shorts.langadapter.java.executor import JavaExecutor
from code2shorts.langadapter.java.test_runner import JavaTestRunner
from code2shorts.langadapter.java.trace.exceptions import (
    InstrumentationFailedError,
    InstrumenterTimeoutError,
)
from code2shorts.langadapter.java.trace.instrumenter import JavaSourceInstrumenter
from code2shorts.langadapter.java.trace.limiter import TraceLimiter
from code2shorts.langadapter.java.trace.normalizer import (
    build_events,
    extract_exception,
    is_trace_limit_exception,
)
from code2shorts.langadapter.java.trace.stream_parser import TraceStreamParser
from code2shorts.langadapter.java.workspace import JavaWorkspace

DEFAULT_BUILD_TIMEOUT_SECONDS = 120.0
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 10.0
DEFAULT_INSTRUMENTER_TIMEOUT_SECONDS = 30.0
DEFAULT_TRACE_MAX_EVENTS = 2000
DEFAULT_TRACE_MAX_LOOP_ITERATIONS = 1000
DEFAULT_TRACE_MAX_CALL_DEPTH = 200
DEFAULT_TRACE_MAX_OUTPUT_BYTES = 1_000_000
DEFAULT_TRACE_MAX_TRACE_SIZE_EVENTS = 2000


class JavaAdapter(LanguageAdapter):
    def __init__(
        self,
        build_timeout_seconds: float = DEFAULT_BUILD_TIMEOUT_SECONDS,
        execution_timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
        instrumenter_timeout_seconds: float = DEFAULT_INSTRUMENTER_TIMEOUT_SECONDS,
        trace_max_events: int = DEFAULT_TRACE_MAX_EVENTS,
        trace_max_loop_iterations: int = DEFAULT_TRACE_MAX_LOOP_ITERATIONS,
        trace_max_call_depth: int = DEFAULT_TRACE_MAX_CALL_DEPTH,
        trace_max_output_bytes: int = DEFAULT_TRACE_MAX_OUTPUT_BYTES,
        trace_max_trace_size_events: int = DEFAULT_TRACE_MAX_TRACE_SIZE_EVENTS,
    ) -> None:
        self._build_timeout_seconds = build_timeout_seconds
        self._execution_timeout_seconds = execution_timeout_seconds
        self._trace_max_events = trace_max_events
        self._trace_max_loop_iterations = trace_max_loop_iterations
        self._trace_max_call_depth = trace_max_call_depth
        self._trace_max_output_bytes = trace_max_output_bytes

        self._compiler = JavaCompiler()
        self._test_runner = JavaTestRunner()
        self._executor = JavaExecutor()
        self._instrumenter = JavaSourceInstrumenter(timeout_seconds=instrumenter_timeout_seconds)
        self._stream_parser = TraceStreamParser()
        self._limiter = TraceLimiter(trace_max_trace_size_events)

    @property
    def language(self) -> SupportedLanguage:
        return SupportedLanguage.JAVA

    def compile(self, code: GeneratedCode, workspace: Workspace) -> CompileResult:
        java_workspace = JavaWorkspace(workspace)
        java_workspace.materialize(code)
        return self._compiler.compile(java_workspace, self._build_timeout_seconds)

    def test(self, code: GeneratedCode, workspace: Workspace) -> TestResult:
        java_workspace = JavaWorkspace(workspace)
        java_workspace.materialize(code)
        return self._test_runner.run(java_workspace, self._build_timeout_seconds)

    def execute(
        self, code: GeneratedCode, workspace: Workspace, input_value: str
    ) -> ExecuteResult:
        java_workspace = JavaWorkspace(workspace)
        java_workspace.materialize(code)
        compile_result = self._compiler.compile(java_workspace, self._build_timeout_seconds)
        if not compile_result.succeeded:
            return ExecuteResult(
                succeeded=False,
                exit_code=-1,
                stdout=compile_result.stdout,
                stderr=compile_result.stderr,
                output="",
                duration_seconds=compile_result.duration_seconds,
                timed_out=compile_result.timed_out,
            )
        main_class = JavaWorkspace.main_class_name(code.entry_point)
        return self._executor.run(
            java_workspace, main_class, input_value, self._execution_timeout_seconds
        )

    def trace(
        self, code: GeneratedCode, workspace: Workspace, input_value: str
    ) -> ExecutionTrace:
        entry_class = JavaWorkspace.main_class_name(code.entry_point)
        algorithm_name = entry_class.rsplit(".", 1)[-1]

        try:
            instrumented_code = self._instrumenter.instrument(code)
        except InstrumentationFailedError as error:
            return self._instrumentation_failed_trace(
                algorithm_name, entry_class, input_value, error.message
            )
        except InstrumenterTimeoutError as error:
            return self._instrumentation_failed_trace(
                algorithm_name, entry_class, input_value, str(error)
            )

        java_workspace = JavaWorkspace(workspace)
        java_workspace.materialize(instrumented_code)

        compile_result = self._compiler.compile(java_workspace, self._build_timeout_seconds)
        if not compile_result.succeeded:
            # The instrumented source failed to compile — that's a bug in
            # OUR instrumentation, not a fact about the traced program.
            return self._instrumentation_failed_trace(
                algorithm_name,
                entry_class,
                input_value,
                f"instrumented source failed to compile: {compile_result.stderr}",
            )

        main_class = JavaWorkspace.main_class_name(instrumented_code.entry_point)
        env = {
            **os.environ,
            "C2S_MAX_EVENTS": str(self._trace_max_events),
            "C2S_MAX_LOOP_ITERATIONS": str(self._trace_max_loop_iterations),
            "C2S_MAX_CALL_DEPTH": str(self._trace_max_call_depth),
            "C2S_MAX_OUTPUT_BYTES": str(self._trace_max_output_bytes),
        }
        execute_result = self._executor.run(
            java_workspace, main_class, input_value, self._execution_timeout_seconds, env=env
        )

        parsed = self._stream_parser.parse(execute_result.stdout)
        limited = self._limiter.apply(parsed.raw_events)
        events = build_events(limited.raw_events)
        exception = extract_exception(limited.raw_events)

        status, succeeded = self._classify(execute_result, exception, limited.truncated)

        return ExecutionTrace(
            algorithm_name=algorithm_name,
            language=SupportedLanguage.JAVA,
            entry_point=main_class,
            input=input_value,
            output=parsed.program_output,
            status=status,
            succeeded=succeeded,
            exit_code=execute_result.exit_code,
            exception=exception,
            duration_seconds=execute_result.duration_seconds,
            timed_out=execute_result.timed_out,
            events=events,
            truncated=limited.truncated,
            truncation_reason=limited.truncation_reason,
            stdout=execute_result.stdout,
            stderr=execute_result.stderr,
        )

    def _classify(
        self, execute_result: ExecuteResult, exception, truncated: bool
    ) -> tuple[TraceStatus, bool]:
        if execute_result.timed_out:
            return TraceStatus.TIMEOUT, False
        if exception is not None and is_trace_limit_exception(exception.exception_class):
            return TraceStatus.TRACE_LIMIT_REACHED, False
        if truncated:
            return TraceStatus.TRACE_LIMIT_REACHED, False
        if exception is not None:
            return TraceStatus.RUNTIME_ERROR, False
        if not execute_result.succeeded:
            return TraceStatus.RUNTIME_ERROR, False
        return TraceStatus.COMPLETED, True

    def _instrumentation_failed_trace(
        self, algorithm_name: str, entry_class: str, input_value: str, message: str
    ) -> ExecutionTrace:
        return ExecutionTrace(
            algorithm_name=algorithm_name,
            language=SupportedLanguage.JAVA,
            entry_point=entry_class,
            input=input_value,
            output="",
            status=TraceStatus.INSTRUMENTATION_FAILED,
            succeeded=False,
            exit_code=-1,
            stderr=message,
        )

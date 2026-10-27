"""EXPERIMENTAL Java trace backend: cs1302-tracer over the Java Debug
Interface, normalized into the SAME ExecutionTrace the instrumenter makes.

    source ──► SourceStructure (our JavaParser tool, JDK 17)
           └─► harness + cs1302-tracer jar (JDK 21+, via run_subprocess)
                  ──► line snapshots ──► SnapshotDiffer ──► raw runtime events
                  ──► TraceLimiter ─► build_events ─► ExecutionTrace

Selected only with `CODE2SHORTS_JAVA_TRACE_BACKEND=jdi`. The instrumenter
stays the default. This backend never falls back to the instrumenter and
never returns a partial trace: anything it cannot derive exactly becomes
a clearly-labelled INSTRUMENTATION_FAILED trace, the same status the
instrumenter path uses when IT cannot produce a trace.

Security (docs/PHASE_8_4_JDI_TRACE_PARITY.md): cs1302-tracer does not
sandbox the program it runs. It is run here exactly like every other tool -
`run_subprocess` (fixed argv, no shell, timeout, whole-tree kill) inside a
temporary workspace - and its own time/snapshot/output budgets are set
explicitly. The program's input reaches it as a FILE (stdin), never as
generated source text, so no input value can change the harness's code.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from code2shorts.codegen.base import GeneratedCode
from code2shorts.core.models import (
    ExceptionInfo,
    ExecutionTrace,
    SupportedLanguage,
    TraceStatus,
)
from code2shorts.execution.sandbox import Workspace, run_subprocess
from code2shorts.langadapter.java.jdi.derive import DerivationError, SnapshotDiffer
from code2shorts.langadapter.java.trace.limiter import TraceLimiter
from code2shorts.langadapter.java.trace.normalizer import build_events, extract_exception
from code2shorts.langadapter.java.workspace import JavaWorkspace

HARNESS_CLASS = "Code2ShortsJdiHarness"
HARNESS_FILE = f"{HARNESS_CLASS}.java"
MINIMUM_JDK = 21
STRUCTURE_MAIN = "com.code2shorts.instrumenter.SourceStructure"
BACKEND_VERSION = "jdi-cs1302-tracer/derive-1"
_MAIN_SOURCE_ROOT = "src/main/java/"
# `Exception in thread "main" java.lang.ArithmeticException: / by zero`
#  `\tat com.x.Main.divide(Main.java:9)`
#  `\tat java.base/java.lang.Integer.parseInt(Integer.java:672)`  (module prefix)
_UNCAUGHT = re.compile(r'^Exception in thread "main" ([\w.$]+)(?:: (.*))?$')
_FRAME = re.compile(r"^\s+at (?:[\w.]+/)?[\w.$]+\.([\w$<>]+)\([\w$]+\.java:(\d+)\)$")


class JdiBackendUnavailable(Exception):
    """Configuration or tooling is missing. Names what to set; no secrets."""


@dataclass(frozen=True)
class JdiToolchain:
    java: Path
    tracer_jar: Path
    structure_jar: Path

    @classmethod
    def resolve(cls, java_home: str | None, tracer_jar: str | None,
                structure_jar: Path) -> "JdiToolchain":
        if not java_home:
            raise JdiBackendUnavailable(
                "CODE2SHORTS_JDI_JAVA_HOME is not set: the JDI backend needs its own "
                f"JDK {MINIMUM_JDK}+ (the rest of Code2Shorts keeps JDK 17)")
        home = Path(java_home)
        java = next((p for p in (home / "bin" / "java.exe", home / "bin" / "java") if p.is_file()),
                    None)
        if java is None:
            raise JdiBackendUnavailable(f"no java executable under {home / 'bin'}")
        major = jdk_major_version(home)
        if major is None or major < MINIMUM_JDK:
            raise JdiBackendUnavailable(
                f"CODE2SHORTS_JDI_JAVA_HOME points at JDK {major}; cs1302-tracer "
                f"needs JDK {MINIMUM_JDK}+")
        if not tracer_jar or not Path(tracer_jar).is_file():
            raise JdiBackendUnavailable(
                "CODE2SHORTS_JDI_TRACER_JAR must point at the built cs1302-tracer "
                "code-tracer-jar-with-dependencies.jar")
        if not structure_jar.is_file():
            raise JdiBackendUnavailable(
                f"instrumenter jar not found at {structure_jar}. Build it: "
                "cd tools/java-instrumenter && mvn -q package")
        return cls(java=java, tracer_jar=Path(tracer_jar), structure_jar=structure_jar)


def jdk_major_version(java_home: Path) -> int | None:
    """From the JDK's `release` file (`JAVA_VERSION="21.0.11"`). No process
    is started to learn the version."""
    release = java_home / "release"
    if not release.is_file():
        return None
    match = re.search(r'^JAVA_VERSION="(\d+)', release.read_text(encoding="utf-8", errors="replace"),
                      re.MULTILINE)
    return int(match.group(1)) if match else None


def harness_source(entry_class: str) -> str:
    """A generic entry point: read ALL of stdin as the one program argument
    and call the real `main` exactly as JavaExecutor does (`java Main <input>`
    always passes one argument, even an empty one). Contains no input data,
    no algorithm knowledge, and no name but the entry class."""
    package, _, simple = entry_class.rpartition(".")
    header = f"package {package};\n\n" if package else ""
    return (
        f"{header}public final class {HARNESS_CLASS} {{\n"
        "    public static void main(String[] ignored) throws java.io.IOException {\n"
        "        String input = new String(System.in.readAllBytes(),"
        " java.nio.charset.StandardCharsets.UTF_8);\n"
        f"        {simple}.main(new String[] {{input}});\n"
        "    }\n"
        "}\n"
    )


class JdiTraceBackend:
    def __init__(self, toolchain: JdiToolchain, execution_timeout_seconds: float,
                 max_snapshots: int, max_output_bytes: int, max_trace_size_events: int,
                 structure_timeout_seconds: float = 30.0) -> None:
        self._toolchain = toolchain
        self._execution_timeout_seconds = execution_timeout_seconds
        self._max_snapshots = max_snapshots
        self._max_output_bytes = max_output_bytes
        self._limiter = TraceLimiter(max_trace_size_events)
        self._structure_timeout_seconds = structure_timeout_seconds
        self.last_snapshots: list[dict] = []

    def trace(self, code: GeneratedCode, workspace: Workspace, input_value: str) -> ExecutionTrace:
        entry_class = JavaWorkspace.main_class_name(code.entry_point)
        algorithm_name = entry_class.rsplit(".", 1)[-1]

        def failed(message: str) -> ExecutionTrace:
            return ExecutionTrace(
                algorithm_name=algorithm_name, language=SupportedLanguage.JAVA,
                entry_point=entry_class, input=input_value, output="",
                status=TraceStatus.INSTRUMENTATION_FAILED, succeeded=False, exit_code=-1,
                stderr=message, instrumenter_version=BACKEND_VERSION)

        sources = {path: text for path, text in code.source_files.items()
                   if path.replace("\\", "/").startswith(_MAIN_SOURCE_ROOT)
                   and path.endswith(".java")}
        if any(Path(p).name == HARNESS_FILE for p in sources):
            return failed(f"JDI_BACKEND_UNSUPPORTED: a source file is named {HARNESS_FILE}")
        structures = []
        for relative, text in sources.items():
            written = workspace.write_file(relative, text)
            result = run_subprocess(
                ["java", "-cp", str(self._toolchain.structure_jar), STRUCTURE_MAIN, str(written)],
                cwd=workspace.path, timeout_seconds=self._structure_timeout_seconds)
            if result.timed_out or result.returncode != 0:
                return failed(f"JDI_STRUCTURE_FAILED: {relative}: {result.stderr.strip()[:500]}")
            structures.append(json.loads(result.stdout))

        entry_dir = Path(code.entry_point.replace("\\", "/")).parent.as_posix()
        harness = workspace.write_file(f"{entry_dir}/{HARNESS_FILE}", harness_source(entry_class))
        stdin_file = workspace.write_file("code2shorts_jdi_stdin.txt", input_value)

        command = [
            str(self._toolchain.java), "-jar", str(self._toolchain.tracer_jar), "trace",
            "-i", str(harness), "--all-breakpoints", "--format", "modern",
            "--stdin-file", str(stdin_file),
            "--timeout-ms", str(int(self._execution_timeout_seconds * 1000)),
            "--max-snapshots", str(self._max_snapshots),
            "--max-output-bytes", str(self._max_output_bytes),
        ]
        started = time.perf_counter()
        # The outer timeout bounds compilation + JVM start + the tracer's own
        # tracing deadline, and kills the whole tree if the tracer hangs.
        result = run_subprocess(command, cwd=workspace.path,
                                timeout_seconds=self._execution_timeout_seconds + 60.0)
        duration = time.perf_counter() - started

        base = dict(algorithm_name=algorithm_name, language=SupportedLanguage.JAVA,
                    entry_point=entry_class, input=input_value,
                    instrumenter_version=BACKEND_VERSION, duration_seconds=duration)
        if result.timed_out:
            return ExecutionTrace(**base, output="", status=TraceStatus.TIMEOUT,
                                  succeeded=False, exit_code=-1, timed_out=True,
                                  stderr=result.stderr)
        if result.returncode == 3:
            # The tracer's own budget stopped the run (exit 3, documented).
            timeout = "timeout" in result.stderr.lower()
            return ExecutionTrace(
                **base, output="", succeeded=False, exit_code=-1, stderr=result.stderr,
                status=TraceStatus.TIMEOUT if timeout else TraceStatus.TRACE_LIMIT_REACHED,
                timed_out=timeout, truncated=not timeout,
                truncation_reason=None if timeout else result.stderr.strip()[:200])
        if result.returncode != 0:
            return failed(f"JDI_TRACER_FAILED (exit {result.returncode}): "
                          f"{result.stderr.strip()[:1000]}")

        steps = json.loads(result.stdout)["steps"]
        self.last_snapshots = steps
        differ = SnapshotDiffer(structures, HARNESS_FILE)
        try:
            raw = differ.derive(steps)
        except DerivationError as error:
            return failed(f"JDI_DERIVATION_FAILED: {error}")

        guest_out = steps[-1].get("stdout", "") if steps else ""
        guest_err = steps[-1].get("stderr", "") if steps else ""
        exception = _uncaught_exception(guest_err) if differ.ended_inside_user_code(steps) else None
        if differ.ended_inside_user_code(steps) and exception is None:
            return failed("JDI_DERIVATION_FAILED: the run ended inside the program without "
                          "a recognisable uncaught exception")
        if exception is not None:
            # The runtime's depth counter is never decremented for a frame
            # that threw, so both of its final events carry the thrower's depth.
            depth = len(differ.user_frames(steps[-1])) - 1
            raw[-1]["call_depth"] = depth
            raw.insert(len(raw) - 1, {
                "event_type": "EXCEPTION_THROWN",
                "call_depth": depth,
                "exception_class": exception.exception_class,
                "message": exception.message,
                "thrown_at_line": exception.thrown_at_line,
                "thrown_in_method": exception.thrown_in_method,
                "description": f"{exception.exception_class.rsplit('.', 1)[-1]}: "
                               f"{exception.message}",
            })

        limited = self._limiter.apply(raw)
        events = build_events(limited.raw_events)
        exception_info = extract_exception(limited.raw_events)
        if exception_info is not None:
            status, succeeded = TraceStatus.RUNTIME_ERROR, False
        elif limited.truncated:
            status, succeeded = TraceStatus.TRACE_LIMIT_REACHED, False
        else:
            status, succeeded = TraceStatus.COMPLETED, True
        return ExecutionTrace(
            **base,
            output="\n".join(guest_out.splitlines()).strip(),
            status=status, succeeded=succeeded,
            # The JVM's own exit status: 1 after an uncaught exception.
            exit_code=1 if exception_info is not None else 0,
            exception=exception_info, events=events,
            truncated=limited.truncated, truncation_reason=limited.truncation_reason,
            stdout=guest_out, stderr=guest_err,
        )


def _uncaught_exception(stderr: str) -> ExceptionInfo | None:
    """The JVM's default uncaught-exception report, read exactly - or None.
    A message spanning several lines is ambiguous and is not parsed."""
    lines = stderr.splitlines()
    for index, line in enumerate(lines):
        match = _UNCAUGHT.match(line)
        if not match:
            continue
        frame = _FRAME.match(lines[index + 1]) if index + 1 < len(lines) else None
        if frame is None:
            return None
        return ExceptionInfo(
            exception_class=match.group(1),
            message=match.group(2) if match.group(2) is not None else "null",
            thrown_at_line=int(frame.group(2)),
            thrown_in_method=frame.group(1),
        )
    return None

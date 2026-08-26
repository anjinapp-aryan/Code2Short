"""JavaSourceInstrumenter: Python orchestrator for the trusted Java
instrumenter tool (tools/java-instrumenter/). Invokes it via
execution.sandbox.run_subprocess like every other Phase 1/2 subprocess —
no Popen, no shell, no bypass of the existing kill-tree/stdin/timeout
machinery. The instrumenter itself only transforms source text; it never
executes the traced program.
"""

from __future__ import annotations

from pathlib import Path

from code2shorts.codegen.base import GeneratedCode
from code2shorts.execution.sandbox import Workspace, run_subprocess
from code2shorts.langadapter.java.trace.exceptions import (
    InstrumentationFailedError,
    InstrumenterTimeoutError,
)

_MAIN_SOURCE_ROOT = "src/main/java/"
_TRACE_RUNTIME_PATH = "src/main/java/com/code2shorts/trace/Code2ShortsTrace.java"

_RUNTIME_HELPER_SOURCE_FILE = (
    Path(__file__).resolve().parents[1] / "resources" / "Code2ShortsTrace.java"
)


def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "tools" / "java-instrumenter" / "pom.xml").is_file():
            return candidate
    raise FileNotFoundError(
        "could not locate repo root (looked for tools/java-instrumenter/pom.xml "
        "in a parent of langadapter/java/trace/instrumenter.py)"
    )


def _default_jar_path() -> Path:
    return (
        _find_repo_root()
        / "tools"
        / "java-instrumenter"
        / "target"
        / "java-instrumenter-1.0.0.jar"
    )


class JavaSourceInstrumenter:
    def __init__(
        self,
        jar_path: Path | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._jar_path = jar_path or _default_jar_path()
        self._timeout_seconds = timeout_seconds

    def instrument(self, code: GeneratedCode) -> GeneratedCode:
        """Instrument every src/main/java/**/*.java file in `code`, leaving
        anything else (e.g. src/test/java) untouched, then append the
        trusted Code2ShortsTrace runtime helper. Raises
        InstrumentationFailedError / InstrumenterTimeoutError on failure —
        never returns a partially-instrumented GeneratedCode.
        """
        if not self._jar_path.is_file():
            raise InstrumentationFailedError(
                "INSTRUMENTER_NOT_BUILT",
                f"instrumenter jar not found at {self._jar_path}. "
                "Build it: cd tools/java-instrumenter && mvn -q package",
            )

        instrumented_files: dict[str, str] = {}
        with Workspace(prefix="code2shorts_instrument_") as workspace:
            for relative_path, content in code.source_files.items():
                if not self._should_instrument(relative_path):
                    instrumented_files[relative_path] = content
                    continue
                written_path = workspace.write_file(relative_path, content)
                instrumented_files[relative_path] = self._run_instrumenter(
                    written_path, workspace
                )

        instrumented_files[_TRACE_RUNTIME_PATH] = self._runtime_helper_source()

        return GeneratedCode(
            language=code.language,
            source_files=instrumented_files,
            entry_point=code.entry_point,
        )

    def _should_instrument(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        return normalized.startswith(_MAIN_SOURCE_ROOT) and normalized.endswith(".java")

    def _run_instrumenter(self, source_path: Path, workspace: Workspace) -> str:
        result = run_subprocess(
            ["java", "-jar", str(self._jar_path), str(source_path)],
            cwd=workspace.path,
            timeout_seconds=self._timeout_seconds,
        )
        if result.timed_out:
            raise InstrumenterTimeoutError(
                f"instrumenter did not finish within {self._timeout_seconds}s "
                f"for {source_path.name}"
            )
        if result.returncode != 0:
            reason, _, message = result.stderr.strip().partition(": ")
            raise InstrumentationFailedError(reason or "UNKNOWN_ERROR", message or result.stderr)
        return result.stdout

    def _runtime_helper_source(self) -> str:
        return _RUNTIME_HELPER_SOURCE_FILE.read_text(encoding="utf-8")

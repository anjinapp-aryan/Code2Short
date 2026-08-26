from __future__ import annotations

from code2shorts.langadapter.base import CompileResult
from code2shorts.langadapter.java.maven import run_maven
from code2shorts.langadapter.java.workspace import JavaWorkspace


class JavaCompiler:
    def compile(self, java_workspace: JavaWorkspace, timeout_seconds: float) -> CompileResult:
        result = run_maven(["compile"], java_workspace.workspace, timeout_seconds)
        return CompileResult(
            succeeded=(result.returncode == 0 and not result.timed_out),
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=result.duration_seconds,
            timed_out=result.timed_out,
        )

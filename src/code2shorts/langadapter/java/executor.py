from __future__ import annotations

from code2shorts.execution.sandbox import run_subprocess
from code2shorts.langadapter.base import ExecuteResult
from code2shorts.langadapter.java.workspace import JavaWorkspace


class JavaExecutor:
    def run(
        self,
        java_workspace: JavaWorkspace,
        main_class: str,
        input_value: str,
        timeout_seconds: float,
        env: dict[str, str] | None = None,
    ) -> ExecuteResult:
        classes_dir = java_workspace.workspace.path / "target" / "classes"
        command = ["java", "-cp", str(classes_dir), main_class, input_value]
        result = run_subprocess(
            command,
            cwd=java_workspace.workspace.path,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        return ExecuteResult(
            succeeded=(result.returncode == 0 and not result.timed_out),
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            output=result.stdout.strip(),
            duration_seconds=result.duration_seconds,
            timed_out=result.timed_out,
        )

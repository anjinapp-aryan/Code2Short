"""Isolated temporary workspaces and timeout-bounded subprocess execution.

Generated code is untrusted input. Nothing here ever `exec`/`eval`s
generated source inside this process — every run happens as a subprocess,
in its own temp directory, with an explicit timeout.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"
_CREATE_NEW_PROCESS_GROUP = 0x00000200  # subprocess.CREATE_NEW_PROCESS_GROUP


@dataclass
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


class Workspace:
    """A disposable temp directory generated code is written into and run from."""

    def __init__(self, prefix: str = "code2shorts_") -> None:
        self._path = Path(tempfile.mkdtemp(prefix=prefix))

    @property
    def path(self) -> Path:
        return self._path

    def write_file(self, relative_path: str, content: str) -> Path:
        target = self._path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def cleanup(self) -> None:
        shutil.rmtree(self._path, ignore_errors=True)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.cleanup()


def _kill_process_tree(pid: int) -> None:
    """Kill pid and any children it spawned (e.g. mvn.cmd -> java.exe).

    A plain Popen.kill() only terminates the direct child. On Windows that
    child is frequently a cmd.exe wrapper around a .cmd script, whose real
    java.exe grandchild would otherwise be orphaned and keep running.
    """
    if _IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=10,
        )
    else:
        import os
        import signal

        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_subprocess(
    command: list[str],
    cwd: Path,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    """Run `command` in `cwd` as a subprocess, never inside this process.

    stdin is explicitly closed (never inherited) so a child that probes for
    interactive input cannot hang waiting on it. On timeout, the whole
    process tree is killed, not just the direct child.

    `env`, when given, replaces the subprocess's environment entirely
    (standard `subprocess` semantics — pass a full `{**os.environ, ...}`
    dict to extend rather than replace). Phase 2 uses this to hand trace
    limits (C2S_MAX_EVENTS etc.) to a traced JVM as environment variables,
    rather than adding a second pipe-reading mechanism to this function.
    Existing Phase 1 callers omit it and see no behavior change.
    """
    popen_kwargs: dict[str, object] = {}
    if _IS_WINDOWS:
        popen_kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP
    else:
        import os

        popen_kwargs["start_new_session"] = True  # own process group for killpg

    started_at = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_kwargs,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        duration_seconds = time.monotonic() - started_at
        return ProcessResult(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            duration_seconds=duration_seconds,
        )
    except subprocess.TimeoutExpired:
        _kill_process_tree(process.pid)
        stdout, stderr = process.communicate()
        duration_seconds = time.monotonic() - started_at
        return ProcessResult(
            returncode=-1,
            stdout=stdout or "",
            stderr=(stderr or "") + f"\n[timed out after {timeout_seconds}s]",
            timed_out=True,
            duration_seconds=duration_seconds,
        )

"""Phase 8.4.1: run_subprocess must leave no process of its execution behind.

Phase 8.4 found that when the JDI tracer dies abnormally, the JVM it was
tracing keeps running: the timeout tree-kill only fires on a timeout, and a
crashed tracer is not a timeout. These tests build real process trees out of
a tiny Python helper (no JDI, no network, no external tools):

    run_subprocess
      -> "tracer" (the direct child)
           -> "program" (a grandchild with its own stdio, as a JDI guest has)
                -> optional further descendants

Each descendant writes its pid to a file before the direct child is allowed
to exit, so liveness checks never race process start-up.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from code2shorts.execution import sandbox
from code2shorts.execution.sandbox import run_subprocess

IS_WINDOWS = sys.platform == "win32"

HELPER = r'''
import os, signal, subprocess, sys, time

role, pid_dir, depth, ending = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
if depth > 0:
    # A descendant gets its own stdio, as the JDI guest does: our pipes must
    # not be what keeps run_subprocess waiting.
    subprocess.Popen(
        [sys.executable, __file__, "descendant", pid_dir, str(depth - 1), "sleep"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
with open(os.path.join(pid_dir, f"{role}-{depth}.pid"), "w") as f:
    f.write(str(os.getpid()))
if role == "tracer":
    # Wait until the whole chain below has recorded its pid.
    want = int(sys.argv[5])
    while len([n for n in os.listdir(pid_dir) if n.endswith(".pid")]) < want:
        time.sleep(0.01)
    print("tree ready", flush=True)
    if ending == "exit0":
        sys.exit(0)
    if ending == "exit3":
        sys.exit(3)
    if ending == "crash":
        os.kill(os.getpid(), signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
time.sleep(120)
'''


def _alive(pid: int) -> bool:
    """True while `pid` is a running (not zombie) process."""
    if IS_WINDOWS:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def _kill(pid: int) -> None:
    """Test-side cleanup of one pid this test created (never by name)."""
    if not _alive(pid):
        return
    if IS_WINDOWS:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(ctypes.c_void_p(handle), 1)
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    else:
        import signal

        os.kill(pid, signal.SIGKILL)


class Tree:
    def __init__(self, root: Path, depth: int = 1) -> None:
        self.depth = depth
        self.pid_dir = root / "pids"
        self.pid_dir.mkdir()
        self.helper = root / "tree_helper.py"
        self.helper.write_text(HELPER, encoding="utf-8")

    def command(self, ending: str) -> list[str]:
        return [sys.executable, str(self.helper), "tracer", str(self.pid_dir),
                str(self.depth), ending, str(self.depth + 1)]

    def pids(self) -> dict[str, int]:
        return {p.stem: int(p.read_text()) for p in self.pid_dir.glob("*.pid") if p.read_text()}

    def descendants(self) -> list[int]:
        return [pid for name, pid in self.pids().items() if name.startswith("descendant")]

    def leftovers(self) -> list[int]:
        return [pid for pid in self.pids().values() if _alive(pid)]

    def cleanup(self) -> None:
        for pid in self.pids().values():
            _kill(pid)


@pytest.fixture
def tree(tmp_path: Path):
    made = Tree(tmp_path)
    yield made
    made.cleanup()  # even when an assertion failed


def test_normal_completion_leaves_no_descendant(tree: Tree, tmp_path: Path) -> None:
    result = run_subprocess(tree.command("exit0"), cwd=tmp_path, timeout_seconds=30)
    assert (result.returncode, result.timed_out) == (0, False)
    assert "tree ready" in result.stdout
    assert tree.descendants(), "the helper never started its descendant"
    assert tree.leftovers() == []


def test_non_zero_exit_is_reported_and_leaves_no_descendant(tree: Tree, tmp_path: Path) -> None:
    result = run_subprocess(tree.command("exit3"), cwd=tmp_path, timeout_seconds=30)
    assert (result.returncode, result.timed_out) == (3, False)
    assert tree.leftovers() == []


def test_timeout_kills_the_whole_tree(tree: Tree, tmp_path: Path) -> None:
    result = run_subprocess(tree.command("hang"), cwd=tmp_path, timeout_seconds=3)
    assert result.timed_out and result.returncode == -1
    assert "tree ready" in result.stdout
    assert tree.leftovers() == []


def test_tracer_crash_does_not_orphan_the_traced_program(tree: Tree, tmp_path: Path) -> None:
    """The Phase 8.4 finding: the direct child dies abnormally; its child
    must not survive it."""
    result = run_subprocess(tree.command("crash"), cwd=tmp_path, timeout_seconds=30)
    assert not result.timed_out and result.returncode != 0
    assert tree.descendants()
    assert tree.leftovers() == []


def test_nested_descendants_are_all_terminated(tmp_path: Path) -> None:
    deep = Tree(tmp_path, depth=3)  # tracer -> child -> grandchild -> great-grandchild
    try:
        run_subprocess(deep.command("crash"), cwd=tmp_path, timeout_seconds=30)
        assert len(deep.descendants()) == 3
        assert deep.leftovers() == []
    finally:
        deep.cleanup()


def test_python_side_exception_still_kills_the_tree(
    tree: Tree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = subprocess.Popen.communicate
    calls = {"n": 0}

    def failing_once(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            deadline = time.monotonic() + 20
            while len(tree.pids()) < tree.depth + 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            raise RuntimeError("injected wrapper failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", failing_once)
    with pytest.raises(RuntimeError, match="injected wrapper failure"):
        run_subprocess(tree.command("hang"), cwd=tmp_path, timeout_seconds=30)
    assert len(tree.pids()) == 2
    assert tree.leftovers() == []


def test_unrelated_process_with_the_same_executable_survives(tree: Tree, tmp_path: Path) -> None:
    """Cleanup is scoped to this execution, never by executable name."""
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        run_subprocess(tree.command("crash"), cwd=tmp_path, timeout_seconds=30)
        assert tree.descendants()
        assert tree.leftovers() == []
        assert unrelated.poll() is None, "an unrelated process was killed"
        assert _alive(unrelated.pid)
    finally:
        unrelated.kill()
        unrelated.wait()


def test_repeated_executions_leave_no_orphans(tmp_path: Path) -> None:
    left: list[int] = []
    for i in range(10):
        run_dir = tmp_path / f"run{i}"
        run_dir.mkdir()
        each = Tree(run_dir)
        try:
            result = run_subprocess(each.command("crash" if i % 2 else "exit0"),
                                    cwd=run_dir, timeout_seconds=30)
            assert not result.timed_out
            assert each.descendants()
            left += each.leftovers()
        finally:
            each.cleanup()
    assert left == []


def test_stdout_stderr_cwd_env_and_stdin_semantics_are_unchanged(tmp_path: Path) -> None:
    script = ("import os, sys; print(os.getcwd()); print(os.environ['C2S_PROBE']);"
              "sys.stderr.write('err'); print(repr(sys.stdin.read())); sys.exit(7)")
    result = run_subprocess([sys.executable, "-c", script], cwd=tmp_path, timeout_seconds=30,
                            env={**os.environ, "C2S_PROBE": "value"})
    assert result.returncode == 7 and not result.timed_out
    lines = result.stdout.splitlines()
    assert Path(lines[0]).resolve() == tmp_path.resolve()
    assert lines[1:] == ["value", "''"]
    assert result.stderr == "err"


GUEST = """import java.nio.file.Files;
import java.nio.file.Path;

public class Guest {
    public static void main(String[] args) throws Exception {
        Files.writeString(Path.of("guest.pid"), Long.toString(ProcessHandle.current().pid()));
        ProcessHandle.current().parent().ifPresent(ProcessHandle::destroyForcibly);
        long n = 0;
        while (true) { n++; }
    }
}
"""


@pytest.mark.integration
@pytest.mark.skipif(
    not (os.environ.get("CODE2SHORTS_JDI_JAVA_HOME") and os.environ.get("CODE2SHORTS_JDI_TRACER_JAR")),
    reason="JDI backend not configured (CODE2SHORTS_JDI_JAVA_HOME + CODE2SHORTS_JDI_TRACER_JAR)",
)
def test_real_tracer_crash_does_not_orphan_the_guest_jvm(tmp_path: Path) -> None:
    """The exact Phase 8.4 scenario with the real cs1302-tracer: the guest
    JVM force-kills its own tracer (an abnormal tracer death), then spins."""
    (tmp_path / "Guest.java").write_text(GUEST, encoding="utf-8")
    java = Path(os.environ["CODE2SHORTS_JDI_JAVA_HOME"]) / "bin" / ("java.exe" if IS_WINDOWS else "java")
    command = [str(java), "-jar", os.environ["CODE2SHORTS_JDI_TRACER_JAR"], "trace",
               "-i", "Guest.java", "-a", "-f", "modern", "--unlimited"]
    guest_pid = None
    try:
        result = run_subprocess(command, cwd=tmp_path, timeout_seconds=60)
        guest_pid = int((tmp_path / "guest.pid").read_text())
        assert not result.timed_out, "the tracer's death was not noticed"
        assert result.returncode != 0
        assert not _alive(guest_pid), "the guest JVM outlived its crashed tracer"
    finally:
        if guest_pid is None and (tmp_path / "guest.pid").exists():
            guest_pid = int((tmp_path / "guest.pid").read_text())
        if guest_pid is not None:
            _kill(guest_pid)


def test_cleanup_never_matches_processes_by_name() -> None:
    """AST check: no killall/pkill and no taskkill /IM anywhere in the seam."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sandbox))
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not strings & {"killall", "pkill", "/IM"}

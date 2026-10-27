"""Isolated temporary workspaces and timeout-bounded subprocess execution.

Generated code is untrusted input. Nothing here ever `exec`/`eval`s
generated source inside this process — every run happens as a subprocess,
in its own temp directory, with an explicit timeout.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

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


_CREATE_SUSPENDED = 0x00000004
_REAP_SECONDS = 5.0  # bound on waiting for a killed tree to be gone


class _WindowsJob:
    """A Windows Job Object holding one execution's whole process tree.

    Phase 8.4.1: taskkill /T walks parent pids, so it cannot find a process
    whose parent already died (a crashed JDI tracer's guest JVM). A job
    tracks membership instead: every descendant joins it at creation and
    cannot leave, and closing the last handle kills whatever is left
    (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE), even if this Python process dies.
    The child is started suspended and assigned before it runs a single
    instruction, so it cannot spawn anything outside the job.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo", ctypes.c_uint64 * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class _ProcessIdList(ctypes.Structure):
            _fields_ = [
                ("NumberOfAssignedProcesses", wintypes.DWORD),
                ("NumberOfProcessIdsInList", wintypes.DWORD),
                ("ProcessIdList", ctypes.c_size_t * 1024),
            ]

        self._ctypes = ctypes
        self._process_id_list = _ProcessIdList
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        self._kernel32 = kernel32
        self._ntdll = ntdll

        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)  # ExtendedLimitInformation
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def adopt_and_resume(self, pid: int) -> None:
        """Assign the suspended child to the job, then let it run."""
        ctypes = self._ctypes
        access = 0x0100 | 0x0001 | 0x0800  # SET_QUOTA | TERMINATE | SUSPEND_RESUME
        process = self._kernel32.OpenProcess(access, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
            if self._ntdll.NtResumeProcess(process) != 0:
                raise OSError("NtResumeProcess failed")
        finally:
            self._kernel32.CloseHandle(process)

    def terminate(self) -> None:
        """Kill every process in the job and wait until each one is gone.

        The job's own active-process count drops before a killed process
        has actually exited, so this waits on the processes themselves:
        handles opened before the kill, which also pin their pids.
        """
        if not self._handle:
            return
        kernel32, ctypes = self._kernel32, self._ctypes
        listing = self._process_id_list()
        handles = []
        if kernel32.QueryInformationJobObject(
            self._handle, 3, ctypes.byref(listing), ctypes.sizeof(listing), None  # ProcessIdList
        ):
            for pid in listing.ProcessIdList[: listing.NumberOfProcessIdsInList]:
                handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
                if handle:
                    handles.append(handle)
        try:
            kernel32.TerminateJobObject(self._handle, 1)
            deadline = time.monotonic() + _REAP_SECONDS
            for handle in handles:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                kernel32.WaitForSingleObject(handle, remaining_ms)
        finally:
            for handle in handles:
                kernel32.CloseHandle(handle)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _kill_process_tree(process: subprocess.Popen, job: _WindowsJob | None = None) -> None:
    """Kill pid and any children it spawned (e.g. mvn.cmd -> java.exe).

    A plain Popen.kill() only terminates the direct child. On Windows that
    child is frequently a cmd.exe wrapper around a .cmd script, whose real
    java.exe grandchild would otherwise be orphaned and keep running.

    Phase 8.4.1: this now runs after EVERY exit, not only a timeout, and
    must also reach descendants whose parent already exited. On Windows the
    job does that; taskkill /T is kept only for when no job could be made.
    On POSIX the child leads its own process group (start_new_session), and
    descendants stay in it after their parent dies, so killpg reaches them.
    """
    if _IS_WINDOWS:
        if job is not None:
            job.terminate()
            return
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            timeout=10,
        )
    else:
        import os
        import signal

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return
        # Reap the direct child (a zombie still counts as a group member),
        # then wait for the group to empty. Killed descendants are
        # reparented and reaped by init (or a subreaper), not by us.
        process.wait()
        deadline = time.monotonic() + _REAP_SECONDS
        while time.monotonic() < deadline:
            try:
                os.killpg(process.pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.01)


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

    Phase 8.4.1: the tree is killed after every exit, not only a timeout,
    so a descendant cannot outlive its parent (a crashed JDI tracer used to
    leave its guest JVM running). The tree is the job (Windows) or process
    group (POSIX) this call created, never anything matched by name.
    """
    popen_kwargs: dict[str, object] = {}
    job: _WindowsJob | None = None
    if _IS_WINDOWS:
        try:
            job = _WindowsJob()
        except OSError as error:
            logger.warning("no job object (%s); falling back to taskkill /T", error)
        popen_kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP | (
            _CREATE_SUSPENDED if job is not None else 0
        )
    else:
        popen_kwargs["start_new_session"] = True  # own process group for killpg

    started_at = time.monotonic()
    try:
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
    except BaseException:
        if job is not None:
            job.close()
        raise
    try:
        if job is not None:
            job.adopt_and_resume(process.pid)
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process, job)
            stdout, stderr = process.communicate()
            duration_seconds = time.monotonic() - started_at
            return ProcessResult(
                returncode=-1,
                stdout=stdout or "",
                stderr=(stderr or "") + f"\n[timed out after {timeout_seconds}s]",
                timed_out=True,
                duration_seconds=duration_seconds,
            )
        duration_seconds = time.monotonic() - started_at
        return ProcessResult(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            duration_seconds=duration_seconds,
        )
    finally:
        # Every exit path, including an exception raised in this function:
        # nothing the command started may outlive the call.
        _kill_process_tree(process, job)
        if process.poll() is None:
            process.kill()
            process.wait()
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
        if job is not None:
            job.close()

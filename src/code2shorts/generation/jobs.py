"""Job tracking: what the pipeline is actually doing, right now.

Progress is not estimated. `WorkflowRunner` already emits a typed
`WorkflowEvent` when each node starts, completes or fails, so a job's
stage list is a projection of those events and nothing else. Nothing here
guesses a percentage from elapsed time, and a stage cannot show as
complete unless the runner said so.

Concurrency is deliberately modest: one background thread per job, a lock
around the job table, and a bounded log. Generation is minutes of CPU-bound
Maven/Manim/FFmpeg work in subprocesses, so a thread pool buys nothing that
matters and a queue is not yet justified.

    NOT SUITABLE FOR A SERVERLESS DEPLOYMENT AS WRITTEN.

That is stated rather than designed around. On Render this runs as one
service; on a platform that freezes between requests, this class is the
seam to replace with a queue - the API talks to `JobManager`, never to a
thread, so the replacement does not reach the UI. See docs/PHASE_7_UI.md.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from code2shorts.generation.manager import STAGE_LABELS, GenerationManager, GenerationRequest
from code2shorts.generation.registry import GenerationStatus
from code2shorts.workflow import WorkflowEvent, WorkflowEventType

MAX_LOG_LINES = 500
"""Bounded so a long run cannot grow memory without limit. The full record
of a run is its artifacts; this is a tail for the Logs tab."""


class StageState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Stage(BaseModel):
    name: str
    label: str
    state: StageState = StageState.QUEUED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    detail: str | None = None


class Job(BaseModel):
    id: str
    algorithm: str
    title: str
    video_format: str = ""
    """The request's `TeachingConfig.video_format`, recorded per job so two
    jobs for one program in different formats stay distinguishable."""
    request_digest: str = ""
    """The request fingerprint's digest: what makes two requests the same
    video (and therefore the same job)."""
    status: GenerationStatus = GenerationStatus.QUEUED
    stages: list[Stage]
    version: int | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    logs: list[str] = Field(default_factory=list)
    cancel_requested: bool = False

    @property
    def progress(self) -> int:
        """Percent complete, from COMPLETED stages only.

        A running stage contributes nothing. Counting it as half would
        make the bar move without anything having finished, which is the
        fake progress this design rejects.
        """
        if not self.stages:
            return 0
        done = sum(1 for stage in self.stages if stage.state is StageState.COMPLETED)
        return round(done * 100 / len(self.stages))

    @property
    def current_stage(self) -> Stage | None:
        for stage in self.stages:
            if stage.state is StageState.RUNNING:
                return stage
        return None


class JobManager:
    """Owns running jobs. The API talks to this, never to a thread."""

    def __init__(self, manager: GenerationManager) -> None:
        self._manager = manager
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    # ---- reading ---------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def active(self) -> list[Job]:
        with self._lock:
            return [
                job.model_copy(deep=True)
                for job in self._jobs.values()
                if job.status in (GenerationStatus.QUEUED, GenerationStatus.RUNNING)
            ]

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            jobs = sorted(
                self._jobs.values(), key=lambda job: job.created_at, reverse=True
            )
            return [job.model_copy(deep=True) for job in jobs[:limit]]

    def job_for_algorithm(self, algorithm: str) -> Job | None:
        """The running job for a program, if any - so the UI can offer
        "view progress" instead of a second Generate button."""
        for job in self.active():
            if job.algorithm == algorithm:
                return job
        return None

    # ---- starting --------------------------------------------------------

    def start(self, request: GenerationRequest, force: bool = False) -> Job:
        """Begin a generation in the background.

        Refuses a second concurrent job for the SAME REQUEST (same request
        fingerprint): it would produce the same video twice. Different
        requests for one program - 9:16 and 16:9, say - are different
        videos and each gets its own job; since Phase 8.1 the registry
        allocates each its own version directory atomically, so they no
        longer race for one. Deduplicating by program alone handed a 16:9
        request the running 9:16 job (Phase 8.2D).
        """
        digest = self._manager.fingerprint(request).digest
        # Check and insert under ONE lock hold: checked outside it, two
        # simultaneous clicks could both see "no job" and both start one.
        with self._lock:
            for running in self._jobs.values():
                if running.request_digest == digest and running.status in (
                    GenerationStatus.QUEUED,
                    GenerationStatus.RUNNING,
                ):
                    return running.model_copy(deep=True)

            job = Job(
                id=uuid.uuid4().hex,
                algorithm=request.algorithm,
                title=request.title,
                video_format=request.config.video_format,
                request_digest=digest,
                stages=[
                    Stage(name=name, label=label) for name, label in STAGE_LABELS.items()
                ],
            )
            self._jobs[job.id] = job
            thread = threading.Thread(
                target=self._run, args=(job.id, request, force), daemon=True,
                name=f"generate-{request.algorithm}",
            )
            self._threads[job.id] = thread
        thread.start()
        return job.model_copy(deep=True)

    def request_cancel(self, job_id: str) -> bool:
        """Ask a job to stop at the next stage boundary.

        Cooperative, not forced. Killing a thread mid-render would leave a
        half-written MP4 and orphaned Maven/Manim subprocesses; stopping
        between nodes leaves the version directory in a state the registry
        can honestly describe.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in (
                GenerationStatus.QUEUED,
                GenerationStatus.RUNNING,
            ):
                return False
            job.cancel_requested = True
            return True

    # ---- the background run ---------------------------------------------

    def _run(self, job_id: str, request: GenerationRequest, force: bool) -> None:
        self._update(job_id, status=GenerationStatus.RUNNING)

        def on_event(event: WorkflowEvent) -> None:
            self._apply_event(job_id, event)

        try:
            version = self._manager.generate(request, on_event=on_event, force=force)
        except Exception as error:  # noqa: BLE001 - the job records failures
            self._update(
                job_id,
                status=GenerationStatus.FAILED,
                error=f"{type(error).__name__}: {error}",
                finished=True,
            )
            return

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.version = version.version
            job.status = version.status
            job.error = version.error
            job.finished_at = datetime.now(UTC)
            if version.status is GenerationStatus.COMPLETED:
                # A reused version completes without emitting any event,
                # so the stage list would otherwise stay all-queued behind
                # a 100%-complete job.
                for stage in job.stages:
                    if stage.state is StageState.QUEUED:
                        stage.state = StageState.COMPLETED

    def _apply_event(self, job_id: str, event: WorkflowEvent) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._log(job, f"{event.type.value} {event.node_name or ''}".strip())

            if event.node_name is None:
                return
            stage = next((s for s in job.stages if s.name == event.node_name), None)
            if stage is None:
                return

            if event.type is WorkflowEventType.NODE_STARTED:
                stage.state = StageState.RUNNING
                stage.started_at = event.timestamp
            elif event.type is WorkflowEventType.NODE_COMPLETED:
                stage.state = StageState.COMPLETED
                stage.completed_at = event.timestamp
            elif event.type is WorkflowEventType.NODE_FAILED:
                stage.state = StageState.FAILED
                stage.completed_at = event.timestamp
                # Event data can carry provider text; the message is kept
                # short and never assumed to be safe to render as markup.
                stage.detail = str(event.data.get("error", ""))[:300] or None

    @staticmethod
    def _log(job: Job, line: str) -> None:
        stamped = f"{datetime.now(UTC).strftime('%H:%M:%S')}  {line}"
        trimmed = deque(job.logs, maxlen=MAX_LOG_LINES)
        trimmed.append(stamped)
        job.logs = list(trimmed)

    def _update(
        self,
        job_id: str,
        status: GenerationStatus | None = None,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if status is not None:
                job.status = status
            if error is not None:
                job.error = error
            if finished:
                job.finished_at = datetime.now(UTC)

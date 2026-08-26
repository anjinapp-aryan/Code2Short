"""Checkpoint abstraction: save/load/resume. A real distributed checkpoint
store (e.g. backed by a database) is future work — this defines the
interface future systems implement, plus one in-memory implementation
sufficient for local dev/tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from code2shorts.workflow.state import Code2ShortsState


class Checkpoint(BaseModel):
    workflow_id: str
    execution_id: str
    last_completed_node: str
    state: Code2ShortsState
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CheckpointStore(ABC):
    @abstractmethod
    def save(self, checkpoint: Checkpoint) -> None: ...

    @abstractmethod
    def load_latest(self, workflow_id: str, execution_id: str) -> Checkpoint | None: ...


class InMemoryCheckpointStore(CheckpointStore):
    def __init__(self) -> None:
        self._latest: dict[tuple[str, str], Checkpoint] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        self._latest[(checkpoint.workflow_id, checkpoint.execution_id)] = checkpoint

    def load_latest(self, workflow_id: str, execution_id: str) -> Checkpoint | None:
        return self._latest.get((workflow_id, execution_id))

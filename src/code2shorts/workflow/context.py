"""WorkflowContext: per-run execution scratch (category C, "transient
execution state" from the state-design split in state.py). NOT persisted,
NOT part of Code2ShortsState — a node needs a handle to the artifact
store and a way to emit events, but that's plumbing, not workflow data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from code2shorts.artifacts.store import ArtifactStore
from code2shorts.workflow.events import WorkflowEvent


@dataclass
class WorkflowContext:
    artifact_store: ArtifactStore
    workflow_id: str
    execution_id: str
    emit: Callable[[WorkflowEvent], None]
    attempt: int = 1

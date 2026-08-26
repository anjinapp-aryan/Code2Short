"""Failure classification. RetryPolicy decides what to do based on this,
never on string-matching an error message.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class FailureKind(StrEnum):
    DETERMINISTIC = "deterministic"
    """The traced/compiled program itself is wrong (bad Java, failing
    trace). Retrying with the same input will fail identically every
    time — never retried automatically."""

    TRANSIENT = "transient"
    """An environment hiccup (a subprocess timeout that might not recur,
    a flaky network call). Safe to retry."""

    PERMANENT = "permanent"
    """A configuration/setup problem (missing Maven, missing jar, bad
    settings). Retrying without fixing the setup will fail identically —
    never retried automatically."""

    VALIDATION = "validation"
    """AI output failed schema or semantic validation (e.g. it referenced
    a TraceEvent index that doesn't exist). Not automatically retried in
    Phase 3 — a future phase may retry with a repair prompt."""


class WorkflowError(BaseModel):
    node_name: str
    kind: FailureKind
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 1


class NodeExecutionError(Exception):
    """Raised by WorkflowNode.run() to signal failure. The runner catches
    this, classifies via `kind`, and decides whether to retry.
    """

    def __init__(self, kind: FailureKind, message: str, detail: dict[str, Any] | None = None) -> None:
        self.kind = kind
        self.message = message
        self.detail = detail or {}
        super().__init__(f"{kind.value}: {message}")

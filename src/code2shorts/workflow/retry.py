"""Lightweight RetryPolicy — no distributed retry infrastructure.

Deterministic and permanent failures are never retried, regardless of
policy, by construction: `should_retry` only consults `retryable_kinds`,
and callers are expected to leave FailureKind.DETERMINISTIC/PERMANENT out
of it (the default policy does exactly that).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from code2shorts.workflow.errors import FailureKind


class BackoffStrategy(StrEnum):
    NONE = "none"
    FIXED = "fixed"
    EXPONENTIAL = "exponential"


class RetryPolicy(BaseModel):
    max_attempts: int = 1
    retryable_kinds: set[FailureKind] = Field(default_factory=lambda: {FailureKind.TRANSIENT})
    backoff: BackoffStrategy = BackoffStrategy.NONE
    backoff_seconds: float = 0.0

    def should_retry(self, kind: FailureKind, attempt: int) -> bool:
        return kind in self.retryable_kinds and attempt < self.max_attempts

    def delay_for_attempt(self, attempt: int) -> float:
        if self.backoff == BackoffStrategy.NONE:
            return 0.0
        if self.backoff == BackoffStrategy.FIXED:
            return self.backoff_seconds
        return self.backoff_seconds * (2 ** (attempt - 1))


NO_RETRY = RetryPolicy(max_attempts=1)

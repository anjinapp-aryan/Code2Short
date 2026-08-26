"""Exceptions raised by the Phase 2 trace pipeline.

Every one of these maps to ExecutionTrace.status == INSTRUMENTATION_FAILED
in JavaAdapter.trace() — never a fabricated/partial trace, never confused
with a RUNTIME_ERROR (which means the traced *program* failed, not our
tooling).
"""

from __future__ import annotations


class InstrumentationFailedError(Exception):
    """The instrumenter tool could not produce instrumented source: a parse
    error, a denylisted/unsupported construct, or an internal instrumenter
    failure. `reason` is the short classifier the Java tool printed
    ("PARSE_ERROR", "UNSUPPORTED_CONSTRUCT", etc.); `message` is the detail.
    """

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(f"{reason}: {message}")


class InstrumenterTimeoutError(Exception):
    """The instrumenter tool itself did not finish within
    instrumenter_timeout_seconds. Distinct from a traced *program* timeout
    (TraceStatus.TIMEOUT) — this is our own trusted tooling hanging on a
    hostile/pathological input, not the traced algorithm running long.
    """

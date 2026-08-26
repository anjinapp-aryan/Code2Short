"""TraceLimiter: Python-side belt-and-suspenders cap on trace size.

The primary enforcement of max_events/max_loop_iterations/max_call_depth
happens INSIDE the JVM (Code2ShortsTrace's counters, driven by env vars —
see JavaExecutor/JavaAdapter.trace()), so a runaway program is killed fast
regardless of what this class does. This class exists for the case where
the JVM-side counters were configured more loosely than the Python-side
budget, or simply as a second independent check — never the only line of
defense.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LimitedEvents:
    raw_events: list[dict]
    truncated: bool
    truncation_reason: str | None


class TraceLimiter:
    def __init__(self, max_trace_size_events: int) -> None:
        self._max_trace_size_events = max_trace_size_events

    def apply(self, raw_events: list[dict]) -> LimitedEvents:
        if len(raw_events) > self._max_trace_size_events:
            return LimitedEvents(
                raw_events=raw_events[: self._max_trace_size_events],
                truncated=True,
                truncation_reason="max_trace_size",
            )
        return LimitedEvents(raw_events=raw_events, truncated=False, truncation_reason=None)

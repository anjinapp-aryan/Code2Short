"""TraceNormalizer: turns raw TRACE: JSON dicts into canonical TraceEvent
objects with a valid, sequential step_index — the one invariant
ExecutionTrace's model validator enforces and this module must never
violate, truncation or not.
"""

from __future__ import annotations

from code2shorts.core.models import ExceptionInfo, TraceEvent

_TRACE_LIMIT_EXCEPTION_CLASS = "com.code2shorts.trace.Code2ShortsTrace$TraceLimitExceededException"


def is_trace_limit_exception(exception_class: str) -> bool:
    return exception_class == _TRACE_LIMIT_EXCEPTION_CLASS


def build_events(raw_events: list[dict]) -> list[TraceEvent]:
    """Convert raw dicts (already in emission order) into TraceEvents with
    a fresh, sequential step_index. Call this AFTER any truncation — the
    input list here is exactly what ends up in ExecutionTrace.events.
    """
    events: list[TraceEvent] = []
    for step_index, raw in enumerate(raw_events):
        events.append(
            TraceEvent(
                step_index=step_index,
                event_type=raw.get("event_type", ""),
                line_number=raw.get("line_number", raw.get("thrown_at_line")),
                call_depth=raw.get("call_depth", 0),
                method=raw.get("method", raw.get("thrown_in_method")),
                variable_name=raw.get("variable_name"),
                old_value=raw.get("old_value"),
                new_value=raw.get("new_value"),
                return_value=raw.get("return_value"),
                iteration=raw.get("iteration"),
                condition_result=raw.get("condition_result"),
                collection_kind=raw.get("collection_kind"),
                collection_operation=raw.get("collection_operation"),
                collection_ordered=raw.get("collection_ordered"),
                collection_size=raw.get("collection_size"),
                collection_keys=raw.get("collection_keys"),
                collection_values=raw.get("collection_values"),
                description=raw.get("description", ""),
            )
        )
    return events


def extract_exception(raw_events: list[dict]) -> ExceptionInfo | None:
    """The EXCEPTION_THROWN event (if any) carries the fields needed to
    populate ExecutionTrace.exception. Preserved separately from — not
    instead of — its place in the events list.
    """
    for raw in raw_events:
        if raw.get("event_type") == "EXCEPTION_THROWN":
            return ExceptionInfo(
                exception_class=raw.get("exception_class", ""),
                message=raw.get("message", ""),
                thrown_at_line=raw.get("thrown_at_line"),
                thrown_in_method=raw.get("thrown_in_method"),
            )
    return None

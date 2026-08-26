"""Reusable helpers for Phase 2 trace tests."""

from __future__ import annotations

from code2shorts.core.models import ExecutionTrace


def assert_traces_equivalent(a: ExecutionTrace, b: ExecutionTrace) -> None:
    """Determinism equality: everything except duration_seconds (the one
    field explicitly allowed to vary run-to-run) must match exactly,
    including event ordering, values, line numbers, and call depth.
    """
    dump_a = a.model_dump(exclude={"duration_seconds"})
    dump_b = b.model_dump(exclude={"duration_seconds"})
    assert dump_a == dump_b

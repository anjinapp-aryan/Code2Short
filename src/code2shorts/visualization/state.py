"""Reconstructs algorithm state at every point in an ExecutionTrace.

This is the whole Code2Shorts-specific mapping layer, and it is
deliberately **algorithm-agnostic**: nothing here knows what "reverse
string" or "two sum" is. It knows only what the trace literally reports —
arrays, their contents, scalar variables, and which cells were touched —
and derives everything else from those facts.

Why reconstruction is needed: the trace records array *mutations*
(`ARRAY_WRITE nums[0] = 7`), not array *snapshots*. Rendering tiles
requires the full contents at each step, so we seed from the array's
initial value and replay every write in order. That keeps the trace
minimal (Phase 2's bounded-size guarantee) while still giving the renderer
complete state.

The pointer rule (see `FrameState.pointers`) is the one piece of
inference, and it is a pure data rule with no algorithm knowledge in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from code2shorts.core.models import ExecutionTrace, TraceEventType

# "chars[3]" -> ("chars", 3). The trace combines array name and index into
# one variable_name string; this is where they come apart again.
_INDEXED_NAME = re.compile(r"^(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\[(?P<index>\d+)\]$")

# "[2, 7, 11, 15]" — how Code2ShortsTrace.repr renders a non-char array.
_BRACKETED_LIST = re.compile(r"^\[(?P<body>.*)\]$", re.DOTALL)


def parse_indexed_name(variable_name: str | None) -> tuple[str, int] | None:
    if not variable_name:
        return None
    match = _INDEXED_NAME.match(variable_name)
    if match is None:
        return None
    return match.group("name"), int(match.group("index"))


def parse_array_value(value: str | None) -> list[str] | None:
    """Interpret a traced value as array contents, or None if it isn't one.

    Two shapes reach us, both produced by `Code2ShortsTrace.repr`:
      - "[2, 7, 11, 15]"  (Arrays.toString — int[]/long[]/Object[]/...)
      - "HELLO"           (char[], where String.valueOf returns the chars)
    """
    if value is None:
        return None
    match = _BRACKETED_LIST.match(value.strip())
    if match is None:
        return None
    body = match.group("body").strip()
    if not body:
        return []
    return [cell.strip() for cell in body.split(",")]


@dataclass
class ArraySnapshot:
    name: str
    cells: list[str]

    def copy(self) -> ArraySnapshot:
        return ArraySnapshot(name=self.name, cells=list(self.cells))


@dataclass
class Pointer:
    """A scalar variable currently addressing a cell of an array."""

    name: str
    array_name: str
    index: int


@dataclass
class FrameState:
    """Complete, renderable state at one trace event."""

    step_index: int
    event_type: str
    line_number: int | None
    description: str
    arrays: dict[str, ArraySnapshot] = field(default_factory=dict)
    scalars: dict[str, str] = field(default_factory=dict)
    read_indices: dict[str, list[int]] = field(default_factory=dict)
    written_indices: dict[str, list[int]] = field(default_factory=dict)
    changed_scalar: str | None = None

    @property
    def primary_array(self) -> ArraySnapshot | None:
        """The array this frame is about — the one just touched, else the
        only/first one. Ordering is deterministic (insertion order), so the
        same trace always yields the same choice."""
        for name in list(self.written_indices) + list(self.read_indices):
            if name in self.arrays:
                return self.arrays[name]
        return next(iter(self.arrays.values()), None)

    @property
    def pointers(self) -> list[Pointer]:
        """Scalars that currently address a valid cell of an array.

        The rule is purely structural: an integer-valued scalar whose value
        is a valid index into an array is shown pointing at that cell. No
        algorithm-specific naming (`left`/`right`/`i`/`slow`) is hardcoded,
        which is what lets one implementation serve every supported
        algorithm.

        Known false positive: a counter that merely happens to hold an
        in-range number (e.g. a tally) renders as a pointer. It still shows
        the variable's true value, so nothing displayed is ever wrong — the
        annotation is just more prominent than it deserves. Documented in
        the Phase 4.2 report rather than papered over with heuristics.
        """
        array = self.primary_array
        if array is None or not array.cells:
            return []
        found: list[Pointer] = []
        for name, value in self.scalars.items():
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(array.cells):
                found.append(Pointer(name=name, array_name=array.name, index=index))
        return found


def reconstruct_frames(trace: ExecutionTrace) -> list[FrameState]:
    """Replay the trace, producing one FrameState per event.

    Pure and deterministic: same trace in, same frames out, no clock, no
    randomness, no filesystem.
    """
    arrays: dict[str, ArraySnapshot] = {}
    scalars: dict[str, str] = {}
    # Names seen used as `name[i]`, so a plain string value can be
    # recognised as char[] contents rather than an ordinary string.
    indexed_names: set[str] = set()
    for event in trace.events:
        parsed = parse_indexed_name(event.variable_name)
        if parsed is not None:
            indexed_names.add(parsed[0])

    frames: list[FrameState] = []
    for event in trace.events:
        changed_scalar: str | None = None
        read_indices: dict[str, list[int]] = {}
        written_indices: dict[str, list[int]] = {}

        if event.event_type == TraceEventType.VARIABLE_ASSIGN.value and event.variable_name:
            name = event.variable_name
            cells = parse_array_value(event.new_value)
            if cells is not None:
                arrays[name] = ArraySnapshot(name=name, cells=cells)
            elif name in indexed_names and event.new_value is not None:
                # char[]: repr() gave us the characters directly.
                arrays[name] = ArraySnapshot(name=name, cells=list(event.new_value))
            else:
                scalars[name] = event.new_value if event.new_value is not None else ""
                changed_scalar = name

        elif event.event_type in (
            TraceEventType.ARRAY_READ.value,
            TraceEventType.ARRAY_WRITE.value,
        ):
            parsed = parse_indexed_name(event.variable_name)
            if parsed is not None:
                array_name, index = parsed
                snapshot = arrays.setdefault(
                    array_name, ArraySnapshot(name=array_name, cells=[])
                )
                if event.event_type == TraceEventType.ARRAY_WRITE.value:
                    while len(snapshot.cells) <= index:
                        snapshot.cells.append("")
                    if event.new_value is not None:
                        snapshot.cells[index] = event.new_value
                    written_indices.setdefault(array_name, []).append(index)
                else:
                    read_indices.setdefault(array_name, []).append(index)

        frames.append(
            FrameState(
                step_index=event.step_index,
                event_type=event.event_type,
                line_number=event.line_number,
                description=event.description,
                arrays={name: snap.copy() for name, snap in arrays.items()},
                scalars=dict(scalars),
                read_indices=read_indices,
                written_indices=written_indices,
                changed_scalar=changed_scalar,
            )
        )
    return frames

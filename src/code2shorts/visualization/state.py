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


UNIT = "\x1f"
"""Separator used by Code2ShortsTrace.collection() between elements. A
control character that cannot appear in Java source, so an element
containing commas, quotes, brackets, newlines or `});` cannot forge a
boundary — splitting on it is unambiguous for any value."""


def _split_units(value: str | None) -> list[str]:
    if not value:
        return []
    return value.split(UNIT)


@dataclass
class MapSnapshot:
    """Observed key -> value contents of a Map at one trace event.

    Semantic contents only. JVM internals (buckets, table capacity, resize
    state, node chains) are never present, because the trace never captures
    them — see ADR-6.2.
    """

    name: str
    keys: list[str]
    values: list[str]
    ordered: bool
    """True when this order is SEMANTIC (a LinkedHashMap preserves
    insertion order by specification). False means the entries were sorted
    canonically for determinism and the order carries no meaning — a
    renderer may display it but must not teach it as insertion order.
    See ADR-6.4."""
    last_operation: str | None = None

    @property
    def entries(self) -> list[tuple[str, str]]:
        return list(zip(self.keys, self.values))

    def copy(self) -> MapSnapshot:
        return MapSnapshot(
            name=self.name,
            keys=list(self.keys),
            values=list(self.values),
            ordered=self.ordered,
            last_operation=self.last_operation,
        )


@dataclass
class SequenceSnapshot:
    """Observed elements of a Deque/List/Queue at one trace event.

    One type serves stack-like and queue-like use, because an `ArrayDeque`
    used as a stack and the same class used as a queue are the same object
    differing only in which end is operated on — and that end is observed,
    not guessed. Splitting this into StackSnapshot/QueueSnapshot would
    force the mapper to infer the author's intent (ADR-6.3).

    `elements` are in the collection's own iteration order, which is
    specified for Deque and List, so it is always semantic here.
    """

    name: str
    elements: list[str]
    last_operation: str | None = None

    #: Which end the last observed operation touched, as reported by the
    #: operation name — "front", "back", or None when not end-specific.
    active_end: str | None = None

    #: Ends observed across the WHOLE run so far, for insertions and for
    #: removals. This is what actually distinguishes stack-like from
    #: queue-like use, and it is observed rather than guessed: a single
    #: operation cannot tell them apart, because `pop` (stack) and
    #: `poll` (queue) both act on the front. What differs is whether
    #: insertion and removal share an end.
    insert_end: str | None = None
    remove_end: str | None = None

    @property
    def discipline(self) -> str:
        """'lifo' | 'fifo' | 'unknown', derived from observed ends.

        'unknown' until both an insertion and a removal have been seen —
        claiming either before that would be a guess.
        """
        if self.insert_end is None or self.remove_end is None:
            return "unknown"
        return "lifo" if self.insert_end == self.remove_end else "fifo"

    @property
    def is_empty(self) -> bool:
        return not self.elements

    def copy(self) -> SequenceSnapshot:
        return SequenceSnapshot(
            name=self.name,
            elements=list(self.elements),
            last_operation=self.last_operation,
            active_end=self.active_end,
            insert_end=self.insert_end,
            remove_end=self.remove_end,
        )


#: Which end of a sequence each observed operation acts on. Derived from
#: the Java API's own documented semantics, never from an algorithm name:
#: Deque.push/pop/addFirst act on the head, offer/add/addLast on the tail.
_FRONT_OPERATIONS = frozenset(
    {"push", "pop", "addFirst", "offerFirst", "pollFirst", "removeFirst", "poll", "remove"}
)
_BACK_OPERATIONS = frozenset({"add", "addLast", "offer", "offerLast", "pollLast", "removeLast"})


_INSERT_OPERATIONS = frozenset(
    {"push", "add", "addFirst", "addLast", "offer", "offerFirst", "offerLast", "addAll"}
)
_REMOVE_OPERATIONS = frozenset(
    {"pop", "poll", "pollFirst", "pollLast", "remove", "removeFirst", "removeLast"}
)


def _active_end_for(operation: str | None) -> str | None:
    if operation in _FRONT_OPERATIONS:
        return "front"
    if operation in _BACK_OPERATIONS:
        return "back"
    return None


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
    maps: dict[str, MapSnapshot] = field(default_factory=dict)
    sequences: dict[str, SequenceSnapshot] = field(default_factory=dict)
    changed_collection: str | None = None
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
    def primary_map(self) -> MapSnapshot | None:
        """The map this frame is about — the one just mutated, else the
        only/first one. Same deterministic rule as `primary_array`:
        insertion order, so identical traces always choose identically."""
        if self.changed_collection in self.maps:
            return self.maps[self.changed_collection]
        return next(iter(self.maps.values()), None)

    @property
    def primary_sequence(self) -> SequenceSnapshot | None:
        """The sequence this frame is about, by the same rule."""
        if self.changed_collection in self.sequences:
            return self.sequences[self.changed_collection]
        return next(iter(self.sequences.values()), None)

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
    maps: dict[str, MapSnapshot] = {}
    sequences: dict[str, SequenceSnapshot] = {}
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
        changed_collection: str | None = None
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

        elif (
            event.event_type == TraceEventType.COLLECTION_MUTATION.value
            and event.variable_name
        ):
            # The trace reports full observed contents at each mutation,
            # so unlike arrays there is nothing to replay - the event IS
            # the snapshot. An 'unsupported' kind is deliberately left
            # unrepresented rather than degraded into a plausible-looking
            # visual; validation reports it instead.
            name = event.variable_name
            changed_collection = name
            if event.collection_kind == "map":
                maps[name] = MapSnapshot(
                    name=name,
                    keys=_split_units(event.collection_keys),
                    values=_split_units(event.collection_values),
                    ordered=bool(event.collection_ordered),
                    last_operation=event.collection_operation,
                )
            elif event.collection_kind == "sequence":
                operation = event.collection_operation
                end = _active_end_for(operation)
                # Carry the ends forward across the run: which end insertions
                # use versus removals is what makes a Deque stack-like or
                # queue-like, and one event alone cannot say.
                previous = sequences.get(name)
                insert_end = previous.insert_end if previous else None
                remove_end = previous.remove_end if previous else None
                if operation in _INSERT_OPERATIONS and end is not None:
                    insert_end = end
                elif operation in _REMOVE_OPERATIONS and end is not None:
                    remove_end = end
                sequences[name] = SequenceSnapshot(
                    name=name,
                    elements=_split_units(event.collection_values),
                    last_operation=operation,
                    active_end=end,
                    insert_end=insert_end,
                    remove_end=remove_end,
                )

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
                maps={name: snap.copy() for name, snap in maps.items()},
                sequences={name: snap.copy() for name, snap in sequences.items()},
                changed_collection=changed_collection,
                scalars=dict(scalars),
                read_indices=read_indices,
                written_indices=written_indices,
                changed_scalar=changed_scalar,
            )
        )
    return frames

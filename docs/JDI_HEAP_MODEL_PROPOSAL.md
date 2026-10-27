# Proposal: Heap Facts from the JDI Backend (documentation only)

**Status:** PROPOSAL. Nothing in this document is implemented, and the
canonical `ExecutionTrace` is unchanged by Phase 8.4. Any part of it needs an
ADR in `ARCHITECTURE_DECISIONS.md` (it is a contract change) and separate
approval.

## What the tracer already provides (verified by run, Phase 8.4)

cs1302-tracer snapshots carry a heap graph on every stop.

**Objects:** `{"id", "type", "kind"}`, where `kind` is one of `array`,
`string`, `box` or `object`. An `object` also carries its `fields`.

**Ran on the tracer's own examples:**
- linked-list `Node` objects with `item` and `next` fields;
- generics (`Pair<Integer,Boolean>`);
- `ArrayList<Integer>`, with boxed elements;
- inheritance (`Employee` / `Manager` / `Person`), stack depth 4;
- references from locals (`{"ref": 107}`), including aliasing (two locals
  holding the same id).

**Not provided:** the contents of JDK objects. A `StringBuilder` arrives as
`{"kind": "object", "fields": []}`, and `toString()` is never invoked.

## The problem it would solve

The instrumenter's trace stores **mutations** of arrays, scalars and
whitelisted collections. It cannot express:
- object identity;
- references and aliasing;
- linked structures (lists, trees, graphs);
- an object's fields over time.

Every future data-structure visual on the roadmap needs those facts.

## Proposed shape (additive, optional)

```
ExecutionTrace
  events: [...]                      # unchanged
  heap_snapshots: list[HeapSnapshot] | None = None    # NEW, optional

HeapSnapshot
  event_index: int                   # the TraceEvent this state follows
  objects: dict[str, HeapObject]     # id -> object, as observed
  roots: dict[str, str]              # local name -> object id, top frame

HeapObject
  type: str                          # e.g. "Node", "int[]"
  kind: "array" | "object" | "string" | "box"
  fields: dict[str, Value] | None
  elements: list[Value] | None
```

## Rules it must keep

1. **Optional.** The instrumenter backend leaves the field `None`. Nothing
   downstream may require it.
2. **Observed only.** It is filled from tracer snapshots; nothing is
   inferred.
3. **Stable identities across one run.** Phase 8.4 found the raw snapshots,
   heap ids included, byte-identical across three runs. That should be
   re-verified per JDK before ids are relied on.
4. **No JDK internals.** JDK objects stay opaque, as the tracer already
   keeps them.
5. **Bounded.** Reuse the tracer's own `--max-heap-objects` and
   `--max-elements` budgets.

## Why it is not part of Phase 8.4

The brief forbids adding heap objects, references or object identities to
the canonical trace in this phase. The JDI backend is also not yet the
default (see `docs/PHASE_8_4_JDI_TRACE_PARITY.md`).

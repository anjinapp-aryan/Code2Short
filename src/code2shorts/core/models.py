"""Language-independent domain models for the Code2Shorts pipeline.

Nothing in this module may import from `code2shorts.langadapter`, `render`,
`animation`, `execution`, `tts`, or `compose`. These types are the shared
contract every stage of the pipeline reads and writes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


TRACE_SCHEMA_VERSION = 3
"""Bumped when the MEANING or FORMAT of trace fields changes.

1 -> Phase 2/4.1: array values rendered via String.valueOf (int[] came out
     as an identity hash such as "[I@7ad041f3").
2 -> Phase 4.2: array values rendered via Code2ShortsTrace.repr, so arrays
     carry real contents ("[2, 7, 11, 15]"); Phase 4.3 adds provenance
     fields alongside.
3 -> Phase 6: COLLECTION_MUTATION events carry observed Map/Deque/List
     contents. Purely additive — a version-2 trace omits the new fields
     and still validates, and nothing that read version 2 changes meaning.
"""


class SupportedLanguage(StrEnum):
    JAVA = "java"
    PYTHON = "python"
    JAVASCRIPT = "javascript"


class TraceStatus(StrEnum):
    """Mutually exclusive trace() outcomes. Never mix these."""

    COMPLETED = "completed"
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    TRACE_LIMIT_REACHED = "trace_limit_reached"
    INSTRUMENTATION_FAILED = "instrumentation_failed"


class TraceEventType(StrEnum):
    """The minimal Phase 2 event vocabulary. Deliberately excludes
    OBJECT_CREATED/FIELD_*/OUTPUT_WRITTEN/LOOP_ENTER/LOOP_EXIT/RETURN —
    either redundant with what's here or deferred to a future
    object/collection tracing phase.
    """

    PROGRAM_START = "PROGRAM_START"
    PROGRAM_END = "PROGRAM_END"
    METHOD_ENTER = "METHOD_ENTER"
    METHOD_EXIT = "METHOD_EXIT"
    VARIABLE_ASSIGN = "VARIABLE_ASSIGN"
    CONDITION_EVALUATED = "CONDITION_EVALUATED"
    LOOP_ITERATION = "LOOP_ITERATION"
    ARRAY_READ = "ARRAY_READ"
    ARRAY_WRITE = "ARRAY_WRITE"
    COLLECTION_MUTATION = "COLLECTION_MUTATION"
    EXCEPTION_THROWN = "EXCEPTION_THROWN"


class ExceptionInfo(BaseModel):
    """What actually crashed, as observed — never inferred."""

    exception_class: str
    message: str
    thrown_at_line: int | None = None
    thrown_in_method: str | None = None


class SourceLocation(BaseModel):
    """Where in the source a trace event actually happened.

    Language-neutral on purpose: a Java adapter fills it from class/method
    context today, a future Python or JavaScript adapter from its own
    equivalents, and nothing above this model needs to know which.

    Exists because `TraceEvent.line_number` alone is ambiguous: a program
    spanning `Main.java` and `ReverseString.java` produces "line 5" and
    "line 6" from *different files*, so a line number without a file
    identity cannot select a line to highlight. This is the model that
    makes code synchronization well-defined.
    """

    file: str | None = None
    line: int | None = None
    column: int | None = None
    method: str | None = None
    class_name: str | None = None


class ValidationResult(BaseModel):
    """One validation check's outcome — schema, semantic, or domain-level.
    Lives here (not in `workflow.state`, where it conceptually belongs)
    because both `workflow` and `ai` need it and `ai.validation` importing
    it from `workflow.state` created a real import cycle (`ai.validation`
    -> `workflow` package init -> `workflow.nodes` -> `ai.validation`).
    `core.models` has no dependents that could cycle back, so it's the
    correct home for a shared leaf type like this.
    """

    stage: str = Field(description="'schema' | 'semantic' | 'domain'")
    passed: bool
    errors: list[str] = Field(default_factory=list)
    subject_artifact_id: str | None = None
    superseded: bool = Field(
        default=False,
        description=(
            "True when a later repair attempt replaced this outcome. The "
            "failed attempt is still recorded — the repair history is "
            "audit evidence, and deleting it would hide how many attempts "
            "a provider needed. But a superseded failure must not be read "
            "as 'the workflow produced invalid output': a real Phase 5 run "
            "reported FAIL overall while every artifact had in fact been "
            "built from a passing attempt. See ValidationSummary."
        ),
    )


class Example(BaseModel):
    """A single worked input/output example for an algorithm."""

    input: str
    output: str
    note: str | None = None


class AlgorithmSpec(BaseModel):
    """What the algorithm is, independent of any target language."""

    name: str
    category: str = Field(description="e.g. 'string', 'array', 'tree', 'graph'")
    function_name: str
    parameters: list[str] = Field(default_factory=list)
    return_description: str
    constraints: list[str] = Field(default_factory=list)
    examples: list[Example]
    time_complexity: str
    space_complexity: str


class LessonSpec(BaseModel):
    """The structured output of lesson planning; input to a LanguageAdapter."""

    topic: str
    algorithm: AlgorithmSpec
    language: SupportedLanguage
    target_audience: str = "Java developers"
    narration_outline: list[str] = Field(
        default_factory=list,
        description="Ordered beats the narration should hit, in prose form.",
    )
    aspect_ratio: str = "9:16"
    target_duration_seconds: int = Field(default=60, gt=0)


class TraceEvent(BaseModel):
    """One observed moment during actual execution of generated code.

    Every field here must come from real, captured execution output —
    never inferred or fabricated by an LLM.

    `event_type` stays a permissive `str` rather than `TraceEventType`
    directly: Phase 2 producers always populate it from
    `TraceEventType.*.value`, but keeping the field itself untyped avoids
    forcing every historical/illustrative value (e.g. earlier "pointer_move"/
    "swap" fixture data) into the newer canonical vocabulary.
    """

    step_index: int = Field(ge=0)
    line_number: int | None = None
    event_type: str = Field(
        description="e.g. 'variable_update', 'comparison', 'swap', "
        "'pointer_move', 'function_call', 'return' (Phase 2 producers use "
        "TraceEventType.*.value)"
    )
    variables: dict[str, Any] = Field(default_factory=dict)
    description: str

    # Phase 2 additions — all optional, additive, no impact on Phase 0/1 usage.
    call_depth: int = 0
    method: str | None = None
    variable_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    return_value: str | None = None
    iteration: int | None = None
    condition_result: bool | None = None

    # Phase 6 collection observation. All optional and additive: a
    # pre-Phase-6 trace omits them entirely and still validates.
    #
    # Contents are SEMANTIC only - entries for a map, elements in
    # iteration order for a sequence. JVM internals (buckets, table
    # capacity, resize state, node chains) are deliberately never
    # captured: they are implementation details that teach nothing
    # about the algorithm, and reading them would need reflection.
    collection_kind: str | None = None
    """'map' | 'sequence' | 'unsupported'."""
    collection_operation: str | None = None
    collection_ordered: bool | None = None
    """True when the observed order is SEMANTIC (LinkedHashMap,
    Deque/List). False means the order is a canonical sort chosen for
    determinism and carries no meaning - see ADR-6.4."""
    collection_size: int | None = None
    collection_keys: str | None = None
    collection_values: str | None = None
    """Unit-separated (U+001F) element text. A separator that cannot
    occur in Java source, so an element containing commas, quotes,
    brackets or newlines cannot forge a boundary."""


class ExecutionTrace(BaseModel):
    """The verified record of one execution run of generated code."""

    algorithm_name: str
    language: SupportedLanguage
    input: str
    output: str
    succeeded: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    events: list[TraceEvent] = Field(default_factory=list)

    # Phase 2 additions — all optional/defaulted, additive, no impact on
    # Phase 0/1 construction sites (e.g. tests/test_core_models.py).
    # Phase 4.3: provenance, so a trace is auditable after the format
    # changes. Phase 4.2 silently altered how array values are rendered
    # (String.valueOf -> repr), and nothing recorded that a trace predated
    # the change. A version field plus toolchain/source identity makes such
    # shifts detectable without a migration framework, which is
    # deliberately not built yet.
    trace_schema_version: int = TRACE_SCHEMA_VERSION
    instrumenter_version: str = ""
    language_version: str = ""
    source_hash: str = ""

    entry_point: str = ""
    status: TraceStatus = TraceStatus.COMPLETED
    exception: ExceptionInfo | None = None
    duration_seconds: float = 0.0
    timed_out: bool = False
    truncated: bool = False
    truncation_reason: str | None = None

    @field_validator("events")
    @classmethod
    def _events_are_ordered(cls, events: list[TraceEvent]) -> list[TraceEvent]:
        for expected_index, event in enumerate(events):
            if event.step_index != expected_index:
                raise ValueError(
                    f"TraceEvent.step_index must be sequential starting at 0; "
                    f"expected {expected_index}, got {event.step_index}"
                )
        return events

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def max_call_depth_reached(self) -> int:
        return max((event.call_depth for event in self.events), default=0)

    @property
    def total_loop_iterations(self) -> int:
        return sum(
            1 for event in self.events if event.event_type == TraceEventType.LOOP_ITERATION
        )


class AnimationStep(BaseModel):
    """One beat of the Animation DSL, grounded in a specific trace event.

    `trace_event_index` must reference a real index into the
    ExecutionTrace.events this AnimationSpec was built from. Builders are
    responsible for validating that link before rendering; this model only
    enforces the shape.
    """

    step_index: int = Field(ge=0)
    trace_event_index: int = Field(ge=0)
    visual_action: str = Field(
        description="e.g. 'highlight', 'compare', 'swap', 'move_pointer', "
        "'show_result', 'show_complexity'"
    )
    narration_text: str
    duration_seconds: float = Field(gt=0)


class AnimationSpec(BaseModel):
    """Deterministic renderer input. No LLM output past this point."""

    lesson_title: str
    aspect_ratio: str = "9:16"
    steps: list[AnimationStep]

    @property
    def total_duration_seconds(self) -> float:
        return sum(step.duration_seconds for step in self.steps)

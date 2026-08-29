"""Deterministic narration-to-visual-timeline alignment.

The governing rule, which this module exists to enforce:

    The VISUAL timeline is authoritative. Audio adapts to it; animation
    timing is never changed merely because a synthesiser produced a
    different duration.

Each `NarrationSegment` is bound to a `VisualizationStepPlan.order`, so the
segment's start time is that step's start on the visual timeline. Segment
audio that is shorter than its step simply leaves silence; audio that is
longer is **truncated in the subtitle track and reported**, rather than
being allowed to push the visuals out of sync.

Nothing here sleeps, guesses, or estimates: every duration is either a
plan-declared visual duration or a real measured audio duration.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from code2shorts.ai.contracts import NarrationResponse, VisualizationPlanResponse
from code2shorts.core.models import ExecutionTrace, ValidationResult


class AlignedSegment(BaseModel):
    """One narration segment placed on the authoritative visual timeline."""

    segment_id: int
    text: str
    visualization_step_order: int
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    audio_path: str | None = None
    audio_duration_seconds: float | None = None
    overflowed: bool = False
    """True when the measured audio is longer than its visual step. The
    audio is not allowed to stretch the timeline, so this is surfaced
    rather than silently absorbed."""

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class AlignmentResult(BaseModel):
    segments: list[AlignedSegment] = Field(default_factory=list)
    total_duration_seconds: float = 0.0
    overflow_count: int = 0


class AlignmentError(Exception):
    """Alignment could not be produced — a missing segment, a step that
    does not exist, or non-monotonic output. Never silently repaired."""


def align_narration(
    narration: NarrationResponse,
    plan: VisualizationPlanResponse,
    audio_by_segment: dict[int, tuple[str, float]] | None = None,
    lead_in_seconds: float = 0.0,
) -> AlignmentResult:
    """Place each narration segment on the visual timeline.

    `audio_by_segment` maps `segment.order` -> (audio_path, measured
    duration). It is optional so alignment can be computed (and tested)
    without synthesising anything; when present, the measured duration is
    used only to detect overflow, never to move a step.
    """
    audio_by_segment = audio_by_segment or {}

    # Visual timeline: step order -> (start, end). Steps are laid end to
    # end in declared order, which is the plan's own semantics.
    starts: dict[int, float] = {}
    ends: dict[int, float] = {}
    cursor = lead_in_seconds
    for step in sorted(plan.steps, key=lambda s: s.order):
        starts[step.order] = cursor
        cursor += float(step.duration_seconds)
        ends[step.order] = cursor
    timeline_end = cursor

    aligned: list[AlignedSegment] = []
    for segment in sorted(narration.segments, key=lambda s: s.order):
        step_order = segment.visualization_step_order
        if step_order not in starts:
            raise AlignmentError(
                f"narration segment {segment.order} references visualization step "
                f"{step_order}, which does not exist in the plan"
            )

        start = starts[step_order]
        end = ends[step_order]

        audio_path: str | None = None
        audio_duration: float | None = None
        overflowed = False
        if segment.order in audio_by_segment:
            audio_path, audio_duration = audio_by_segment[segment.order]
            if audio_duration > (end - start) + 1e-6:
                overflowed = True

        aligned.append(
            AlignedSegment(
                segment_id=segment.order,
                text=segment.text,
                visualization_step_order=step_order,
                start_seconds=start,
                end_seconds=end,
                audio_path=audio_path,
                audio_duration_seconds=audio_duration,
                overflowed=overflowed,
            )
        )

    _assert_well_formed(aligned)

    return AlignmentResult(
        segments=aligned,
        total_duration_seconds=timeline_end,
        overflow_count=sum(1 for s in aligned if s.overflowed),
    )


def _assert_well_formed(segments: list[AlignedSegment]) -> None:
    """Structural guarantees the composer and subtitle writer rely on."""
    previous_end = -1.0
    for segment in segments:
        if segment.start_seconds < 0 or segment.end_seconds < 0:
            raise AlignmentError(f"segment {segment.segment_id} has a negative timestamp")
        if segment.end_seconds <= segment.start_seconds:
            raise AlignmentError(
                f"segment {segment.segment_id} has end <= start "
                f"({segment.end_seconds} <= {segment.start_seconds})"
            )
        if segment.start_seconds + 1e-9 < previous_end:
            raise AlignmentError(
                f"segment {segment.segment_id} overlaps the previous segment "
                f"(starts {segment.start_seconds}, previous ended {previous_end})"
            )
        previous_end = segment.end_seconds


def validate_alignment(
    result: AlignmentResult, plan: VisualizationPlanResponse
) -> ValidationResult:
    """Domain check reported into the workflow's ValidationSummary."""
    errors: list[str] = []

    if not result.segments:
        errors.append("alignment produced no segments")

    covered = {s.visualization_step_order for s in result.segments}
    missing = sorted({step.order for step in plan.steps} - covered)
    if missing:
        errors.append(f"visualization steps with no narration: {missing}")

    for segment in result.segments:
        if segment.overflowed:
            errors.append(
                f"segment {segment.segment_id} audio "
                f"({segment.audio_duration_seconds:.2f}s) exceeds its visual step "
                f"({segment.duration_seconds:.2f}s)"
            )

    return ValidationResult(stage="domain", passed=not errors, errors=errors)


def validate_teaching_synchronization(
    result: AlignmentResult,
    plan: VisualizationPlanResponse,
    trace: "ExecutionTrace | None" = None,
    tolerance_seconds: float = 0.05,
) -> ValidationResult:
    """Semantic synchronization, not merely technical.

    `validate_timeline` and FFmpeg both prove that total audio and total
    video have the same length. That is necessary and nowhere near
    sufficient: a track can match in total while every individual sentence
    describes a state the learner is no longer looking at.

    This checks the relationship the learner actually experiences — that
    while segment N is being spoken, the visuals showing the state segment
    N describes are the ones on screen:

      * a segment's spoken window equals its own step's visual window,
      * that step exists, and cites a real trace event,
      * a segment never spans a step boundary (narration continuing over a
        state change is exactly the "explains one thing while showing
        another" failure),
      * segments advance in the same order as the steps they narrate,
      * every segment carries MEASURED audio, so timing is never based on
        a character-count estimate.
    """
    errors: list[str] = []
    steps = {step.order: step for step in plan.steps}

    # The visual timeline, computed the same way align_narration does.
    windows: dict[int, tuple[float, float]] = {}
    cursor = 0.0
    for step in sorted(plan.steps, key=lambda s: s.order):
        windows[step.order] = (cursor, cursor + float(step.duration_seconds))
        cursor += float(step.duration_seconds)

    real_events = {event.step_index for event in trace.events} if trace else None

    previous_step_order: int | None = None
    for segment in sorted(result.segments, key=lambda s: s.segment_id):
        where = f"segment {segment.segment_id}"
        order = segment.visualization_step_order

        step = steps.get(order)
        if step is None:
            errors.append(f"{where}: narrates step {order}, which is not in the plan")
            continue

        if real_events is not None and step.trace_event_index not in real_events:
            errors.append(
                f"{where}: its step cites trace event {step.trace_event_index}, "
                "which does not exist"
            )

        start, end = windows[order]
        if abs(segment.start_seconds - start) > tolerance_seconds:
            errors.append(
                f"{where}: speech starts at {segment.start_seconds:.2f}s but its "
                f"visual moment starts at {start:.2f}s"
            )
        if abs(segment.end_seconds - end) > tolerance_seconds:
            errors.append(
                f"{where}: speech ends at {segment.end_seconds:.2f}s but its "
                f"visual moment ends at {end:.2f}s"
            )

        # Narration must not run past the state it describes.
        if segment.audio_duration_seconds is None:
            errors.append(
                f"{where}: no measured audio duration — timing would rest on an "
                "estimate rather than the real speech"
            )
        elif segment.start_seconds + segment.audio_duration_seconds > end + tolerance_seconds:
            errors.append(
                f"{where}: speech runs {segment.audio_duration_seconds:.2f}s past "
                "the visual state it describes"
            )

        if previous_step_order is not None and order < previous_step_order:
            errors.append(
                f"{where}: narrates step {order} after step {previous_step_order} — "
                "the learner hears the steps out of order"
            )
        previous_step_order = order

    return ValidationResult(stage="domain", passed=not errors, errors=errors)


def validate_each_moment_is_narrated_once(
    result: AlignmentResult,
    plan: VisualizationPlanResponse,
    trace: "ExecutionTrace",
) -> ValidationResult:
    """The same words may not be spoken over materially different states.

    Section 15 draws the line deliberately: identical narration is NOT
    automatically wrong. A sentence like "the pointers move inward" can
    legitimately accompany the movement, and two adjacent steps that show
    the same values are one coherent teaching moment however they were
    planned. What is wrong is one paragraph read out while the visible
    state moves on beneath it — the learner hears a description of a
    picture that is no longer on screen.

    So the comparison is against the reconstructed FrameState, not against
    the text alone: repetition is reported only when the two steps differ
    in what they SHOW (their scalars or their array contents).

    Observed on a real run: six consecutive segments repeated one
    moment's sentence while left/right advanced 0/6 -> 1/5. Reported, never
    rewritten — shortening narration is prohibited (ADR-5.11), and the
    repair belongs to the planning stage.
    """
    from code2shorts.visualization.state import reconstruct_frames

    frames = {frame.step_index: frame for frame in reconstruct_frames(trace)}
    steps = {step.order: step for step in plan.steps}

    def visible(order: int):
        step = steps.get(order)
        if step is None:
            return None
        frame = frames.get(step.trace_event_index)
        if frame is None:
            return None
        arrays = {
            name: tuple(snapshot.cells) for name, snapshot in frame.arrays.items()
        }
        return (tuple(sorted(frame.scalars.items())), tuple(sorted(arrays.items())))

    errors: list[str] = []
    previous_text: str | None = None
    previous_id: str | None = None
    previous_state: object = None

    for segment in result.segments:
        text = " ".join(segment.text.split()).strip().lower()
        state = visible(segment.visualization_step_order)
        if (
            text
            and text == previous_text
            and state is not None
            and previous_state is not None
            and state != previous_state
        ):
            errors.append(
                f"segment {segment.segment_id} repeats the narration of "
                f"{previous_id} while the visible state changed, so one of "
                "the two states is described by words written for the other"
            )
        previous_text = text
        previous_id = segment.segment_id
        previous_state = state

    return ValidationResult(stage="domain", passed=not errors, errors=errors)


def validate_narration_describes_its_moment(
    result: AlignmentResult,
    plan: VisualizationPlanResponse,
    trace: "ExecutionTrace",
    min_overlap: int = 1,
) -> ValidationResult:
    """Does the SPEECH for a moment talk about that moment's state?

    `validate_teaching_synchronization` proves the timing lines up. It
    cannot tell whether the words describe the right thing: a segment can
    occupy exactly the correct window and still explain the next
    iteration. This is the content half.

    The check is deliberately weak and one-directional. It only asks
    whether a segment mentions ANY concrete token from the trace event its
    step cites — a variable name, an index, or a value. Narration is prose
    and may legitimately reason, so this reports a WARNING-shaped failure
    for segments that mention nothing from their own moment while naming a
    different moment's variable instead. It never rewrites narration and
    never shortens it.
    """
    errors: list[str] = []
    steps = {step.order: step for step in plan.steps}
    events = {event.step_index: event for event in trace.events}

    def tokens_of(event) -> set[str]:
        raw = " ".join(
            filter(None, [event.variable_name, event.new_value, event.old_value])
        )
        found = {piece for piece in re.split(r"[^A-Za-z0-9_]+", raw) if piece}
        return {piece for piece in found if len(piece) > 1 or piece.isdigit()}

    all_variables = {
        event.variable_name.split("[", 1)[0]
        for event in trace.events
        if event.variable_name
    }

    for segment in result.segments:
        step = steps.get(segment.visualization_step_order)
        if step is None:
            continue
        event = events.get(step.trace_event_index)
        if event is None:
            continue

        mine = tokens_of(event)
        if not mine:
            continue                      # nothing concrete to check against

        spoken = {
            piece for piece in re.split(r"[^A-Za-z0-9_]+", segment.text) if piece
        }
        overlap = len(mine & spoken)
        if overlap >= min_overlap:
            continue

        # It mentions nothing from its own moment. Only complain when it
        # names some OTHER traced variable, which is the "describes a
        # different state" failure rather than ordinary prose.
        others = (all_variables & spoken) - mine
        if others:
            errors.append(
                f"segment {segment.segment_id} narrates step "
                f"{segment.visualization_step_order} (about {sorted(mine)}) but "
                f"mentions {sorted(others)} instead"
            )

    return ValidationResult(stage="domain", passed=not errors, errors=errors)

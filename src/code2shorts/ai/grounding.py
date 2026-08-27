"""Trace-grounding validation for educational plans.

The Phase 5.3 rule, and the reason this module exists:

    An educational claim about RUNTIME STATE is believable only if a real
    TraceEvent says so. An explanation of WHY is not checked against the
    trace, because no event records a "why".

`ClaimKind` carries that distinction, and this module enforces it. A
moment marked `OBSERVED` must cite real evidence; one marked `EXPLANATION`
or `COMMENTARY` may reason freely, but still may not smuggle in a fake
event index or a source line that does not exist.

What is deliberately NOT here: any notion of duration, step budget or
"too many moments". Dropping a conceptual transition to shorten a video is
prohibited by ADR-5.11, so this module has no lever to do it.
"""

from __future__ import annotations

import re

from code2shorts.ai.contracts import (
    ClaimKind,
    EducationalMoment,
    EducationalPlanResponse,
)
from code2shorts.core.models import ExecutionTrace, ValidationResult

# A claim like "fast = 3", "nums[2] = 0" or "chars[0] = 'O'".
#
# Only `=` / `==` count as an assertion. English "is" was tried and removed:
# it matched ordinary prose such as "left is less than right" as
# `left = less`, rejecting a perfectly good explanation. A validator that
# fires on grammar rather than on claims trains people to ignore it.
#
# The value alternation accepts a quoted char/string, because char arrays
# are the common case here and `\w+` silently skipped `'Z'` — letting a
# fabricated `chars[7] = 'Z'` through unchecked.
_ASSIGNMENT = re.compile(
    r"\b(?P<name>[A-Za-z_]\w*)\s*(?:\[\s*(?P<index>\d+)\s*\])?\s*==?\s*"
    r"(?P<value>'[^']*'|\"[^\"]*\"|-?\w+)"
)


def _known_variables(trace: ExecutionTrace) -> set[str]:
    names: set[str] = set()
    for event in trace.events:
        name = getattr(event, "variable_name", None)
        if name:
            # `chars[0]` also tells us `chars` exists.
            names.add(name)
            names.add(name.split("[", 1)[0])
    return names


def validate_educational_plan(
    plan: EducationalPlanResponse,
    trace: ExecutionTrace,
    source_files: dict[str, str] | None = None,
) -> ValidationResult:
    """Every execution-dependent claim must be supported by the trace."""
    errors: list[str] = []
    real_indices = {event.step_index for event in trace.events}
    known = _known_variables(trace)

    max_source_line = 0
    if source_files:
        max_source_line = max(
            (len(text.splitlines()) for text in source_files.values()), default=0
        )

    seen_ids: set[str] = set()
    for moment in plan.moments:
        where = f"moment {moment.id!r}"

        if moment.id in seen_ids:
            errors.append(f"{where}: duplicate moment id")
        seen_ids.add(moment.id)

        # 1. An OBSERVED claim without evidence is exactly the failure mode
        #    this phase exists to prevent.
        if moment.claim_kind is ClaimKind.OBSERVED and not moment.evidence_event_indices:
            errors.append(
                f"{where}: claim_kind=observed asserts runtime state but cites "
                "no trace evidence"
            )

        # 2. Evidence must point at events that really happened.
        for index in moment.evidence_event_indices:
            if index not in real_indices:
                errors.append(
                    f"{where}: cites trace event {index}, which does not exist "
                    f"(trace has {len(real_indices)} events)"
                )

        # 3. Source lines must exist in the real source.
        for line in moment.source_lines:
            if line < 1 or (max_source_line and line > max_source_line):
                errors.append(
                    f"{where}: references source line {line}, outside the real "
                    f"source (1..{max_source_line})"
                )

        # 4. Prerequisites must exist and must come earlier — a moment that
        #    depends on one the learner has not seen teaches nothing.
        for prerequisite in moment.prerequisite_ids:
            if prerequisite == moment.id:
                errors.append(f"{where}: lists itself as a prerequisite")

        # 5. Stated runtime values must match the trace.
        errors.extend(_value_errors(moment, trace, known))

    errors.extend(_ordering_errors(plan))

    return ValidationResult(stage="semantic", passed=not errors, errors=errors)


def _value_errors(
    moment: EducationalMoment, trace: ExecutionTrace, known: set[str]
) -> list[str]:
    """Check concrete `name = value` assertions against the cited events.

    Only applied to OBSERVED moments: an explanation may legitimately say
    "left = 0 initially" while discussing the general algorithm.
    """
    if moment.claim_kind is not ClaimKind.OBSERVED:
        return []

    errors: list[str] = []
    cited = [e for e in trace.events if e.step_index in set(moment.evidence_event_indices)]
    cited_text = " ".join(
        filter(None, (e.description for e in cited))
    ) + " " + " ".join(
        filter(None, (str(getattr(e, "value", "") or "") for e in cited))
    )

    for match in _ASSIGNMENT.finditer(moment.explanation):
        name = match.group("name")
        base = name.split("[", 1)[0]
        # Only judge names the trace actually knows about; prose like
        # "index i" or "step 2" must not be mistaken for a variable claim.
        if base not in known:
            continue
        value = match.group("value").strip("'\"")
        index = match.group("index")
        needle = f"{base}[{index}]" if index is not None else base
        if needle not in cited_text:
            errors.append(
                f"moment {moment.id!r}: asserts {needle} but no cited event "
                f"concerns it"
            )
        elif value not in cited_text:
            errors.append(
                f"moment {moment.id!r}: asserts {needle} = {value}, which the "
                "cited trace events do not show"
            )
    return errors


def _ordering_errors(plan: EducationalPlanResponse) -> list[str]:
    """Prerequisites must be satisfied by an earlier moment, and must not
    form a cycle. A 15-line check rather than a graph dependency."""
    errors: list[str] = []
    position = {moment.id: i for i, moment in enumerate(plan.moments)}

    for i, moment in enumerate(plan.moments):
        for prerequisite in moment.prerequisite_ids:
            if prerequisite not in position:
                errors.append(
                    f"moment {moment.id!r}: prerequisite {prerequisite!r} is not "
                    "a moment in this plan"
                )
            elif position[prerequisite] >= i:
                errors.append(
                    f"moment {moment.id!r}: prerequisite {prerequisite!r} appears "
                    "later, so the learner meets it after needing it"
                )
    return errors

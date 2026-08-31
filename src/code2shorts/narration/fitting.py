"""Fit a proposed visual timeline to MEASURED speech, before rendering.

Why this exists
---------------
`alignment.py` enforces that the visual timeline is authoritative: audio
never stretches the visuals. That rule is correct, but it only holds at
alignment time — it assumes the plan's `duration_seconds` were sane in
the first place.

They are proposed by an LLM, and a real model routinely proposes 1.0s
steps for narration that takes 3.5s to speak. The measured Phase 5 run:
25 of 25 segments overflowed, and `validate_timeline` correctly failed
rather than truncating.

So the fix belongs HERE, upstream of the renderer: trusted code widens
each visual step until its narration actually fits, and only then is the
plan handed to Manim. By the time `align_narration` runs, the timeline it
treats as authoritative is one that can hold the speech.

The rules this deliberately obeys
---------------------------------
* **Widen only, never shrink.** A step is never shortened to make audio
  fit, so nothing is ever cut off. `max()` is the whole policy.
* **No truncation, no `-shortest`, no silent duration manipulation, no
  video stretching** — the Phase 4.4 prohibitions still hold. A step
  simply gets more screen time.
* **Measured, never estimated.** Durations come from the TTS provider's
  real output, the same numbers alignment uses.
* **The LLM's proposal is a floor, not an override.** If the model asked
  for a longer step than the speech needs, it keeps it — pacing is
  content, and only the *impossible* part is corrected.
"""

from __future__ import annotations

from pydantic import BaseModel

from code2shorts.ai.contracts import NarrationResponse, VisualizationPlanResponse
from code2shorts.narration.audio import BREATH_SECONDS

DEFAULT_PADDING_SECONDS = BREATH_SECONDS
"""Breathing room after speech ends so one segment does not butt straight
into the next. Small and fixed — not a tuning knob for hiding overflow.

Phase 6.1: this is now the ONLY deliberate pause between segments, and it
is shared with `narration.audio` so there is one number rather than two.
Before the synthesiser's padding was trimmed, this 0.35 s sat on top of
~0.86 s of TTS silence, and the measured gap after every sentence was
1.21-1.24 s. Same intent, an accurate amount."""


class FitAdjustment(BaseModel):
    """One widened step, recorded so the change is reportable rather than
    invisible."""

    step_order: int
    proposed_seconds: float
    fitted_seconds: float
    audio_seconds: float


class FitResult(BaseModel):
    plan: VisualizationPlanResponse
    adjustments: list[FitAdjustment] = []

    @property
    def changed(self) -> bool:
        return bool(self.adjustments)


def fit_plan_to_narration(
    plan: VisualizationPlanResponse,
    narration: NarrationResponse,
    audio_by_segment: dict[int, tuple[str, float]],
    padding_seconds: float = DEFAULT_PADDING_SECONDS,
) -> FitResult:
    """Widen visual steps so each one can hold its narration audio.

    `audio_by_segment` maps `segment.order` -> (path, measured seconds),
    exactly as `align_narration` takes it.

    Returns a NEW plan; the input is not mutated. Steps with no narration
    are untouched. Raises nothing: a step whose audio already fits is
    simply left alone.
    """
    # segment.order -> the step it narrates
    needed: dict[int, float] = {}
    for segment in narration.segments:
        if segment.order not in audio_by_segment:
            continue
        _, audio_seconds = audio_by_segment[segment.order]
        step_order = segment.visualization_step_order
        # Several segments may share a step; the step must hold all of
        # them laid end to end, so sum rather than max.
        needed[step_order] = needed.get(step_order, 0.0) + float(audio_seconds)

    adjustments: list[FitAdjustment] = []
    steps = []
    for step in plan.steps:
        required = needed.get(step.order, 0.0)
        target = required + padding_seconds if required else 0.0
        proposed = float(step.duration_seconds)
        if target > proposed + 1e-6:
            fitted = round(target, 3)
            adjustments.append(
                FitAdjustment(
                    step_order=step.order,
                    proposed_seconds=proposed,
                    fitted_seconds=fitted,
                    audio_seconds=round(required, 3),
                )
            )
            steps.append(step.model_copy(update={"duration_seconds": fitted}))
        else:
            steps.append(step)

    return FitResult(plan=plan.model_copy(update={"steps": steps}), adjustments=adjustments)

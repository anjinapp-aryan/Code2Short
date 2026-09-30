"""The one model of WHEN each visualization step is actually on screen.

Phase 6.1 forensics. The renderer surrounds every step with animation:
the title fades in, each step fades the previous one out and itself in,
and the scene fades out at the end. `align_narration` laid steps end to
end using only `duration_seconds` and knew nothing about any of that, so
the two timelines described different videos:

    audio timeline (30 steps) ......... 160.414 s
    animation the renderer inserts ....  20.950 s
    real video ........................ 181.370 s   (measured 181.37)

The gap is not a rounding error, it is structural, and it has two
symptoms that were reported as separate complaints:

  * narration drifts EARLIER relative to the visuals as the video runs,
    reaching a full 21 s of lead by the end — "the narration is talking
    ahead of the visualization";
  * the audio track runs out 22.1 s before the video does — the silent
    tail (measured: audio goes silent at 159.28 s, video ends 181.39 s).

Both are the same defect. This module is the fix: the renderer and the
aligner now read the same constants and the same window arithmetic, so
a step's narration is placed exactly when that step's frame is fully
drawn and holding.

Deliberately dependency-free and pure — no Manim import, no clock — so
the aligner can use it without pulling the rendering stack in.
"""

from __future__ import annotations

from code2shorts.ai.contracts import VisualizationPlanResponse

TITLE_FADE_IN_SECONDS = 1.0
"""Manim's default `run_time`. It was implicit — `self.play(FadeIn(title))`
with no run_time — which is exactly how a whole second went unmodelled."""

TITLE_HOLD_SECONDS = 0.4
STEP_FADE_OUT_SECONDS = 0.25
"""Fading the PREVIOUS step out. Not paid before the first step."""

STEP_FADE_IN_SECONDS = 0.4
FINAL_FADE_OUT_SECONDS = 0.3
"""The closing fade. This is the ONLY silence the end of a video is
allowed to carry, and `media/validation.py` enforces that."""


def step_windows(
    plan: VisualizationPlanResponse, lead_in_seconds: float = 0.0
) -> dict[int, tuple[float, float]]:
    """Step order -> (start, end) of the interval where its frame HOLDS.

    The window deliberately excludes the fades. Narration placed inside it
    is spoken over a frame that is fully drawn and not moving, which is
    what "the narration describes what is on screen" has to mean in
    practice — during a cross-fade there are two states on screen and
    neither is the one being described.
    """
    windows: dict[int, tuple[float, float]] = {}
    cursor = lead_in_seconds + TITLE_FADE_IN_SECONDS + TITLE_HOLD_SECONDS

    for position, step in enumerate(sorted(plan.steps, key=lambda s: s.order)):
        if position > 0:
            cursor += STEP_FADE_OUT_SECONDS
        cursor += STEP_FADE_IN_SECONDS
        start = cursor
        cursor += float(step.duration_seconds)
        windows[step.order] = (start, cursor)

    return windows


def total_video_seconds(
    plan: VisualizationPlanResponse, lead_in_seconds: float = 0.0
) -> float:
    """Length of the rendered scene, animation included.

    This is what the muxed audio track must be padded to, so that the
    file's audio and video streams end together and any trailing silence
    is exactly the closing fade rather than an unexplained gap.
    """
    windows = step_windows(plan, lead_in_seconds)
    if not windows:
        return lead_in_seconds + TITLE_FADE_IN_SECONDS + TITLE_HOLD_SECONDS
    last_end = max(end for _, end in windows.values())
    return last_end + FINAL_FADE_OUT_SECONDS

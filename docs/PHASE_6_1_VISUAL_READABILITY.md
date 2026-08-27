# Phase 6.1 — Teaching-first visual layout & readability

## 1. The original problem

The pipeline was technically correct and the video was unteachable. On a
1080×1920 frame the Java code was too small to read on a phone, the array
was small, and large black bands filled the top and bottom of every frame.
A learner could not follow the algorithm without pausing and zooming.

## 2. Measured root cause

Not a font-size problem. **The scene's coordinate frame did not match the
output aspect.**

A probe scene rendered at the real resolution reported:

```
PROBE frame_width : 14.222222222222221
PROBE frame_height: 8.0
PROBE pixel_width : 1080
PROBE pixel_height: 1920
```

Manim keeps `frame_width` at its **16:9** default (14.22 units) even when
`--resolution 1080,1920` is passed; only the pixel dimensions change. The
scene was therefore a 16:9 canvas being rendered into a 9:16 image, and
Manim letterboxed it.

Drawing the frame border proved it: the border occupied roughly **31% of
the 1920 px height**, and the remaining ~69% was structurally unreachable
black. Content was never "too small" — most of the canvas was being thrown
away, and every element was squeezed into the surviving band.

This also explains why the layout constants looked reasonable
(`TITLE_Y = 3.55` … `CODE_Y = -2.65` spans most of an 8-unit frame) while
the output looked tiny.

## 3. Design goals

Teaching clarity first; duration is not a constraint. The learner must
identify the code, the executing line, the variables, the data structure,
the pointers and the current step without pausing or zooming.

## 4. Layout strategy

The renderer now derives the frame from the real pixel aspect:

```python
config.frame_height = 8.0
config.frame_width = config.frame_height * (config.pixel_width / config.pixel_height)
```

giving a **4.5 × 8 unit** frame where **1 unit = 240 px on both axes**. The
full image is addressable and pixels are square.

Explicit, non-overlapping bands (absolute `move_to`, never relative
chaining, so they cannot collide):

| Band | y | Purpose |
|---|---|---|
| Title | +3.15 | small — the lesson is the content |
| Scalars | +2.45 | variable readout |
| Array / collection | +0.55 | pointer labels rise above it |
| Caption | −0.55 | explanation |
| Code panel | −2.25 | the primary teaching surface |

## 5. Code readability policy

* The panel **fills** the safe width (`scale_to_fit_width`) instead of only
  being capped. Previously it was only ever shrunk, so a short snippet
  stayed small and the code was the least readable thing on screen.
* Band height 2.30 units (552 px); readability floor
  `MIN_CODE_LINE_HEIGHT_UNITS = 0.20` (48 px per line).
* `DEFAULT_WINDOW_RADIUS` 6 → **4** (13 lines → 9). 13 lines × 0.20 units
  exceeds the 2.30-unit band, so the old window could only fit by pushing
  text below the floor. Fewer lines shown larger teaches better, and 9
  lines still span a loop body plus its condition in every fixture.

## 6. Array / data-structure readability policy

* `CELL_SIDE = 0.52` units = **125 px** per cell.
* Cell glyphs are **fitted inside their box** (58% of height, 80% of
  width). Font size alone cannot guarantee this for multi-character values
  such as `12`, and the first render after enlarging showed letters
  overflowing into neighbouring cells.
* The whole row is capped at the safe width so long arrays cannot run off
  the frame.
* Pointer labels use a dedicated smaller font (30) and tighter offsets;
  stacked when pointers share a cell.

## 7. Safe-area policy

`SAFE_MARGIN_X = 0.18` (43 px), `SAFE_MARGIN_Y = 0.55` (132 px), giving a
4.14 × 6.90 unit safe box. Mobile players overlay controls and progress
bars at the extreme edges, so teaching content stays inside it. Asserted
by test for every band.

## 8. Source-window strategy

The window narrowed, but **`SourceLocation.line` and the highlighted line
are untouched**. Readability was not bought by renumbering: a test asserts
that a 9-line and a 13-line window highlight the same line 15, and the
Phase 4.5.1 regression suite still passes.

## 9. Why the solution is generic

Nothing added branches on an algorithm. The fix is a frame-geometry
correction plus band constants; every existing structural rule (the
pointer rule, `primary_array`/`primary_map`/`primary_sequence` dispatch)
is unchanged. Verified on binary search (scalars + 3 pointers, no
collection) and palindrome (2 pointers, char array).

## 10–11. Tests and visual verification

16 new tests in `tests/test_visual_readability.py`: frame-aspect
correction, square-pixel derivation, every band inside the safe area, band
ordering and separation, code panel inside its band, window vs readability
floor, panel fills width, cell pixel size, glyph fitting, no whole-scene
scaling, and the line-mapping invariant.

They check geometry, **not** readability — whether a human can read the
code is settled by inspecting real frames, which remains mandatory.

## 12. Performance

Manim render time rose (palindrome ~119 s → ~174 s) because far more of
the frame now contains drawn content. No frame-by-frame post-processing
was added.

## 13. Limitations

* The readability floor is enforced by construction (window size vs band
  height), not measured per-render; a pathologically long source line can
  still force the panel narrower via `scale_to_fit_width`.
* Title font is generous; a very long lesson title consumes more of the
  title band than ideal.
* Collection panels (map/sequence) were re-fitted to the new frame but
  have had less visual iteration than the array path.

## 14. Reuse audit

No new dependency; `pyproject.toml` unchanged. Manim CE's own `config`,
`Code`, `Square`, `Text` and `scale_to_fit_*` cover everything needed. No
external layout library was considered necessary — the problem was one
incorrect frame dimension, not a missing layout engine.

## 15. Architectural impact

Small and contained: one config emission in `build_scene_source`, layout
constants in `primitives.py`, and one default in `code_state.py`. No
contract, trace, validation or security change. AI output remains data;
the renderer remains repository-owned; every dynamic value still goes
through `repr()`.

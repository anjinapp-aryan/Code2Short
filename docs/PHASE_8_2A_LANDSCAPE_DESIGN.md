# PHASE 8.2A — LANDSCAPE DESIGN AUDIT

- **Date:** 2026-09-30
- **Branch:** `feature/phase-8.1-pipeline-parity` @ `9fbba54`
- **Mode:** design and architecture audit only. No source, test, configuration or artifact was changed, and nothing was rendered. The only measurement was building Manim `Text` objects in memory, the same way `tests/test_code_layout.py` measures geometry.

Unit convention used throughout: *units* are Manim scene units. The portrait
frame is 4.5 × 8.0 units at **240 px/unit**
(`primitives.PIXELS_PER_UNIT`).

---

## 1. Executive Summary

- **A real 16:9 composition can be added without rewriting the portrait
  renderer.** None of the Phase 8.2A hard-stop conditions applies.
  - Every portrait position and size is a module constant in
    `visualization/primitives.py`, interpolated into generated Manim source.
  - There is exactly one portrait-specific composer (`compose_vertical`)
    and one frame line (`config.frame_height = 8.0`).
  - The trace, educational plan, narration, timeline, fingerprint and media
    stages need no change.
- **Key finding, measured.** The font-size constants are *not* the
  typography you see. Every text element except pointer labels is first
  scaled to fit the 4.14-unit safe width. In the accepted video the title
  renders at ~34% of its nominal size, captions at ~56% and scalars at
  ~55%. The accepted look is therefore defined by **pixel widths and
  fit-to-width rules**, not by point sizes.
- **Recommended landscape frame: 8.0 × 4.5 units at the same
  240 px/unit.** Manim stroke widths (which follow
  `pixel_width / frame_width`), cell sizes, padding, and the whole proven
  code-panel sizing then keep their exact pixel meaning in both formats.
  The 16:9 frame is not a scaled portrait; it has the same pixel density
  and a different arrangement.
- **Recommended composition: Option A, adapted.** A title band across the
  top, then two fixed columns: the code panel on the left and the data
  structure plus variables on the right. The explanation band spans the
  full width at the bottom.
  - The left column is **exactly the portrait code viewport, 4.14 units =
    994 px**, so Phase 6.5.1/6.5.3 code sizing is reused unchanged and code
    renders at identical pixel size.
- **Mechanism.** One small, immutable layout value per orientation
  (`PORTRAIT` built from today's constants, `LANDSCAPE` new).
  - It is selected from `VideoProfile.orientation` and handed to the
    existing `ManimVideoRenderer` through its constructor, beside
    `resolution`.
  - Primitives take `layout=PORTRAIT` as a default parameter.
  - One new composer function sits beside `compose_vertical`.
- **Portrait protection.** The Phase 8.0 gate: the scene regenerated from
  the accepted 6.5.3 plan and trace must stay **byte-identical**
  (sha256 `5b0b81e05bc703df…`).

---

## 2. Current 9:16 Architecture

```
VisualizationPlan (fitted) + ExecutionTrace + source
        │
ManimVideoRenderer(fps, resolution)            visualization/manim_renderer.py
        │  build_scene_source(plan, class, RenderContext)
        │     reconstruct_frames(trace)          visualization/state.py
        │     resolve_source_locations(...)      visualization/code_state.py
        │     per step, dispatch on WHAT STATE EXISTS (never on algorithm):
        │        map_panel | sequence_panel | array_row + pointer_arrows
        │        scalar_panel
        │        fit_window_radius → build_code_state → code_panel
        │        caption_text
        │        compose_vertical(structure…, caption, code)   ← the portrait composition
        │        FadeOut(previous) / FadeIn(current) / wait(duration)   visualization/timing.py
        │     scene header: config.frame_height = 8.0; frame_width from pixel aspect
        ▼
manim render -qh --fps 30 --resolution W,H  →  output.mp4
        ▼
MediaComposer (-c:v copy) → final.mp4 → validate_final_video(MediaPolicy(profile px))
```

The format enters in two places. `webapp/pipeline.py::build_renderer(profile)`
passes `profile.resolution`. `build_scene_source` derives `frame_width` from
the pixel aspect.

## 3. Current 9:16 Geometry

All values below come from `primitives.py`. Items marked *measured* were
measured in-process with Manim 0.21 during this audit.

### Frame and safe area
| Item | Units | Pixels |
|---|---|---|
| Frame | 4.5 × 8.0 (x −2.25…2.25, y −4…4) | 1080 × 1920 |
| Scale | 1 unit | 240 px |
| Safe margin X / Y | 0.18 / 0.55 | 43 / 132 |
| Safe width | 4.14 | 994 |
| Safe top / bottom | +3.45 / −3.45 | 132 / 1788 from top |

### Vertical bands (portrait is a single centred column; every x = 0)
| Band | Position | Notes |
|---|---|---|
| Title | `TITLE_Y = 3.25` | 40 pt bold, `scale_to_fit_width(4.14)` |
| Scalars | `SCALARS_Y = 2.72` | 34 pt, fit to 4.14 |
| Array | `ARRAY_Y = 1.08` | pointer labels rise ~0.9 above |
| Map/sequence | top `STRUCTURE_TOP = 2.40`, max h `1.45` | top-aligned |
| Caption | `CAPTION_Y = −0.15` default; **moved by `compose_vertical`** under the structure | wrap 46 chars, ≤ 5 lines, max h 1.30 |
| Code | `CODE_Y = −2.05` default; **fills the remainder** down to `SAFE_BOTTOM` | |
| Band gap | `BAND_GAP = 0.14` (34 px) | |

### Code panel (Phase 6.5.1 / 6.5.3)
| Item | Value |
|---|---|
| Viewport width | `CODE_VIEWPORT_WIDTH = 4.14` (994 px), **constant** |
| Viewport centre x | `CODE_VIEWPORT_CENTER_X = 0.0` |
| Height | remainder from `compose_vertical`; standalone default `CODE_MAX_HEIGHT = 2.50` |
| Padding X / Y / chrome strip | 0.16 / 0.14 / 0.20 |
| Content width | 4.14 − 2 × 0.16 = 3.82 |
| Sizing | 80th-percentile line width (`REPRESENTATIVE_PERCENTILE`); outliers shrink alone (floor 0.55; active line floor 0.82); hard containment at the inner right edge; shared indent removed (`_dedent_window`) |
| Line height target / guaranteed floor | 0.20 u (48 px) / 0.175 u (42 px) |
| Window radius | default 4 (9 lines), grows up to 8 within `CODE_HEIGHT_BUDGET = 2.60` (`fit_window_radius`) |
| Line estimate | `CODE_LINE_ASPECT 2.30 × 4.14 / (chars + 4)`; 53-char signature → 0.183 u (44 px) |
| Theme / panel / border / numbers | monokai / `#0D1117` / `#30363D` / `#8B949E`; context opacity 0.92 |
| Active line | `SurroundingRectangle` yellow, stroke 5, fill 0.16 |

### Array and pointers
| Item | Value |
|---|---|
| Cell | `CELL_SIDE 0.52` (125 px), `CELL_BUFF 0.06`; 7 cells = **4.00 u (960 px)** natural (*measured*); shrink above 8 cells; `scale_to_fit_width(4.14)` |
| Cell glyph | 52 pt, fitted to 58% × 80% of the box |
| Index labels | 30 pt, 0.12 below |
| Pointer labels | 30 pt, **not fitted** (*measured*: "left" 0.588 × 0.320 u = 141 × 77 px; "right" 0.826 × 0.405 u = 198 × 97 px) |
| Pointer offsets | 0.30 + depth × 0.34 above the cell; one arrow per cell from the innermost label |
| Colours | written YELLOW, read TEAL, else WHITE |

### Effective typography (measured; this is what the accepted video shows)
| Element | Nominal | Natural extent | Fit rule | Effective in 9:16 |
|---|---|---|---|---|
| Title (v1 title, 41 chars) | 40 pt bold | 12.35 × 0.43 u | width → 4.14 | ×0.335 → **0.144 u ≈ 35 px** tall |
| Caption line | 26 pt | 0.1616 u/char, 0.351 u line | 46 chars → 4.14 | ×0.557 → **21.6 px/char, ~47 px line box** |
| Caption 5 lines | 26 pt, spacing 0.8 | 2.30 u | same | 1.28 u ≤ cap 1.30 |
| Scalars (3 vars) | 34 pt | 7.58 × 0.46 u | width → 4.14 | ×0.546 → **0.25 u ≈ 60 px** |
| Pointer label | 30 pt | as above | none | 77–97 px |
| Code line | 20 pt | n/a | fitted to 3.82 u content width | 42–48+ px per line |

### Timeline
`visualization/timing.py`: title fade 1.0 s, hold 0.4 s, step fade-out 0.25 s,
fade-in 0.4 s, final fade 0.3 s. This is **format-independent** and shared with
`align_narration`.

## 4. Repository Format Assumptions

| File | Current responsibility | Portrait assumption |
|---|---|---|
| `visualization/primitives.py` | All geometry + generated Manim source | Frame 4.5 × 8; x = 0 everywhere; band y constants; `SAFE_WIDTH` used for every fit; `CODE_VIEWPORT_*`; `CODE_HEIGHT_BUDGET`; `CAPTION_CHARS_PER_LINE 46`; `compose_vertical` stacks vertically |
| `visualization/manim_renderer.py` | Scene assembly, dispatch, frame header, render command | `config.frame_height = 8.0` literal; always calls `compose_vertical`; default `resolution="1080x1920"` |
| `visualization/code_state.py` | Window selection | `DEFAULT_WINDOW_RADIUS 4`; comments about "9:16 code band" (logic is width/height-agnostic) |
| `visualization/timing.py` | Fade/hold timing | none |
| `visualization/renderer.py` | `RenderContext`, `RenderResult` | description only (`'1080x1920'` example) |
| `visualization/fake_renderer.py` | test double | `"1080x1920"` default |
| `core/video_profile.py` | Profiles | `LANDSCAPE_HD` refused (`unsupported_reason`) |
| `webapp/pipeline.py` | `build_renderer(profile)`, `MediaPolicy` from profile | none (already profile-driven) |
| `media/validation.py` | Media policy | 1080 × 1920 defaults; callers pass profile px (Phase 8.1) |
| `media/composer.py` | mux | none (`-c:v copy`) |
| `workflow/nodes.py` | Stages | none |
| `generation/fingerprint.py` | Identity | none (format is in `video_format`) |
| `webapp/templates/*` | UI text | "1080 × 1920" literals (display only) |
| `tests/test_visual_readability.py`, `test_code_layout.py`, `test_code_viewport.py`, `test_real_render.py` | **Portrait regression guard** | pin 240 px/unit, 4.5-unit frame, 1080 × 1920; must keep passing unchanged |

## 5. Existing Reusable Components

| Component | Where | Landscape use |
|---|---|---|
| `VideoProfile` + `resolve_video_profile` | `core/video_profile.py` | selects orientation; unchanged apart from lifting the refusal |
| `ManimVideoRenderer(fps, resolution)` | `manim_renderer.py` | already the per-format carrier; gains `layout` beside `resolution` |
| Frame derivation from pixel aspect | `build_scene_source` header | reused; `frame_height` becomes a layout value |
| State dispatch (map / sequence / array) | `build_scene_source` | unchanged; algorithm-agnostic |
| `array_row`, `pointer_arrows`, `scalar_panel`, `map_panel`, `sequence_panel`, `caption_text`, `title_text` | `primitives.py` | reused; they read a position/width from `layout` instead of a module constant |
| `code_panel` + `repin_viewport` + `_dedent_window` | `primitives.py` | reused **as is** at the same 4.14-unit width; only the viewport centre x comes from `layout` |
| `fit_window_radius` | `primitives.py` | reused; width and height budget come from `layout` |
| `compose_vertical` | `primitives.py` | portrait only; stays byte-for-byte |
| `timing.py`, `align_narration`, `fit_plan_to_narration` | | unchanged; the timeline doesn't depend on layout |
| Real-Manim geometry harness | `tests/test_code_layout.py::_render_composed` | reused for landscape geometry tests |
| `MediaPolicy(width, height)` | `media/validation.py` | already profile-driven (Phase 8.1) |

## 6. Portrait vs Landscape Differences

| | 9:16 | 16:9 (proposed) |
|---|---|---|
| Pixels | 1080 × 1920 | 1920 × 1080 (**same 2,073,600 px/frame**) |
| Frame (units) | 4.5 × 8.0 | **8.0 × 4.5** |
| px/unit | 240 | **240** (same) |
| Scarce axis | width (994 px safe) | height (936 px safe) |
| Structure | one column: title / scalars / structure / caption / code | title band / [code ‖ structure + scalars] / caption band |
| Code viewport | 4.14 × remainder (typically 2.9–3.4 u) | 4.14 × **2.42 u** |
| Code window | 9 lines, grows to ≤ 17 | 9 lines, grows to ~11 |
| Structure width | 4.14 u | **2.86 u** |
| Caption | 46 chars/line, ≤ 5 lines | **80 chars/line, ≤ 3 lines** (same glyph size) |

## 7. Proposed 16:9 Composition

### Candidate evaluation

The candidates are not ranked. For each: strengths, weaknesses, and risks.

**Option A: title / code ‖ visualization / caption.**
- **Strengths:**
  - Code and state are visible together, which is what landscape offers
    that portrait cannot.
  - The code column can reuse the proven 994 px viewport exactly.
  - The caption gets the full width, so it wraps into ≤ 3 lines at the same
    glyph size.
  - Fixed columns keep elements from jumping between steps.
  - It reuses every primitive.
- **Weaknesses:**
  - The structure column (686 px) is narrower than portrait's 994 px, so
    the 7-cell array is ~71% scale.
  - Very long arrays and wide trees are tighter than in portrait.
- **Risks:** pointer-label collision at the smaller pitch, and an empty
  right column on intro steps.

**Option B: title / code + visualization stacked full-width / caption.**
- **Strengths:** the structure gets the full 1728 px; closest to portrait.
- **Weaknesses:** it doesn't fit.
  - The portrait stack needs title 0.45 + scalars 0.3 + structure 1.45 +
    caption 0.8 + code ≥ 2.3 = **≈ 5.3 u** against **3.9 u** of safe height.
  - Code or structure would have to shrink below the proven floors.
- **Risks:** this is the "portrait squeezed into landscape" outcome the
  brief forbids.

**Option C: free-floating (title, code left, visualization right, explanation under code).**
- **Strengths:** the tallest visualization column.
- **Weaknesses:**
  - The explanation is confined to the code column: 46 chars/line and up
    to 5 lines, taken out of the code's height.
  - Without bands, positions depend on content, so things drift between
    steps.
- **Risks:** caption/code collision, and it can't reuse the band
  arithmetic.

**Option D (from evidence): Option A with the columns swapped (state left, code right).**
- **Strengths:** the eye lands on the state first.
- **Weaknesses:** the code-left, state-right convention is common in
  code-explainer videos, and the narration usually names the code line
  first ("We read chars[left]…").
- **Risks:** none beyond A's. This could be a layout value later if
  needed.

**Recommended direction: Option A.** It is the only candidate that fits both
the proven code sizing and full-width captions into 1080 px of height without
dropping below measured readability floors. It is also a pure recomposition
of the existing primitives.

### Wireframe: 1920 × 1080 (px from top-left; units in brackets)

```
x:   0   96                              1090 1138                         1824 1920
y:   ┌───────────────────────────────────────────────────────────────────────────┐
  0  │                        (top margin 60 px / 0.25 u)                        │
 60  │  ┌─────────────────────────────────────────────────────────────────────┐  │
     │  │                       TITLE  (centred, ≤ 1728 px, ≤ 43 px tall)     │  │ y +2.00…+1.60
156  │  └─────────────────────────────────────────────────────────────────────┘  │
190  │  ┌──────────────────────────────────┐    ┌──────────────────────────────┐ │ y +1.46
     │  │ ● ● ●                            │    │ input = RACECAR  left = 2 …  │ │ scalars row
     │  │  7  while (left < right) {       │    │                              │ │
     │  │  8      char a = chars[left];    │    │        left        right     │ │ pointer labels
     │  │  9 ▐    char b = chars[right]; ▌ │    │          ↓           ↓       │ │
     │  │ 10      if (a != b) {            │    │   ┌─┬─┬─┬─┬─┬─┬─┐            │ │ array (89 px cells)
     │  │ 11          return false;        │    │   │R│A│C│E│C│A│R│            │ │
     │  │ 12      }                        │    │   └─┴─┴─┴─┴─┴─┴─┘            │ │
     │  │ 13      left++;                  │    │    0 1 2 3 4 5 6             │ │ indices
     │  │ 14      right--;                 │    │                              │ │
     │  │ 15  }                            │    │                              │ │
770  │  └──────────────────────────────────┘    └──────────────────────────────┘ │ y −0.96
     │     CODE: x 96–1090 (994 px, 4.14 u)       STRUCTURE: x 1138–1824 (686 px) │
804  │  ┌─────────────────────────────────────────────────────────────────────┐  │ y −1.10
     │  │  EXPLANATION: full width, ≤ 80 chars/line, ≤ 3 lines (same glyph   │  │
     │  │  size as 9:16: 21.6 px/char)                                        │  │
996  │  └─────────────────────────────────────────────────────────────────────┘  │ y −1.90
     │                    (bottom margin 84 px / 0.35 u)                          │
1080 └───────────────────────────────────────────────────────────────────────────┘
```

Regions in scene units (frame x −4…4, y −2.25…2.25):

| Region | x | y | Size (u / px) |
|---|---|---|---|
| Safe box | −3.60…3.60 | −1.90…2.00 | 7.20 × 3.90 / 1728 × 936 |
| Title band | −3.60…3.60 | 1.60…2.00 | 7.20 × 0.40 / 1728 × 96 |
| Content region | | −0.96…1.46 | height 2.42 / 581 |
| Code column | −3.60…0.54 (centre −1.53) | −0.96…1.46 | 4.14 × 2.42 / 994 × 581 |
| Column gutter | 0.54…0.74 | | 0.20 / 48 |
| Structure column | 0.74…3.60 (centre 2.17) | −0.96…1.46 | 2.86 × 2.42 / 686 × 581 |
| Explanation band | −3.60…3.60 | −1.90…−1.10 | 7.20 × 0.80 / 1728 × 192 |
| Gaps title→content, content→caption | | | 0.14 / 34 (`BAND_GAP`) |

## 8. PALINDROME State-by-State Layout

The step kinds come from the real v1 plan
(`output/real-test/palindrome/library/palindrome/v1/visualization_plan.json`).
Positions are the fixed regions of §7. Nothing moves between steps except
content inside a region.

"Captions" here means the burned-in explanation band, the only on-screen text
the renderer draws per step. `subtitles.srt` is a separate file and is **not**
burned in.

| # | State (trace) | Code column | Structure column | Pointers | Explanation band |
|---|---|---|---|---|---|
| 1 | Opening (`PROGRAM_START`, `intro`) | window around the method, no highlight | **empty** (nothing observed yet) | none | intro sentence |
| 2 | Input (`input = "RACECAR"`, line 20) | window around `main`, line 20 highlighted | scalars row `input = RACECAR` only (a String is a scalar) | none | "The input string RACECAR…" |
| 3 | Array creation (`chars`, line 21) | line 21 highlighted | array R A C E C A R, indices 0–6, centred in column; scalars row above | none | |
| 4 | left/right init (lines 5, 6) | line 5, then 6 | array; scalars `input … left = 0`, then `right = 6` | `left` → cell 0, then `right` → cell 6 | |
| 5 | while (`left < right` = true, line 7) | line 7 | unchanged | 0 / 6 | "0 is less than 6" |
| 6 | First comparison (`chars[0]`, `chars[6]`, `a != b` false; lines 8–10) | lines 8, 9, 10 | cells 0 and 6 TEAL (read) | 0 / 6 | |
| 7 | Pointer movement (lines 13, 14) | 13, then 14 | scalars update | `left` → 1, `right` → 5 | |
| 8 | Second comparison (`chars[1]`, `chars[5]`) | 8, 9, 10 | cells 1, 5 TEAL | 1 / 5 | |
| 9 | Middle condition (`left = right = 3`, `left < right` false) | line 7 | scalars `left = 3 right = 3` | both on cell 3, **stacked**: `left` at depth 0, `right` above, one arrow | |
| 10 | Return (`return true`, line 16) and completion | line 16 highlighted; completion has no highlight | final array | 3 / 3 | "…returns true" |

Vertical budget of the structure column (measured sizes, array scaled
2.86 / 4.00 = 0.715):

| Item | Height (u) |
|---|---|
| Scalars row | 0.17 |
| Gap | 0.14 |
| Stacked labels (0.30 + 0.34 + 0.405) | ≈ 1.05 |
| Cells | 0.37 |
| Indices + buffer | 0.30 |
| **Total** | **≈ 2.03 of 2.42** |

The block is centred vertically in its column.

## 9. Code Panel Design

| Parameter | 16:9 value | Derivation |
|---|---|---|
| Panel width | **4.14 u = 994 px** | identical to `CODE_VIEWPORT_WIDTH`; reuses the proven per-line size |
| Panel centre x | **−1.53 u** | left edge on the safe margin (−3.60) |
| Panel height | remainder of the content region, **≤ 2.42 u (581 px)** | |
| Horizontal / vertical padding / chrome strip | 0.16 / 0.14 / 0.20 | unchanged constants |
| Body height budget | 2.42 − 0.48 = **1.94 u** | replaces `CODE_HEIGHT_BUDGET 2.60` for this layout |
| Font strategy | unchanged: fit representative width to the 3.82 u content width, outlier shrink, hard containment | |
| Minimum readable | **0.175 u = 42 px** per line (`ABS_MIN_CODE_LINE_HEIGHT_UNITS`), target 0.20 u = 48 px | same pixels as portrait |
| Visible lines | 9 at 0.20; up to 11 at 0.175 (`fit_window_radius` with budget 1.94) | |
| Overflow strategy | unchanged: per-line outlier scale (floors 0.55 / 0.82), then hard containment; window never exceeds the height budget | |
| Line numbers / indentation | unchanged (`line_numbers_from`, shared-indent removal only) | |

Because the width is identical, **every code line renders at the same pixel
size as in the accepted 9:16 video.** The only difference is that fewer
context lines are shown: the landscape window grows to ~11 lines rather than
up to 17.

## 10. Visualization Design

- **Array.** Fit to the 2.86 u column. Seven cells scale to **0.372 u =
  89 px** (portrait 125 px), a pitch of 0.415 u (100 px). Glyphs follow
  the box (~52 px).
- **Placement.** Centred in the column at a fixed y, so the array never
  jumps. Map and sequence panels are top-aligned in the same column,
  capped at 2.42 u.
- **Emphasis.** Unchanged: TEAL for read, YELLOW for written, one arrow
  per addressed cell. The "middle element" is not special-cased; it is
  just the cell both pointers address.
- **Separation from code.** The 48 px gutter plus the panel's own
  background keep the code a distinct surface. The structure column has
  no panel, as in portrait.

## 11. Typography Design

The effective sizes are derived from §3's measurements, keeping the
portrait's pixel sizes wherever the layout allows.

| Element | 9:16 effective | 16:9 rule | 16:9 effective |
|---|---|---|---|
| Title | 35 px tall (fit to 994 px) | fit width ≤ 1728 px **and** height ≤ 0.18 u | **≤ 43 px** (1.25× portrait, so the header doesn't look lost on a wide canvas) |
| Code line | 42–48+ px | same panel width → same fit | **identical** |
| Explanation | 21.6 px/char, ~47 px line | 80 chars/line into 7.20 u (46 × 1728 / 994 ≈ 80), ≤ 3 lines, band ≤ 0.80 u | **identical glyph size**; 3 lines ≈ 0.74 u |
| Scalars | 60 px (fit to 994 px) | fit to the 2.86 u column | **≈ 42 px** (70% of portrait); if it would go below 0.15 u, wrap into two rows |
| Pointer labels | 77–97 px (unfitted) | unchanged unless §13's collision rule applies | same |
| Array glyphs | ~70 px | follow the cell scale | ~52 px |

Hierarchy is preserved: code ≥ explanation ≥ title-scale header, with the
array as the visual centre of the right column.

## 12. Safe Areas

| Edge | Margin | Reason |
|---|---|---|
| Left / right | 96 px (0.40 u) = 5% | player and browser chrome; TV overscan between action-safe and title-safe |
| Top | 60 px (0.25 u) = 5.6% | YouTube title overlay when paused |
| Bottom | 84 px (0.35 u) = 7.8% | YouTube progress bar and controls |

- **Title-safe option.** A strict TV title-safe 10% (192 / 108 px) would
  leave 1536 × 864 px. It still fits Option A only if the code panel drops
  to ~3.7 u (≈ 40 px lines, below portrait) or the caption goes to 2 lines.
  This is a product choice, recorded as an open question (§26).
- **Subtitle overlap.** If a viewer enables YouTube CC, the platform draws
  it over the bottom ~15–20% of the picture, on top of the burned-in
  explanation band. The same is true in portrait today. The design keeps
  the band inside the safe box but cannot avoid a platform overlay. The
  choice is whether to publish the SRT as CC at all, since it duplicates
  the burned-in text.

## 13. Pointer / Array Geometry

- **Converged pointers** (`left = right`) already stack, with one arrow from
  the innermost label. Measured stack: 0.30 + 0.34 + 0.405 ≈ 1.05 u above
  the cell, which fits.
- **Adjacent pointers: risk, existing in portrait too.**
  - Measured label widths are 0.588 u ("left") and 0.826 u ("right").
    Their half-width sum is **0.707 u**.
  - Portrait pitch is 0.58 u: labels on *adjacent* cells already overlap
    in 9:16 (for example an even-length input like `ABBA` at `left = 1`,
    `right = 2`). RACECAR never has adjacent pointers (0/6, 1/5, 2/4, 3/3).
  - At the 16:9 pitch of 0.415 u, two cells apart (0.83 u) clears by only
    ~0.12 u, and adjacent cells overlap by ~0.29 u.
- **Proposed generic rule, landscape only.** When labels on different
  cells would overlap horizontally, assign them increasing depths exactly
  as converged labels are stacked today. Arrows still come from each
  cell's innermost label.
  - This reads geometry, never names.
  - Portrait keeps its current behaviour, so the byte gate holds.
  - Adopting the rule in portrait later is a separate, deliberate change.
- **Long arrays.** Above 8 cells the existing shrink applies. At 2.86 u a
  20-cell array gives ~0.13 u (31 px) cells, below readability. See §17.

## 14. Caption / Explanation Placement

- **Placement.** Full-width band at the bottom (y −1.10…−1.90), centred
  block, wrapped to 80 chars, at most 3 lines.
- **Truncation.** The same presentation-only ellipsis rule as portrait's
  `_wrap`. Narration is never shortened (ADR-5.11).
- **Budget.** 3 × 80 = 240 characters against a 5 × 46 = 230-character
  budget in portrait, so the same narrations fit without more truncation.
- **Fixed position.** Unlike portrait, the band doesn't move up under the
  structure. Portrait needs that because its structure height varies above
  a single column; in landscape the columns sit above a fixed band, so a
  fixed band is the more stable choice.
- **Synchronisation.** Timing is unchanged, since the explanation is
  still drawn per step.

## 15. Layout Abstraction

The minimum needed, and nothing speculative:

```
VideoProfile.orientation ──► layout_for(profile) ──► CompositionLayout (frozen dataclass)
                                                         PORTRAIT  = today's constants
                                                         LANDSCAPE = §7 values
build_renderer(profile) ──► ManimVideoRenderer(fps, resolution, layout)
                                   │
                          build_scene_source(..., layout)
                                   ├─ header: config.frame_height = layout.frame_height  ("8.0" | "4.5")
                                   ├─ primitives(..., layout=layout)   positions / widths / budgets
                                   └─ layout.compose → compose_vertical | compose_columns
```

- **Fields (about 20 numbers, no behaviour).** Frame height, safe box,
  title (y, max width, max height), scalars (x, y, max width), structure
  box (centre x, top, max width, max height, array y), caption (x, y, max
  width, chars per line, max lines, max height), code viewport (centre x,
  width, height budget, region top/bottom), and a composer selector.
- **`PORTRAIT` references the existing module constants.** The constants
  stay, and tests keep pinning them. There's no numeric drift.
- **The layout travels in the renderer constructor, beside `resolution`.**
  Not through `RenderContext`, because `RenderVideoNode` builds that and
  would pull `workflow/` into the change. `build_renderer(profile)`
  already derives resolution from the profile and would derive the layout
  from the same profile.
- **Consistency guard.** `ManimVideoRenderer` raises `RenderingFailure` if
  the layout's aspect doesn't match the resolution's. It is structurally
  impossible to draw a portrait layout into a landscape frame.
- **Not proposed:** a layout engine, constraint solver, class hierarchy
  per format, a copy of the renderer, or per-algorithm positions.

## 16. Reuse Audit

| Candidate | Finding | Verdict |
|---|---|---|
| `VideoProfile` | exists (Phase 8.0) | **REUSE** |
| `RenderContext` | exists; built in `workflow/` | not used for layout; avoids touching `workflow/` |
| `ManimVideoRenderer` constructor | already carries per-format `resolution` | **ADAPT** (add `layout`) |
| `LayoutConfig` / `SceneConfig` / `VisualizationConfig` / geometry helpers | **none exist** (repository search) | build one small dataclass |
| `compose_vertical`, `repin_viewport`, `fit_window_radius`, `code_panel` | exist | **REUSE**; `compose_columns` is composed from the same pieces |
| `test_code_layout._render_composed` | real-Manim measurement harness | **REUSE** for landscape tests |
| Manim `config.frame_height` / `frame_width` | used today | **REUSE** (frame 8 × 4.5) |
| Manim `Mobject.arrange`, `next_to`, `align_to`, `to_edge`, `scale_to_fit_*` | used today | **REUSE** |
| Manim `VGroup.arrange_in_grid` | available | not needed; the columns are two fixed boxes |
| Manim `MovingCameraScene` / camera zoom | available | **reject**: camera scaling would change stroke/text pixel density and couple layout to camera state |
| Manim responsive or layout system | **none in Manim CE 0.21** | — |
| Third-party layout libraries | not needed for two fixed columns | none; zero new dependencies |

## 17. Algorithm Independence

- **No algorithm-specific branching.** The layout has none. The dispatch
  stays "what state exists", and regions are generic boxes.
- **Arrays / strings / sorting / searching.** Fit the structure column up
  to ~8–10 cells at readable size.
- **Stacks / queues.** `sequence_panel` goes vertical for LIFO, which suits
  a tall column. A horizontal queue of more than 6 elements hits the same
  width limit as long arrays.
- **Maps.** Top-aligned list, fits.
- **Linked lists, trees, graphs, DP tables (not implemented today).** These
  want width or area. The 686 × 581 px column is adequate for small cases
  (a tree of depth ≤ 3, a DP table of ≤ 8 × 6).
- **Wide structures.** Beyond those sizes a second landscape variant is
  needed: structure full-width on top, code and explanation side by side
  below. Choose it **once per video** from the widest frame, never per
  step, so nothing jumps.
  - This is another `CompositionLayout` value, not a new mechanism.
  - Not needed for Phase 8.2.
- **A test must keep this true.** The rule-7 check
  (`test_no_algorithm_specific_branches_in_visualization_package`) is a
  **text grep**. Any new `visualization/` file must not mention an
  algorithm name, **even in a comment**, or that test fails.

## 18. Performance Risks

| Factor | 16:9 vs accepted 9:16 | Risk |
|---|---|---|
| Pixels per frame | 1920 × 1080 = 1080 × 1920 = 2,073,600 | none expected |
| Frame count | identical (timeline doesn't depend on layout; v1: 3717 frames) | none |
| Mobject count per step | identical (same primitives) | none |
| Cairo raster cost | same pixel count; wider frame | low |
| Partial movie files / disk | similar (~12 MB final, same order of intermediates) | low |
| FFmpeg | stream copy + AAC, unchanged | none |
| Memory | same frame buffers | low |
| 4K (not this phase) | 4× pixels | render time, memory and temp disk likely several times higher; to be benchmarked in 8.3 |

The expectation is that the render stage takes about as long as portrait
(~110 s for v1). **Not benchmarked.**

## 19. Fingerprint / Versioning

- **No collisions.** `PALINDROME + VERTICAL_HD` (`video_format = "youtube_short"`)
  and `PALINDROME + LANDSCAPE_HD` (`"landscape_hd"`) produce different
  digests. This is already tested
  (`test_same_content_in_a_different_profile_never_collides`), and a 16:9
  request is reported as a config change and never served the 9:16 video
  (`test_a_different_format_is_reported_as_a_config_change`).
- **No stale 16:9 cache can exist.** Landscape has always been refused
  before a version was created.
- **`RENDERER_VERSION` should not be bumped when 16:9 is enabled.** The
  portrait output stays byte-identical, so a bump would needlessly
  invalidate every 9:16 video.
- **Gap, not a blocker.** `RENDERER_VERSION` is global. A later change that
  only affects landscape would force portrait re-renders too. A
  per-layout renderer version is a possible future refinement.
  **Fingerprint logic needs no change.**

## 20. Test Strategy (for Phase 8.2 implementation)

| Group | Test | Level |
|---|---|---|
| Profile | `LANDSCAPE_HD` → 1920 × 1080, 16:9, landscape; renderable; 4K profiles still refused | unit |
| Layout selection | `layout_for(VERTICAL_HD) is PORTRAIT`, `layout_for(LANDSCAPE_HD) is LANDSCAPE`; `PORTRAIT` fields equal the module constants | unit |
| Guard | layout/resolution aspect mismatch raises `RenderingFailure` before Manim runs | unit |
| **Portrait regression** | `build_scene_source` for the 6.5.3 plan + trace, default layout **and** explicit `PORTRAIT`, is byte-identical to the accepted `scene.py`; every existing visual test unchanged | unit (fixture files) |
| Geometry (real Manim, `_render_composed` harness) | every mobject inside the landscape safe box; code group inside its column; structure + labels inside theirs; caption inside its band; no pairwise overlap between regions | integration |
| Code | 53-char signature: inside the panel, line ≥ 0.175 u; long file: window lines × height ≤ budget; nested block: relative indentation preserved; line numbers inside the panel | integration |
| Visualization | 7-cell array inside the column; 20-cell array inside the column (size reported); converged pointers stacked; **adjacent pointers do not overlap** (synthetic `FrameState`, no Java) | unit + integration |
| Typography | code line ≥ 42 px; explanation glyph = portrait's ±5%; title ≤ 43 px; scalars ≥ 0.15 u | integration |
| Timeline invariance | `total_video_seconds`, `align_narration` and fit results identical for PORTRAIT and LANDSCAPE on the same plan | unit |
| Pipeline | `DefaultPipelineFactory` for `LANDSCAPE_HD` builds a renderer with 1920 × 1080 + LANDSCAPE and `MediaPolicy(1920, 1080)` | unit |
| Rule 7 | no algorithm names in `visualization/` (existing) | unit |
| E2E | real palindrome, `video_format = landscape_hd`, through the web path, into `output/real-test/landscape/`: all validators pass, probed 1920 × 1080, 9:16 v1/v2 untouched, then a frame-by-frame visual review against §8 | E2E |

## 21. Risk Matrix

| Risk | Cause | Impact | Mitigation | How to test |
|---|---|---|---|---|
| Portrait regression | parameterising `primitives.py` changes emitted text | the accepted baseline shifts | default `layout=PORTRAIT`; byte-identity gate after **each** function change; no global constant rewrite | 6.5.3 `scene.py` sha gate + existing visual tests |
| Global / camera scaling | choosing a different px/unit or camera zoom | every proven size changes meaning | keep 240 px/unit (frame 8 × 4.5); no camera | assert frame height 4.5 in landscape header; stroke density unchanged |
| Code overflow | long lines in a shorter panel | code escapes the panel | unchanged width, so unchanged containment; height budget 1.94 | long-line + long-file geometry tests |
| Pointer collision | smaller pitch (0.415 u) vs 0.83 u labels | labels overlap | overlap-aware depth stacking (landscape) | adjacent-pointer synthetic test |
| Caption collision | 3-line band vs columns | text over code | fixed band + `BAND_GAP`; max 3 lines | region-overlap geometry test |
| Font readability | scalars at 70%, cells at 71% | small state text | floors (scalars ≥ 0.15 u; cells ≥ 0.30 u), with a 2-row scalar wrap | typography tests; real frame review |
| Safe areas | platform overlays, TV overscan | clipped header or controls over text | 5% / 5.6% / 7.8% margins; title-safe open question | geometry inside the safe box |
| Long arrays / wide structures | 2.86 u column | tiny cells | report size; the per-video wide variant is future work | 20-cell test records the size |
| Mechanical edits | constant → field replacement across 8 functions | a missed or duplicated site (CLAUDE.md's `collectionVars.clear()` lesson) | scope by hand, one function at a time, gate each | byte gate per step |
| Rule-7 text grep | a comment naming an algorithm | test red | no algorithm names in new files | existing test |
| Empty right column on intro steps | no state observed yet | half-empty frame (the portrait top is empty the same way) | accept for stability; optional future: centre code when no step in the video has structure | frame review |
| CC overlay | platform draws over the band | duplicated text | product decision on publishing the SRT | n/a |

## 22. Minimal Implementation Plan

Each step ends with the portrait byte gate and the full suite (no new
failures).

1. **Layout value.** Add `visualization/layout.py` with a frozen
   `CompositionLayout`, `PORTRAIT` built from the current constants, and
   `layout_for(profile)`, where 16:9 raises until step 5. Tests: fields
   equal the constants.
2. **Thread the layout through `primitives.py` by hand, one function at a
   time,** with a `layout=PORTRAIT` default: `title_text`, `scalar_panel`,
   `array_row`, `map_panel`, `sequence_panel`, `caption_text` (chars and
   lines), `code_panel` + `repin_viewport` (centre x, width),
   `fit_window_radius` (width, budget). Gate after each function.
3. **Renderer.** `build_scene_source(..., layout)` emits the frame height
   from the layout (portrait text identical) and dispatches the composer.
   `ManimVideoRenderer(..., layout=PORTRAIT)` adds the aspect guard. Gate.
4. **Landscape composer.** Add `compose_columns`: code into its column
   remainder (reusing the `compose_vertical` code block and
   `repin_viewport`), the structure centred in its column, the caption in
   the fixed band. Add landscape-only overlap-aware pointer depths.
5. **`LANDSCAPE` values** from §7 and §11; `layout_for(LANDSCAPE_HD)`
   returns them.
6. **Tests from §20** (unit + real-Manim geometry).
7. **Enable.** `build_renderer` passes `layout_for(profile)`; clear
   `LANDSCAPE_HD.unsupported_reason`; the 4K profiles stay refused.
8. **Real E2E.** Palindrome 16:9 through the web path into
   `output/real-test/landscape/`, then validators and a frame review
   against §8 and §25.

Suggested split: steps 1–3 are **8.2B**, a zero-visual-change refactor with
a hard gate. Steps 4–8 are **8.2C**, the landscape layout.

## 23. Files Expected to Change

| File | Why | Expected change | Risk |
|---|---|---|---|
| `visualization/layout.py` (new) | layout values | ~80 lines: dataclass, `PORTRAIT`, `LANDSCAPE`, `layout_for` | low |
| `visualization/primitives.py` | positions and widths per layout | constant reads → `layout.` fields behind `layout=PORTRAIT` defaults; new `compose_columns`; landscape-only pointer depth rule | **high** (baseline file): byte gate + existing tests |
| `visualization/manim_renderer.py` | frame height + composer choice + guard | header from layout; composer dispatch; `layout` constructor arg | medium: byte gate |
| `webapp/pipeline.py` | pass the layout | `build_renderer(profile)` adds `layout=layout_for(profile)` | low |
| `core/video_profile.py` | enable 16:9 | `LANDSCAPE_HD.unsupported_reason = None` | low (tests updated to the new intended behaviour, since 16:9 refusal is the old contract) |
| `tests/test_landscape_layout.py` (new) | §20 | new tests | — |
| `docs/VIDEO-FORMATS.md` | status | 16:9 implemented | — |

## 24. Files That MUST NOT Change

- `core/models.py` (`ExecutionTrace`, `TraceEvent`)
- `ai/` (educational plan, visualization plan, narration contracts and prompts)
- `narration/` (TTS, alignment, fitting, subtitles)
- `visualization/timing.py`, `visualization/state.py`, `visualization/code_state.py`
- `workflow/`
- `media/`
- `generation/` (fingerprint, registry, manager, jobs)
- `langadapter/`, `execution/`, `tools/java-instrumenter/`
- the Java fixtures (including palindrome)
- every existing test's assertions (the one exception is the deliberate
  flip of the 16:9-refusal expectation in step 7, recorded as a contract
  change)
- all accepted outputs under `output/`

## 25. Acceptance Criteria for Phase 8.2

1. **Portrait is untouched.** The 6.5.3 `scene.py` is byte-identical;
   the full suite has no new failures; the 9:16 real test videos are
   untouched.
2. **16:9 is a real composition.** `LANDSCAPE_HD` renders 1920 × 1080 with
   the §7 composition, not a scaled, letterboxed or cropped portrait.
3. **Same content.** Trace, educational plan, narration, alignment and
   duration are identical between 9:16 and 16:9 for the same plan.
4. **Geometry.** All content inside the safe box, no region overlap, code
   inside its panel, pointers inside the frame, including for a
   53-character line, a long file and adjacent pointers.
5. **Typography.** Code line ≥ 42 px; explanation glyph equal to portrait's;
   title ≤ 43 px; scalars ≥ 0.15 u.
6. **Validators and identity.** Media, timeline, final gate and the golden
   audio-experience check all pass on a real E2E palindrome 16:9 run; the
   registry records a separate `landscape_hd` version.
7. **Scope.** No algorithm names in `visualization/`; no new dependency;
   4K still refused.

## 26. Recommended Next Action

**Phase 8.2B.** Introduce the layout seam with zero visual change (§22
steps 1–3), gated by portrait byte-identity. It is the risky half and
should land and be verified on its own. Then **Phase 8.2C** builds the
landscape layout and the real 16:9 E2E.

Open product questions to settle before 8.2C:

1. Safe margins: 5% (proposed) or strict TV title-safe 10%?
2. Code-left / state-right (proposed) or swapped?
3. Should the adjacent-pointer overlap rule also be applied to 9:16 later
   (a deliberate portrait change)?
4. Should `subtitles.srt` be published as platform CC, given it duplicates
   the burned-in explanation band?

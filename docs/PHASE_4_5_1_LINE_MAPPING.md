# Phase 4.5.1 — Source-Line Mapping Correctness

A corrective phase. One defect: displayed Java line numbers could be offset
by one, so the rendered video highlighted the wrong statement.

## The guarantee

```
TraceEvent.line_number
  == SourceLocation.line
  == CodeState.start_line + highlight_offset
  == the label Manim actually DISPLAYS at the highlight
  == the real text of that line in the original file
```

Every link is now asserted programmatically, including the last one — which
is where the defect lived, and which earlier verification never reached.

## Root cause

**Manim's `Code` mobject silently discards leading and trailing empty lines
while its line-number column keeps counting from `line_numbers_from`.**

Measured on the installed Manim 0.21.0 (no other version was tried):

| input | rendered lines | labels |
|---|---|---|
| `"\nAAA\nBBB\nCCC"` (4 lines, leading blank) | **3** | `2,3,4` |
| `"\n\nAAA\nBBB"` (4 lines, two leading blanks) | **2** | `1,2` |
| `"AAA\nBBB\n"` (trailing blank) | **2** | `1,2` |
| `"AAA\n\nBBB"` (interior blank) | 3 | `1,2,3` |
| `"   \nAAA\nBBB"` (whitespace-only first line) | 3 | `1,2,3` |

So: leading and trailing **truly empty** lines are stripped; interior blanks
and whitespace-only lines survive.

When a code window began on a blank line, that line vanished inside Manim
while the labels still started at `line_numbers_from`. Content shifted up by
one relative to its label, so **every displayed number read one low and the
highlight box landed on the following statement**.

### Where it was *not*

Ruled out by measurement, not assumption:

- **Not the instrumenter / trace.** `SourceLocation.line == TraceEvent.line_number`
  held for all 166 events across five algorithms.
- **Not `CodeState`.** `driver.py sync` reported `mismatches=0` throughout —
  the window content and offset arithmetic were always right.
- **Not `line_numbers_from`.** In isolation it behaves correctly:
  `line_numbers_from=3` over 3 lines renders `3,4,5`.

The defect existed **only** in the interaction: a correct window handed to
Manim, whose stripping our labels did not account for.

### How it was caught

Not by a test — by rendering Palindrome, extracting a frame, and cropping
the code panel at 3×. At full-frame scale the panel is small enough that the
labels are unreadable, which is why it survived earlier inspection. The
programmatic checks in place at the time all passed, because none of them
looked at the rendered label.

## The fix

`visualization/code_state.py::_trim_blank_edges` — trim leading and trailing
empty lines from the window and advance `start_line` to match, so Manim has
nothing left to strip.

```python
lines, start_line = _trim_blank_edges(all_lines[start - 1 : end], start, location.line)
```

It never trims past the highlighted line, so a highlight legitimately sitting
on a blank line stays visible.

### Why this is architecturally correct

- **`ExecutionTrace` stays canonical.** Untouched.
- **`SourceLocation.line` still equals the original Java source line.** The
  fix never rewrites a semantic line number to compensate for rendering.
- **`CodeState` semantics are unchanged.** Its contract was already
  "`lines[i]` is file line `start_line + i`"; the fix *preserves* that
  invariant through rendering rather than redefining it.
- **The Manim-specific knowledge is confined to one private helper** whose
  docstring states the measured behaviour it compensates for. No Manim type
  or concept enters the domain model.
- **Smallest correct fix.** One helper, ~20 lines, two call sites. No new
  mapping subsystem, no renderer redesign, no dependency.

Chosen over the alternative (adjusting `line_numbers_from` inside
`code_panel`) because trimming makes the state render-safe for *any*
renderer, and keeps the invariant true at the layer that declares it.

## Reuse audit

Focused, per the brief. **Result: no change, no new dependency.**

| Candidate | Verdict |
|---|---|
| Manim `Code` / `code_lines` / `line_numbers_from` | Already in use; sufficient once its blank-line behaviour is accounted for. **Keep.** |
| Another Manim version | **Rejected** — the brief forbids version-shopping to make a test pass, and the defect is compensable. |
| Another visualization library | **Rejected** — Phase 4.2A decision stands; nothing here proves Manim insufficient. |
| A new line-mapping subsystem | **Rejected** — over-engineering for a 20-line helper. |

## Verification

### Programmatic (authoritative)

`tests/test_line_mapping.py` — 35 tests. The key piece is
`decode_displayed_labels`, which recovers the labels Manim will actually
draw: the line-number Paragraph's `lines_text` is every label concatenated
with no separator (`'9'+'10'+'11'` → `'91011'`), and each line's glyph count
gives its digit width. Walking those widths reconstructs the real labels —
rather than assuming they follow `line_numbers_from`, which is the exact
assumption the defect hid behind.

On **real traces**, all five algorithms:

| algorithm | events | verified | files |
|---|---|---|---|
| reverse_string | 25 | 23 | 2 |
| palindrome | 30 | 28 | 1 |
| two_sum | 17 | 15 | 1 |
| move_zeroes | 67 | 65 | 1 |
| remove_duplicates | 37 | 35 | 1 |
| **total** | **176** | **166** | — |

**0 mismatches.** (The 10 unverified events are `PROGRAM_START`/`PROGRAM_END`,
which carry no source line.)

### Visual (supporting)

Frames extracted and code panels cropped at 3×:

- **move_zeroes** — labels start at `1` (`package …` = real line 1), the
  interior blank at line 2 is preserved, box on line **7**
  `int value = nums[fast];`, caption `nums[1] -> 1` with `fast=1`. Exact.
- **two_sum** — the *windowed* case: labels **5–17** (start ≠ 1), box on line
  **11** `int b = nums[j];`, caption `nums[1] -> 7` with `j=1`. Exact.
- **palindrome** — before the fix: first label `2`, box on
  `char b = chars[right];`. After: first label `3`, box on line **8**
  `char a = chars[left];`, matching caption `chars[0] -> R`.

### Regression protection

These fail if the fix is reverted — verified by temporarily disabling
`_trim_blank_edges` and confirming both failed:

- `test_line_mapping.py::test_window_starting_on_a_blank_line_keeps_labels_correct`
- `test_code_synchronization.py::test_window_never_starts_or_ends_on_a_blank_line`

Plus `test_manim_strips_blank_edges_but_our_windows_never_expose_it`, which
pins the upstream Manim behaviour so a future version change is noticed.

## Performance

No measurable impact.

| | value |
|---|---|
| `_trim_blank_edges` | **0.31 µs/call** |
| `build_code_state` (incl. trim) | 4.9 µs/call |
| added cost per render (~4 steps) | **≈ 0.02 ms** |

The 5-algorithm table moved from a 27.14 s to a 30.00 s mean, but that is
**not** attributable to the fix: 0.02 ms cannot produce a 2.9 s delta. Three
back-to-back renders of the identical scene measured 17.6 / 17.7 / 17.7 s in
isolation versus 24.2 s for the same algorithm inside the sequential
5-algorithm script — a 37 % context difference that dwarfs any possible
effect. Trimming also *removes* lines, so if anything it renders marginally
less.

## Test results

| Run | Result |
|---|---|
| `pytest -m "not integration"` | **299 passed**, 78 deselected, 12.6 s |
| `pytest tests/test_e2e_golden_path.py` | **6 passed**, 157 s |
| `pytest` (full) | **377 passed**, 0 failed, 0 skipped, 383 s |
| `scripts/run_narrated_golden_path.py` | completed, total 27.3 s |

Phase 4.5 count was 342; +35 from `test_line_mapping.py`.

## Known limitations

- **Emoji in Java source breaks the render.** Manim raises
  `ValueError: Text '…' rendered fewer glyph(s) than its non-space
  characters` because the font has no 1:1 glyph. `CodeState` maps the line
  correctly; the failure is purely Manim text shaping. Documented and pinned
  by `test_emoji_in_source_is_a_known_manim_limitation_not_a_mapping_bug`
  rather than worked around, since any workaround (font substitution, glyph
  stripping) would alter the displayed source. Other Unicode — accents, CJK,
  Kannada, mathematical symbols — renders and maps correctly.
- **`decode_displayed_labels` depends on glyph-per-character correspondence.**
  It is test-only, and would need revisiting if a ligature-heavy font were
  adopted.
- **Verified against Manim 0.21.0 only.** A future upgrade should re-run
  `test_manim_strips_blank_edges_but_our_windows_never_expose_it` first.

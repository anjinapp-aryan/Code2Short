"""Code synchronization: ExecutionTrace -> SourceLocation -> CodeState.

The architectural rule this module enforces:

    The trace determines WHAT executed. The renderer determines HOW it
    looks. An LLM may explain the execution but never decides which source
    line ran.

Nothing here consults a VisualizationPlan's opinion about lines — the
highlighted line is derived exclusively from `TraceEvent.line_number` plus
the method context the event occurred in. A plan can choose *which trace
event* to show; it cannot choose which line that event happened on.

Dependency direction: this imports `core` only. No Manim types appear in
any model here; the renderer turns CodeState into mobjects, not this.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from code2shorts.core.models import ExecutionTrace, SourceLocation, TraceEventType

DEFAULT_WINDOW_RADIUS = 4
"""Lines shown above and below the executing line, so 9 lines at most.

Phase 6.1 lowered this from 6 (a 13-line window). The code band is 2.30
units tall and the readability floor is 0.20 units (48 px) per line, so a
13-line window plus its chrome could only fit by scaling the text below
that floor. Showing fewer lines LARGER teaches better than showing more
lines that nobody can read — and the window still spans the whole
construct in every supported fixture (a loop body plus its condition).

This is a presentation choice only: `SourceLocation.line` and the
highlighted line are unaffected, so the Phase 4.5.1 mapping invariant
holds regardless of how many lines are visible."""


class CodeState(BaseModel):
    """Presentation-ready code view for one trace event.

    Deliberately free of Manim types and of source *content* decisions:
    it reports which real file, which real lines, and which real line to
    highlight. It never alters source text.
    """

    file: str | None = None
    lines: list[str] = Field(default_factory=list, description="the visible window")
    start_line: int = 1
    highlight_line: int | None = None
    total_lines: int = 0
    truncated: bool = False

    @property
    def highlight_offset(self) -> int | None:
        """0-based index of the highlighted line WITHIN `lines`."""
        if self.highlight_line is None:
            return None
        offset = self.highlight_line - self.start_line
        return offset if 0 <= offset < len(self.lines) else None


def _class_of_method(method: str | None) -> str | None:
    """'ReverseString.reverse' -> 'ReverseString'."""
    if not method:
        return None
    return method.rsplit(".", 1)[0] if "." in method else None


def _file_for_class(class_name: str | None, source_files: dict[str, str]) -> str | None:
    if not class_name:
        return None
    suffix = f"/{class_name}.java"
    for path in source_files:
        normalized = path.replace("\\", "/")
        if normalized.endswith(suffix) or normalized.endswith(f"{class_name}.java"):
            return path
    return None


def resolve_source_locations(
    trace: ExecutionTrace, source_files: dict[str, str], entry_point: str | None = None
) -> dict[int, SourceLocation]:
    """Map every trace event to the real file/line/method it occurred at.

    File identity is DERIVED from method context rather than stored per
    event: `METHOD_ENTER` carries "Class.method", so the innermost open
    method determines the file, and events before any method entry belong
    to the entry-point file (the instrumented `main`). This needs no change
    to the trace runtime or instrumenter and is exact for the supported
    Java subset, which has no nested/anonymous classes or lambdas — the
    constructs that would break the assumption are already rejected at
    instrument time by DenylistValidator.
    """
    locations: dict[int, SourceLocation] = {}
    stack: list[str] = []  # open methods, innermost last

    for event in trace.events:
        if event.event_type == TraceEventType.METHOD_ENTER.value and event.method:
            stack.append(event.method)

        method = stack[-1] if stack else None
        class_name = _class_of_method(method)
        file = _file_for_class(class_name, source_files)
        if file is None:
            file = entry_point if entry_point in source_files else None

        locations[event.step_index] = SourceLocation(
            file=file,
            line=event.line_number,
            method=method.rsplit(".", 1)[-1] if method and "." in method else method,
            class_name=class_name,
        )

        if event.event_type == TraceEventType.METHOD_EXIT.value and stack:
            stack.pop()

    return locations



def enclosing_block(lines: list[str], line: int) -> tuple[int, int] | None:
    """The brace-delimited block containing `line`, as 1-based inclusive
    bounds, or None if it cannot be determined confidently.

    Purely structural — it counts braces and knows nothing about methods,
    classes or algorithms. A learner following execution inside one method
    is not helped by the file's package declaration or the `main` wrapper
    scrolling past, so when the enclosing block fits the visible window the
    renderer prefers it over a window centred blindly on the cursor.

    Returns None for a block that would not fit; the caller then falls back
    to the centred window, which always contains the executing line.
    """
    if line < 1 or line > len(lines):
        return None

    # Walk backwards to the innermost unclosed '{' that opens a block
    # containing this line.
    depth = 0
    start: int | None = None
    for index in range(line - 1, -1, -1):
        text = lines[index]
        depth += text.count("}") - text.count("{")
        if depth < 0:                      # found an unmatched opener
            start = index + 1              # 1-based
            break
    if start is None:
        return None

    depth = 0
    for index in range(start - 1, len(lines)):
        text = lines[index]
        depth += text.count("{") - text.count("}")
        if depth == 0 and index + 1 >= line:
            return start, index + 1
    return None



def _first_nested_block_line(all_lines: list[str]) -> int | None:
    """1-based line of the first INDENTED block opener, or None.

    In a Java class file the first block opener at column 0 is the class
    itself; the first one that is indented is the first method. Anchoring
    an introduction window there skips the package declaration and the
    class header without knowing what either of those things is.

    Returns None when the shape is unexpected, so the caller keeps its
    previous behaviour rather than guessing.
    """
    for index, text in enumerate(all_lines):
        stripped = text.strip()
        if not stripped or not stripped.endswith("{"):
            continue
        if len(text) - len(text.lstrip(" ")) > 0:
            return index + 1
    return None


def build_code_state(
    location: SourceLocation,
    source_files: dict[str, str],
    window_radius: int = DEFAULT_WINDOW_RADIUS,
) -> CodeState:
    """Window the real source around the executing line.

    Long files are windowed rather than shrunk: scaling a 200-line file to
    fit a 9:16 code band makes every line unreadable, which defeats the
    purpose of showing code at all. Short files are shown whole.

    The window is deterministic — same location and source always produce
    the same range — and clamps at both ends so the executing line stays
    visible even at the very start or end of a file.
    """
    if location.file is None or location.file not in source_files:
        return CodeState()

    all_lines = source_files[location.file].splitlines()
    total = len(all_lines)
    if total == 0:
        return CodeState(file=location.file, total_lines=0)

    window_size = window_radius * 2 + 1
    if location.line is None:
        # The introduction window is deliberately NOT widened by the
        # fitter. Given no executing line the fitter grew this to 14 lines,
        # and a window that tall mixes long signatures with short braces,
        # so the representative-width sizing produced a very wide spread of
        # per-line sizes. Fewer lines, more evenly sized, reads better as
        # an establishing shot.
        window_size = DEFAULT_WINDOW_RADIUS * 2 + 1
    if location.line is None or total <= window_size:
        start = 1
        truncated = False
        if location.line is None and total > window_size:
            # The INTRODUCTION step cites no source line, so this used to
            # fall through to "show the whole file" - all 24 lines of a
            # real fixture. A window that tall is bound by HEIGHT
            # rather than width, so the panel shrank to fit the band and
            # the opening frame carried the smallest text in the video,
            # most of it package/class/main boilerplate.
            #
            # With no executing line to centre on, the algorithm itself is
            # the subject, so the window opens at the first block that is
            # nested inside something else - in Java that is the first
            # method, never the package line or the class declaration.
            # Purely structural: it reads indentation and braces, and
            # knows nothing about methods, algorithms or line numbers.
            start = _first_nested_block_line(all_lines) or 1
            truncated = start > 1 or total > window_size
        window = all_lines[start - 1 : start + window_size - 1] \
            if (location.line is None and total > window_size) else all_lines
        lines, start_line = _trim_blank_edges(window, start, location.line)
        return CodeState(
            file=location.file,
            lines=lines,
            start_line=start_line,
            highlight_line=location.line,
            total_lines=total,
            truncated=truncated,
        )

    # Prefer the enclosing block when it fits: following execution inside
    # one method is clearer than a window centred blindly on the cursor,
    # which drags in the package declaration or the main() wrapper. Falls
    # back to the centred window whenever the block does not fit.
    block = enclosing_block(all_lines, location.line)
    if block is not None and block[1] - block[0] + 1 <= window_size:
        start, end = block
        # Spend any spare room on context after the block rather than
        # leaving the panel half empty.
        spare = window_size - (end - start + 1)
        if spare > 0:
            # Split the spare room ABOVE and below. Spending it all
            # downward left the executing line pinned to the top of the
            # panel: at `if (a != b)` the window began on that very line,
            # so the two reads it compares — `char a = chars[left]` and
            # `char b = chars[right]`, named in the caption — were off
            # screen. Context is what makes the active line mean anything
            # (section 9), and half of it lives above.
            above = spare // 2
            start = max(1, start - above)
            end = min(total, start + window_size - 1)
            start = max(1, end - window_size + 1)
    else:
        # The block does not fit, so the window must be narrower than it.
        # Centre on the executing line, but STAY INSIDE the block.
        #
        # Phase 6.4, measured: a blind centred window at `int left = 0;`
        # (line 5) spanned lines 1-9, which pulled in the package
        # declaration and the 54-character method signature. Because the
        # panel is fitted to the safe width, the longest visible line sets
        # the font for every line, so those two boilerplate lines shrank
        # the code from 79 px per line to 44 px - a 1.8x reduction caused
        # entirely by text the learner does not need to read.
        #
        # Clamping to the block's interior keeps the window on the code
        # being executed. It is a purely structural rule: `enclosing_block`
        # counts braces and knows nothing about methods or algorithms.
        start = max(1, location.line - window_radius)
        end = min(total, start + window_size - 1)
        start = max(1, end - window_size + 1)
        if block is not None:
            interior_start, interior_end = block[0] + 1, block[1] - 1
            # Only clamp when the executing line is INSIDE the interior.
            # The active line can be the block's own opening line - a
            # METHOD_ENTER event highlights the signature - and clamping
            # then pushes the window past it. The regression test
            # `test_real_trace_line_mapping_holds_to_the_displayed_label`
            # caught exactly that on remove_duplicates: the window started
            # at line 5 while line 4 was highlighted, so the panel showed
            # no highlight at all. Keeping the executing line visible
            # outranks every readability gain.
            if (
                interior_start <= location.line <= interior_end
                and interior_end - interior_start + 1 >= window_size
            ):
                start = min(max(start, interior_start), interior_end - window_size + 1)
                end = start + window_size - 1

    lines, start_line = _trim_blank_edges(
        all_lines[start - 1 : end], start, location.line
    )
    return CodeState(
        file=location.file,
        lines=lines,
        start_line=start_line,
        highlight_line=location.line,
        total_lines=total,
        truncated=True,
    )


def _trim_blank_edges(
    lines: list[str], start_line: int, highlight_line: int | None
) -> tuple[list[str], int]:
    """Drop leading/trailing EMPTY lines, adjusting `start_line` to match.

    This is a correctness fix, not cosmetics. Manim's `Code` mobject
    silently discards leading and trailing empty lines while its line-number
    column keeps counting from `line_numbers_from`. A window that began on a
    blank line therefore rendered every label one too low and drew the
    highlight box around the *following* statement — the video pointed
    viewers at the wrong line of code.

    Verified Manim behaviour (0.21): leading and trailing truly-empty lines
    are stripped (all of them), interior blanks are kept, and whitespace-only
    lines such as "   " are kept. This trims exactly the same set, so the
    invariant `lines[i]` is file line `start_line + i` survives rendering.

    Never trims past the highlighted line, so a highlight that legitimately
    sits on a blank line stays visible.
    """
    leading = 0
    while leading < len(lines) and lines[leading] == "":
        if highlight_line is not None and start_line + leading >= highlight_line:
            break
        leading += 1
    lines = lines[leading:]
    start_line += leading

    while lines and lines[-1] == "":
        last_line_number = start_line + len(lines) - 1
        if highlight_line is not None and last_line_number <= highlight_line:
            break
        lines = lines[:-1]

    return lines, start_line

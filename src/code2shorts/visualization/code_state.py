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
    if location.line is None or total <= window_size:
        lines, start_line = _trim_blank_edges(all_lines, 1, location.line)
        return CodeState(
            file=location.file,
            lines=lines,
            start_line=start_line,
            highlight_line=location.line,
            total_lines=total,
            truncated=False,
        )

    # Center the window on the executing line, then clamp so a line near
    # either end of the file still yields a full-size window.
    start = max(1, location.line - window_radius)
    end = min(total, start + window_size - 1)
    start = max(1, end - window_size + 1)

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

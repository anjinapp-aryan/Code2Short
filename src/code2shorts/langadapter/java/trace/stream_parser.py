"""TraceStreamParser: separates a traced program's own stdout from the
TRACE: lines Code2ShortsTrace emits, preserving order. A traced program may
legitimately call System.out.println — that output must never be silently
lost or mistaken for a trace event, and vice versa.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

_PREFIX = "TRACE:"


@dataclass
class ParsedStream:
    program_output: str
    raw_events: list[dict] = field(default_factory=list)
    malformed_line_count: int = 0


class TraceStreamParser:
    def parse(self, stdout: str) -> ParsedStream:
        output_lines: list[str] = []
        raw_events: list[dict] = []
        malformed_line_count = 0

        for line in stdout.splitlines():
            if line.startswith(_PREFIX):
                try:
                    raw_events.append(json.loads(line[len(_PREFIX) :]))
                except json.JSONDecodeError:
                    malformed_line_count += 1
            else:
                output_lines.append(line)

        return ParsedStream(
            program_output="\n".join(output_lines).strip(),
            raw_events=raw_events,
            malformed_line_count=malformed_line_count,
        )

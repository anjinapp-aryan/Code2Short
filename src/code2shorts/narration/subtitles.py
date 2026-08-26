"""SRT subtitle generation from aligned narration.

Uses the `srt` library (MIT), which is **already installed** as a Manim
transitive dependency — zero new dependency, and no reason to hand-roll a
format that has a correct, frozen implementation available. See
docs/PHASE_4_4_REUSE_AUDIT.md.

Subtitle text is treated as untrusted data throughout: it is written into
a data file, never into a command.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import srt

from code2shorts.core.models import ValidationResult
from code2shorts.narration.alignment import AlignmentResult

# A cue is terminated by a blank line, and the `srt` parser is lenient
# enough to start a NEW cue at any line that looks like an index followed
# by a timing line — no blank line required. Any newline surviving inside
# cue text is therefore a cue-injection vector, not a formatting nuisance.
_FORBIDDEN = ("\x00",)
_NEWLINES = ("\r\n", "\r", "\n", " ", " ", "\x0b", "\x0c", "\x85")


def sanitize_subtitle_text(text: str) -> str:
    """Make text safe to embed in an SRT cue, changing as little as possible.

    Newlines are collapsed to spaces. This is a SECURITY control, not
    cosmetics: a payload containing a blank line followed by an index and
    an SRT timing line otherwise parses back as a **second cue** with
    attacker-chosen timestamps.

    Found by `test_srt_injection_cannot_forge_extra_cues`, which failed
    against an earlier single-pass newline-pair collapse — that removed the
    blank line but left the injected timing line intact, and the lenient
    parser accepted it anyway. Collapsing every newline removes the vector
    entirely, because a timing line can only ever begin at the start of a
    line.

    A narration segment is one spoken utterance, so it has no legitimate
    need for internal line breaks. Everything else — quotes, apostrophes,
    ampersands, Unicode, emoji, and Java syntax such as arr[i] or
    HashMap<K,V> — is preserved exactly, because subtitle text is written
    to a data file and never reaches a shell.
    """
    cleaned = "".join(ch for ch in text if ch not in _FORBIDDEN)
    for newline in _NEWLINES:
        cleaned = cleaned.replace(newline, " ")
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned.strip()


def build_srt(alignment: AlignmentResult) -> str:
    """Render aligned segments as an SRT document."""
    subtitles = [
        srt.Subtitle(
            index=position,
            start=timedelta(seconds=segment.start_seconds),
            end=timedelta(seconds=segment.end_seconds),
            content=sanitize_subtitle_text(segment.text),
        )
        for position, segment in enumerate(alignment.segments, start=1)
    ]
    return srt.compose(subtitles)


def write_srt(alignment: AlignmentResult, output_path: Path) -> Path:
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_srt(alignment), encoding="utf-8")
    return output_path


def validate_srt(content: str) -> ValidationResult:
    """Parse the produced SRT back and check the guarantees a player needs.

    Round-tripping through the parser is the point: it proves the document
    we emitted is actually well-formed, rather than merely that our
    generator believed it was.
    """
    errors: list[str] = []
    try:
        subtitles = list(srt.parse(content))
    except Exception as error:  # noqa: BLE001 - srt raises assorted parse errors
        return ValidationResult(
            stage="domain", passed=False, errors=[f"SRT does not parse: {error}"]
        )

    if not subtitles:
        errors.append("SRT contains no cues")

    previous_end = timedelta(seconds=-1)
    for subtitle in subtitles:
        if subtitle.start.total_seconds() < 0 or subtitle.end.total_seconds() < 0:
            errors.append(f"cue {subtitle.index} has a negative timestamp")
        if subtitle.end <= subtitle.start:
            errors.append(f"cue {subtitle.index} has end <= start")
        if subtitle.start + timedelta(microseconds=1) < previous_end:
            errors.append(f"cue {subtitle.index} is not monotonic")
        if any(ch in subtitle.content for ch in _FORBIDDEN):
            errors.append(f"cue {subtitle.index} contains a forbidden control character")
        if any(nl in subtitle.content for nl in _NEWLINES):
            errors.append(f"cue {subtitle.index} contains a newline (cue-injection vector)")
        previous_end = max(previous_end, subtitle.end)

    return ValidationResult(stage="domain", passed=not errors, errors=errors)

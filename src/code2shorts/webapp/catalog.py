"""Which programs the UI offers, and where their Java lives.

Phase 7.0 offers exactly one: PALINDROME. The brief is explicit that one
program must work end to end before the flow generalises, and a picker
listing six algorithms would invite exactly the broad testing this phase
defers. The shape is a registry rather than a constant, so adding the
others later is data - but they are deliberately not listed yet.

Source is READ from a directory, not imported from the test package:
`src/` must not depend on `tests/`. The default root is the repository's
existing Java fixtures, so the UI generates from byte-identical source to
the one the pipeline tests and golden paths use; `CODE2SHORTS_PROGRAMS_DIR`
points it elsewhere for a deployment that ships its own programs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_PROGRAMS_DIR = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "java" / "algorithms"
)
ENTRY_POINT = "src/main/java/com/code2shorts/algorithms/Main.java"


def programs_dir() -> Path:
    override = os.environ.get("CODE2SHORTS_PROGRAMS_DIR")
    return Path(override) if override else DEFAULT_PROGRAMS_DIR


@dataclass(frozen=True)
class Program:
    slug: str
    title: str
    summary: str
    input_value: str
    expected_output: str


PROGRAMS: dict[str, Program] = {
    "palindrome": Program(
        slug="palindrome",
        title="Palindrome",
        summary="Check whether a string reads the same forwards and backwards, "
        "using two pointers that walk inward.",
        input_value="RACECAR",
        expected_output="true",
    ),
}


def get(slug: str) -> Program | None:
    return PROGRAMS.get(slug)


@lru_cache(maxsize=8)
def source_for(slug: str) -> tuple[tuple[tuple[str, str], ...], str]:
    """The Java source, as a hashable pair so it can be cached.

    Cached because the fingerprint hashes this on every page load and a
    library listing must not re-read the source from disk each time.

    `slug` reaches this from a URL, so it is validated against the
    registry BEFORE it is used to build a path - a slug that is not a
    known program never becomes a directory name.
    """
    if slug not in PROGRAMS:
        raise KeyError(slug)
    root = programs_dir() / slug
    files = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.java"))
    }
    if not files:
        raise FileNotFoundError(f"no Java source found for {slug!r} under {root}")
    return tuple(files.items()), ENTRY_POINT


def source_files(slug: str) -> tuple[dict[str, str], str]:
    files, entry_point = source_for(slug)
    return dict(files), entry_point

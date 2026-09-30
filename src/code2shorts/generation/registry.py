"""The library: which videos exist, what produced them, and in what order.

Videos already live on disk under `output/`, but as bare files with no
record of what configuration produced them. That is why nothing could
answer "do I already have this?" - the question needs the inputs, and the
filesystem only kept the result.

Storage is a JSON index plus per-version directories:

    <root>/
      index.json                     every version ever produced
      palindrome/
        v1/  final.mp4  trace.json  plan.json  narration.wav  ...
        v2/  ...

Deliberately a file, not a database. The registry holds tens of rows, is
read far more than written, and must work on a machine with nothing
installed. If it ever needs concurrent writers, that is the moment to
introduce one - not before.

A NEW VERSION IS ALWAYS A NEW DIRECTORY. Nothing here overwrites a
previous render, because the whole point of versioning under active
development is that a newer render cannot destroy the ability to inspect
the previous one.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from code2shorts.generation.fingerprint import ContentFingerprint, RequestFingerprint


class GenerationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


REUSABLE = (GenerationStatus.COMPLETED,)
"""Only a COMPLETED version may be served as "you already have this".

A failed or cancelled run can leave a plausible-looking MP4 behind - a
render that finished before composition failed, say - and treating that as
a hit would serve a video the pipeline itself rejected."""


class GenerationVersion(BaseModel):
    """One generation attempt, successful or not."""

    version: int
    algorithm: str
    status: GenerationStatus = GenerationStatus.QUEUED
    request_fingerprint: RequestFingerprint
    content_fingerprint: ContentFingerprint | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    title: str = ""
    error: str | None = None
    artifacts: dict[str, str] = Field(
        default_factory=dict,
        description="logical name -> path RELATIVE to this version's "
        "directory. Relative so the library survives being moved, and so "
        "no absolute filesystem path is ever handed to a browser.",
    )

    @property
    def is_reusable(self) -> bool:
        return self.status in REUSABLE and "final_video" in self.artifacts


_ROOT_LOCKS: dict[Path, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


def _lock_for(root: Path) -> threading.RLock:
    """One lock per library ROOT, not per instance: two registries opened
    on the same directory in one process must still take turns."""
    key = root.resolve()
    with _ROOT_LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


REPLACE_ATTEMPTS = 20
REPLACE_BACKOFF_SECONDS = 0.02
"""Windows refuses `os.replace` onto a file another thread has open for
reading (WinError 5) - a page load reading the index while a job saves it.
The window is milliseconds, so a short bounded retry closes it; a replace
that still fails after ~0.4 s is a real error and is raised."""


class GenerationRegistry:
    """Reads and writes the library index.

    Every mutation rewrites the whole index through a temporary file and
    an atomic replace, so an interrupted write cannot leave a half-written
    index behind - losing the record of a 3-minute render to a truncated
    JSON file is not an acceptable failure mode.
    """

    INDEX_NAME = "index.json"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_for(self._root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def index_path(self) -> Path:
        return self._root / self.INDEX_NAME

    # ---- reading ---------------------------------------------------------

    def all_versions(self) -> list[GenerationVersion]:
        if not self.index_path.is_file():
            return []
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A corrupt index must not take the application down. It is a
            # cache of things that are still on disk, so report empty and
            # let the next write rebuild it.
            return []
        return [GenerationVersion.model_validate(row) for row in raw.get("versions", [])]

    def versions_for(self, algorithm: str) -> list[GenerationVersion]:
        """Newest first."""
        return sorted(
            (v for v in self.all_versions() if v.algorithm == algorithm),
            key=lambda v: v.version,
            reverse=True,
        )

    def current_version(self, algorithm: str) -> GenerationVersion | None:
        """The newest REUSABLE version, which is not always the newest.

        A failed v3 does not demote a working v2 - the library must keep
        showing the video that plays.
        """
        for version in self.versions_for(algorithm):
            if version.is_reusable:
                return version
        return None

    def get(self, algorithm: str, version: int) -> GenerationVersion | None:
        for candidate in self.versions_for(algorithm):
            if candidate.version == version:
                return candidate
        return None

    def find_reusable(
        self, fingerprint: RequestFingerprint
    ) -> GenerationVersion | None:
        """An existing, completed video generated from exactly these
        inputs. This is the duplicate-generation guard."""
        digest = fingerprint.digest
        for version in self.versions_for(fingerprint.algorithm):
            if version.is_reusable and version.request_fingerprint.digest == digest:
                return version
        return None

    def algorithms(self) -> list[str]:
        return sorted({v.algorithm for v in self.all_versions()})

    # ---- paths -----------------------------------------------------------

    def version_dir(self, algorithm: str, version: int) -> Path:
        return self._root / algorithm / f"v{version}"

    def artifact_path(self, version: GenerationVersion, name: str) -> Path | None:
        """Resolve one artifact, refusing anything outside its own version
        directory.

        The name reaches this from an HTTP route, so it is untrusted:
        `../../../etc/passwd` as an artifact name must not resolve. The
        check is containment after resolution, which also catches symlinks
        and Windows short names - matching on ".." would not.
        """
        relative = version.artifacts.get(name)
        if relative is None:
            return None
        base = self.version_dir(version.algorithm, version.version).resolve()
        candidate = (base / relative).resolve()
        if not candidate.is_relative_to(base):
            return None
        return candidate if candidate.is_file() else None

    # ---- writing ---------------------------------------------------------

    def next_version_number(self, algorithm: str) -> int:
        """One past the highest version the index OR the disk knows of.

        The disk counts because a version directory is the thing that must
        never be reused: if the index ever lost a row, numbering from the
        index alone would hand out `v1/` again, on top of a real video.
        """
        existing = [v.version for v in self.versions_for(algorithm)]
        algorithm_dir = self._root / algorithm
        if algorithm_dir.is_dir():
            for child in algorithm_dir.iterdir():
                number = child.name.removeprefix("v")
                if child.is_dir() and child.name.startswith("v") and number.isdigit():
                    existing.append(int(number))
        return max(existing, default=0) + 1

    def create_version(
        self,
        algorithm: str,
        fingerprint: RequestFingerprint,
        title: str = "",
    ) -> GenerationVersion:
        # Number, directory and row are allocated as one step, so two
        # generations started together cannot both become "v2".
        with self._lock:
            version = GenerationVersion(
                version=self.next_version_number(algorithm),
                algorithm=algorithm,
                request_fingerprint=fingerprint,
                title=title,
            )
            # exist_ok=False: a new version is always a NEW directory.
            self.version_dir(algorithm, version.version).mkdir(parents=True, exist_ok=False)
            self.save(version)
            return version

    def save(self, version: GenerationVersion) -> None:
        # Read-modify-write of the whole index; unlocked, two concurrent
        # saves each wrote back only their own row.
        with self._lock:
            versions = [
                existing
                for existing in self.all_versions()
                if not (
                    existing.algorithm == version.algorithm
                    and existing.version == version.version
                )
            ]
            versions.append(version)
            versions.sort(key=lambda v: (v.algorithm, v.version))
            self._write({"versions": [v.model_dump(mode="json") for v in versions]})

    def _write(self, payload: dict) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self._root, delete=False, suffix=".tmp"
        )
        try:
            with handle as stream:
                json.dump(payload, stream, indent=2)
            for attempt in range(REPLACE_ATTEMPTS):
                try:
                    Path(handle.name).replace(self.index_path)
                    break
                except PermissionError:
                    if attempt == REPLACE_ATTEMPTS - 1:
                        raise
                    time.sleep(REPLACE_BACKOFF_SECONDS)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise

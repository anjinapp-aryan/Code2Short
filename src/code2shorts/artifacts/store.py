"""ArtifactStore: save/get/exists/find_by_checksum/list_children.

Phase 3 provides one real, tested implementation (InMemoryArtifactStore) —
suitable for local dev/tests. No PostgreSQL/Redis/S3 — those are future
adapters behind this same interface, added only when an explicit
requirement needs them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from code2shorts.artifacts.models import Artifact


class ArtifactStore(ABC):
    @abstractmethod
    def save(self, artifact: Artifact) -> None: ...

    @abstractmethod
    def get(self, artifact_id: str) -> Artifact | None: ...

    @abstractmethod
    def exists(self, artifact_id: str) -> bool: ...

    @abstractmethod
    def find_by_checksum(self, checksum: str) -> Artifact | None: ...

    @abstractmethod
    def list_children(self, artifact_id: str) -> list[Artifact]:
        """Artifacts whose input_artifact_ids includes artifact_id."""


class InMemoryArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self._by_id: dict[str, Artifact] = {}
        self._by_checksum: dict[str, str] = {}  # checksum -> first artifact id seen

    def save(self, artifact: Artifact) -> None:
        self._by_id[artifact.id] = artifact
        self._by_checksum.setdefault(artifact.checksum, artifact.id)

    def get(self, artifact_id: str) -> Artifact | None:
        return self._by_id.get(artifact_id)

    def exists(self, artifact_id: str) -> bool:
        return artifact_id in self._by_id

    def find_by_checksum(self, checksum: str) -> Artifact | None:
        artifact_id = self._by_checksum.get(checksum)
        return self._by_id.get(artifact_id) if artifact_id else None

    def list_children(self, artifact_id: str) -> list[Artifact]:
        return [a for a in self._by_id.values() if artifact_id in a.input_artifact_ids]

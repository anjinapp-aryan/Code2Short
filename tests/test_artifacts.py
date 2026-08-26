from code2shorts.artifacts import Artifact, ArtifactType, InMemoryArtifactStore


def test_create_computes_checksum_deterministically() -> None:
    a = Artifact.create(ArtifactType.SOURCE, "test", content={"x": 1, "y": 2})
    b = Artifact.create(ArtifactType.SOURCE, "test", content={"y": 2, "x": 1})
    assert a.checksum == b.checksum
    assert a.id != b.id  # distinct instances even with identical content


def test_different_content_produces_different_checksum() -> None:
    a = Artifact.create(ArtifactType.SOURCE, "test", content={"x": 1})
    b = Artifact.create(ArtifactType.SOURCE, "test", content={"x": 2})
    assert a.checksum != b.checksum


def test_store_save_get_exists() -> None:
    store = InMemoryArtifactStore()
    artifact = Artifact.create(ArtifactType.SOURCE, "test", content={"x": 1})
    assert not store.exists(artifact.id)
    store.save(artifact)
    assert store.exists(artifact.id)
    assert store.get(artifact.id) == artifact
    assert store.get("nonexistent") is None


def test_find_by_checksum_returns_first_match() -> None:
    store = InMemoryArtifactStore()
    a = Artifact.create(ArtifactType.SOURCE, "test", content={"x": 1})
    b = Artifact.create(ArtifactType.SOURCE, "test", content={"x": 1})  # same content
    store.save(a)
    store.save(b)
    found = store.find_by_checksum(a.checksum)
    assert found is not None
    assert found.id == a.id  # first one saved wins
    assert store.find_by_checksum("nonexistent") is None


def test_lineage_via_list_children() -> None:
    store = InMemoryArtifactStore()
    source = Artifact.create(ArtifactType.SOURCE, "test", content={"x": 1})
    store.save(source)
    compilation = Artifact.create(
        ArtifactType.COMPILATION, "test", content={"ok": True}, input_artifact_ids=[source.id]
    )
    store.save(compilation)
    trace = Artifact.create(
        ArtifactType.TRACE, "test", content={"ok": True}, input_artifact_ids=[compilation.id]
    )
    store.save(trace)

    children_of_source = store.list_children(source.id)
    assert [c.id for c in children_of_source] == [compilation.id]

    children_of_compilation = store.list_children(compilation.id)
    assert [c.id for c in children_of_compilation] == [trace.id]

    assert store.list_children(trace.id) == []

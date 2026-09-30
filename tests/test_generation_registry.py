"""PHASE 7.0 — generation identity, duplicate prevention, versioning.

The critical behaviour this phase promises:

    generate palindrome        -> a video is produced
    generate palindrome again  -> NOTHING is generated
    press Re-generate          -> a new version IS produced
    the previous version       -> still there, still playable

Every test here runs WITHOUT Maven, a JVM, Manim, FFmpeg or a model. The
pipeline is replaced by a fake node list, which is the point: this file
tests the DECISION in front of the pipeline, not the pipeline. The
pipeline has its own tests and they are unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code2shorts.artifacts.models import Artifact, ArtifactType
from code2shorts.artifacts.store import InMemoryArtifactStore
from code2shorts.generation import (
    GenerationManager,
    GenerationRegistry,
    GenerationRequest,
    GenerationStatus,
    PipelineFactory,
    TeachingConfig,
    fingerprint_request,
    hash_source_files,
)
from code2shorts.generation.fingerprint import RENDERER_VERSION
from code2shorts.workflow import NodeResult, WorkflowNode

SOURCE = {
    "src/main/java/Main.java": (
        "public final class Main {\n"
        "    public static boolean isPalindrome(char[] c) { return true; }\n"
        "}\n"
    )
}


# ---- a fake pipeline: the same NODE interface, none of the tooling -------


class _FakeNode(WorkflowNode):
    """Stands in for one real node. Writes a file and registers an
    artifact exactly the way the real nodes do, so the manager's
    artifact-collection path is genuinely exercised."""

    def __init__(self, name: str, output_dir: Path, fail: bool = False) -> None:
        self.name = name
        self._output_dir = output_dir
        self._fail = fail

    def run(self, state, context) -> NodeResult:
        if self._fail:
            raise RuntimeError(f"{self.name} exploded")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        # `Artifact.create` computes the checksum, which is the same
        # construction path every real node uses - so the manager's
        # content fingerprint is exercised, not bypassed.
        if self.name == "compose_media":
            target = self._output_dir / "final.mp4"
            target.write_bytes(b"not-a-real-mp4-but-a-real-file")
            artifact = Artifact.create(
                type=ArtifactType.FINAL_VIDEO,
                producer=self.name,
                content={"path": str(target)},
            )
        else:
            artifact = Artifact.create(
                type=ArtifactType.TRACE,
                producer=self.name,
                content={"stage": self.name, "events": [1, 2]},
            )
        context.artifact_store.save(artifact)
        return NodeResult(
            state=state.model_copy(
                update={"artifact_ids": {**state.artifact_ids, self.name: artifact.id}}
            )
        )


class _FakePipeline(PipelineFactory):
    def __init__(self, fail_at: str | None = None) -> None:
        self.builds = 0
        self._fail_at = fail_at

    def build(self, request: GenerationRequest, output_dir: Path) -> list:
        self.builds += 1
        return [
            _FakeNode(name, output_dir, fail=(name == self._fail_at))
            for name in ("trace", "visualization_plan", "compose_media")
        ]


def _request(**overrides) -> GenerationRequest:
    values = {
        "algorithm": "palindrome",
        "source_files": SOURCE,
        "entry_point": "src/main/java/Main.java",
        "input_value": "RACECAR",
        "title": "Palindrome",
        "config": TeachingConfig(),
        "llm_provider": "mock",
    }
    values.update(overrides)
    return GenerationRequest(**values)


@pytest.fixture
def manager(tmp_path: Path):
    pipeline = _FakePipeline()
    return (
        GenerationManager(
            GenerationRegistry(tmp_path / "library"),
            pipeline,
            artifact_store=InMemoryArtifactStore(),
        ),
        pipeline,
    )


# ---- THE critical test ---------------------------------------------------


def test_generating_twice_does_not_generate_twice(manager) -> None:
    """The single most important behaviour in this phase.

    A second request for the same configuration must cost nothing: no
    Maven, no JVM, no LLM call, no Manim, no FFmpeg. The proof is that the
    pipeline was never even BUILT a second time.
    """
    generator, pipeline = manager

    first = generator.generate(_request())
    assert first.status is GenerationStatus.COMPLETED
    assert pipeline.builds == 1

    second = generator.generate(_request())

    assert pipeline.builds == 1, "the pipeline ran again for an identical request"
    assert second.version == first.version
    assert generator.registry.versions_for("palindrome") == [first]


def test_regenerate_is_explicit_and_keeps_the_previous_version(manager) -> None:
    """`force` is what the Re-generate button sets, and it must not
    destroy what it replaces."""
    generator, pipeline = manager

    first = generator.generate(_request())
    second = generator.generate(_request(), force=True)

    assert pipeline.builds == 2
    assert second.version == first.version + 1

    versions = generator.registry.versions_for("palindrome")
    assert [v.version for v in versions] == [2, 1], "newest first"

    # Both videos still exist, in their own directories.
    for version in versions:
        video = generator.registry.artifact_path(version, "final_video")
        assert video is not None and video.is_file()
    assert (
        generator.registry.artifact_path(versions[0], "final_video")
        != generator.registry.artifact_path(versions[1], "final_video")
    )


def test_the_current_version_is_the_newest_that_actually_works(tmp_path: Path) -> None:
    """A failed regeneration must not demote a working video.

    Otherwise a user who tries a new voice, hits a broken model and
    reloads the library finds their working video gone.
    """
    registry = GenerationRegistry(tmp_path / "library")
    good = GenerationManager(registry, _FakePipeline(), InMemoryArtifactStore())
    first = good.generate(_request())

    broken = GenerationManager(
        registry, _FakePipeline(fail_at="visualization_plan"), InMemoryArtifactStore()
    )
    second = broken.generate(_request(config=TeachingConfig(voice="af_sky")), force=True)

    assert second.status is GenerationStatus.FAILED
    assert second.error is not None
    current = registry.current_version("palindrome")
    assert current is not None and current.version == first.version


# ---- what counts as "the same video" -------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("voice", "af_sky"),
        ("audience", "advanced"),
        ("teaching_style", "concise"),
        ("tts_provider", "sapi"),
    ],
)
def test_changing_a_teaching_choice_requires_a_new_video(manager, field, value) -> None:
    generator, pipeline = manager
    generator.generate(_request())

    changed = _request(config=TeachingConfig(**{field: value}))
    decision = generator.decide(changed)

    assert decision.should_generate
    assert decision.changed == ["config"], "the UI must be able to say WHAT changed"


def test_changing_the_java_source_requires_a_new_video(manager) -> None:
    generator, _pipeline = manager
    generator.generate(_request())

    edited = dict(SOURCE)
    edited["src/main/java/Main.java"] += "// a change\n"
    decision = generator.decide(_request(source_files=edited))

    assert decision.should_generate
    assert "source_hash" in decision.changed


def test_a_new_renderer_version_invalidates_existing_videos(manager) -> None:
    """The reason a plain "final.mp4 exists" check is wrong.

    Phase 6.5.3 changed every frame the renderer produces. A video made
    before it is not the video the current code would make, and the
    library must not present it as current.
    """
    generator, _pipeline = manager
    version = generator.generate(_request())

    stale = version.model_copy(deep=True)
    stale.request_fingerprint = stale.request_fingerprint.model_copy(
        update={"renderer_version": "6.4.2"}
    )
    generator.registry.save(stale)

    decision = generator.decide(_request())
    assert decision.should_generate
    assert decision.changed == ["renderer_version"]
    assert RENDERER_VERSION != "6.4.2"


def test_the_llm_provider_is_part_of_the_identity(manager) -> None:
    generator, _pipeline = manager
    generator.generate(_request())
    decision = generator.decide(_request(llm_provider="gemini"))
    assert decision.should_generate and "llm_provider" in decision.changed


def test_a_file_on_disk_alone_is_not_a_generated_video(tmp_path: Path) -> None:
    """Explicitly NOT a "does final.mp4 exist" check.

    A stray file in the right place must not be served as a generation -
    it has no fingerprint, so nothing knows what produced it.
    """
    registry = GenerationRegistry(tmp_path / "library")
    planted = registry.version_dir("palindrome", 1)
    planted.mkdir(parents=True)
    (planted / "final.mp4").write_bytes(b"stray")

    assert registry.current_version("palindrome") is None
    assert registry.find_reusable(
        fingerprint_request("palindrome", SOURCE, TeachingConfig(), "mock")
    ) is None


def test_a_failed_run_is_never_reused(tmp_path: Path) -> None:
    registry = GenerationRegistry(tmp_path / "library")
    generator = GenerationManager(
        registry, _FakePipeline(fail_at="trace"), InMemoryArtifactStore()
    )
    failed = generator.generate(_request())

    assert failed.status is GenerationStatus.FAILED
    assert not failed.is_reusable
    assert registry.find_reusable(generator.fingerprint(_request())) is None
    # ...and asking again is allowed to try, because nothing usable exists.
    assert generator.decide(_request()).should_generate


# ---- fingerprint mechanics ------------------------------------------------


def test_the_source_hash_ignores_key_order_but_not_content() -> None:
    reordered = dict(reversed(list(SOURCE.items())))
    assert hash_source_files(SOURCE) == hash_source_files(reordered)

    moved = {"src/main/java/Other.java": next(iter(SOURCE.values()))}
    assert hash_source_files(SOURCE) != hash_source_files(moved), (
        "moving a class between files changes what the trace resolves"
    )


def test_the_content_fingerprint_records_what_the_run_produced(manager) -> None:
    """Groundwork for partial regeneration: the trace hash is recorded so
    a later run can discover the trace did not change."""
    generator, _pipeline = manager
    version = generator.generate(_request())

    content = version.content_fingerprint
    assert content is not None
    assert content.trace_hash, "the trace stage must be fingerprinted"
    assert content.request.digest == version.request_fingerprint.digest


def test_two_runs_of_the_same_inputs_share_their_produced_stages(tmp_path: Path) -> None:
    generator = GenerationManager(
        GenerationRegistry(tmp_path / "library"), _FakePipeline(), InMemoryArtifactStore()
    )
    first = generator.generate(_request())
    second = generator.generate(_request(), force=True)

    shared = first.content_fingerprint.stages_shared_with(second.content_fingerprint)
    assert "trace" in shared, (
        "identical inputs produced a different trace hash, so partial "
        "regeneration could never reuse anything"
    )


# ---- the registry itself --------------------------------------------------


def test_the_registry_survives_a_corrupt_index(tmp_path: Path) -> None:
    """A truncated index is a cache problem, not an outage."""
    registry = GenerationRegistry(tmp_path / "library")
    registry.create_version(
        "palindrome", fingerprint_request("palindrome", SOURCE, TeachingConfig(), "mock")
    )
    registry.index_path.write_text("{ not json", encoding="utf-8")

    assert registry.all_versions() == []
    # ...and it recovers on the next write rather than staying broken.
    registry.create_version(
        "palindrome", fingerprint_request("palindrome", SOURCE, TeachingConfig(), "mock")
    )
    assert len(registry.all_versions()) == 1


def test_an_artifact_name_cannot_escape_its_version_directory(tmp_path: Path) -> None:
    """Artifact names arrive from an HTTP route and are untrusted."""
    registry = GenerationRegistry(tmp_path / "library")
    version = registry.create_version(
        "palindrome", fingerprint_request("palindrome", SOURCE, TeachingConfig(), "mock")
    )
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")

    version.artifacts = {
        "escape": "../../../secret.txt",
        "absolute": str(secret),
    }
    registry.save(version)

    assert registry.artifact_path(version, "escape") is None
    assert registry.artifact_path(version, "absolute") is None
    assert registry.artifact_path(version, "missing") is None

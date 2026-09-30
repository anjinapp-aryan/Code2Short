"""GenerationManager: decide whether to generate, then let the EXISTING
pipeline do it.

This module contains no pipeline logic. It builds the same `Workflow` of
`workflow/nodes.py` nodes that `tests/test_phase4_pipeline_integration.py`
and the golden-path scripts build, and hands it to the same
`WorkflowRunner`. Java execution, tracing, the LLM stages, TTS, Manim and
FFmpeg all stay exactly where they are; nothing about how a video is made
moved into the UI layer.

What is genuinely new is only the decision in front of it:

    request ─> fingerprint ─> registry
                                │
                 already there? ├── yes ─> REUSE (no work at all)
                                └── no  ─> run the existing pipeline

and the recording behind it: a new version directory, never an overwrite.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from code2shorts.artifacts.models import ArtifactType
from code2shorts.artifacts.store import ArtifactStore, InMemoryArtifactStore
from code2shorts.core.models import SupportedLanguage
from code2shorts.generation.fingerprint import (
    ContentFingerprint,
    RequestFingerprint,
    TeachingConfig,
    fingerprint_request,
    hash_content,
)
from code2shorts.generation.registry import (
    GenerationRegistry,
    GenerationStatus,
    GenerationVersion,
)
from code2shorts.workflow import (
    Code2ShortsState,
    ComposeMediaNode,
    CompileNode,
    EducationalPlanNode,
    ExplainNode,
    FinalValidationNode,
    NarrationNode,
    RenderVideoNode,
    TraceNode,
    VisualizationPlanNode,
    Workflow,
    WorkflowEvent,
    WorkflowMetadata,
    WorkflowRequest,
    WorkflowRunner,
)

# Stage order, as the UI shows it. Derived from the node list rather than
# written twice, so a node added to the pipeline appears in the UI without
# anyone remembering to update a list here.
STAGE_LABELS: dict[str, str] = {
    "compile": "Java compilation",
    "trace": "Execution trace",
    "explain": "Explanation",
    "educational_plan": "Teaching plan",
    "visualization_plan": "Visualization plan",
    "narration": "Narration",
    "narration_timing": "Speech timing",
    "render_video": "Visualization render",
    "compose_media": "Speech and composition",
    "final_validation": "Validation",
}

# Which artifact each finished stage contributes to the content
# fingerprint. Only stages whose output could differ between two runs of
# the same request - a compilation result adds nothing to identity.
FINGERPRINTED_STAGES: dict[str, str] = {
    "trace": "trace_hash",
    "explanation": "explanation_hash",
    "educational_plan": "educational_plan_hash",
    "visualization_plan": "visualization_plan_hash",
    "narration": "narration_hash",
}


@dataclass(frozen=True)
class GenerationRequest:
    """What the UI asks for. No filesystem paths, no provider objects."""

    algorithm: str
    source_files: dict[str, str]
    entry_point: str
    input_value: str
    title: str
    config: TeachingConfig
    llm_provider: str = "mock"


@dataclass(frozen=True)
class GenerationDecision:
    """Whether work is needed, and why.

    `reason` and `changed` exist so the UI can say "the voice changed"
    rather than only "regeneration required" - a user asked to spend three
    minutes of compute deserves to know what changed.
    """

    should_generate: bool
    reason: str
    existing: GenerationVersion | None = None
    changed: list[str] | None = None


class PipelineFactory:
    """Builds the node list. Injected so tests can supply fakes for the
    parts that need Maven, a JVM, Manim, FFmpeg or a real model - exactly
    as the existing pipeline tests already do.

    The default implementation is deliberately NOT written here: choosing
    a renderer and a TTS engine is a wiring decision that already exists
    in the golden-path scripts, and duplicating it would create the second
    pipeline this design exists to avoid. `webapp` supplies it.
    """

    def build(
        self, request: GenerationRequest, output_dir: Path
    ) -> list:  # list[WorkflowNode]
        raise NotImplementedError


class GenerationManager:
    def __init__(
        self,
        registry: GenerationRegistry,
        pipeline_factory: PipelineFactory,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self._registry = registry
        self._pipeline = pipeline_factory
        self._artifact_store = artifact_store or InMemoryArtifactStore()

    @property
    def registry(self) -> GenerationRegistry:
        return self._registry

    # ---- the decision ----------------------------------------------------

    def fingerprint(self, request: GenerationRequest) -> RequestFingerprint:
        return fingerprint_request(
            algorithm=request.algorithm,
            source_files=request.source_files,
            config=request.config,
            llm_provider=request.llm_provider,
        )

    def decide(self, request: GenerationRequest) -> GenerationDecision:
        """Never generates. Answers whether generating is warranted.

        Separated from `generate` on purpose: the UI shows this answer and
        waits. Automatic regeneration on a page load is how a user ends up
        paying for a render they did not ask for.
        """
        wanted = self.fingerprint(request)
        exact = self._registry.find_reusable(wanted)
        if exact is not None:
            return GenerationDecision(
                should_generate=False,
                reason="An existing video already exists for this configuration.",
                existing=exact,
            )

        current = self._registry.current_version(request.algorithm)
        if current is None:
            return GenerationDecision(
                should_generate=True,
                reason="No video has been generated for this program yet.",
            )
        changed = wanted.differences(current.request_fingerprint)
        return GenerationDecision(
            should_generate=True,
            reason="The configuration has changed since the current video.",
            existing=current,
            changed=changed,
        )

    # ---- running the EXISTING pipeline -----------------------------------

    def generate(
        self,
        request: GenerationRequest,
        on_event: Callable[[WorkflowEvent], None] | None = None,
        force: bool = False,
    ) -> GenerationVersion:
        """Run the pipeline and record a NEW version.

        `force` is what the Re-generate button sets. Without it a request
        that matches an existing completed video returns that video and
        does no work - the duplicate-generation guard is here, in the one
        place generation can start, rather than in the UI where a second
        caller could bypass it.

        A format that cannot be rendered yet is refused before a version
        is created, so it never leaves a `running` row behind.
        """
        request.config.video_profile.require_renderable()
        if not force:
            decision = self.decide(request)
            if not decision.should_generate and decision.existing is not None:
                return decision.existing

        fingerprint = self.fingerprint(request)
        version = self._registry.create_version(
            request.algorithm, fingerprint, title=request.title
        )
        version.status = GenerationStatus.RUNNING
        self._registry.save(version)

        output_dir = self._registry.version_dir(request.algorithm, version.version)
        started = datetime.now(UTC)

        state = Code2ShortsState(
            request=WorkflowRequest(
                topic=request.title or request.algorithm,
                language=SupportedLanguage.JAVA,
                source_files=dict(request.source_files),
                entry_point=request.entry_point,
                input_value=request.input_value,
            ),
            metadata=WorkflowMetadata(
                workflow_id=f"{request.algorithm}-v{version.version}",
                execution_id=uuid.uuid4().hex,
            ),
        )

        runner = WorkflowRunner(self._artifact_store, event_sink=on_event or (lambda _e: None))
        workflow = Workflow(
            f"{request.algorithm}-video", self._pipeline.build(request, output_dir)
        )

        try:
            result = runner.run(workflow, state)
        except Exception as error:  # noqa: BLE001 - a failed run is a RESULT
            # A crashed pipeline must leave a recorded failure, not an
            # absent version: a library that silently forgets failed runs
            # cannot show the user why nothing appeared.
            version.status = GenerationStatus.FAILED
            version.error = f"{type(error).__name__}: {error}"
            version.completed_at = datetime.now(UTC)
            self._registry.save(version)
            return version

        version.completed_at = datetime.now(UTC)
        version.duration_seconds = (version.completed_at - started).total_seconds()
        version.content_fingerprint = self._content_fingerprint(fingerprint, result.state)
        version.artifacts = self._collect_artifacts(result.state, output_dir)

        if result.succeeded and "final_video" in version.artifacts:
            version.status = GenerationStatus.COMPLETED
        else:
            version.status = GenerationStatus.FAILED
            version.error = (
                f"pipeline failed at {result.failed_node}"
                if result.failed_node
                else "pipeline produced no final video"
            )
        self._registry.save(version)
        return version

    # ---- recording -------------------------------------------------------

    def _content_fingerprint(
        self, request: RequestFingerprint, state: Code2ShortsState
    ) -> ContentFingerprint:
        content = ContentFingerprint(request=request)
        for stage, field in FINGERPRINTED_STAGES.items():
            artifact_id = state.artifact_ids.get(stage)
            if artifact_id is None:
                continue
            artifact = self._artifact_store.get(artifact_id)
            if artifact is not None:
                # The artifact's own checksum already IS a content hash of
                # canonical JSON, so it is reused rather than recomputed.
                setattr(content, field, artifact.checksum or hash_content(artifact.content))
        return content

    def _collect_artifacts(
        self, state: Code2ShortsState, output_dir: Path
    ) -> dict[str, str]:
        """Map logical names to files inside this version's directory.

        Only files that are actually there. Listing an artifact the run did
        not produce would give the details page a link to a 404, which
        reads as a broken product rather than an incomplete run.
        """
        found: dict[str, str] = {}
        base = output_dir.resolve()
        for artifact_id in state.artifact_ids.values():
            artifact = self._artifact_store.get(artifact_id)
            if artifact is None:
                continue
            path_value = None
            if isinstance(artifact.content, dict):
                path_value = artifact.content.get("path") or artifact.content.get(
                    "output_path"
                )
            if not path_value:
                continue
            candidate = Path(str(path_value))
            if not candidate.is_absolute():
                candidate = base / candidate
            if not candidate.is_file():
                continue
            name = {
                ArtifactType.FINAL_VIDEO: "final_video",
                ArtifactType.RENDERED_VIDEO: "rendered_video",
                ArtifactType.AUDIO: "narration_audio",
            }.get(artifact.type, artifact.type.value)
            try:
                found[name] = candidate.resolve().relative_to(base).as_posix()
            except ValueError:
                # Produced outside the version directory (a temp workspace,
                # say). Not linkable, so not listed.
                continue

        # JSON stages are written next to the video so the Artifacts tab
        # can show them without the store being alive.
        for stage, artifact_id in state.artifact_ids.items():
            artifact = self._artifact_store.get(artifact_id)
            if artifact is None or not isinstance(artifact.content, dict):
                continue
            target = output_dir / f"{stage}.json"
            try:
                import json

                target.write_text(
                    json.dumps(artifact.content, indent=2, default=str), encoding="utf-8"
                )
            except (OSError, TypeError, ValueError):
                continue
            found.setdefault(stage, target.name)
        return found

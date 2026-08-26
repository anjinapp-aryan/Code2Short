"""Code2ShortsState: the one workflow state type, deliberately NOT a giant
mutable bag. See ARCHITECTURE_DECISIONS.md, "Phase 3: workflow state
design" for the full reasoning; summary:

- A. immutable input            -> WorkflowRequest (never mutated)
- B. derived state               -> NOT modeled separately: every derived
                                     result is an Artifact, referenced by
                                     id in `artifact_ids`. Modeling it
                                     twice (once as an Artifact, once as a
                                     "derived state" field) would be two
                                     sources of truth for the same fact.
- C. transient execution state    -> lives in WorkflowContext (per-run
                                     scratch: attempt count, artifact
                                     store handle), NOT in persisted state.
- D. persisted artifacts           -> `artifact_ids: dict[str, str]`,
                                     stage name -> Artifact.id. Content
                                     lives once in the ArtifactStore.
- E. AI-generated data               -> same as D — an ExplanationArtifact
                                     IS an Artifact, no special case.
- F. validation information           -> `validation: ValidationSummary`.
- G. workflow metadata                  -> `metadata: WorkflowMetadata`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from code2shorts.core.models import SupportedLanguage, ValidationResult
from code2shorts.workflow.errors import WorkflowError

__all__ = [
    "Code2ShortsState",
    "ValidationResult",
    "ValidationSummary",
    "WorkflowMetadata",
    "WorkflowRequest",
]


class WorkflowRequest(BaseModel):
    """A. Immutable input. Nodes read this; nothing ever writes to it after
    the workflow starts."""

    topic: str
    language: SupportedLanguage
    source_files: dict[str, str] = Field(
        description="Relative path -> Java source. Phase 3 does not run "
        "CodeGenerator; callers supply source directly (see Known "
        "limitations)."
    )
    entry_point: str
    input_value: str = ""


class ValidationSummary(BaseModel):
    results: list[ValidationResult] = Field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        """Did every check that still stands pass?

        Superseded repair attempts are excluded. They stay in `results` as
        audit evidence, but a failure that a later attempt already replaced
        is not evidence that the run produced invalid output — no node ever
        builds an artifact from an attempt that failed validation.
        """
        return all(result.passed for result in self.results if not result.superseded)

    @property
    def every_attempt_passed(self) -> bool:
        """Stricter: nothing ever failed, not even a repaired attempt.
        Useful for judging provider quality rather than run validity."""
        return all(result.passed for result in self.results)

    def record(self, result: ValidationResult) -> None:
        self.results.append(result)

    def record_history(self, history: list[ValidationResult]) -> None:
        """Record one node's full generate/repair history, marking every
        attempt but the last as superseded."""
        for result in history[:-1]:
            self.results.append(result.model_copy(update={"superseded": True}))
        if history:
            self.results.append(history[-1])


class WorkflowMetadata(BaseModel):
    workflow_id: str
    execution_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_node_names: list[str] = Field(default_factory=list)


class Code2ShortsState(BaseModel):
    request: WorkflowRequest
    artifact_ids: dict[str, str] = Field(
        default_factory=dict, description="stage name -> Artifact.id"
    )
    validation: ValidationSummary = Field(default_factory=ValidationSummary)
    errors: list[WorkflowError] = Field(default_factory=list)
    metadata: WorkflowMetadata

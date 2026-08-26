"""WorkflowNode contract:

    input state
        v
    execute node          (produces artifacts via context.artifact_store,
                            emits events via context.emit)
        v
    updated state (new artifact_ids / validation entries)
        v
    NodeResult

Nodes must never import an LLM/agent framework directly. An AI-backed node
depends on `code2shorts.llm.provider.LLMProvider` (already
framework-neutral — see ai/ package), never on LangGraph/LangChain/etc.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from code2shorts.workflow.context import WorkflowContext
from code2shorts.workflow.state import Code2ShortsState


class NodeResult(BaseModel):
    state: Code2ShortsState
    artifact_ids_created: list[str] = Field(default_factory=list)


class WorkflowNode(ABC):
    name: str

    @abstractmethod
    def run(self, state: Code2ShortsState, context: WorkflowContext) -> NodeResult:
        """Raise workflow.errors.NodeExecutionError on failure — never
        return a partially-updated state silently."""

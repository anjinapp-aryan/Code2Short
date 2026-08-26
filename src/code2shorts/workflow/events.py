"""Structured workflow events — not string-only logging. A sink (test spy,
future OpenTelemetry exporter, etc.) receives typed WorkflowEvent objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class WorkflowEventType(StrEnum):
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_COMPLETED = "workflow_completed"
    WORKFLOW_FAILED = "workflow_failed"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    ARTIFACT_CREATED = "artifact_created"
    ARTIFACT_VALIDATED = "artifact_validated"
    VALIDATION_FAILED = "validation_failed"
    RETRY_STARTED = "retry_started"
    RETRY_COMPLETED = "retry_completed"
    AI_CALL_STARTED = "ai_call_started"  # == "llm_request" in Phase 4's spec vocabulary
    AI_CALL_COMPLETED = "ai_call_completed"  # == "llm_response"
    VALIDATION_STARTED = "validation_started"
    REPAIR_STARTED = "repair_started"
    RENDERING_STARTED = "rendering_started"
    RENDERING_COMPLETED = "rendering_completed"


class WorkflowEvent(BaseModel):
    type: WorkflowEventType
    workflow_id: str
    execution_id: str
    node_name: str | None = None
    artifact_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict)

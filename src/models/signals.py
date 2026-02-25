"""Pydantic models for intent signals."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

# ============================================================================
# Signal Type Models
# ============================================================================


class SignalType(BaseModel):
    """Signal type definition (job_change, funding_round, etc.)."""

    id: str
    name: str
    description: str | None
    category: str  # people, company, technology, behavior
    config_schema: dict[str, Any]
    enabled: bool
    created_at: datetime


# ============================================================================
# Signal Configuration Models
# ============================================================================


class TriggerConfig(BaseModel):
    """Configuration for signal trigger (what to monitor)."""

    type: str = Field(..., description="Signal type ID (e.g., 'job_change')")
    filters: dict[str, Any] = Field(default_factory=dict, description="Filters for the signal type")


class ConditionConfig(BaseModel):
    """Configuration for signal conditions (additional filters)."""

    operator: str = Field(default="AND", description="Logical operator (AND/OR)")
    conditions: list[dict[str, Any]] = Field(default_factory=list)


class ActionConfig(BaseModel):
    """Configuration for signal action (what to do when fired)."""

    type: str = Field(..., description="Action type (slack, webhook, email)")
    webhook_url: str | None = Field(None, description="Webhook URL for notifications")
    email_recipients: list[str] | None = Field(None, description="Email recipients")
    message_template: str | None = Field(None, description="Custom message template (optional)")


# ============================================================================
# Signal Models
# ============================================================================


class Signal(BaseModel):
    """User-configured intent signal."""

    id: UUID
    org_id: UUID
    name: str
    description: str | None
    is_active: bool

    # Configuration
    trigger_config: dict[str, Any]
    condition_config: dict[str, Any] | None
    action_config: dict[str, Any]

    # Metadata
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    last_fired_at: datetime | None
    fire_count: int


class SignalExecution(BaseModel):
    """Signal execution log entry."""

    id: UUID
    signal_id: UUID
    org_id: UUID

    # Trigger data
    trigger_data: dict[str, Any]
    matched_at: datetime

    # Action result
    action_type: str
    action_status: str  # success, failed, pending
    action_response: dict[str, Any] | None
    error_message: str | None
    executed_at: datetime


# ============================================================================
# Request/Response Models
# ============================================================================


class CreateSignalRequest(BaseModel):
    """Request to create a new signal."""

    name: str = Field(..., min_length=1, max_length=200, description="Signal name")
    description: str | None = Field(None, max_length=1000, description="Description")
    is_active: bool = Field(default=True, description="Whether signal is active")

    trigger_config: TriggerConfig = Field(..., description="Trigger configuration")
    condition_config: ConditionConfig | None = Field(None, description="Additional conditions")
    action_config: ActionConfig = Field(..., description="Action configuration")


class UpdateSignalRequest(BaseModel):
    """Request to update a signal."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=1000)
    is_active: bool | None = None

    trigger_config: TriggerConfig | None = None
    condition_config: ConditionConfig | None = None
    action_config: ActionConfig | None = None


class SignalTestResponse(BaseModel):
    """Response from testing a signal."""

    signal_id: UUID
    would_fire: bool
    matched_data: dict[str, Any] | None
    match_count: int = 0
    message: str


class SignalExecutionListResponse(BaseModel):
    """Response for listing signal executions."""

    signal_id: UUID
    total_count: int
    executions: list[SignalExecution]

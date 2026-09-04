from datetime import datetime
from typing import Literal
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.recovery_analysis import RecoveryAnalysisResponse

RecoveryRecommendedAction = Literal["retry", "contact", "wait", "skip", "close"]
RecoveryToolAction = Literal[
    "retry_payment",
    "create_payment_link",
    "send_recovery_message",
    "schedule_followup",
    "stop_recovery",
]


class RecoveryActionContext(BaseModel):
    """Bounded metadata for deterministic internal action execution."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["manual", "ai_recommendation", "recovery_agent"] = "manual"
    note: str | None = Field(default=None, max_length=300)


class RecoveryToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    context: RecoveryActionContext = Field(default_factory=RecoveryActionContext)


class RetryPaymentInput(RecoveryToolInput):
    pass


class CreatePaymentLinkInput(RecoveryToolInput):
    pass


class SendRecoveryMessageInput(RecoveryToolInput):
    pass


class ScheduleFollowupInput(RecoveryToolInput):
    wait_minutes: int | None = Field(default=None, ge=1, le=10_080)


class StopRecoveryInput(RecoveryToolInput):
    pass


class ActionResult(BaseModel):
    case_id: UUID
    action: RecoveryToolAction
    status: Literal["simulated", "completed", "failed"] = "simulated"
    success: bool
    outcome: Literal["recovered", "failed", "scheduled", "stopped", "blocked"]
    amount_recovered: Decimal = Decimal("0.00")
    message: str
    guardrails_applied: list[str] = Field(default_factory=list)
    payment_link_id: str | None = None
    payment_link_url: str | None = None
    scheduled_at: datetime | None = None
    scheduled_action: str | None = None


class PersistedAIRecommendation(BaseModel):
    """The bounded recommendation stored by analysis, before tool execution."""

    action: RecoveryRecommendedAction
    reason: str | None


class RecoveryActionExecutionResponse(BaseModel):
    case_id: UUID
    ai_recommendation: PersistedAIRecommendation
    action_execution_result: ActionResult

class RecoveryWorkflowResponse(BaseModel):
    """Combined response for AI analysis followed by action execution."""

    analysis: RecoveryAnalysisResponse
    execution: RecoveryActionExecutionResponse

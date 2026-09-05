from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

RecoveryAction = Literal["retry", "contact", "wait", "skip", "close"]


class RecoveryDecision(BaseModel):
    """The bounded, validated recommendation returned by the LLM."""

    model_config = ConfigDict(extra="forbid")

    action: RecoveryAction
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    next_step: str = Field(min_length=1, max_length=500)
    stop: bool
    wait_minutes: int | None = Field(default=None, ge=1, le=10_080)

    @model_validator(mode="after")
    def validate_wait_timing(self) -> "RecoveryDecision":
        if self.action == "wait" and self.wait_minutes is None:
            raise ValueError("WAIT decisions require wait_minutes.")
        if self.action != "wait" and self.wait_minutes is not None:
            raise ValueError("wait_minutes is only allowed for WAIT decisions.")
        return self


class PaymentHistoryItem(BaseModel):
    status: str
    amount: Decimal
    failure_reason: str | None
    paid_at: datetime | None
    created_at: datetime


class PreviousRecoveryOutcome(BaseModel):
    status: str
    amount_at_risk: Decimal
    amount_recovered: Decimal
    ai_decision: str | None
    updated_at: datetime


class RecoveryAttemptItem(BaseModel):
    action: str
    actor: str
    created_at: datetime
    recommended_action: str | None = None
    outcome: str | None = None


class RecoveryDecisionContext(BaseModel):
    """The deliberately limited, non-PII context provided to the LLM."""

    case_id: UUID
    case_status: str
    amount_at_risk: Decimal
    amount_recovered: Decimal
    attempt_count: int
    max_recovery_attempts: int
    existing_ai_decision: str | None
    existing_ai_reason: str | None
    last_attempt_at: datetime | None
    payment: PaymentHistoryItem
    customer_payment_history: list[PaymentHistoryItem]
    customer_has_successfully_paid_before: bool
    previous_recovery_outcomes: list[PreviousRecoveryOutcome]
    previous_recovery_attempts: list[RecoveryAttemptItem]

    # Scheduling and lifecycle context (prevents infinite wait loops and duplicate links)
    trigger: str = "manual"
    is_scheduled_followup: bool = False
    scheduled_action: str | None = None
    scheduled_at: datetime | None = None
    current_time: datetime | None = None
    is_scheduled_due: bool = False
    wait_elapsed: bool = False
    active_payment_link_id: str | None = None
    payment_link_expires_at: datetime | None = None
    is_payment_link_expired: bool = False
    last_notification_status: str | None = None
    last_notification_failure_reason: str | None = None


class RecoveryAnalysisResponse(BaseModel):
    case_id: UUID
    decision: RecoveryDecision
    guardrails_applied: list[str]
    stored: bool
    analyzed_at: datetime

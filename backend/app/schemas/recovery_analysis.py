from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RecoveryAction = Literal["retry", "contact", "wait", "skip", "close"]


class RecoveryDecision(BaseModel):
    """The bounded, validated recommendation returned by the LLM."""

    model_config = ConfigDict(extra="forbid")

    action: RecoveryAction
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    next_step: str = Field(min_length=1, max_length=500)
    stop: bool


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


class RecoveryAnalysisResponse(BaseModel):
    case_id: UUID
    decision: RecoveryDecision
    guardrails_applied: list[str]
    stored: bool
    analyzed_at: datetime

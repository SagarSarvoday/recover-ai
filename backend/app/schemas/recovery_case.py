from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


def compute_payment_link_status(case: RecoveryCase) -> str:
    if case.status == "recovered":
        return "paid"
    if not getattr(case, "razorpay_payment_link_id", None):
        return "not_created"
    expires_at = getattr(case, "payment_link_expires_at", None)
    if expires_at is not None:
        now = datetime.now(timezone.utc) if expires_at.tzinfo is not None else datetime.utcnow()
        if expires_at < now:
            return "expired"
    return "active"


class RecoveryCaseResponse(BaseModel):
    """API shape for a recovery case. Column names that differ from the DB are mapped here."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID | None
    customer_name: str | None
    customer_email: str | None
    payment_id: UUID
    payment_status: str
    failure_reason: str | None
    payment_amount: Decimal
    status: str
    amount_at_risk: Decimal
    recovered_amount: Decimal
    attempt_count: int
    ai_decision: str | None
    ai_reason: str | None
    ai_confidence: float | None = None
    next_action_at: datetime | None
    scheduled_action: str | None = None
    razorpay_payment_link_id: str | None = None
    payment_link_status: str = "not_created"
    payment_link_expires_at: datetime | None = None
    payment_link_created_at: datetime | None = None
    payment_link_sent_at: datetime | None = None
    payment_link_paid_at: datetime | None = None
    last_attempt_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_models(
        cls,
        case: RecoveryCase,
        customer: Customer | None,
        payment: Payment,
    ) -> "RecoveryCaseResponse":
        raw_conf = getattr(case, "ai_confidence", None)
        confidence = float(raw_conf) if raw_conf is not None else None
        return cls(
            id=case.id,
            customer_id=case.customer_id,
            customer_name=customer.name if customer is not None else None,
            customer_email=customer.email if customer is not None else None,
            payment_id=case.payment_id,
            payment_status=payment.status,
            failure_reason=payment.failure_reason,
            payment_amount=payment.amount,
            status=case.status,
            amount_at_risk=case.amount_at_risk,
            recovered_amount=case.amount_recovered,
            attempt_count=case.attempt_count,
            ai_decision=case.ai_decision,
            ai_reason=case.ai_decision_note,
            ai_confidence=confidence,
            next_action_at=getattr(case, "next_action_at", None),
            scheduled_action=getattr(case, "scheduled_action", None),
            razorpay_payment_link_id=getattr(case, "razorpay_payment_link_id", None),
            payment_link_status=compute_payment_link_status(case),
            payment_link_expires_at=getattr(case, "payment_link_expires_at", None),
            payment_link_created_at=getattr(case, "payment_link_created_at", None),
            payment_link_sent_at=getattr(case, "payment_link_sent_at", None),
            payment_link_paid_at=getattr(case, "payment_link_paid_at", None),
            last_attempt_at=getattr(case, "last_attempt_at", None),
            created_at=case.created_at,
            updated_at=case.updated_at,
        )

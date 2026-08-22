from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


class RecoveryCaseResponse(BaseModel):
    """API shape for a recovery case. Column names that differ from the DB are mapped here."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    customer_name: str
    customer_email: str
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
    next_action_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_models(
        cls,
        case: RecoveryCase,
        customer: Customer,
        payment: Payment,
    ) -> "RecoveryCaseResponse":
        return cls(
            id=case.id,
            customer_id=case.customer_id,
            customer_name=customer.name,
            customer_email=customer.email,
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
            next_action_at=case.last_attempt_at,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )

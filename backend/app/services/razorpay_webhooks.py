from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.razorpay_webhook_event import RazorpayWebhookEvent
from app.schemas.razorpay import RazorpayWebhookEnvelope

SUPPORTED_RAZORPAY_EVENTS = {"payment.failed", "payment_link.paid"}


class InvalidRazorpayWebhookPayloadError(ValueError):
    pass


class IncompleteRazorpayPaymentPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class RazorpayFailedPaymentData:
    payment_id: str
    customer_id: str | None
    customer_name: str | None
    customer_email: str
    customer_contact: str | None
    amount: Decimal
    currency: str
    failure_reason: str


@dataclass(frozen=True)
class RazorpayPaidPaymentLinkData:
    payment_link_id: str
    successful_payment_id: str
    amount: Decimal


def extract_external_entity_id(webhook: RazorpayWebhookEnvelope) -> str | None:
    """Validate only the identifiers needed for the supported event audit record."""
    if webhook.event == "payment.failed":
        entity = webhook.payload.get("payment", {}).get("entity", {})
    elif webhook.event == "payment_link.paid":
        entity = webhook.payload.get("payment_link", {}).get("entity", {})
    else:
        return None

    entity_id = entity.get("id") if isinstance(entity, dict) else None
    if not isinstance(entity_id, str) or not entity_id:
        raise InvalidRazorpayWebhookPayloadError(
            "Supported Razorpay webhook is missing its external entity identifier."
        )
    return entity_id


def claim_webhook_event(
    db: Session,
    *,
    razorpay_event_id: str,
    event_type: str,
    external_entity_id: str | None,
    processing_status: Literal["processed", "ignored"],
) -> bool:
    """Atomically claim a webhook event. False means it was already processed."""
    statement = (
        insert(RazorpayWebhookEvent)
        .values(
            razorpay_event_id=razorpay_event_id,
            event_type=event_type,
            external_entity_id=external_entity_id,
            processing_status=processing_status,
        )
        .on_conflict_do_nothing(index_elements=["razorpay_event_id"])
        .returning(RazorpayWebhookEvent.id)
    )
    inserted_event_id = db.execute(statement).scalar_one_or_none()
    return inserted_event_id is not None


def record_webhook_event(
    db: Session,
    *,
    razorpay_event_id: str,
    event_type: str,
    external_entity_id: str | None,
    processing_status: Literal["processed", "ignored"],
) -> bool:
    inserted = claim_webhook_event(
        db,
        razorpay_event_id=razorpay_event_id,
        event_type=event_type,
        external_entity_id=external_entity_id,
        processing_status=processing_status,
    )
    if not inserted:
        return False
    db.commit()
    return True


def extract_failed_payment_data(webhook: RazorpayWebhookEnvelope) -> RazorpayFailedPaymentData:
    entity = webhook.payload.get("payment", {}).get("entity", {})
    if not isinstance(entity, dict):
        raise IncompleteRazorpayPaymentPayloadError("Missing payment entity.")

    notes = entity.get("notes") if isinstance(entity.get("notes"), dict) else {}
    payment_id = entity.get("id")
    customer_id = entity.get("customer_id")
    customer_email = entity.get("email")
    customer_name = entity.get("customer_name") or notes.get("customer_name") or notes.get("name")
    customer_contact = entity.get("contact")
    amount = entity.get("amount")
    currency = entity.get("currency")
    failure_reason = entity.get("error_description") or entity.get("error_reason")

    if not isinstance(payment_id, str) or not payment_id:
        raise IncompleteRazorpayPaymentPayloadError("Missing Razorpay payment identifier.")
    if customer_id is not None and (not isinstance(customer_id, str) or not customer_id):
        raise IncompleteRazorpayPaymentPayloadError("Invalid Razorpay customer identifier.")
    if not isinstance(customer_email, str) or not customer_email:
        raise IncompleteRazorpayPaymentPayloadError("Missing customer email.")
    if customer_name is not None and (not isinstance(customer_name, str) or not customer_name):
        raise IncompleteRazorpayPaymentPayloadError("Invalid customer name.")
    if customer_contact is not None and not isinstance(customer_contact, str):
        raise IncompleteRazorpayPaymentPayloadError("Invalid customer contact.")
    if not isinstance(amount, int) or amount <= 0:
        raise IncompleteRazorpayPaymentPayloadError("Missing or invalid payment amount.")
    if not isinstance(currency, str) or len(currency) != 3:
        raise IncompleteRazorpayPaymentPayloadError("Missing or invalid payment currency.")
    if not isinstance(failure_reason, str) or not failure_reason:
        raise IncompleteRazorpayPaymentPayloadError("Missing payment failure reason.")

    return RazorpayFailedPaymentData(
        payment_id=payment_id,
        customer_id=customer_id,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_contact=customer_contact,
        amount=Decimal(amount) / Decimal("100"),
        currency=currency.upper(),
        failure_reason=failure_reason,
    )


def extract_paid_payment_link_data(webhook: RazorpayWebhookEnvelope) -> RazorpayPaidPaymentLinkData:
    payment_link = webhook.payload.get("payment_link", {}).get("entity", {})
    payment = webhook.payload.get("payment", {}).get("entity", {})
    if not isinstance(payment_link, dict) or not isinstance(payment, dict):
        raise IncompleteRazorpayPaymentPayloadError("Missing payment link or successful payment entity.")

    payment_link_id = payment_link.get("id")
    successful_payment_id = payment.get("id")
    amount = payment.get("amount")
    if not isinstance(payment_link_id, str) or not payment_link_id:
        raise IncompleteRazorpayPaymentPayloadError("Missing Razorpay payment link identifier.")
    if not isinstance(successful_payment_id, str) or not successful_payment_id:
        raise IncompleteRazorpayPaymentPayloadError("Missing successful Razorpay payment identifier.")
    if not isinstance(amount, int) or amount <= 0:
        raise IncompleteRazorpayPaymentPayloadError("Missing or invalid successful payment amount.")

    return RazorpayPaidPaymentLinkData(
        payment_link_id=payment_link_id,
        successful_payment_id=successful_payment_id,
        amount=Decimal(amount) / Decimal("100"),
    )


def ingest_payment_failed_webhook(
    db: Session,
    *,
    razorpay_event_id: str,
    webhook: RazorpayWebhookEnvelope,
) -> Literal["processed", "duplicate"]:
    """Atomically ingest one verified payment.failed webhook into RecoverAI."""
    data = extract_failed_payment_data(webhook)
    claimed = claim_webhook_event(
        db,
        razorpay_event_id=razorpay_event_id,
        event_type=webhook.event,
        external_entity_id=data.payment_id,
        processing_status="processed",
    )
    if not claimed:
        return "duplicate"

    customer = None
    if data.customer_id:
        customer = db.scalar(
            select(Customer).where(Customer.razorpay_customer_id == data.customer_id)
        )
    if customer is None:
        customer = db.scalar(select(Customer).where(Customer.email == data.customer_email))
    if customer is None:
        if data.customer_name is None:
            raise IncompleteRazorpayPaymentPayloadError(
                "A customer name is required to create a new RecoverAI customer."
            )
        customer = Customer(
            name=data.customer_name,
            email=data.customer_email,
            phone=data.customer_contact,
            razorpay_customer_id=data.customer_id,
        )
        db.add(customer)
        db.flush()
    else:
        if data.customer_id:
            customer.razorpay_customer_id = data.customer_id
        customer.phone = data.customer_contact or customer.phone

    payment = db.scalar(
        select(Payment).where(Payment.razorpay_payment_id == data.payment_id)
    )
    if payment is None:
        payment = Payment(
            customer_id=customer.id,
            amount=data.amount,
            currency=data.currency,
            status="failed",
            failure_reason=data.failure_reason,
            razorpay_payment_id=data.payment_id,
        )
        db.add(payment)
        db.flush()
    else:
        payment.customer_id = customer.id
        payment.amount = data.amount
        payment.currency = data.currency
        payment.status = "failed"
        payment.failure_reason = data.failure_reason

    recovery_case = db.scalar(select(RecoveryCase).where(RecoveryCase.payment_id == payment.id))
    if recovery_case is None:
        recovery_case = RecoveryCase(
            customer_id=customer.id,
            payment_id=payment.id,
            status="open",
            amount_at_risk=data.amount,
            amount_recovered=Decimal("0.00"),
            attempt_count=0,
        )
        db.add(recovery_case)
        db.flush()

    db.add(
        AuditLog(
            entity_type="payment",
            entity_id=payment.id,
            action="razorpay_payment_failed_ingested",
            actor="razorpay_webhook",
            details={
                "razorpay_event_id": razorpay_event_id,
                "razorpay_payment_id": data.payment_id,
                "recovery_case_id": str(recovery_case.id),
            },
        )
    )
    db.commit()
    return "processed"


def ingest_payment_link_paid_webhook(
    db: Session,
    *,
    razorpay_event_id: str,
    webhook: RazorpayWebhookEnvelope,
) -> Literal["processed", "ignored", "duplicate"]:
    """Atomically settle an existing recovery case for a paid Razorpay payment link."""
    data = extract_paid_payment_link_data(webhook)
    recovery_case = db.scalar(
        select(RecoveryCase).where(RecoveryCase.razorpay_payment_link_id == data.payment_link_id)
    )
    if recovery_case is None:
        inserted = record_webhook_event(
            db,
            razorpay_event_id=razorpay_event_id,
            event_type=webhook.event,
            external_entity_id=data.payment_link_id,
            processing_status="ignored",
        )
        return "ignored" if inserted else "duplicate"

    claimed = claim_webhook_event(
        db,
        razorpay_event_id=razorpay_event_id,
        event_type=webhook.event,
        external_entity_id=data.payment_link_id,
        processing_status="processed",
    )
    if not claimed:
        return "duplicate"

    payment = db.get(Payment, recovery_case.payment_id)
    if payment is None:
        raise IncompleteRazorpayPaymentPayloadError(
            "Matching recovery case is missing its internal payment record."
        )

    already_recovered = recovery_case.status == "recovered"
    payment.status = "succeeded"
    payment.failure_reason = None
    payment.paid_at = datetime.now(timezone.utc)
    payment.razorpay_success_payment_id = data.successful_payment_id

    if not already_recovered:
        recovery_case.amount_recovered = min(recovery_case.amount_at_risk, data.amount)
        recovery_case.status = "recovered"

    db.add(
        AuditLog(
            entity_type="recovery_case",
            entity_id=recovery_case.id,
            action=(
                "razorpay_payment_link_paid_already_recovered"
                if already_recovered
                else "razorpay_payment_link_paid_recovered"
            ),
            actor="razorpay_webhook",
            details={
                "razorpay_event_id": razorpay_event_id,
                "razorpay_payment_link_id": data.payment_link_id,
                "razorpay_success_payment_id": data.successful_payment_id,
                "amount_recovered": str(recovery_case.amount_recovered),
            },
        )
    )
    db.commit()
    return "processed"

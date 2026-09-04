import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.razorpay_webhook_event import RazorpayWebhookEvent
from app.models.transaction import Transaction
from app.schemas.razorpay import RazorpayWebhookEnvelope
from app.services.recovery_agent import run_recovery_agent

logger = logging.getLogger(__name__)

SUPPORTED_RAZORPAY_EVENTS = {"payment.failed", "payment_link.paid"}


class InvalidRazorpayWebhookPayloadError(ValueError):
    pass


class IncompleteRazorpayPaymentPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class RazorpayFailedPaymentData:
    payment_id: str
    order_id: str | None
    customer_id: str | None
    customer_name: str | None
    customer_email: str | None
    customer_contact: str | None
    amount: Decimal
    currency: str
    failure_reason: str
    payment_method: str | None


PLACEHOLDER_EMAILS = {"void@razorpay.com"}


def _usable_email(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    if not email or email in PLACEHOLDER_EMAILS or "@" not in email:
        return None
    return email


def _normalize_phone(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    digits = "".join(character for character in value if character.isdigit())
    if not 7 <= len(digits) <= 15:
        return None
    return f"+{digits}"


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


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
    order_id = _optional_text(entity.get("order_id"))
    customer_id = _optional_text(entity.get("customer_id"))
    customer_email = _usable_email(entity.get("email"))
    customer_name = _optional_text(
        entity.get("customer_name") or notes.get("customer_name") or notes.get("name")
    )
    customer_contact = _normalize_phone(entity.get("contact"))
    amount = entity.get("amount")
    currency = entity.get("currency")
    failure_reason = _optional_text(entity.get("error_description") or entity.get("error_reason"))
    payment_method = _optional_text(entity.get("method"))

    if not isinstance(payment_id, str) or not payment_id:
        raise IncompleteRazorpayPaymentPayloadError("Missing Razorpay payment identifier.")
    if not isinstance(amount, int) or amount <= 0:
        raise IncompleteRazorpayPaymentPayloadError("Missing or invalid payment amount.")
    if not isinstance(currency, str) or len(currency) != 3:
        raise IncompleteRazorpayPaymentPayloadError("Missing or invalid payment currency.")

    return RazorpayFailedPaymentData(
        payment_id=payment_id,
        order_id=order_id,
        customer_id=customer_id,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_contact=customer_contact,
        amount=Decimal(amount) / Decimal("100"),
        currency=currency.upper(),
        failure_reason=failure_reason or "Razorpay reported a failed payment.",
        payment_method=payment_method,
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
) -> Literal["processed", "ignored", "duplicate"]:
    """Atomically ingest one verified payment.failed webhook into RecoverAI."""
    data = extract_failed_payment_data(webhook)
    merchant = resolve_merchant(db, webhook.account_id)
    if merchant is None:
        inserted = record_webhook_event(
            db,
            razorpay_event_id=razorpay_event_id,
            event_type=webhook.event,
            external_entity_id=data.payment_id,
            processing_status="ignored",
        )
        return "ignored" if inserted else "duplicate"

    payment = db.scalar(
        select(Payment).where(Payment.razorpay_payment_id == data.payment_id)
    )
    if payment is not None and payment.merchant_id != merchant.id:
        inserted = record_webhook_event(
            db,
            razorpay_event_id=razorpay_event_id,
            event_type=webhook.event,
            external_entity_id=data.payment_id,
            processing_status="ignored",
        )
        return "ignored" if inserted else "duplicate"

    claimed = claim_webhook_event(
        db,
        razorpay_event_id=razorpay_event_id,
        event_type=webhook.event,
        external_entity_id=data.payment_id,
        processing_status="processed",
    )
    if not claimed:
        return "duplicate"

    transaction = None
    if data.order_id:
        transaction = db.scalar(
            select(Transaction).where(Transaction.razorpay_order_id == data.order_id)
        )
        if transaction is not None and transaction.merchant_id != merchant.id:
            # A globally unique provider order cannot be reassigned through a webhook.
            db.rollback()
            inserted = record_webhook_event(
                db,
                razorpay_event_id=razorpay_event_id,
                event_type=webhook.event,
                external_entity_id=data.payment_id,
                processing_status="ignored",
            )
            return "ignored" if inserted else "duplicate"

    # A known order is authoritative for merchant/customer ownership. Provider
    # checkout contact fields are only a fallback for legacy payment-only flows.
    customer = db.get(Customer, transaction.customer_id) if transaction is not None else resolve_customer_identity(db, merchant.id, data)

    if payment is None:
        payment = Payment(
            merchant_id=merchant.id,
            transaction_id=transaction.id if transaction is not None else None,
            customer_id=customer.id if customer is not None else None,
            amount=data.amount,
            currency=data.currency,
            status="failed",
            failure_reason=data.failure_reason,
            payment_method=data.payment_method,
            razorpay_payment_id=data.payment_id,
        )
        db.add(payment)
        db.flush()
    else:
        if transaction is not None:
            if payment.transaction_id not in {None, transaction.id}:
                db.rollback()
                inserted = record_webhook_event(
                    db,
                    razorpay_event_id=razorpay_event_id,
                    event_type=webhook.event,
                    external_entity_id=data.payment_id,
                    processing_status="ignored",
                )
                return "ignored" if inserted else "duplicate"
            payment.transaction_id = transaction.id
            payment.customer_id = transaction.customer_id
        elif customer is not None:
            payment.customer_id = customer.id
        payment.amount = data.amount
        payment.currency = data.currency
        payment.status = "failed"
        payment.failure_reason = data.failure_reason
        payment.payment_method = data.payment_method

    recovery_case = db.scalar(
        select(RecoveryCase).where(
            RecoveryCase.merchant_id == merchant.id,
            RecoveryCase.payment_id == payment.id,
        )
    )
    if recovery_case is None:
        recovery_case = RecoveryCase(
            merchant_id=payment.merchant_id,
            customer_id=payment.customer_id,
            payment_id=payment.id,
            status="open",
            amount_at_risk=data.amount,
            amount_recovered=Decimal("0.00"),
            attempt_count=0,
        )
        db.add(recovery_case)
        db.flush()
    elif transaction is not None:
        recovery_case.customer_id = transaction.customer_id
    elif customer is not None:
        recovery_case.customer_id = customer.id

    db.add(
        AuditLog(
            entity_type="payment",
            entity_id=payment.id,
            action="razorpay_payment_failed_ingested",
            actor="razorpay_webhook",
            details={
                "razorpay_event_id": razorpay_event_id,
                "razorpay_payment_id": data.payment_id,
                "razorpay_order_id": data.order_id,
                "transaction_id": str(transaction.id) if transaction is not None else None,
                "recovery_case_id": str(recovery_case.id),
            },
        )
    )
    db.commit()

    if recovery_case.status in {"open", "in_progress"}:
        if merchant.ai_agent_enabled:
            try:
                run_recovery_agent(
                    db,
                    recovery_case.id,
                    trigger="payment_failed_webhook",
                    max_recovery_attempts=settings.recovery_max_attempts,
                    settings=settings,
                )
            except Exception:
                logger.exception(
                    "Autonomous recovery agent execution failed for case %s following payment.failed webhook",
                    recovery_case.id,
                )
        else:
            logger.info(
                "Merchant %s has AI agent disabled; skipping autonomous agent execution for case %s.",
                merchant.id,
                recovery_case.id,
            )

    return "processed"


def resolve_customer_identity(
    db: Session,
    merchant_id: UUID,
    data: RazorpayFailedPaymentData,
) -> Customer | None:
    """Resolve only meaningful provider identity; financial ingestion never depends on it."""
    customer = None
    if data.customer_id:
        customer = db.scalar(
            select(Customer).where(
                Customer.merchant_id == merchant_id,
                Customer.razorpay_customer_id == data.customer_id,
            )
        )
    if customer is None and data.customer_email:
        customer = db.scalar(
            select(Customer).where(
                Customer.merchant_id == merchant_id,
                Customer.email == data.customer_email,
            )
        )
    if customer is None and data.customer_contact:
        customer = db.scalar(
            select(Customer).where(
                Customer.merchant_id == merchant_id,
                Customer.phone == data.customer_contact,
            )
        )

    has_identity = any((data.customer_id, data.customer_email, data.customer_contact))
    if customer is None and not has_identity:
        return None
    if customer is None:
        customer = Customer(
            merchant_id=merchant_id,
            name=data.customer_name,
            email=data.customer_email,
            phone=data.customer_contact,
            razorpay_customer_id=data.customer_id,
        )
        db.add(customer)
        db.flush()
        return customer

    if data.customer_id and customer.razorpay_customer_id in {None, data.customer_id}:
        customer.razorpay_customer_id = data.customer_id
    if data.customer_name and customer.name is None:
        customer.name = data.customer_name
    if data.customer_email and customer.email is None:
        customer.email = data.customer_email
    if data.customer_contact and customer.phone is None:
        customer.phone = data.customer_contact
    return customer


def resolve_merchant(db: Session, account_id: str | None) -> Merchant | None:
    """Resolve merchant ownership exclusively from Razorpay's trusted account identifier."""
    if not account_id:
        return None
    return db.scalar(select(Merchant).where(Merchant.razorpay_account_id == account_id))


def ingest_payment_link_paid_webhook(
    db: Session,
    *,
    razorpay_event_id: str,
    webhook: RazorpayWebhookEnvelope,
) -> Literal["processed", "ignored", "duplicate"]:
    """Atomically settle an existing recovery case for a paid Razorpay payment link."""
    data = extract_paid_payment_link_data(webhook)
    merchant = resolve_merchant(db, webhook.account_id)
    if merchant is None:
        inserted = record_webhook_event(
            db,
            razorpay_event_id=razorpay_event_id,
            event_type=webhook.event,
            external_entity_id=data.payment_link_id,
            processing_status="ignored",
        )
        return "ignored" if inserted else "duplicate"

    recovery_case = db.scalar(
        select(RecoveryCase).where(
            RecoveryCase.merchant_id == merchant.id,
            RecoveryCase.razorpay_payment_link_id == data.payment_link_id,
        )
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

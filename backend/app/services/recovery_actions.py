import logging
from dataclasses import dataclass, field
from typing import Callable, Literal
from uuid import UUID
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.razorpay import RazorpayPaymentLinkRequest
from app.schemas.recovery_actions import (
    ActionResult,
    CreatePaymentLinkInput,
    RecoveryActionContext,
    RecoveryRecommendedAction,
    RecoveryToolAction,
    RecoveryToolInput,
    RetryPaymentInput,
    ScheduleFollowupInput,
    SendRecoveryMessageInput,
    StopRecoveryInput,
)
from app.services.notification_service import NotificationService
from app.services.razorpay_service import RazorpayService
from app.services.recovery_state_machine import transition_case_status

logger = logging.getLogger(__name__)

ACTION_TOOL_MAP: dict[RecoveryRecommendedAction, RecoveryToolAction] = {
    "retry": "retry_payment",
    "contact": "create_payment_link",
    "wait": "schedule_followup",
    "skip": "stop_recovery",
    "close": "stop_recovery",
}


class InvalidRecoveryActionError(ValueError):
    pass


@dataclass(frozen=True)
class RecoveryActionExecutionContext:
    """Server-owned dependencies; this object is never exposed to an LLM."""

    db: Session
    max_recovery_attempts: int
    action_context: RecoveryActionContext = field(default_factory=RecoveryActionContext)
    actor: str = "recovery_action_dispatcher"
    razorpay_service: RazorpayService | None = None
    notification_service: NotificationService | None = None


def _record_action_attempt(
    db: Session,
    result: ActionResult,
    action_context: RecoveryActionContext,
    actor: str,
    *,
    simulated: bool = True,
    metadata: dict[str, str] | None = None,
) -> None:
    db.add(
        AuditLog(
            entity_type="recovery_case",
            entity_id=result.case_id,
            action=f"recovery_action_{result.action}",
            actor=actor,
            details={
                "simulated": simulated,
                "tool_action": result.action,
                "success": result.success,
                "message": result.message,
                "guardrails_applied": result.guardrails_applied,
                "source": action_context.source,
                "note": action_context.note,
                **(metadata or {}),
            },
        )
    )
    db.commit()


def _validate_case(
    db: Session,
    case_id: UUID,
    max_recovery_attempts: int,
    requires_attempt_capacity: bool,
) -> tuple[RecoveryCase | None, list[str], str | None]:
    case = db.get(RecoveryCase, case_id)
    if case is None:
        return None, ["case_not_found"], "Recovery case not found."
    if case.status == "recovered":
        return case, ["case_already_recovered"], "Recovery case is already recovered."
    if case.status == "closed":
        return case, ["case_already_closed"], "Recovery case is already closed."
    if requires_attempt_capacity and case.attempt_count >= max_recovery_attempts:
        return (
            case,
            ["maximum_recovery_attempts_reached"],
            "Maximum recovery attempts have been reached.",
        )

    payment = db.get(Payment, case.payment_id)
    if payment is None or payment.customer_id != case.customer_id:
        return case, ["required_payment_information_missing"], "Required payment information is missing."
    return case, [], None


def _usable_contact_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _execute_simulated_tool(
    tool_input: RecoveryToolInput,
    execution_context: RecoveryActionExecutionContext,
    tool_action: RecoveryToolAction,
    message: str,
    requires_attempt_capacity: bool = False,
    mutate_case: Callable[[RecoveryCase], None] | None = None,
    outcome: Literal[
        "recovered",
        "failed",
        "scheduled",
        "stopped",
        "blocked",
    ] = "scheduled",
) -> ActionResult:
    case, guardrails, blocked_message = _validate_case(
        execution_context.db,
        tool_input.case_id,
        execution_context.max_recovery_attempts,
        requires_attempt_capacity,
    )
    result = ActionResult(
    case_id=tool_input.case_id,
    action=tool_action,
    success=blocked_message is None,
    outcome="blocked" if blocked_message else outcome,
    amount_recovered=Decimal("0.00"),
    message=blocked_message or message,
    guardrails_applied=guardrails,
)

    if result.success and mutate_case is not None and case is not None:
        mutate_case(case)

    _record_action_attempt(
        execution_context.db,
        result,
        tool_input.context,
        execution_context.actor,
    )
    return result


def _execute_payment_link_and_notify(
    tool_input: RecoveryToolInput,
    execution_context: RecoveryActionExecutionContext,
    tool_action: RecoveryToolAction,
) -> ActionResult:
    db = execution_context.db
    case, guardrails, blocked_message = _validate_case(
        db,
        tool_input.case_id,
        execution_context.max_recovery_attempts,
        requires_attempt_capacity=True,
    )
    if blocked_message is not None or case is None:
        result = ActionResult(
            case_id=tool_input.case_id,
            action=tool_action,
            status="failed",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message=blocked_message or "Recovery case could not be processed.",
            guardrails_applied=guardrails,
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor, simulated=False)
        return result

    customer = db.get(Customer, case.customer_id) if case.customer_id else None
    if customer is None:
        result = ActionResult(
            case_id=case.id,
            action=tool_action,
            status="failed",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message="Customer information is missing.",
            guardrails_applied=["required_customer_information_missing"],
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor, simulated=False)
        return result

    cust_merchant_id = getattr(customer, "merchant_id", None)
    case_merchant_id = getattr(case, "merchant_id", None)
    if cust_merchant_id is not None and case_merchant_id is not None and cust_merchant_id != case_merchant_id:
        result = ActionResult(
            case_id=case.id,
            action=tool_action,
            status="failed",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message="Customer does not belong to the merchant for this recovery case.",
            guardrails_applied=["customer_merchant_mismatch"],
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor, simulated=False)
        return result

    payment = db.get(Payment, case.payment_id)
    if payment is None:
        result = ActionResult(
            case_id=case.id,
            action=tool_action,
            status="failed",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message="Payment information is missing.",
            guardrails_applied=["required_payment_information_missing"],
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor, simulated=False)
        return result

    pay_cust_id = getattr(payment, "customer_id", None)
    cust_id = getattr(customer, "id", None)
    if pay_cust_id is not None and cust_id is not None and pay_cust_id != cust_id:
        result = ActionResult(
            case_id=case.id,
            action=tool_action,
            status="failed",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message="Payment customer does not match recovery case customer.",
            guardrails_applied=["customer_payment_mismatch"],
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor, simulated=False)
        return result

    recovered_amount = min(max(case.amount_recovered, Decimal("0.00")), case.amount_at_risk)
    outstanding_amount = max(Decimal("0.00"), case.amount_at_risk - recovered_amount)
    if outstanding_amount == Decimal("0.00"):
        result = ActionResult(
            case_id=case.id,
            action=tool_action,
            status="failed",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message="There is no outstanding recoverable amount for this case.",
            guardrails_applied=["no_outstanding_recoverable_amount"],
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor, simulated=False)
        return result

    merchant = db.get(Merchant, case.merchant_id) if getattr(case, "merchant_id", None) else None
    expiry_hours = getattr(merchant, "default_payment_link_expiry_hours", None) or 48
    notification_service = execution_context.notification_service or NotificationService(settings)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=int(expiry_hours))
    existing_payment_link_id = getattr(case, "razorpay_payment_link_id", None)
    existing_expires_at = getattr(case, "payment_link_expires_at", None)
    if existing_expires_at is not None and existing_expires_at.tzinfo is None:
        existing_expires_at = existing_expires_at.replace(tzinfo=timezone.utc)

    is_link_active = bool(
        existing_payment_link_id and (existing_expires_at is None or existing_expires_at > now)
    )

    if is_link_active:
        payment_link_id = existing_payment_link_id
        payment_link_url = f"https://rzp.io/i/{existing_payment_link_id}"

        notif_result = notification_service.send_recovery_payment_email(
            customer=customer,
            payment=payment,
            payment_link=payment_link_url,
            expires_at=existing_expires_at or expires_at,
            merchant=merchant,
            db=db,
            recovery_case=case,
            actor=execution_context.actor,
        )

        guardrails_applied = ["existing_payment_link_reused"]
        if not notif_result.success and notif_result.reason and notif_result.reason != "email_disabled":
            guardrails_applied.append(notif_result.reason)

        action_label = "payment retry link" if tool_action == "retry_payment" else "payment link"
        if notif_result.success:
            msg = f"Active Razorpay {action_label} reused and sent to customer; awaiting payment confirmation."
        elif notif_result.status == "email_disabled":
            msg = f"Active Razorpay {action_label} reused (email delivery disabled); awaiting payment confirmation."
        else:
            msg = f"Active Razorpay {action_label} reused, but notification failed ({notif_result.reason}); awaiting payment confirmation."

        case.payment_link_sent_at = now
        transition_case_status(
            case,
            "payment_link_active",
            db=db,
            reason="payment_link_reused",
            actor=execution_context.actor,
        )
        db.commit()

        result = ActionResult(
            case_id=case.id,
            action=tool_action,
            status="completed",
            success=True,
            outcome="scheduled",
            amount_recovered=Decimal("0.00"),
            message=msg,
            guardrails_applied=guardrails_applied,
            payment_link_id=payment_link_id,
            payment_link_url=payment_link_url,
        )
        _record_action_attempt(
            db,
            result,
            tool_input.context,
            execution_context.actor,
            simulated=False,
            metadata={
                "razorpay_payment_link_id": payment_link_id,
                "payment_link_url": payment_link_url,
                "notification_channel": "email",
                "notification_status": notif_result.status,
            },
        )
        return result

    customer_email = _usable_contact_value(getattr(customer, "email", None))
    customer_phone = _usable_contact_value(getattr(customer, "phone", None))
    if customer_email is None and customer_phone is None:
        result = ActionResult(
            case_id=case.id,
            action=tool_action,
            status="failed",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message="Customer contact information is unavailable for payment-link delivery.",
            guardrails_applied=["customer_contact_information_missing"],
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor, simulated=False)
        return result

    amount_in_paise = int((outstanding_amount * Decimal("100")).to_integral_value())
    expire_by = int(expires_at.timestamp())
    request = RazorpayPaymentLinkRequest(
        amount=amount_in_paise,
        currency=payment.currency,
        reference_id=str(case.id),
        description=f"RecoverAI recovery payment for case {case.id}",
        customer_name=customer.name,
        customer_email=customer_email,
        customer_contact=customer_phone,
        expire_by=expire_by,
    )
    razorpay_service = execution_context.razorpay_service or RazorpayService(settings)
    try:
        payment_link = razorpay_service.create_payment_link(request)
    except Exception as error:
        logger.warning(
            "Razorpay payment-link creation failed for recovery case %s: exception_type=%s",
            case.id,
            type(error).__name__,
        )
        result = ActionResult(
            case_id=case.id,
            action=tool_action,
            status="failed",
            success=False,
            outcome="failed",
            amount_recovered=Decimal("0.00"),
            message="Razorpay payment-link creation failed; no link was saved.",
            guardrails_applied=[],
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor, simulated=False)
        return result

    case.razorpay_payment_link_id = payment_link.id
    case.payment_link_expires_at = expires_at
    case.payment_link_created_at = now
    case.payment_link_sent_at = now
    case.attempt_count += 1
    case.last_attempt_at = now
    transition_case_status(
        case,
        "payment_link_active",
        db=db,
        reason="payment_link_created",
        actor=execution_context.actor,
    )
    db.commit()

    payment_link_id = payment_link.id
    payment_link_url = payment_link.short_url or f"https://rzp.io/i/{payment_link.id}"

    notif_result = notification_service.send_recovery_payment_email(
        customer=customer,
        payment=payment,
        payment_link=payment_link_url,
        expires_at=expires_at,
        merchant=merchant,
        db=db,
        recovery_case=case,
        actor=execution_context.actor,
    )

    guardrails_applied = []
    if not notif_result.success and notif_result.reason and notif_result.reason != "email_disabled":
        guardrails_applied.append(notif_result.reason)

    action_label = "payment retry link" if tool_action == "retry_payment" else "payment link"
    if notif_result.success:
        msg = f"Razorpay {action_label} created and sent to customer; awaiting payment confirmation."
    elif notif_result.status == "email_disabled":
        msg = f"Razorpay {action_label} created (email delivery disabled); awaiting payment confirmation."
    else:
        msg = f"Razorpay {action_label} created, but notification failed ({notif_result.reason}); awaiting payment confirmation."

    if payment_link.short_url:
        msg = f"{msg} {payment_link.short_url}"

    result = ActionResult(
        case_id=case.id,
        action=tool_action,
        status="completed",
        success=True,
        outcome="scheduled",
        amount_recovered=Decimal("0.00"),
        message=msg,
        guardrails_applied=guardrails_applied,
        payment_link_id=payment_link_id,
        payment_link_url=payment_link_url,
    )
    audit_metadata = {
        "razorpay_payment_link_id": payment_link_id,
        "payment_link_url": payment_link_url,
        "notification_channel": "email",
        "notification_status": notif_result.status,
    }
    _record_action_attempt(
        db,
        result,
        tool_input.context,
        execution_context.actor,
        simulated=False,
        metadata=audit_metadata,
    )
    return result


def retry_payment(
    tool_input: RetryPaymentInput,
    execution_context: RecoveryActionExecutionContext,
) -> ActionResult:
    return _execute_payment_link_and_notify(tool_input, execution_context, "retry_payment")


def create_payment_link(
    tool_input: CreatePaymentLinkInput,
    execution_context: RecoveryActionExecutionContext,
) -> ActionResult:
    return _execute_payment_link_and_notify(tool_input, execution_context, "create_payment_link")


def send_recovery_message(
    tool_input: SendRecoveryMessageInput,
    execution_context: RecoveryActionExecutionContext,
) -> ActionResult:
    return _execute_simulated_tool(
        tool_input,
        execution_context,
        "send_recovery_message",
        "Recovery message simulated; no message was sent.",
        requires_attempt_capacity=True,
    )


def schedule_followup(
    tool_input: ScheduleFollowupInput,
    execution_context: RecoveryActionExecutionContext,
) -> ActionResult:
    if tool_input.wait_minutes is None:
        result = ActionResult(
            case_id=tool_input.case_id,
            action="schedule_followup",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message="WAIT requires validated AI timing before a follow-up can be scheduled.",
            guardrails_applied=["missing_wait_minutes"],
        )
        _record_action_attempt(execution_context.db, result, tool_input.context, execution_context.actor)
        return result

    db = execution_context.db
    case, guardrails, blocked_message = _validate_case(
        db, tool_input.case_id, execution_context.max_recovery_attempts, requires_attempt_capacity=True
    )
    if blocked_message is not None or case is None:
        result = ActionResult(
            case_id=tool_input.case_id, action="schedule_followup", success=False, outcome="blocked",
            amount_recovered=Decimal("0.00"), message=blocked_message or "Recovery case could not be scheduled.",
            guardrails_applied=guardrails,
        )
        _record_action_attempt(db, result, tool_input.context, execution_context.actor)
        return result

    scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=tool_input.wait_minutes)
    existing = None
    if hasattr(db, "scalar"):
        existing = db.scalar(
            select(ScheduledRecoveryAction).where(
                ScheduledRecoveryAction.recovery_case_id == case.id,
                ScheduledRecoveryAction.action == "retry",
                ScheduledRecoveryAction.status.in_(("pending", "running")),
            )
        )
    if existing is None:
        db.add(ScheduledRecoveryAction(
            recovery_case_id=case.id,
            merchant_id=case.merchant_id,
            action="retry",
            scheduled_at=scheduled_at,
            status="pending",
        ))
    else:
        scheduled_at = existing.scheduled_at
    case.next_action_at = scheduled_at
    case.scheduled_action = "retry"
    transition_case_status(
        case,
        "waiting",
        db=db,
        reason="wait_scheduled",
        actor=execution_context.actor,
    )
    result = ActionResult(
        case_id=case.id, action="schedule_followup", status="completed", success=True, outcome="scheduled",
        amount_recovered=Decimal("0.00"),
        message="Follow-up retry scheduled from the validated AI wait interval.",
        guardrails_applied=[], scheduled_at=scheduled_at, scheduled_action="retry",
    )
    _record_action_attempt(db, result, tool_input.context, execution_context.actor,
                           metadata={"wait_minutes": str(tool_input.wait_minutes), "scheduled_action": "retry", "scheduled_at": scheduled_at.isoformat()})
    return result


def stop_recovery(
    tool_input: StopRecoveryInput,
    execution_context: RecoveryActionExecutionContext,
) -> ActionResult:
    def close_case(case: RecoveryCase) -> None:
        transition_case_status(
            case,
            "closed",
            db=execution_context.db,
            reason="stop_recovery",
            actor=execution_context.actor,
        )
        case.ai_decision = "close"
        case.ai_decision_note = "Recovery stopped by the deterministic action dispatcher."

    return _execute_simulated_tool(
        tool_input,
        execution_context,
        "stop_recovery",
        "Recovery workflow stopped; no external provider was called.",
        mutate_case=close_case,
        outcome="stopped",
    )


def execute_recovery_action(
    action: str,
    case_id: UUID,
    context: RecoveryActionExecutionContext,
    *,
    wait_minutes: int | None = None,
) -> ActionResult:
    """Dispatch a bounded recovery recommendation to one deterministic tool only."""
    if action not in ACTION_TOOL_MAP:
        raise InvalidRecoveryActionError("Recovery action is not allowed.")

    tool_action = ACTION_TOOL_MAP[action]  # type: ignore[index]
    if tool_action == "retry_payment":
        return retry_payment(RetryPaymentInput(case_id=case_id, context=context.action_context), context)
    if tool_action == "create_payment_link":
        return create_payment_link(
            CreatePaymentLinkInput(case_id=case_id, context=context.action_context), context
        )
    if tool_action == "schedule_followup":
        return schedule_followup(
            ScheduleFollowupInput(case_id=case_id, context=context.action_context, wait_minutes=wait_minutes), context
        )
    return stop_recovery(StopRecoveryInput(case_id=case_id, context=context.action_context), context)

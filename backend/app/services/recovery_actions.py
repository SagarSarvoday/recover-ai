from dataclasses import dataclass, field
from typing import Callable, Literal
from uuid import UUID
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
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


def _record_action_attempt(
    db: Session,
    result: ActionResult,
    action_context: RecoveryActionContext,
    actor: str,
) -> None:
    db.add(
        AuditLog(
            entity_type="recovery_case",
            entity_id=result.case_id,
            action=f"recovery_action_{result.action}",
            actor=actor,
            details={
                "simulated": True,
                "tool_action": result.action,
                "success": result.success,
                "message": result.message,
                "guardrails_applied": result.guardrails_applied,
                "source": action_context.source,
                "note": action_context.note,
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


def retry_payment(
    tool_input: RetryPaymentInput,
    execution_context: RecoveryActionExecutionContext,
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
            action="retry_payment",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message=blocked_message or "Recovery case could not be processed.",
            guardrails_applied=guardrails,
        )

        _record_action_attempt(
            db,
            result,
            tool_input.context,
            execution_context.actor,
        )

        return result

    payment = db.get(Payment, case.payment_id)

    if payment is None:
        result = ActionResult(
            case_id=tool_input.case_id,
            action="retry_payment",
            success=False,
            outcome="blocked",
            amount_recovered=Decimal("0.00"),
            message="Payment information is missing.",
            guardrails_applied=["required_payment_information_missing"],
        )

        _record_action_attempt(
            db,
            result,
            tool_input.context,
            execution_context.actor,
        )

        return result

    # Record that a recovery attempt was made.
    case.attempt_count += 1
    case.last_attempt_at = datetime.now(timezone.utc)
    case.status = "in_progress"

    # Deterministic simulation:
    # transient/temporary payment failures succeed on retry.
    failure_reason = getattr(payment, "failure_reason", None) or ""
    failure_reason = failure_reason.lower()

    recoverable_failure = any(
        keyword in failure_reason
        for keyword in (
            "temporary",
            "transient",
            "timeout",
            "network",
            "bank error",
        )
    )

    if recoverable_failure:
        recovered_amount = min(
            case.amount_at_risk - case.amount_recovered,
            payment.amount,
        )

        case.amount_recovered += recovered_amount
        case.status = "recovered"

        payment.status = "succeeded"
        payment.failure_reason = None
        payment.paid_at = datetime.now(timezone.utc)

        result = ActionResult(
            case_id=tool_input.case_id,
            action="retry_payment",
            success=True,
            outcome="recovered",
            amount_recovered=recovered_amount,
            message=(
                f"Simulated payment retry succeeded. "
                f"₹{recovered_amount:.2f} recovered."
            ),
            guardrails_applied=guardrails,
        )
    else:
        result = ActionResult(
            case_id=tool_input.case_id,
            action="retry_payment",
            success=False,
            outcome="failed",
            amount_recovered=Decimal("0.00"),
            message="Simulated payment retry failed.",
            guardrails_applied=guardrails,
        )

    _record_action_attempt(
        db,
        result,
        tool_input.context,
        execution_context.actor,
    )

    return result


def create_payment_link(
    tool_input: CreatePaymentLinkInput,
    execution_context: RecoveryActionExecutionContext,
) -> ActionResult:
    return _execute_simulated_tool(
        tool_input,
        execution_context,
        "create_payment_link",
        "Payment-link creation simulated; no payment link was created.",
        requires_attempt_capacity=True,
    )


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
    return _execute_simulated_tool(
        tool_input,
        execution_context,
        "schedule_followup",
        "Follow-up scheduling simulated; no follow-up was scheduled.",
    )


def stop_recovery(
    tool_input: StopRecoveryInput,
    execution_context: RecoveryActionExecutionContext,
) -> ActionResult:
    def close_case(case: RecoveryCase) -> None:
        case.status = "closed"
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
            ScheduleFollowupInput(case_id=case_id, context=context.action_context), context
        )
    return stop_recovery(StopRecoveryInput(case_id=case_id, context=context.action_context), context)

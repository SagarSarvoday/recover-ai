"""Safely schedule a short development-only WAIT follow-up through production logic."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.recovery_actions import ActionResult, RecoveryActionContext
from app.services.legacy_merchant_bootstrap import LEGACY_MERCHANT_ID
from app.services.recovery_actions import RecoveryActionExecutionContext, execute_recovery_action

TESTABLE_ENVIRONMENTS = {"development", "test"}
_RECOVERABLE_RETRY_KEYWORDS = ("temporary", "transient", "timeout", "network", "bank error")


class DevelopmentSchedulerTestError(RuntimeError):
    """Raised when a controlled scheduler test would not be safe to create."""


def schedule_development_wait_retry(
    db: Session,
    case_id: UUID,
    *,
    app_environment: str,
    max_recovery_attempts: int,
) -> ActionResult:
    """Schedule a one-minute WAIT retry through the normal action dispatcher.

    The function deliberately does not insert a job directly. It validates the
    same ownership/status/attempt rules that the scheduler will enforce later,
    and only permits a non-recoverable simulated retry so no customer payment
    link or simulated payment recovery is produced during observation.
    """
    if app_environment.lower() not in TESTABLE_ENVIRONMENTS:
        raise DevelopmentSchedulerTestError("Scheduler test creation is development/test-only.")

    case = db.get(RecoveryCase, case_id)
    if case is None:
        raise DevelopmentSchedulerTestError("Recovery case was not found.")
    if case.merchant_id != LEGACY_MERCHANT_ID:
        raise DevelopmentSchedulerTestError("Recovery case is not owned by the legacy development merchant.")
    if case.status not in {"open", "in_progress"}:
        raise DevelopmentSchedulerTestError("Recovery case is not open or in progress.")
    if case.attempt_count >= max_recovery_attempts:
        raise DevelopmentSchedulerTestError("Recovery case has reached its maximum recovery attempts.")
    if case.next_action_at is not None:
        raise DevelopmentSchedulerTestError("Recovery case already has a scheduled action.")

    payment = db.get(Payment, case.payment_id)
    if payment is None or payment.customer_id != case.customer_id:
        raise DevelopmentSchedulerTestError("Recovery case is missing valid payment information.")
    failure_reason = (payment.failure_reason or "").lower()
    if any(keyword in failure_reason for keyword in _RECOVERABLE_RETRY_KEYWORDS):
        raise DevelopmentSchedulerTestError(
            "Recovery case could simulate a successful retry; choose a non-recoverable failed payment."
        )

    active_job = db.scalar(
        select(ScheduledRecoveryAction.id).where(
            ScheduledRecoveryAction.recovery_case_id == case.id,
            ScheduledRecoveryAction.action == "retry",
            ScheduledRecoveryAction.status.in_(("pending", "running")),
        )
    )
    if active_job is not None:
        raise DevelopmentSchedulerTestError("Recovery case already has an active scheduled retry.")

    result = execute_recovery_action(
        "wait",
        case.id,
        RecoveryActionExecutionContext(
            db=db,
            max_recovery_attempts=max_recovery_attempts,
            action_context=RecoveryActionContext(
                source="manual", note="development_scheduler_end_to_end_test"
            ),
            actor="development_scheduler_test",
        ),
        wait_minutes=1,
    )
    if not result.success or result.scheduled_action != "retry" or result.scheduled_at is None:
        raise DevelopmentSchedulerTestError("The normal WAIT action did not create the test retry schedule.")
    return result

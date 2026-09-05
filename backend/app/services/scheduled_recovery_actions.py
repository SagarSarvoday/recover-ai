"""Durable, database-backed execution of delayed recovery actions."""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.merchant import Merchant
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.recovery_actions import RecoveryActionContext
from app.services.razorpay_service import RazorpayService
from app.services.recovery_actions import RecoveryActionExecutionContext, execute_recovery_action
from app.services.recovery_agent import run_recovery_agent
from app.services.recovery_state_machine import transition_case_status

logger = logging.getLogger(__name__)
LEASE_SECONDS = 60


def claim_due_scheduled_actions(db: Session, *, now: datetime | None = None, limit: int = 20) -> list[UUID]:
    """Claim due work with row locks; SKIP LOCKED makes concurrent workers safe."""
    now = now or datetime.now(timezone.utc)
    rows = db.scalars(
        select(ScheduledRecoveryAction)
        .where(
            ScheduledRecoveryAction.scheduled_at <= now,
            or_(
                ScheduledRecoveryAction.status == "pending",
                and_(
                    ScheduledRecoveryAction.status == "running",
                    ScheduledRecoveryAction.lease_expires_at < now,
                ),
            ),
        )
        .order_by(ScheduledRecoveryAction.scheduled_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    for job in rows:
        job.status = "running"
        job.attempt_count += 1
        job.lease_expires_at = lease_expires_at
    db.commit()
    if rows:
        logger.info("Claimed scheduled recovery actions: job_ids=%s", [str(job.id) for job in rows])
    return [job.id for job in rows]


def execute_claimed_scheduled_action(
    db: Session,
    job_id: UUID,
    *,
    max_recovery_attempts: int,
    now: datetime | None = None,
    razorpay_service: RazorpayService | None = None,
) -> None:
    """Execute one claimed action exactly once, or durably record why it was skipped/failed."""
    now = now or datetime.now(timezone.utc)
    job = db.get(ScheduledRecoveryAction, job_id)
    if job is None or job.status != "running":
        return
    if job.scheduled_at > now:
        return

    case = db.get(RecoveryCase, job.recovery_case_id)
    if case is None:
        _finish_job(db, job, "skipped", now, "Recovery case no longer exists.")
        return
    if case.merchant_id != job.merchant_id:
        _finish_job(db, job, "skipped", now, "Merchant ownership no longer matches the scheduled action.")
        return
    if case.status in {"recovered", "closed"}:
        _finish_job(db, job, "skipped", now, f"Recovery case is already {case.status}.")
        return
    if case.attempt_count >= max_recovery_attempts:
        _finish_job(db, job, "skipped", now, "Maximum recovery attempts have been reached.")
        return

    if str(case.status) == "waiting":
        transition_case_status(
            case,
            "in_progress",
            db=db,
            reason="wait_interval_elapsed",
            actor="scheduled_recovery_worker",
        )

    merchant = db.get(Merchant, case.merchant_id)
    if merchant is not None and merchant.ai_agent_enabled:
        _finish_job(db, job, "completed", now, None)
        case.next_action_at = None
        case.scheduled_action = None
        db.commit()
        try:
            run_recovery_agent(
                db,
                case.id,
                trigger="scheduled_action",
                max_recovery_attempts=max_recovery_attempts,
                settings=settings,
                razorpay_service=razorpay_service,
            )
        except Exception:
            logger.exception(
                "Autonomous recovery agent execution failed for case %s following scheduled action",
                case.id,
            )
        return

    try:
        result = execute_recovery_action(
            job.action,
            case.id,
            RecoveryActionExecutionContext(
                db=db,
                max_recovery_attempts=max_recovery_attempts,
                action_context=RecoveryActionContext(source="ai_recommendation", note="scheduled_wait_followup"),
                actor="scheduled_recovery_worker",
                razorpay_service=razorpay_service,
            ),
        )
    except Exception as error:
        logger.exception("Scheduled recovery action failed: job_id=%s exception_type=%s", job.id, type(error).__name__)
        _finish_job(db, job, "failed", now, "Scheduled recovery action failed internally.")
        return

    if not result.success and any(
        guardrail in {"case_already_recovered", "case_already_closed", "maximum_recovery_attempts_reached"}
        for guardrail in result.guardrails_applied
    ):
        _finish_job(db, job, "skipped", now, result.message)
        return

    # The deterministic action was invoked for merchant with agent disabled.
    _finish_job(db, job, "completed", now, None)


def run_scheduled_recovery_actions(session_factory: object, *, max_recovery_attempts: int) -> int:
    """Run one polling cycle. A new session per job avoids stale state after commits."""
    claim_db = session_factory()  # type: ignore[operator]
    try:
        job_ids = claim_due_scheduled_actions(claim_db)
    finally:
        claim_db.close()
    for job_id in job_ids:
        db = session_factory()  # type: ignore[operator]
        try:
            execute_claimed_scheduled_action(db, job_id, max_recovery_attempts=max_recovery_attempts)
        finally:
            db.close()
    return len(job_ids)


def _finish_job(db: Session, job: ScheduledRecoveryAction, status: str, now: datetime, error_message: str | None) -> None:
    job.status = status
    job.executed_at = now
    job.error_message = error_message
    job.lease_expires_at = None
    case = db.get(RecoveryCase, job.recovery_case_id)
    if case is not None and case.next_action_at == job.scheduled_at:
        case.next_action_at = None
        case.scheduled_action = None
    db.commit()
    logger.info(
        "Finished scheduled recovery action: job_id=%s case_id=%s status=%s",
        job.id,
        job.recovery_case_id,
        status,
    )

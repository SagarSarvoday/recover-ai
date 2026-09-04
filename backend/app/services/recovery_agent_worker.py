"""Continuous AI recovery agent worker.

Merchant-aware background worker that periodically surveys enabled merchants,
identifies eligible recovery cases, and executes bounded run_recovery_agent()
cycles with concurrency protection, audit logging, and resilient error handling.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.audit_log import AuditLog
from app.models.merchant import Merchant
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.services.recovery_agent import run_recovery_agent

logger = logging.getLogger(__name__)

# In-process concurrency lock to prevent the same recovery case from running
# simultaneously in multiple worker iterations.
_in_flight_case_ids: set[UUID] = set()
_in_flight_lock = threading.Lock()

# Rate limit for audit log skips to prevent bloating the audit_log table.
_last_skip_logged: dict[tuple[UUID, str], float] = {}
_skip_lock = threading.Lock()
SKIP_LOG_THROTTLE_SECONDS = 60.0


def claim_case_in_flight(case_id: UUID) -> bool:
    """Atomically claim a recovery case for worker execution."""
    with _in_flight_lock:
        if case_id in _in_flight_case_ids:
            return False
        _in_flight_case_ids.add(case_id)
        return True


def release_case_in_flight(case_id: UUID) -> None:
    """Release a claimed recovery case from worker execution."""
    with _in_flight_lock:
        _in_flight_case_ids.discard(case_id)


def is_case_in_flight(case_id: UUID) -> bool:
    with _in_flight_lock:
        return case_id in _in_flight_case_ids


def _audit_worker_event(
    db: Session,
    case_id: UUID,
    action: str,
    *,
    merchant_id: UUID,
    details: dict | None = None,
    throttle: bool = False,
    throttle_reason: str = "",
) -> None:
    """Safely log a worker lifecycle event."""
    if throttle:
        now_ts = time.time()
        key = (case_id, throttle_reason)
        with _skip_lock:
            last_time = _last_skip_logged.get(key, 0.0)
            if now_ts - last_time < SKIP_LOG_THROTTLE_SECONDS:
                return
            _last_skip_logged[key] = now_ts

    safe_details = {"merchant_id": str(merchant_id), **(details or {})}
    try:
        db.add(
            AuditLog(
                entity_type="recovery_case",
                entity_id=case_id,
                action=action,
                actor="recovery_agent_worker",
                details=safe_details,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Failed to record worker audit log for case %s action %s",
            case_id,
            action,
            exc_info=True,
        )


def find_eligible_cases_for_merchant(
    db: Session,
    merchant_id: UUID,
    *,
    max_recovery_attempts: int,
    cooldown_seconds: int = 60,
    now: datetime | None = None,
) -> tuple[list[RecoveryCase], list[tuple[UUID, str]]]:
    """Identify cases requiring immediate AI recovery processing and record skipped reasons.

    Returns:
        tuple of (eligible_cases, list of (case_id, skip_reason))
    """
    now = now or datetime.now(timezone.utc)
    cooldown_threshold = now - timedelta(seconds=cooldown_seconds)

    # 1. Query open and in_progress candidate cases belonging to this merchant
    candidates = db.scalars(
        select(RecoveryCase)
        .where(
            RecoveryCase.merchant_id == merchant_id,
            RecoveryCase.status.in_(["open", "in_progress"]),
            RecoveryCase.attempt_count < max_recovery_attempts,
        )
        .order_by(RecoveryCase.created_at.asc())
    ).all()

    eligible: list[RecoveryCase] = []
    skipped: list[tuple[UUID, str]] = []

    for case in candidates:
        # Check in-flight lock
        if is_case_in_flight(case.id):
            skipped.append((case.id, "already_in_flight"))
            continue

        # Check for active scheduled actions (pending or running)
        has_active_schedule = db.scalar(
            select(ScheduledRecoveryAction.id)
            .where(
                ScheduledRecoveryAction.recovery_case_id == case.id,
                ScheduledRecoveryAction.status.in_(["pending", "running"]),
            )
            .limit(1)
        ) is not None

        if has_active_schedule:
            skipped.append((case.id, "active_schedule_exists"))
            continue

        # Check if case has an active payment link awaiting customer payment via webhook
        if case.razorpay_payment_link_id is not None:
            skipped.append((case.id, "awaiting_payment_link_webhook"))
            continue

        # Check if case was already analyzed and is within cooldown
        if case.attempt_count > 0 or case.ai_decision is not None:
            reference_time = case.last_attempt_at or case.updated_at
            if reference_time is not None:
                # Ensure timezone-aware comparison
                if reference_time.tzinfo is None:
                    reference_time = reference_time.replace(tzinfo=timezone.utc)
                if reference_time > cooldown_threshold:
                    skipped.append((case.id, "cooldown_active"))
                    continue

        eligible.append(case)

    return eligible, skipped


def process_eligible_case(
    session_factory: object,
    case_id: UUID,
    merchant_id: UUID,
    *,
    max_recovery_attempts: int,
    settings: Settings,
) -> bool:
    """Execute one bounded autonomous recovery agent cycle for an eligible case."""
    if not claim_case_in_flight(case_id):
        logger.debug("Case %s is already in flight; skipping worker processing.", case_id)
        return False

    db: Session = session_factory()  # type: ignore[operator]
    try:
        case = db.get(RecoveryCase, case_id)
        if case is None:
            return False

        # Verify merchant ownership and enabled state
        merchant = db.get(Merchant, merchant_id)
        if merchant is None or not merchant.ai_agent_enabled:
            return False

        if case.status in {"recovered", "closed"}:
            return False

        if case.attempt_count >= max_recovery_attempts:
            return False

        _audit_worker_event(
            db,
            case.id,
            "recovery_agent_worker_cycle",
            merchant_id=merchant.id,
            details={
                "attempt_count": case.attempt_count,
                "amount_at_risk": str(case.amount_at_risk),
            },
        )

        run_recovery_agent(
            db,
            case.id,
            trigger="continuous_worker",
            max_recovery_attempts=max_recovery_attempts,
            settings=settings,
        )
        return True
    except Exception:
        logger.exception("Continuous recovery agent worker failed for case %s", case_id)
        return False
    finally:
        release_case_in_flight(case_id)
        db.close()


def run_continuous_recovery_agent_cycle(
    session_factory: object,
    *,
    max_recovery_attempts: int,
    settings: Settings,
    cooldown_seconds: int = 60,
) -> int:
    """Run one continuous worker survey across all merchants with ai_agent_enabled = True.

    Returns the number of cases processed.
    """
    survey_db: Session = session_factory()  # type: ignore[operator]
    try:
        enabled_merchants = survey_db.scalars(
            select(Merchant).where(Merchant.ai_agent_enabled.is_(True))
        ).all()
        merchant_ids = [m.id for m in enabled_merchants]
    finally:
        survey_db.close()

    if not merchant_ids:
        return 0

    total_processed = 0

    for merchant_id in merchant_ids:
        merchant_db: Session = session_factory()  # type: ignore[operator]
        eligible_cases: list[RecoveryCase] = []
        skipped_cases: list[tuple[UUID, str]] = []
        try:
            eligible_cases, skipped_cases = find_eligible_cases_for_merchant(
                merchant_db,
                merchant_id,
                max_recovery_attempts=max_recovery_attempts,
                cooldown_seconds=cooldown_seconds,
            )
            # Log skipped cases (throttled to avoid flooding DB)
            for case_id, reason in skipped_cases:
                _audit_worker_event(
                    merchant_db,
                    case_id,
                    "recovery_agent_worker_skip",
                    merchant_id=merchant_id,
                    details={"reason": reason},
                    throttle=True,
                    throttle_reason=reason,
                )
        except Exception:
            logger.exception("Error finding eligible cases for merchant %s", merchant_id)
            continue
        finally:
            merchant_db.close()

        for case in eligible_cases:
            try:
                success = process_eligible_case(
                    session_factory,
                    case.id,
                    merchant_id,
                    max_recovery_attempts=max_recovery_attempts,
                    settings=settings,
                )
                if success:
                    total_processed += 1
            except Exception:
                logger.exception(
                    "Continuous worker encountered unexpected error on case %s",
                    case.id,
                )

    return total_processed

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_merchant
from app.models.audit_log import AuditLog
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.schemas.merchant import (
    MerchantAgentStatusResponse,
    MerchantProfileResponse,
    RazorpayAccountUpdateRequest,
)

router = APIRouter(prefix="/merchant", tags=["merchant"])


def get_merchant_agent_status_data(db: Session, merchant: Merchant) -> MerchantAgentStatusResponse:
    enabled = bool(merchant.ai_agent_enabled)
    agent_status = "running" if enabled else "stopped"

    active_cases = db.scalar(
        select(func.count(RecoveryCase.id)).where(
            RecoveryCase.merchant_id == merchant.id,
            RecoveryCase.status.in_(["open", "in_progress"]),
        )
    ) or 0

    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    recovered_today = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), Decimal("0.00")))
        .where(
            Payment.merchant_id == merchant.id,
            Payment.status == "succeeded",
            Payment.paid_at >= today_start,
        )
    ) or Decimal("0.00")

    actions_today = db.scalar(
        select(func.count(AuditLog.id))
        .join(RecoveryCase, AuditLog.entity_id == RecoveryCase.id)
        .where(
            RecoveryCase.merchant_id == merchant.id,
            AuditLog.entity_type == "recovery_case",
            AuditLog.action.in_([
                "recovery_agent_action",
                "recovery_action_retry_payment",
                "recovery_action_create_payment_link",
                "recovery_action_schedule_followup",
                "recovery_action_stop_recovery",
            ]),
            AuditLog.created_at >= today_start,
        )
    ) or 0

    last_case_activity = db.scalar(
        select(func.max(AuditLog.created_at))
        .join(RecoveryCase, AuditLog.entity_id == RecoveryCase.id)
        .where(
            RecoveryCase.merchant_id == merchant.id,
            AuditLog.entity_type == "recovery_case",
        )
    )
    last_merchant_activity = db.scalar(
        select(func.max(AuditLog.created_at))
        .where(
            AuditLog.entity_type == "merchant",
            AuditLog.entity_id == merchant.id,
        )
    )
    candidates = [dt for dt in [last_case_activity, last_merchant_activity, merchant.ai_agent_started_at] if dt is not None]
    last_activity_at = max(candidates) if candidates else None

    return MerchantAgentStatusResponse(
        enabled=enabled,
        status=agent_status,
        started_at=merchant.ai_agent_started_at,
        last_activity_at=last_activity_at,
        active_cases=active_cases,
        actions_today=actions_today,
        recovered_today=Decimal(str(recovered_today)),
    )


@router.get("/me", response_model=MerchantProfileResponse, summary="Get the authenticated merchant")
def get_merchant_profile(
    current_merchant: Merchant = Depends(get_current_merchant),
) -> Merchant:
    return current_merchant


@router.get(
    "/agent",
    response_model=MerchantAgentStatusResponse,
    summary="Get current AI recovery agent status",
)
def get_agent_status(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> MerchantAgentStatusResponse:
    try:
        return get_merchant_agent_status_data(db, current_merchant)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to load agent status.",
        ) from None


@router.post(
    "/agent/start",
    response_model=MerchantAgentStatusResponse,
    summary="Start continuous autonomous AI recovery agent",
)
def start_merchant_agent(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> MerchantAgentStatusResponse:
    now = datetime.now(timezone.utc)
    if not current_merchant.ai_agent_enabled:
        current_merchant.ai_agent_enabled = True
        current_merchant.ai_agent_started_at = now
        current_merchant.ai_agent_updated_at = now
        db.add(
            AuditLog(
                entity_type="merchant",
                entity_id=current_merchant.id,
                action="recovery_agent_enabled",
                actor="merchant",
                details={
                    "merchant_id": str(current_merchant.id),
                    "started_at": now.isoformat(),
                },
            )
        )
        try:
            db.commit()
            db.refresh(current_merchant)
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to start AI agent mode.",
            ) from None

    return get_merchant_agent_status_data(db, current_merchant)


@router.post(
    "/agent/stop",
    response_model=MerchantAgentStatusResponse,
    summary="Stop continuous autonomous AI recovery agent",
)
def stop_merchant_agent(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> MerchantAgentStatusResponse:
    now = datetime.now(timezone.utc)
    if current_merchant.ai_agent_enabled:
        current_merchant.ai_agent_enabled = False
        current_merchant.ai_agent_updated_at = now
        db.add(
            AuditLog(
                entity_type="merchant",
                entity_id=current_merchant.id,
                action="recovery_agent_disabled",
                actor="merchant",
                details={
                    "merchant_id": str(current_merchant.id),
                    "stopped_at": now.isoformat(),
                },
            )
        )
        try:
            db.commit()
            db.refresh(current_merchant)
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to stop AI agent mode.",
            ) from None

    return get_merchant_agent_status_data(db, current_merchant)


@router.put(
    "/razorpay-account",
    response_model=MerchantProfileResponse,
    summary="Configure the authenticated merchant's Razorpay account",
)
def update_razorpay_account(
    update: RazorpayAccountUpdateRequest,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> Merchant:
    if current_merchant.razorpay_account_id == update.razorpay_account_id:
        return current_merchant

    try:
        existing_merchant = db.scalar(
            select(Merchant).where(Merchant.razorpay_account_id == update.razorpay_account_id)
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to update Razorpay account configuration.",
        ) from None

    if existing_merchant is not None and existing_merchant.id != current_merchant.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Razorpay account ID is already assigned to another merchant.",
        )

    current_merchant.razorpay_account_id = update.razorpay_account_id
    try:
        db.commit()
        db.refresh(current_merchant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Razorpay account ID is already assigned to another merchant.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to update Razorpay account configuration.",
        ) from None
    return current_merchant


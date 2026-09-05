from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_merchant
from app.models.audit_log import AuditLog
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.schemas.merchant import (
    IntegrationStatus,
    MerchantAgentStatusResponse,
    MerchantMetricsResponse,
    MerchantProfileResponse,
    MerchantSettingsResponse,
    MerchantSettingsUpdateRequest,
    RazorpayAccountUpdateRequest,
    RecoveryConfiguration,
    WebhookInstruction,
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


@router.get(
    "/metrics",
    response_model=MerchantMetricsResponse,
    summary="Get full merchant observability and pipeline metrics",
)
def get_merchant_metrics(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> MerchantMetricsResponse:
    now_utc = datetime.now(timezone.utc)

    failed_payments = db.scalar(
        select(func.count(Payment.id)).where(
            Payment.merchant_id == current_merchant.id,
            Payment.status == "failed",
        )
    ) or 0

    cases_aggregates = db.execute(
        select(
            func.coalesce(func.sum(RecoveryCase.amount_at_risk), Decimal("0.00")),
            func.coalesce(func.sum(RecoveryCase.amount_recovered), Decimal("0.00")),
        ).where(RecoveryCase.merchant_id == current_merchant.id)
    ).one()
    total_value_at_risk = cases_aggregates[0]
    recovered_amount = cases_aggregates[1]

    recovery_rate = float(
        round((recovered_amount / total_value_at_risk * Decimal("100")), 2)
    ) if total_value_at_risk > 0 else 0.0

    active_recoveries = db.scalar(
        select(func.count(RecoveryCase.id)).where(
            RecoveryCase.merchant_id == current_merchant.id,
            RecoveryCase.status.in_(["open", "in_progress", "waiting", "payment_link_active"]),
        )
    ) or 0

    waiting_cases = db.scalar(
        select(func.count(RecoveryCase.id)).where(
            RecoveryCase.merchant_id == current_merchant.id,
            or_(
                RecoveryCase.status == "waiting",
                and_(
                    RecoveryCase.status.in_(["open", "in_progress"]),
                    RecoveryCase.scheduled_action.is_not(None),
                ),
            ),
        )
    ) or 0

    payment_links_sent = db.scalar(
        select(func.count(RecoveryCase.id)).where(
            RecoveryCase.merchant_id == current_merchant.id,
            RecoveryCase.razorpay_payment_link_id.is_not(None),
        )
    ) or 0

    expired_payment_links = db.scalar(
        select(func.count(RecoveryCase.id)).where(
            RecoveryCase.merchant_id == current_merchant.id,
            RecoveryCase.razorpay_payment_link_id.is_not(None),
            RecoveryCase.status != "recovered",
            RecoveryCase.payment_link_expires_at.is_not(None),
            RecoveryCase.payment_link_expires_at < now_utc,
        )
    ) or 0

    notification_failures = db.scalar(
        select(func.count(AuditLog.id))
        .join(RecoveryCase, AuditLog.entity_id == RecoveryCase.id)
        .where(
            RecoveryCase.merchant_id == current_merchant.id,
            AuditLog.entity_type == "recovery_case",
            AuditLog.action == "notification_failed",
        )
    ) or 0

    return MerchantMetricsResponse(
        failed_payments=failed_payments,
        total_value_at_risk=total_value_at_risk,
        recovered_amount=recovered_amount,
        recovery_rate=recovery_rate,
        active_recoveries=active_recoveries,
        waiting_cases=waiting_cases,
        payment_links_sent=payment_links_sent,
        expired_payment_links=expired_payment_links,
        customer_notification_failures=notification_failures,
    )


def _build_merchant_settings(merchant: Merchant) -> MerchantSettingsResponse:
    rzp_acct = getattr(merchant, "razorpay_account_id", None)
    has_key_id = bool(settings.razorpay_key_id)
    has_key_secret = settings.razorpay_key_secret is not None
    has_webhook_secret = settings.razorpay_webhook_secret is not None

    integrations = IntegrationStatus(
        razorpay_account_id=rzp_acct,
        razorpay_configured=bool(rzp_acct and has_key_id and has_key_secret),
        razorpay_key_id_configured=has_key_id,
        razorpay_key_secret_configured=has_key_secret,
        razorpay_webhook_secret_configured=has_webhook_secret,
        smtp_configured=bool(settings.email_enabled and settings.smtp_host),
        smtp_host=settings.smtp_host if settings.email_enabled else None,
        smtp_port=settings.smtp_port if settings.email_enabled else None,
        smtp_from_email=settings.email_from if settings.email_enabled else None,
        email_enabled=settings.email_enabled,
        ai_agent_enabled=bool(merchant.ai_agent_enabled),
        onboarding_complete=bool(rzp_acct and has_webhook_secret),
    )

    webhook = WebhookInstruction(
        webhook_url="/api/v1/webhooks/razorpay",
        secret_configured=has_webhook_secret,
        required_events=["payment.failed", "payment_link.paid"],
    )

    recovery = RecoveryConfiguration(
        max_recovery_attempts=getattr(merchant, "max_recovery_attempts", 3) or 3,
        default_payment_link_expiry_hours=getattr(merchant, "default_payment_link_expiry_hours", 48) or 48,
        default_wait_minutes=getattr(merchant, "default_wait_minutes", 60) or 60,
        auto_notify_customer=getattr(merchant, "auto_notify_customer", True) if getattr(merchant, "auto_notify_customer", None) is not None else True,
    )

    return MerchantSettingsResponse(
        id=merchant.id,
        name=merchant.name,
        email=merchant.email,
        business_name=getattr(merchant, "business_name", None),
        support_email=getattr(merchant, "support_email", None),
        support_phone=getattr(merchant, "support_phone", None),
        integrations=integrations,
        webhook=webhook,
        recovery=recovery,
    )


@router.get(
    "/settings",
    response_model=MerchantSettingsResponse,
    summary="Get complete merchant settings and integration configuration status",
)
def get_merchant_settings(
    current_merchant: Merchant = Depends(get_current_merchant),
) -> MerchantSettingsResponse:
    return _build_merchant_settings(current_merchant)


@router.put(
    "/settings",
    response_model=MerchantSettingsResponse,
    summary="Update merchant profile and recovery configuration",
)
def update_merchant_settings(
    payload: MerchantSettingsUpdateRequest,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> MerchantSettingsResponse:
    if payload.razorpay_account_id is not None and payload.razorpay_account_id != current_merchant.razorpay_account_id:
        existing = db.scalar(
            select(Merchant).where(
                Merchant.razorpay_account_id == payload.razorpay_account_id,
                Merchant.id != current_merchant.id,
            )
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Razorpay account ID is already assigned to another merchant.",
            )
        current_merchant.razorpay_account_id = payload.razorpay_account_id

    changes: dict[str, object] = {}
    if payload.business_name is not None:
        current_merchant.business_name = payload.business_name
        changes["business_name"] = payload.business_name
    if payload.support_email is not None:
        current_merchant.support_email = payload.support_email
        changes["support_email"] = payload.support_email
    if payload.support_phone is not None:
        current_merchant.support_phone = payload.support_phone
        changes["support_phone"] = payload.support_phone
    if payload.max_recovery_attempts is not None:
        current_merchant.max_recovery_attempts = payload.max_recovery_attempts
        changes["max_recovery_attempts"] = payload.max_recovery_attempts
    if payload.default_payment_link_expiry_hours is not None:
        current_merchant.default_payment_link_expiry_hours = payload.default_payment_link_expiry_hours
        changes["default_payment_link_expiry_hours"] = payload.default_payment_link_expiry_hours
    if payload.default_wait_minutes is not None:
        current_merchant.default_wait_minutes = payload.default_wait_minutes
        changes["default_wait_minutes"] = payload.default_wait_minutes
    if payload.auto_notify_customer is not None:
        current_merchant.auto_notify_customer = payload.auto_notify_customer
        changes["auto_notify_customer"] = payload.auto_notify_customer

    if changes:
        db.add(
            AuditLog(
                entity_type="merchant",
                entity_id=current_merchant.id,
                action="merchant_settings_updated",
                actor="merchant",
                details=changes,
            )
        )

    try:
        db.commit()
        db.refresh(current_merchant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Configuration conflict occurred while updating settings.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to save merchant settings.",
        ) from None

    return _build_merchant_settings(current_merchant)


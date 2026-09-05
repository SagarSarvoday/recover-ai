from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    AuthenticationConfigurationError,
    create_access_token,
    generate_password_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.models.audit_log import AuditLog
from app.models.merchant import Merchant
from app.models.merchant_password_reset_token import MerchantPasswordResetToken
from app.schemas.auth import (
    AccessTokenResponse,
    AuthenticatedMerchantResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    MerchantLoginRequest,
    MerchantRegistrationRequest,
    ResetPasswordRequest,
    ResetPasswordResponse,
)
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/auth", tags=["auth"])
_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid email or password.",
    headers={"WWW-Authenticate": "Bearer"},
)


@router.post(
    "/register",
    response_model=AuthenticatedMerchantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a merchant",
)
def register_merchant(
    registration: MerchantRegistrationRequest,
    db: Session = Depends(get_db),
) -> Merchant:
    try:
        existing_merchant = db.scalar(select(Merchant).where(Merchant.email == registration.email))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to register merchant.",
        ) from None
    if existing_merchant is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered.")

    merchant = Merchant(
        name=registration.name,
        email=registration.email,
        password_hash=hash_password(registration.password),
    )
    db.add(merchant)
    try:
        db.commit()
        db.refresh(merchant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered.") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to register merchant.",
        ) from None
    return merchant


@router.post(
    "/login",
    response_model=AccessTokenResponse,
    summary="Authenticate a merchant",
)
def login_merchant(
    credentials: MerchantLoginRequest,
    db: Session = Depends(get_db),
) -> AccessTokenResponse:
    try:
        merchant = db.scalar(select(Merchant).where(Merchant.email == credentials.email))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to authenticate merchant.",
        ) from None

    if merchant is None or not verify_password(credentials.password, merchant.password_hash):
        raise _INVALID_CREDENTIALS
    try:
        access_token = create_access_token(merchant.id)
    except AuthenticationConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from None
    return AccessTokenResponse(access_token=access_token)


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    summary="Request a password reset link",
)
def forgot_password(
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> ForgotPasswordResponse:
    generic_response = ForgotPasswordResponse()
    try:
        merchant = db.scalar(select(Merchant).where(Merchant.email == payload.email))
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to process password reset request.",
        ) from None

    if merchant is None:
        # Anti-enumeration guarantee: return identical success message without revealing absence
        return generic_response

    raw_token, token_hash = generate_password_reset_token()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.password_reset_token_expire_minutes)

    reset_token = MerchantPasswordResetToken(
        merchant_id=merchant.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(reset_token)
    db.add(
        AuditLog(
            entity_type="merchant",
            entity_id=merchant.id,
            action="password_reset_requested",
            actor="merchant",
            details={"email": merchant.email},
        )
    )
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to process password reset request.",
        ) from None

    notification_service = NotificationService(settings)
    notification_service.send_password_reset_email(merchant, raw_token, db=db, actor="auth_service")

    return generic_response


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    summary="Reset merchant password using token",
)
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> ResetPasswordResponse:
    token_hash = hash_reset_token(payload.token)
    now = datetime.now(timezone.utc)

    try:
        reset_token = db.scalar(
            select(MerchantPasswordResetToken).where(
                MerchantPasswordResetToken.token_hash == token_hash
            )
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify reset token.",
        ) from None

    if (
        reset_token is None
        or reset_token.used_at is not None
        or (reset_token.expires_at.replace(tzinfo=timezone.utc) if reset_token.expires_at.tzinfo is None else reset_token.expires_at) <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token.",
        )

    merchant = db.get(Merchant, reset_token.merchant_id)
    if merchant is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token.",
        )

    merchant.password_hash = hash_password(payload.new_password)
    merchant.updated_at = now
    reset_token.used_at = now

    # Invalidate any other pending reset tokens for this merchant
    other_tokens = db.scalars(
        select(MerchantPasswordResetToken).where(
            MerchantPasswordResetToken.merchant_id == merchant.id,
            MerchantPasswordResetToken.id != reset_token.id,
            MerchantPasswordResetToken.used_at.is_(None),
        )
    ).all()
    for tok in other_tokens:
        tok.used_at = now

    db.add(
        AuditLog(
            entity_type="merchant",
            entity_id=merchant.id,
            action="password_reset_completed",
            actor="merchant",
            details={"merchant_id": str(merchant.id)},
        )
    )

    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to complete password reset.",
        ) from None

    return ResetPasswordResponse()


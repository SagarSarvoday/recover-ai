from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    AuthenticationConfigurationError,
    create_access_token,
    hash_password,
    verify_password,
)
from app.models.merchant import Merchant
from app.schemas.auth import (
    AccessTokenResponse,
    AuthenticatedMerchantResponse,
    MerchantLoginRequest,
    MerchantRegistrationRequest,
)

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

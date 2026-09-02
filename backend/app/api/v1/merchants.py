from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_merchant
from app.models.merchant import Merchant
from app.schemas.merchant import MerchantProfileResponse, RazorpayAccountUpdateRequest

router = APIRouter(prefix="/merchant", tags=["merchant"])


@router.get("/me", response_model=MerchantProfileResponse, summary="Get the authenticated merchant")
def get_merchant_profile(
    current_merchant: Merchant = Depends(get_current_merchant),
) -> Merchant:
    return current_merchant


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

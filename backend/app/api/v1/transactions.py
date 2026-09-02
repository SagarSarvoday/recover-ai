from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_merchant
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.transaction import Transaction
from app.schemas.razorpay import RazorpayOrderRequest
from app.schemas.transaction import TransactionCreateRequest, TransactionResponse
from app.services.razorpay_service import RazorpayConfigurationError, RazorpayService

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _to_paise(amount: Decimal) -> int:
    return int((amount * Decimal("100")).to_integral_value())


@router.post("", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(
    request: TransactionCreateRequest,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> Transaction:
    customer = db.get(Customer, request.customer_id)
    if customer is None or customer.merchant_id != current_merchant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")
    try:
        existing = db.scalar(
            select(Transaction).where(
                Transaction.merchant_id == current_merchant.id,
                Transaction.merchant_transaction_id == request.merchant_transaction_id,
            )
        )
    except SQLAlchemyError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unable to create transaction.") from None
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="merchant_transaction_id is already in use.")

    try:
        order = RazorpayService(settings).create_order(
            RazorpayOrderRequest(
                amount=_to_paise(request.amount),
                currency=request.currency,
                receipt=request.merchant_transaction_id[:40],
            )
        )
    except RazorpayConfigurationError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from None
    except Exception:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to create Razorpay order.") from None

    transaction = Transaction(
        merchant_id=current_merchant.id,
        customer_id=customer.id,
        merchant_transaction_id=request.merchant_transaction_id,
        razorpay_order_id=order.id,
        amount=request.amount,
        currency=request.currency,
        status="pending",
    )
    db.add(transaction)
    try:
        db.commit()
        db.refresh(transaction)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Transaction already exists.") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unable to store transaction.") from None
    return transaction


@router.get("", response_model=list[TransactionResponse])
def list_transactions(
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> list[Transaction]:
    try:
        return db.scalars(
            select(Transaction)
            .where(Transaction.merchant_id == current_merchant.id)
            .order_by(Transaction.created_at.desc())
        ).all()
    except SQLAlchemyError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unable to load transactions.") from None


@router.get("/{transaction_id}", response_model=TransactionResponse)
def get_transaction(
    transaction_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> Transaction:
    transaction = db.get(Transaction, transaction_id)
    if transaction is None or transaction.merchant_id != current_merchant.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")
    return transaction

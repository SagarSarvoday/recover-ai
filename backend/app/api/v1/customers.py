from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_merchant
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.transaction import Transaction
from app.schemas.customer import (
    CustomerCreateRequest,
    CustomerDetailResponse,
    CustomerPaymentItem,
    CustomerRecoveryCaseItem,
    CustomerResponse,
    CustomerUpdateRequest,
)
from app.schemas.recovery_case import compute_payment_link_status

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerResponse], summary="List merchant-scoped customers")
def list_customers(
    query: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> list[Customer]:
    stmt = select(Customer).where(Customer.merchant_id == current_merchant.id)
    if query:
        term = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Customer.name.ilike(term),
                Customer.email.ilike(term),
                Customer.phone.ilike(term),
            )
        )
    stmt = stmt.order_by(Customer.created_at.desc()).limit(limit).offset(offset)
    try:
        return list(db.scalars(stmt).all())
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to query customers.",
        ) from None


@router.post(
    "",
    response_model=CustomerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new customer for the authenticated merchant",
)
def create_customer(
    request: CustomerCreateRequest,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> Customer:
    existing = db.scalar(
        select(Customer).where(
            Customer.merchant_id == current_merchant.id,
            Customer.email == request.email,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A customer with this email already exists for your store.",
        )

    now = datetime.now(timezone.utc)
    customer = Customer(
        merchant_id=current_merchant.id,
        name=request.name,
        email=request.email,
        phone=request.phone,
        created_at=now,
        updated_at=now,
    )
    db.add(customer)
    db.flush()

    db.add(
        AuditLog(
            entity_type="customer",
            entity_id=customer.id,
            action="customer_created",
            actor="merchant",
            details={
                "merchant_id": str(current_merchant.id),
                "email": customer.email,
                "name": customer.name,
            },
        )
    )

    try:
        db.commit()
        db.refresh(customer)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A customer with this email already exists for your store.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to create customer.",
        ) from None

    return customer


@router.get(
    "/{customer_id}",
    response_model=CustomerDetailResponse,
    summary="Get customer details with payment and recovery history",
)
def get_customer_details(
    customer_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> CustomerDetailResponse:
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.merchant_id == current_merchant.id,
        )
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    payments = list(
        db.scalars(
            select(Payment)
            .where(
                Payment.customer_id == customer.id,
                Payment.merchant_id == current_merchant.id,
            )
            .order_by(Payment.created_at.desc())
        ).all()
    )

    recovery_cases = list(
        db.scalars(
            select(RecoveryCase)
            .where(
                RecoveryCase.customer_id == customer.id,
                RecoveryCase.merchant_id == current_merchant.id,
            )
            .order_by(RecoveryCase.created_at.desc())
        ).all()
    )

    successful_payments = [p for p in payments if p.status == "succeeded"]
    failed_payments = [p for p in payments if p.status == "failed"]
    total_spent = sum((p.amount for p in successful_payments), start=Decimal("0.00"))
    total_recovered = sum((c.amount_recovered for c in recovery_cases), start=Decimal("0.00"))
    total_at_risk = sum((c.amount_at_risk for c in recovery_cases), start=Decimal("0.00"))
    recovery_rate = (
        float(round((total_recovered / total_at_risk) * 100, 1))
        if total_at_risk > Decimal("0.00")
        else 0.0
    )
    active_cases_count = len([
        c for c in recovery_cases
        if c.status in ("open", "in_progress", "waiting", "payment_link_active")
    ])

    payment_items = [
        CustomerPaymentItem(
            id=p.id,
            amount=p.amount,
            currency=p.currency,
            status=p.status,
            failure_reason=p.failure_reason,
            paid_at=p.paid_at,
            created_at=p.created_at,
        )
        for p in payments
    ]

    case_items = [
        CustomerRecoveryCaseItem(
            id=c.id,
            payment_id=c.payment_id,
            status=c.status,
            amount_at_risk=c.amount_at_risk,
            amount_recovered=c.amount_recovered,
            attempt_count=c.attempt_count,
            ai_decision=c.ai_decision,
            razorpay_payment_link_id=c.razorpay_payment_link_id,
            payment_link_status=compute_payment_link_status(c),
            created_at=c.created_at,
        )
        for c in recovery_cases
    ]

    case_ids = [c.id for c in recovery_cases]
    notif_logs: list[dict] = []
    if case_ids:
        logs = db.scalars(
            select(AuditLog)
            .where(
                AuditLog.entity_type == "recovery_case",
                AuditLog.entity_id.in_(case_ids),
                AuditLog.action.in_(("notification_sent", "notification_failed")),
            )
            .order_by(AuditLog.created_at.desc())
        ).all()
        notif_logs = [
            {
                "id": str(l.id),
                "case_id": str(l.entity_id),
                "action": l.action,
                "actor": l.actor,
                "details": l.details,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ]

    return CustomerDetailResponse(
        customer=CustomerResponse.model_validate(customer),
        total_payments_count=len(payments),
        successful_payments_count=len(successful_payments),
        failed_payments_count=len(failed_payments),
        total_spent=total_spent,
        total_recovered=total_recovered,
        recovery_rate=recovery_rate,
        active_cases_count=active_cases_count,
        payments=payment_items,
        recovery_cases=case_items,
        notifications=notif_logs,
    )


@router.put(
    "/{customer_id}",
    response_model=CustomerResponse,
    summary="Update customer details",
)
def update_customer(
    customer_id: UUID,
    request: CustomerUpdateRequest,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> Customer:
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.merchant_id == current_merchant.id,
        )
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    if request.email is not None and request.email != customer.email:
        conflict = db.scalar(
            select(Customer).where(
                Customer.merchant_id == current_merchant.id,
                Customer.email == request.email,
                Customer.id != customer.id,
            )
        )
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A customer with this email already exists for your store.",
            )
        customer.email = request.email

    if request.name is not None:
        customer.name = request.name
    if request.phone is not None:
        customer.phone = request.phone

    customer.updated_at = datetime.now(timezone.utc)

    db.add(
        AuditLog(
            entity_type="customer",
            entity_id=customer.id,
            action="customer_updated",
            actor="merchant",
            details={
                "merchant_id": str(current_merchant.id),
                "updated_email": customer.email,
                "updated_name": customer.name,
            },
        )
    )

    try:
        db.commit()
        db.refresh(customer)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A customer with this email already exists for your store.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to update customer.",
        ) from None

    return customer


@router.delete(
    "/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a customer with no associated transaction or payment history",
)
def delete_customer(
    customer_id: UUID,
    db: Session = Depends(get_db),
    current_merchant: Merchant = Depends(get_current_merchant),
) -> None:
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.merchant_id == current_merchant.id,
        )
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )

    has_transactions = db.scalar(
        select(Transaction.id).where(Transaction.customer_id == customer.id).limit(1)
    )
    has_payments = db.scalar(
        select(Payment.id).where(Payment.customer_id == customer.id).limit(1)
    )
    if has_transactions or has_payments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete customer with existing transaction or payment history.",
        )

    db.delete(customer)
    db.add(
        AuditLog(
            entity_type="customer",
            entity_id=customer_id,
            action="customer_deleted",
            actor="merchant",
            details={"merchant_id": str(current_merchant.id)},
        )
    )
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to delete customer.",
        ) from None

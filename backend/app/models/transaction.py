from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Transaction(Base):
    """A merchant business payment intent, which may have many provider attempts."""

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("merchant_id", "merchant_transaction_id", name="transactions_merchant_external_key"),
        UniqueConstraint("razorpay_order_id", name="transactions_razorpay_order_id_key"),
        CheckConstraint("amount > 0", name="transactions_amount_chk"),
        CheckConstraint("status IN ('created', 'pending', 'paid', 'cancelled')", name="transactions_status_chk"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("merchants.id", ondelete="RESTRICT"), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False)
    merchant_transaction_id: Mapped[str] = mapped_column(Text, nullable=False)
    razorpay_order_id: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="INR")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    merchant: Mapped["Merchant"] = relationship(back_populates="transactions")
    customer: Mapped["Customer"] = relationship(back_populates="transactions")
    payments: Mapped[list["Payment"]] = relationship(back_populates="transaction")

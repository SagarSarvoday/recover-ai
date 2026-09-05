from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Authentication is intentionally out of scope; this remains nullable until onboarding exists.
    password_hash: Mapped[str | None] = mapped_column(Text)
    razorpay_account_id: Mapped[str | None] = mapped_column(Text, unique=True)
    ai_agent_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false", default=False
    )
    ai_agent_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_agent_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    business_name: Mapped[str | None] = mapped_column(Text)
    support_email: Mapped[str | None] = mapped_column(Text)
    support_phone: Mapped[str | None] = mapped_column(Text)
    max_recovery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3", default=3
    )
    default_payment_link_expiry_hours: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="48", default=48
    )
    default_wait_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="60", default=60
    )
    auto_notify_customer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customers: Mapped[list["Customer"]] = relationship(back_populates="merchant")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="merchant")
    payments: Mapped[list["Payment"]] = relationship(back_populates="merchant")
    recovery_cases: Mapped[list["RecoveryCase"]] = relationship(back_populates="merchant")
    password_reset_tokens: Mapped[list["MerchantPasswordResetToken"]] = relationship(
        back_populates="merchant", cascade="all, delete-orphan"
    )

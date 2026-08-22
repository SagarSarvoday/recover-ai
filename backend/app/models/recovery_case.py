from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'in_progress', 'recovered', 'closed')",
            name="recovery_cases_status_chk",
        ),
        CheckConstraint("amount_at_risk >= 0", name="recovery_cases_amount_at_risk_chk"),
        CheckConstraint("amount_recovered >= 0", name="recovery_cases_amount_recovered_chk"),
        CheckConstraint("attempt_count >= 0", name="recovery_cases_attempt_count_chk"),
        CheckConstraint(
            "ai_decision IS NULL OR ai_decision IN ('retry', 'wait', 'contact', 'skip', 'close')",
            name="recovery_cases_ai_decision_chk",
        ),
        CheckConstraint(
            "amount_recovered <= amount_at_risk",
            name="recovery_cases_recovered_lte_risk_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    amount_at_risk: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amount_recovered: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    ai_decision: Mapped[str | None] = mapped_column(Text)
    ai_decision_note: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    customer: Mapped["Customer"] = relationship(back_populates="recovery_cases")
    payment: Mapped["Payment"] = relationship(back_populates="recovery_case")

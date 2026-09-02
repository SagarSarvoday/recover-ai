from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ScheduledRecoveryAction(Base):
    """A durable, leased recovery action that can be processed by any worker."""

    __tablename__ = "scheduled_recovery_actions"
    __table_args__ = (
        CheckConstraint("action IN ('retry', 'contact')", name="scheduled_recovery_actions_action_chk"),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'skipped', 'failed')",
            name="scheduled_recovery_actions_status_chk",
        ),
        CheckConstraint("attempt_count >= 0", name="scheduled_recovery_actions_attempt_count_chk"),
        UniqueConstraint("recovery_case_id", "action", "scheduled_at", name="scheduled_recovery_actions_case_action_time_key"),
        Index(
            "scheduled_recovery_actions_one_active_case_action_key",
            "recovery_case_id",
            "action",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recovery_cases.id", ondelete="CASCADE"), nullable=False
    )
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("merchants.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

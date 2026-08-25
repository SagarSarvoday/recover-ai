import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RazorpayWebhookEvent(Base):
    """Minimal idempotency and audit record for incoming Razorpay webhooks."""

    __tablename__ = "razorpay_webhook_events"
    __table_args__ = (
        CheckConstraint(
            "processing_status IN ('processed', 'ignored')",
            name="razorpay_webhook_events_processing_status_chk",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    razorpay_event_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    external_entity_id: Mapped[str | None] = mapped_column(Text)
    processing_status: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

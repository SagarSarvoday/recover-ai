import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class MerchantProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    business_name: str | None = None
    support_email: str | None = None
    support_phone: str | None = None
    razorpay_account_id: str | None = None
    ai_agent_enabled: bool = False
    ai_agent_started_at: datetime | None = None
    ai_agent_updated_at: datetime | None = None
    max_recovery_attempts: int = 3
    default_payment_link_expiry_hours: int = 48
    default_wait_minutes: int = 60
    auto_notify_customer: bool = True
    created_at: datetime
    updated_at: datetime

    @field_validator("ai_agent_enabled", mode="before")
    @classmethod
    def validate_ai_agent_enabled(cls, value: object) -> bool:
        return bool(value)

    @field_validator("max_recovery_attempts", mode="before")
    @classmethod
    def validate_max_recovery_attempts(cls, value: object) -> int:
        return int(value) if value is not None else 3

    @field_validator("default_payment_link_expiry_hours", mode="before")
    @classmethod
    def validate_default_payment_link_expiry_hours(cls, value: object) -> int:
        return int(value) if value is not None else 48

    @field_validator("default_wait_minutes", mode="before")
    @classmethod
    def validate_default_wait_minutes(cls, value: object) -> int:
        return int(value) if value is not None else 60

    @field_validator("auto_notify_customer", mode="before")
    @classmethod
    def validate_auto_notify_customer(cls, value: object) -> bool:
        return bool(value) if value is not None else True


class MerchantAgentStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    status: Literal["running", "stopped"]
    started_at: datetime | None = None
    last_activity_at: datetime | None = None
    active_cases: int
    actions_today: int
    recovered_today: Decimal

    @field_validator("enabled", mode="before")
    @classmethod
    def validate_enabled(cls, value: object) -> bool:
        return bool(value)


class RazorpayAccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    razorpay_account_id: str = Field(min_length=1, max_length=100)

    @field_validator("razorpay_account_id")
    @classmethod
    def validate_razorpay_account_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Razorpay account ID is required.")
        if not re.fullmatch(r"acc_[A-Za-z0-9]{6,96}", normalized):
            raise ValueError("Razorpay account ID must use the acc_ prefix and alphanumeric identifier.")
        return normalized


class MerchantMetricsResponse(BaseModel):
    failed_payments: int
    total_value_at_risk: Decimal
    recovered_amount: Decimal
    recovery_rate: float
    active_recoveries: int
    waiting_cases: int
    payment_links_sent: int
    expired_payment_links: int
    customer_notification_failures: int


class WebhookInstruction(BaseModel):
    webhook_url: str
    secret_configured: bool
    required_events: list[str] = [
        "payment.failed",
        "payment_link.paid",
    ]
    instructions: str = (
        "In your Razorpay Dashboard under Settings → Webhooks, add a new webhook pointing to this URL. "
        "Select events 'payment.failed' and 'payment_link.paid', and set the secret matching RAZORPAY_WEBHOOK_SECRET."
    )


class IntegrationStatus(BaseModel):
    razorpay_account_id: str | None
    razorpay_configured: bool
    razorpay_key_id_configured: bool
    razorpay_key_secret_configured: bool
    razorpay_webhook_secret_configured: bool
    smtp_configured: bool
    smtp_host: str | None
    smtp_port: int | None
    smtp_from_email: str | None
    email_enabled: bool
    ai_agent_enabled: bool
    onboarding_complete: bool


class RecoveryConfiguration(BaseModel):
    max_recovery_attempts: int
    default_payment_link_expiry_hours: int
    default_wait_minutes: int
    auto_notify_customer: bool


class MerchantSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    business_name: str | None
    support_email: str | None
    support_phone: str | None
    integrations: IntegrationStatus
    webhook: WebhookInstruction
    recovery: RecoveryConfiguration


class MerchantSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: str | None = Field(default=None, max_length=150)
    support_email: str | None = Field(default=None, max_length=255)
    support_phone: str | None = Field(default=None, max_length=30)
    razorpay_account_id: str | None = None
    max_recovery_attempts: int | None = Field(default=None, ge=1, le=5)
    default_payment_link_expiry_hours: int | None = Field(default=None, ge=1, le=168)
    default_wait_minutes: int | None = Field(default=None, ge=15, le=1440)
    auto_notify_customer: bool | None = None

    @field_validator("business_name", "support_phone", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> str | None:
        if isinstance(value, str):
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None

    @field_validator("support_email", mode="before")
    @classmethod
    def validate_support_email(cls, value: object) -> str | None:
        if isinstance(value, str):
            trimmed = value.strip().lower()
            if not trimmed:
                return None
            if "@" not in trimmed or "." not in trimmed.split("@")[-1]:
                raise ValueError("Invalid support email address format.")
            return trimmed
        return None

    @field_validator("razorpay_account_id", mode="before")
    @classmethod
    def validate_optional_razorpay_account_id(cls, value: object) -> str | None:
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                return None
            if not re.fullmatch(r"acc_[A-Za-z0-9]{6,96}", trimmed):
                raise ValueError("Razorpay account ID must use the acc_ prefix and alphanumeric identifier.")
            return trimmed
        return None

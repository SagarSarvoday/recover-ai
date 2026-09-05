from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


PLACEHOLDER_EMAILS = {"void@razorpay.com", "noreply@recoverai.local"}


def validate_customer_email(value: str) -> str:
    cleaned = value.strip().lower()
    if not cleaned or cleaned in PLACEHOLDER_EMAILS:
        raise ValueError("Invalid or placeholder email address.")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cleaned):
        raise ValueError("Invalid email format.")
    return cleaned


class CustomerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        return validate_customer_email(v)

    @field_validator("name", "phone")
    @classmethod
    def clean_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None


class CustomerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_customer_email(v)

    @field_validator("name", "phone")
    @classmethod
    def clean_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        return s or None


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    merchant_id: UUID
    name: str | None
    email: str | None
    phone: str | None
    razorpay_customer_id: str | None
    created_at: datetime
    updated_at: datetime


class CustomerPaymentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    amount: Decimal
    currency: str
    status: str
    failure_reason: str | None
    paid_at: datetime | None
    created_at: datetime


class CustomerRecoveryCaseItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    payment_id: UUID
    status: str
    amount_at_risk: Decimal
    amount_recovered: Decimal
    attempt_count: int
    ai_decision: str | None
    razorpay_payment_link_id: str | None
    payment_link_status: str
    created_at: datetime


class CustomerDetailResponse(BaseModel):
    customer: CustomerResponse
    total_payments_count: int
    successful_payments_count: int = 0
    failed_payments_count: int = 0
    total_spent: Decimal
    total_recovered: Decimal
    recovery_rate: float = 0.0
    active_cases_count: int = 0
    payments: list[CustomerPaymentItem]
    recovery_cases: list[CustomerRecoveryCaseItem]
    notifications: list[dict] = []

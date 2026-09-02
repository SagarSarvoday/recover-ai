import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MerchantProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    email: str
    razorpay_account_id: str | None
    created_at: datetime
    updated_at: datetime


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

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_transaction_id: str = Field(min_length=1, max_length=100)
    customer_id: UUID
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="INR", min_length=3, max_length=3)

    @field_validator("merchant_transaction_id")
    @classmethod
    def normalize_merchant_transaction_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("merchant_transaction_id is required.")
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("currency must be a three-letter currency code.")
        return normalized


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    merchant_transaction_id: str
    razorpay_order_id: str | None
    amount: Decimal
    currency: str
    status: str
    customer_id: UUID
    created_at: datetime
    updated_at: datetime

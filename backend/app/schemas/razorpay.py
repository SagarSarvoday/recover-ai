from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RazorpayPaymentLinkRequest(BaseModel):
    """Minimal, typed request for a Razorpay Test Mode payment link."""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0, description="Amount in the smallest currency unit, e.g. paise.")
    currency: str = Field(default="INR", min_length=3, max_length=3)
    reference_id: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=500)
    customer_name: str | None = Field(default=None, max_length=100)
    customer_email: str | None = Field(default=None, max_length=254)
    customer_contact: str | None = Field(default=None, max_length=20)


class RazorpayPaymentLinkResult(BaseModel):
    id: str
    short_url: str | None = None
    status: str | None = None
    amount: int
    currency: str
    raw_response: dict[str, Any]


class RazorpayOrderRequest(BaseModel):
    """Server-side request for a Razorpay order; amount is in paise."""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    receipt: str = Field(min_length=1, max_length=40)


class RazorpayOrderResult(BaseModel):
    id: str
    amount: int
    currency: str
    status: str | None = None
    raw_response: dict[str, Any]


class RazorpayWebhookEnvelope(BaseModel):
    """Validated top-level structure for a Razorpay webhook payload."""

    model_config = ConfigDict(extra="allow")

    event: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any]
    account_id: str | None = Field(default=None, min_length=1, max_length=100)


class RazorpayWebhookResponse(BaseModel):
    event_id: str
    event_type: str
    status: Literal["processed", "ignored", "duplicate"]

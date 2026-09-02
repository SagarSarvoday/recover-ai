import hashlib
import hmac
from typing import Any, Protocol

from app.core.config import Settings
from app.schemas.razorpay import (
    RazorpayOrderRequest,
    RazorpayOrderResult,
    RazorpayPaymentLinkRequest,
    RazorpayPaymentLinkResult,
)


class RazorpayConfigurationError(RuntimeError):
    """Raised before an API call when Test Mode credentials are unavailable."""


class RazorpayClientProtocol(Protocol):
    class order:  # type: ignore[valid-type]
        @staticmethod
        def create(payload: dict[str, Any]) -> dict[str, Any]: ...

    class payment_link:  # type: ignore[valid-type]
        @staticmethod
        def create(payload: dict[str, Any]) -> dict[str, Any]: ...


class RazorpayService:
    """Razorpay Test Mode client used by webhook verification and recovery payment links."""

    def __init__(self, settings: Settings, client: RazorpayClientProtocol | None = None) -> None:
        self._settings = settings
        self._client = client

    def _require_api_credentials(self) -> tuple[str, str]:
        if not self._settings.razorpay_key_id or self._settings.razorpay_key_secret is None:
            raise RazorpayConfigurationError("Razorpay Test Mode API credentials are not configured.")
        return self._settings.razorpay_key_id, self._settings.razorpay_key_secret.get_secret_value()

    def _get_client(self) -> RazorpayClientProtocol:
        if self._client is not None:
            return self._client

        key_id, key_secret = self._require_api_credentials()
        try:
            import razorpay

            self._client = razorpay.Client(auth=(key_id, key_secret))
        except Exception as error:
            raise RazorpayConfigurationError("Unable to initialize the Razorpay Test Mode client.") from error
        return self._client

    def create_payment_link(self, request: RazorpayPaymentLinkRequest) -> RazorpayPaymentLinkResult:
        """Create a real payment link only when a caller explicitly adopts this service."""
        payload: dict[str, Any] = {
            "amount": request.amount,
            "currency": request.currency.upper(),
            "accept_partial": False,
        }
        if request.reference_id:
            payload["reference_id"] = request.reference_id
        if request.description:
            payload["description"] = request.description
        customer = {
            key: value
            for key, value in {
                "name": request.customer_name,
                "email": request.customer_email,
                "contact": request.customer_contact,
            }.items()
            if value is not None
        }
        if customer:
            payload["customer"] = customer
        notify = {
            key: True
            for key, enabled in {
                "email": request.customer_email is not None,
                "sms": request.customer_contact is not None,
            }.items()
            if enabled
        }
        if notify:
            payload["notify"] = notify

        response = self._get_client().payment_link.create(payload)
        return RazorpayPaymentLinkResult(
            id=response["id"],
            short_url=response.get("short_url"),
            status=response.get("status"),
            amount=response.get("amount", request.amount),
            currency=response.get("currency", payload["currency"]),
            raw_response=response,
        )

    def create_order(self, request: RazorpayOrderRequest) -> RazorpayOrderResult:
        """Create a merchant transaction's Razorpay order from the backend only."""
        payload = {
            "amount": request.amount,
            "currency": request.currency.upper(),
            "receipt": request.receipt,
        }
        response = self._get_client().order.create(payload)
        return RazorpayOrderResult(
            id=response["id"],
            amount=response.get("amount", request.amount),
            currency=response.get("currency", payload["currency"]),
            status=response.get("status"),
            raw_response=response,
        )

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verify a Razorpay webhook with the unparsed request body."""
        if self._settings.razorpay_webhook_secret is None:
            raise RazorpayConfigurationError("Razorpay webhook secret is not configured.")

        expected_signature = hmac.new(
            self._settings.razorpay_webhook_secret.get_secret_value().encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected_signature, signature)

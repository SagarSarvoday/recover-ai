import hashlib
import hmac
import unittest

from pydantic import SecretStr

from app.core.config import Settings
from app.schemas.razorpay import RazorpayOrderRequest, RazorpayPaymentLinkRequest
from app.services.razorpay_service import RazorpayConfigurationError, RazorpayService


class FakePaymentLinkClient:
    class order:
        received_payload: dict | None = None

        @classmethod
        def create(cls, payload: dict) -> dict:
            cls.received_payload = payload
            return {"id": "order_test_123", "amount": payload["amount"], "currency": payload["currency"], "status": "created"}

    class payment_link:
        received_payload: dict | None = None

        @classmethod
        def create(cls, payload: dict) -> dict:
            cls.received_payload = payload
            return {
                "id": "plink_test_123",
                "short_url": "https://rzp.io/i/test_123",
                "status": "created",
                "amount": payload["amount"],
                "currency": payload["currency"],
            }


def build_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://example",
        "razorpay_key_id": "rzp_test_example",
        "razorpay_key_secret": SecretStr("test_secret"),
        "razorpay_webhook_secret": SecretStr("webhook_secret"),
    }
    values.update(overrides)
    return Settings(**values)


class RazorpayServiceTests(unittest.TestCase):
    def test_creates_typed_payment_link_request_with_injected_client(self) -> None:
        service = RazorpayService(build_settings(), FakePaymentLinkClient())

        result = service.create_payment_link(
            RazorpayPaymentLinkRequest(
                amount=50000,
                reference_id="recovery-case-1",
                customer_email="customer@example.com",
            )
        )

        self.assertEqual(result.id, "plink_test_123")
        self.assertEqual(result.amount, 50000)
        self.assertEqual(FakePaymentLinkClient.payment_link.received_payload["currency"], "INR")
        self.assertEqual(
            FakePaymentLinkClient.payment_link.received_payload["notify"],
            {"email": True},
        )

    def test_enables_email_and_sms_notify_when_both_contacts_are_present(self) -> None:
        FakePaymentLinkClient.payment_link.received_payload = None
        service = RazorpayService(build_settings(), FakePaymentLinkClient())

        service.create_payment_link(
            RazorpayPaymentLinkRequest(
                amount=100,
                customer_email="customer@example.com",
                customer_contact="+919876543210",
            )
        )

        self.assertEqual(
            FakePaymentLinkClient.payment_link.received_payload["notify"],
            {"email": True, "sms": True},
        )

    def test_creates_a_typed_order_with_injected_client(self) -> None:
        service = RazorpayService(build_settings(), FakePaymentLinkClient())

        result = service.create_order(RazorpayOrderRequest(amount=199900, currency="INR", receipt="ORDER_123"))

        self.assertEqual(result.id, "order_test_123")
        self.assertEqual(result.amount, 199900)
        self.assertEqual(FakePaymentLinkClient.order.received_payload, {"amount": 199900, "currency": "INR", "receipt": "ORDER_123"})

    def test_verifies_valid_raw_webhook_body_signature(self) -> None:
        raw_body = b'{"event":"payment_link.paid"}'
        signature = hmac.new(b"webhook_secret", raw_body, hashlib.sha256).hexdigest()

        self.assertTrue(RazorpayService(build_settings()).verify_webhook_signature(raw_body, signature))

    def test_rejects_invalid_webhook_signature(self) -> None:
        self.assertFalse(
            RazorpayService(build_settings()).verify_webhook_signature(b"{}", "not-a-valid-signature")
        )

    def test_requires_webhook_secret(self) -> None:
        settings = build_settings(razorpay_webhook_secret=None)

        with self.assertRaises(RazorpayConfigurationError):
            RazorpayService(settings).verify_webhook_signature(b"{}", "signature")

    def test_requires_api_credentials_before_creating_client(self) -> None:
        settings = build_settings(razorpay_key_secret=None)

        with self.assertRaises(RazorpayConfigurationError):
            RazorpayService(settings).create_payment_link(RazorpayPaymentLinkRequest(amount=100))


if __name__ == "__main__":
    unittest.main()

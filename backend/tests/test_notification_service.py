import unittest
from datetime import datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr

from app.core.config import Settings
from app.services.notification_service import (
    NotificationResult,
    NotificationService,
    build_recovery_email_content,
    is_usable_email,
)


class FakeAuditSession:
    def __init__(self) -> None:
        self.logs: list[object] = []
        self.commits = 0

    def add(self, item: object) -> None:
        self.logs.append(item)

    def commit(self) -> None:
        self.commits += 1


class NotificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_id = uuid4()
        self.customer_id = uuid4()
        self.case_id = uuid4()
        self.payment_id = uuid4()

        self.customer = SimpleNamespace(
            id=self.customer_id,
            merchant_id=self.merchant_id,
            name="Rahul Sharma",
            email="rahul@example.com",
            phone="+919876543210",
        )
        self.payment = SimpleNamespace(
            id=self.payment_id,
            merchant_id=self.merchant_id,
            customer_id=self.customer_id,
            amount=Decimal("1499.00"),
            currency="INR",
            status="failed",
        )
        self.case = SimpleNamespace(
            id=self.case_id,
            merchant_id=self.merchant_id,
            customer_id=self.customer_id,
            payment_id=self.payment_id,
            status="in_progress",
        )
        self.db = FakeAuditSession()
        self.sent_messages: list[EmailMessage] = []

    def fake_smtp_sender(self, message: EmailMessage, settings: Settings) -> None:
        self.sent_messages.append(message)

    def test_is_usable_email(self) -> None:
        self.assertTrue(is_usable_email("customer@example.com"))
        self.assertTrue(is_usable_email("user.name+tag@sub.domain.org"))
        self.assertFalse(is_usable_email(None))
        self.assertFalse(is_usable_email(""))
        self.assertFalse(is_usable_email("invalid-email"))
        self.assertFalse(is_usable_email("void@razorpay.com"))
        self.assertFalse(is_usable_email("noreply@recoverai.local"))
        self.assertFalse(is_usable_email("@missinguser.com"))
        self.assertFalse(is_usable_email("missingdomain@"))

    def test_build_recovery_email_content(self) -> None:
        expires = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        subject, plain, html = build_recovery_email_content(
            customer_name="Rahul",
            amount=Decimal("1499.00"),
            payment_link="https://rzp.io/i/testlink123",
            expires_at=expires,
            currency="INR",
        )
        self.assertIn("Payment required", subject)
        self.assertIn("₹1499.00", plain)
        self.assertIn("https://rzp.io/i/testlink123", plain)
        self.assertIn("September 10, 2026", plain)
        self.assertIn("https://rzp.io/i/testlink123", html)
        self.assertIn("Complete Payment", html)

    def test_customer_not_found(self) -> None:
        service = NotificationService()
        result = service.send_recovery_payment_email(
            customer=None,
            payment=self.payment,  # type: ignore[arg-type]
            payment_link="https://rzp.io/i/test",
            db=self.db,  # type: ignore[arg-type]
            recovery_case=self.case,  # type: ignore[arg-type]
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "customer_not_found")
        self.assertEqual(len(self.db.logs), 1)
        self.assertEqual(self.db.logs[0].action, "notification_failed")

    def test_customer_merchant_mismatch(self) -> None:
        service = NotificationService()
        mismatched_customer = SimpleNamespace(
            id=self.customer_id,
            merchant_id=uuid4(),  # different merchant
            name="Impostor",
            email="test@example.com",
        )
        result = service.send_recovery_payment_email(
            customer=mismatched_customer,  # type: ignore[arg-type]
            payment=self.payment,  # type: ignore[arg-type]
            payment_link="https://rzp.io/i/test",
            db=self.db,  # type: ignore[arg-type]
            recovery_case=self.case,  # type: ignore[arg-type]
        )
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "customer_merchant_mismatch")

    def test_customer_email_missing_audits_and_fails_gracefully(self) -> None:
        service = NotificationService()
        self.customer.email = ""
        result = service.send_recovery_payment_email(
            customer=self.customer,  # type: ignore[arg-type]
            payment=self.payment,  # type: ignore[arg-type]
            payment_link="https://rzp.io/i/test",
            db=self.db,  # type: ignore[arg-type]
            recovery_case=self.case,  # type: ignore[arg-type]
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.reason, "customer_email_missing")
        self.assertEqual(len(self.db.logs), 1)
        self.assertEqual(self.db.logs[0].action, "notification_failed")
        self.assertEqual(self.db.logs[0].details.get("reason"), "customer_email_missing")
        # Ensure recovery case was not marked recovered
        self.assertEqual(self.case.status, "in_progress")

    def test_email_disabled_skips_smtp_with_controlled_status(self) -> None:
        settings = Settings(
            email_enabled=False,
            database_url="sqlite:///:memory:",
            secret_key=SecretStr("test-secret-key-that-is-at-least-32-chars-long"),
        )
        service = NotificationService(settings=settings, smtp_sender=self.fake_smtp_sender)
        result = service.send_recovery_payment_email(
            customer=self.customer,  # type: ignore[arg-type]
            payment=self.payment,  # type: ignore[arg-type]
            payment_link="https://rzp.io/i/test",
            db=self.db,  # type: ignore[arg-type]
            recovery_case=self.case,  # type: ignore[arg-type]
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "email_disabled")
        self.assertEqual(result.reason, "email_disabled")
        self.assertEqual(len(self.sent_messages), 0)
        self.assertEqual(len(self.db.logs), 1)
        self.assertEqual(self.db.logs[0].action, "notification_failed")
        self.assertEqual(self.db.logs[0].details.get("reason"), "email_disabled")
        # Ensure recovery case status is untouched
        self.assertEqual(self.case.status, "in_progress")

    def test_successful_email_delivery(self) -> None:
        settings = Settings(
            email_enabled=True,
            email_from="support@merchant.com",
            smtp_host="smtp.example.com",
            smtp_port=587,
            database_url="sqlite:///:memory:",
            secret_key=SecretStr("test-secret-key-that-is-at-least-32-chars-long"),
        )
        service = NotificationService(settings=settings, smtp_sender=self.fake_smtp_sender)
        expires = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        result = service.send_recovery_payment_email(
            customer=self.customer,  # type: ignore[arg-type]
            payment=self.payment,  # type: ignore[arg-type]
            payment_link="https://rzp.io/i/testlink999",
            expires_at=expires,
            db=self.db,  # type: ignore[arg-type]
            recovery_case=self.case,  # type: ignore[arg-type]
        )
        self.assertTrue(result.success)
        self.assertEqual(result.status, "sent")
        self.assertEqual(result.recipient_email, "rahul@example.com")
        self.assertEqual(len(self.sent_messages), 1)

        msg = self.sent_messages[0]
        self.assertEqual(msg["To"], "rahul@example.com")
        self.assertEqual(msg["From"], "support@merchant.com")
        self.assertIn("Payment required", msg["Subject"])
        payload = str(msg)
        self.assertIn("https://rzp.io/i/testlink999", payload)

        # Verify audit log
        self.assertEqual(len(self.db.logs), 1)
        self.assertEqual(self.db.logs[0].action, "notification_sent")
        self.assertTrue(self.db.logs[0].details.get("success"))
        self.assertEqual(self.db.logs[0].details.get("recipient"), "rahul@example.com")
        # Case status remains in_progress until webhook
        self.assertEqual(self.case.status, "in_progress")

    def test_smtp_delivery_failure_is_handled_safely(self) -> None:
        def failing_sender(message: EmailMessage, settings: Settings) -> None:
            raise ConnectionRefusedError("SMTP server unreachable password=my_super_secret")

        settings = Settings(
            email_enabled=True,
            smtp_host="smtp.example.com",
            database_url="sqlite:///:memory:",
            secret_key=SecretStr("test-secret-key-that-is-at-least-32-chars-long"),
        )
        service = NotificationService(settings=settings, smtp_sender=failing_sender)
        result = service.send_recovery_payment_email(
            customer=self.customer,  # type: ignore[arg-type]
            payment=self.payment,  # type: ignore[arg-type]
            payment_link="https://rzp.io/i/testlink999",
            db=self.db,  # type: ignore[arg-type]
            recovery_case=self.case,  # type: ignore[arg-type]
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertIn("SMTP server unreachable", result.reason)
        # Verify secret was sanitized
        self.assertNotIn("my_super_secret", result.reason)
        self.assertIn("[redacted]", result.reason)

        self.assertEqual(len(self.db.logs), 1)
        self.assertEqual(self.db.logs[0].action, "notification_failed")
        self.assertEqual(self.case.status, "in_progress")


if __name__ == "__main__":
    unittest.main()

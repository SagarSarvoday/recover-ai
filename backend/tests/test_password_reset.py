import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.auth import forgot_password, reset_password
from app.core.config import Settings
from app.core.database import get_db
from app.core.security import (
    generate_password_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.main import app
from app.models.merchant import Merchant
from app.models.merchant_password_reset_token import MerchantPasswordResetToken
from app.schemas.auth import ForgotPasswordRequest, ResetPasswordRequest
from app.services.notification_service import (
    NotificationResult,
    NotificationService,
    build_password_reset_email_content,
)


class FakeDb:
    def __init__(
        self,
        merchants: list[Merchant] | None = None,
        tokens: list[MerchantPasswordResetToken] | None = None,
    ) -> None:
        self.merchants: list[Merchant] = merchants or []
        self.tokens: list[MerchantPasswordResetToken] = tokens or []
        self.audit_logs: list[object] = []
        self.commits: int = 0
        self.rollbacks: int = 0

    def add(self, item: object) -> None:
        if isinstance(item, Merchant):
            if not item.id:
                item.id = uuid4()
            self.merchants.append(item)
        elif isinstance(item, MerchantPasswordResetToken):
            if not item.id:
                item.id = uuid4()
            self.tokens.append(item)
        else:
            self.audit_logs.append(item)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def get(self, model: object, obj_id: object) -> object | None:
        if model is Merchant:
            return next((m for m in self.merchants if m.id == obj_id), None)
        if model is MerchantPasswordResetToken:
            return next((t for t in self.tokens if t.id == obj_id), None)
        return None

    def scalar(self, statement: object) -> object | None:
        params = set(statement.compile().params.values())
        for m in self.merchants:
            if m.email in params:
                return m
        for t in self.tokens:
            if t.token_hash in params:
                return t
        return None

    def scalars(self, statement: object) -> object:
        params = set(statement.compile().params.values())
        merchant_ids = {m.id for m in self.merchants if m.id in params}
        matched = [
            t
            for t in self.tokens
            if t.merchant_id in merchant_ids and t.used_at is None
        ]

        class ScalarResult:
            def all(self) -> list[MerchantPasswordResetToken]:
                return matched

        return ScalarResult()


class PasswordResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_id = uuid4()
        self.old_password = "old-secure-password"
        self.merchant = Merchant(
            id=self.merchant_id,
            name="Apex Merchant",
            email="merchant@apex.com",
            password_hash=hash_password(self.old_password),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db = FakeDb(merchants=[self.merchant])
        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    # -------------------------------------------------------------------------
    # 1. Security & Token Generation Tests
    # -------------------------------------------------------------------------
    def test_token_generation_entropy_and_hashing(self) -> None:
        raw_token, token_hash = generate_password_reset_token()
        self.assertIsInstance(raw_token, str)
        self.assertGreaterEqual(len(raw_token), 32)
        self.assertEqual(len(token_hash), 64)  # SHA-256 hex digest length
        self.assertEqual(hash_reset_token(raw_token), token_hash)

        # Ensure tokens are cryptographically distinct
        raw_token_2, token_hash_2 = generate_password_reset_token()
        self.assertNotEqual(raw_token, raw_token_2)
        self.assertNotEqual(token_hash, token_hash_2)

    def test_email_template_content(self) -> None:
        subject, plain, html = build_password_reset_email_content(
            merchant_name="Apex Merchant",
            reset_url="https://app.recoverai.com/reset-password?token=secret123",
            expires_minutes=20,
        )
        self.assertIn("Reset your RecoverAI password", subject)
        self.assertIn("Apex Merchant", plain)
        self.assertIn("https://app.recoverai.com/reset-password?token=secret123", plain)
        self.assertIn("20 minutes", plain)
        self.assertIn("https://app.recoverai.com/reset-password?token=secret123", html)
        self.assertIn("20 minutes", html)

    # -------------------------------------------------------------------------
    # 2. Forgot Password Endpoint Tests
    # -------------------------------------------------------------------------
    @patch.object(NotificationService, "send_password_reset_email")
    def test_forgot_password_existing_merchant_creates_token_and_sends_email(
        self, mock_send_email: MagicMock
    ) -> None:
        mock_send_email.return_value = NotificationResult(success=True, status="delivered")

        response = self.client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "merchant@apex.com"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            data["message"],
            "If an account exists for this email, you will receive a password reset link shortly.",
        )

        # Verify token created in DB
        self.assertEqual(len(self.db.tokens), 1)
        token_record = self.db.tokens[0]
        self.assertEqual(token_record.merchant_id, self.merchant_id)
        self.assertEqual(len(token_record.token_hash), 64)
        self.assertIsNone(token_record.used_at)
        self.assertGreater(token_record.expires_at, datetime.now(timezone.utc))

        # Verify notification service was called with raw token (different from hash)
        mock_send_email.assert_called_once()
        call_args = mock_send_email.call_args
        raw_token_sent = call_args[0][1]
        self.assertNotEqual(raw_token_sent, token_record.token_hash)
        self.assertEqual(hash_reset_token(raw_token_sent), token_record.token_hash)

    @patch.object(NotificationService, "send_password_reset_email")
    def test_forgot_password_nonexistent_merchant_anti_enumeration(
        self, mock_send_email: MagicMock
    ) -> None:
        response = self.client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "unknown@apex.com"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            data["message"],
            "If an account exists for this email, you will receive a password reset link shortly.",
        )

        # No token in DB and no email sent
        self.assertEqual(len(self.db.tokens), 0)
        mock_send_email.assert_not_called()

    @patch.object(NotificationService, "send_password_reset_email")
    def test_forgot_password_email_normalization(
        self, mock_send_email: MagicMock
    ) -> None:
        mock_send_email.return_value = NotificationResult(success=True, status="delivered")

        response = self.client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "   MERCHANT@apex.COM   "},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.db.tokens), 1)
        mock_send_email.assert_called_once()

    # -------------------------------------------------------------------------
    # 3. Reset Password Endpoint Tests
    # -------------------------------------------------------------------------
    def test_reset_password_success(self) -> None:
        raw_token, token_hash = generate_password_reset_token()
        now = datetime.now(timezone.utc)
        reset_token = MerchantPasswordResetToken(
            id=uuid4(),
            merchant_id=self.merchant_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=20),
            used_at=None,
            created_at=now,
        )
        self.db.tokens.append(reset_token)

        new_password = "brand-new-secure-password"
        response = self.client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": new_password},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(
            data["message"],
            "Password has been successfully reset. You can now log in with your new password.",
        )

        # Verify token is marked as used
        self.assertIsNotNone(reset_token.used_at)

        # Verify password hash updated
        self.assertTrue(verify_password(new_password, self.merchant.password_hash))
        self.assertFalse(verify_password(self.old_password, self.merchant.password_hash))

    def test_reset_password_single_use_replay_rejected(self) -> None:
        raw_token, token_hash = generate_password_reset_token()
        now = datetime.now(timezone.utc)
        reset_token = MerchantPasswordResetToken(
            id=uuid4(),
            merchant_id=self.merchant_id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=20),
            used_at=now,  # Already used
            created_at=now,
        )
        self.db.tokens.append(reset_token)

        response = self.client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "brand-new-secure-password"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid or expired password reset token.")

    def test_reset_password_expired_token_rejected(self) -> None:
        raw_token, token_hash = generate_password_reset_token()
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        reset_token = MerchantPasswordResetToken(
            id=uuid4(),
            merchant_id=self.merchant_id,
            token_hash=token_hash,
            expires_at=past,  # Expired
            used_at=None,
            created_at=past - timedelta(minutes=20),
        )
        self.db.tokens.append(reset_token)

        response = self.client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token, "new_password": "brand-new-secure-password"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid or expired password reset token.")

    def test_reset_password_invalid_token_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/auth/reset-password",
            json={"token": "completely-bogus-token", "new_password": "brand-new-secure-password"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Invalid or expired password reset token.")

    def test_reset_password_short_password_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/auth/reset-password",
            json={"token": "some-token", "new_password": "short"},
        )
        self.assertEqual(response.status_code, 422)

    def test_reset_password_invalidates_all_other_active_tokens(self) -> None:
        raw_token_1, token_hash_1 = generate_password_reset_token()
        raw_token_2, token_hash_2 = generate_password_reset_token()
        now = datetime.now(timezone.utc)

        tok1 = MerchantPasswordResetToken(
            id=uuid4(),
            merchant_id=self.merchant_id,
            token_hash=token_hash_1,
            expires_at=now + timedelta(minutes=20),
            used_at=None,
            created_at=now,
        )
        tok2 = MerchantPasswordResetToken(
            id=uuid4(),
            merchant_id=self.merchant_id,
            token_hash=token_hash_2,
            expires_at=now + timedelta(minutes=20),
            used_at=None,
            created_at=now,
        )
        self.db.tokens.extend([tok1, tok2])

        # Reset using tok1
        response = self.client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token_1, "new_password": "brand-new-secure-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(tok1.used_at)
        self.assertIsNotNone(tok2.used_at)

        # Attempting to use tok2 should now be rejected as used
        response2 = self.client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw_token_2, "new_password": "another-new-password"},
        )
        self.assertEqual(response2.status_code, 400)

    # -------------------------------------------------------------------------
    # 4. Notification Service Password Reset Tests
    # -------------------------------------------------------------------------
    def test_send_password_reset_email_success(self) -> None:
        mock_sender = MagicMock()
        settings = Settings(
            database_url="sqlite:///:memory:",
            secret_key=SecretStr("test-secret-key-that-is-at-least-32-chars-long"),
            email_enabled=True,
            email_from="noreply@recoverai.com",
            smtp_host="localhost",
            smtp_port=1025,
            frontend_base_url="https://app.recoverai.com",
        )
        service = NotificationService(settings, smtp_sender=mock_sender)

        audit_db = FakeDb()
        result = service.send_password_reset_email(
            self.merchant, "test_raw_token_xyz", db=audit_db, actor="test_actor"
        )
        self.assertTrue(result.success)
        self.assertEqual(result.status, "delivered")
        mock_sender.assert_called_once()
        msg = mock_sender.call_args[0][0]
        self.assertIn("test_raw_token_xyz", msg.as_string())
        plain_body = msg.get_body(preferencelist=("plain",)).get_content()
        self.assertIn("https://app.recoverai.com/reset-password?token=test_raw_token_xyz", plain_body)

        # Audit log verified - raw token NOT in audit log
        self.assertEqual(len(audit_db.audit_logs), 1)
        log = audit_db.audit_logs[0]
        self.assertEqual(log.action, "password_reset_email_sent")
        self.assertNotIn("test_raw_token_xyz", str(log.details))

    def test_send_password_reset_email_disabled(self) -> None:
        mock_sender = MagicMock()
        settings = Settings(
            database_url="sqlite:///:memory:",
            secret_key=SecretStr("test-secret-key-that-is-at-least-32-chars-long"),
            email_enabled=False,
            frontend_base_url="https://app.recoverai.com",
        )
        service = NotificationService(settings, smtp_sender=mock_sender)

        audit_db = FakeDb()
        result = service.send_password_reset_email(
            self.merchant, "test_raw_token_xyz", db=audit_db
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "email_disabled")
        mock_sender.assert_not_called()
        self.assertEqual(len(audit_db.audit_logs), 1)
        self.assertEqual(audit_db.audit_logs[0].action, "password_reset_email_disabled")

    def test_send_password_reset_email_smtp_error(self) -> None:
        def raise_smtp_error(msg: object, settings: object) -> None:
            raise ConnectionRefusedError("SMTP server down password=sensitive_secret")

        settings = Settings(
            database_url="sqlite:///:memory:",
            secret_key=SecretStr("test-secret-key-that-is-at-least-32-chars-long"),
            email_enabled=True,
            email_from="noreply@recoverai.com",
            smtp_host="localhost",
            smtp_port=1025,
            frontend_base_url="https://app.recoverai.com",
        )
        service = NotificationService(settings, smtp_sender=raise_smtp_error)

        audit_db = FakeDb()
        result = service.send_password_reset_email(
            self.merchant, "test_raw_token_xyz", db=audit_db
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(audit_db.audit_logs), 1)
        log = audit_db.audit_logs[0]
        self.assertEqual(log.action, "password_reset_email_failed")
        # Sensitive values sanitized
        self.assertNotIn("sensitive_secret", log.details["error"])


if __name__ == "__main__":
    unittest.main()

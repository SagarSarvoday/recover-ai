import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr

from app.api.v1.auth import login_merchant, register_merchant
from app.core.security import get_current_merchant
from app.models.merchant import Merchant
from app.services.legacy_merchant_bootstrap import (
    LEGACY_MERCHANT_EMAIL,
    LEGACY_MERCHANT_ID,
    LEGACY_RAZORPAY_ACCOUNT_ID,
    LegacyMerchantBootstrapError,
    initialize_legacy_merchant_password,
)
from app.schemas.auth import (
    AuthenticatedMerchantResponse,
    MerchantLoginRequest,
    MerchantRegistrationRequest,
)


class FakeSession:
    def __init__(self) -> None:
        self.merchants: list[Merchant] = []
        self.commits = 0

    def scalar(self, statement: object) -> Merchant | None:
        values = set(statement.compile().params.values())
        return next((merchant for merchant in self.merchants if merchant.email in values), None)

    def get(self, model: object, merchant_id: object) -> Merchant | None:
        if model is Merchant:
            return next((merchant for merchant in self.merchants if merchant.id == merchant_id), None)
        return None

    def add(self, merchant: Merchant) -> None:
        merchant.id = uuid4()
        merchant.created_at = datetime.now(timezone.utc)
        merchant.updated_at = merchant.created_at
        self.merchants.append(merchant)

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, merchant: Merchant) -> None:
        pass

    def rollback(self) -> None:
        pass


class MerchantAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FakeSession()
        self.jwt_secret = SecretStr("test-only-jwt-secret-with-32-bytes")

    def register(self, email: str = "merchant@example.com") -> Merchant:
        return register_merchant(
            MerchantRegistrationRequest(name="Merchant", email=email, password="secure-password"),
            self.db,
        )

    def test_successful_registration_hashes_password_and_returns_safe_data(self) -> None:
        merchant = self.register()
        response = AuthenticatedMerchantResponse.model_validate(merchant)

        self.assertEqual(merchant.email, "merchant@example.com")
        self.assertNotEqual(merchant.password_hash, "secure-password")
        self.assertNotIn("password_hash", response.model_dump())
        self.assertIsNone(merchant.razorpay_account_id)

    def test_duplicate_registration_is_rejected(self) -> None:
        self.register()

        with self.assertRaises(HTTPException) as error:
            self.register()

        self.assertEqual(error.exception.status_code, 409)

    def test_successful_login_returns_a_jwt_and_dependency_loads_merchant(self) -> None:
        merchant = self.register()
        with patch.object(
            __import__("app.core.security", fromlist=["settings"]).settings,
            "jwt_secret_key",
            self.jwt_secret,
        ):
            token = login_merchant(
                MerchantLoginRequest(email=merchant.email, password="secure-password"), self.db
            )
            authenticated = get_current_merchant(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=token.access_token), self.db
            )

        self.assertEqual(token.token_type, "bearer")
        self.assertTrue(token.access_token)
        self.assertEqual(authenticated.id, merchant.id)

    def test_wrong_password_and_unknown_email_return_the_same_error(self) -> None:
        self.register()
        errors: list[HTTPException] = []
        for credentials in (
            MerchantLoginRequest(email="merchant@example.com", password="wrong-password"),
            MerchantLoginRequest(email="unknown@example.com", password="wrong-password"),
        ):
            with self.assertRaises(HTTPException) as error:
                login_merchant(credentials, self.db)
            errors.append(error.exception)

        self.assertEqual(errors[0].status_code, 401)
        self.assertEqual(errors[1].status_code, 401)
        self.assertEqual(errors[0].detail, errors[1].detail)

    def legacy_merchant(self) -> Merchant:
        merchant = Merchant(
            id=LEGACY_MERCHANT_ID,
            name="Legacy Merchant",
            email=LEGACY_MERCHANT_EMAIL,
            password_hash=None,
            razorpay_account_id=LEGACY_RAZORPAY_ACCOUNT_ID,
        )
        self.db.merchants.append(merchant)
        return merchant

    def test_development_bootstrap_sets_only_the_legacy_password_hash(self) -> None:
        merchant = self.legacy_merchant()

        initialized = initialize_legacy_merchant_password(
            self.db, "legacy-password", app_environment="development"
        )

        self.assertEqual(initialized.id, LEGACY_MERCHANT_ID)
        self.assertEqual(initialized.email, LEGACY_MERCHANT_EMAIL)
        self.assertEqual(initialized.razorpay_account_id, LEGACY_RAZORPAY_ACCOUNT_ID)
        self.assertNotEqual(initialized.password_hash, "legacy-password")
        with patch.object(
            __import__("app.core.security", fromlist=["settings"]).settings,
            "jwt_secret_key",
            self.jwt_secret,
        ):
            token = login_merchant(
                MerchantLoginRequest(email=LEGACY_MERCHANT_EMAIL, password="legacy-password"), self.db
            )
        self.assertTrue(token.access_token)
        self.assertIs(merchant, initialized)

    def test_bootstrap_is_one_time_and_development_only(self) -> None:
        merchant = self.legacy_merchant()
        with self.assertRaises(LegacyMerchantBootstrapError):
            initialize_legacy_merchant_password(self.db, "legacy-password", app_environment="production")
        self.assertIsNone(merchant.password_hash)

        initialize_legacy_merchant_password(self.db, "legacy-password", app_environment="development")
        with self.assertRaises(LegacyMerchantBootstrapError):
            initialize_legacy_merchant_password(self.db, "another-password", app_environment="development")

    def test_bootstrap_rejects_a_mismatched_legacy_mapping(self) -> None:
        merchant = self.legacy_merchant()
        merchant.razorpay_account_id = "acc_unexpected"

        with self.assertRaises(LegacyMerchantBootstrapError):
            initialize_legacy_merchant_password(self.db, "legacy-password", app_environment="development")
        self.assertIsNone(merchant.password_hash)


if __name__ == "__main__":
    unittest.main()

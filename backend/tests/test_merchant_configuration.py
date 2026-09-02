import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.merchant import Merchant


class FakeSession:
    def __init__(self, merchants: list[Merchant]) -> None:
        self.merchants = merchants
        self.commits = 0

    def get(self, model: object, merchant_id: object) -> Merchant | None:
        if model is Merchant:
            return next((merchant for merchant in self.merchants if merchant.id == merchant_id), None)
        return None

    def scalar(self, statement: object) -> Merchant | None:
        values = set(statement.compile().params.values())
        return next(
            (
                merchant
                for merchant in self.merchants
                if merchant.razorpay_account_id in values
            ),
            None,
        )

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, merchant: Merchant) -> None:
        merchant.updated_at = datetime.now(timezone.utc)

    def rollback(self) -> None:
        pass


def make_merchant(email: str, account_id: str | None = None) -> Merchant:
    now = datetime.now(timezone.utc)
    return Merchant(
        id=uuid4(),
        name=email.split("@", 1)[0],
        email=email,
        password_hash="not-returned",
        razorpay_account_id=account_id,
        created_at=now,
        updated_at=now,
    )


class MerchantConfigurationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_a = make_merchant("merchant-a@example.com")
        self.merchant_b = make_merchant("merchant-b@example.com")
        self.db = FakeSession([self.merchant_a, self.merchant_b])
        app.dependency_overrides[get_db] = lambda: self.db
        self.jwt_secret_patch = patch(
            "app.core.security.settings.jwt_secret_key",
            SecretStr("merchant-configuration-test-secret-32"),
        )
        self.jwt_secret_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.jwt_secret_patch.stop()
        app.dependency_overrides.clear()
        self.client.close()

    def authorization_header(self, merchant: Merchant) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(merchant.id)}"}

    def test_unauthenticated_profile_returns_401(self) -> None:
        response = self.client.get("/api/v1/merchant/me")

        self.assertEqual(response.status_code, 401)

    def test_authenticated_profile_returns_only_current_merchant(self) -> None:
        response = self.client.get(
            "/api/v1/merchant/me", headers=self.authorization_header(self.merchant_a)
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], str(self.merchant_a.id))
        self.assertEqual(body["email"], self.merchant_a.email)
        self.assertNotEqual(body["id"], str(self.merchant_b.id))
        self.assertNotIn("password_hash", body)

    def test_unauthenticated_account_update_returns_401(self) -> None:
        response = self.client.put(
            "/api/v1/merchant/razorpay-account",
            json={"razorpay_account_id": "acc_merchantone123"},
        )

        self.assertEqual(response.status_code, 401)

    def test_authenticated_merchant_can_set_trimmed_account_id(self) -> None:
        response = self.client.put(
            "/api/v1/merchant/razorpay-account",
            headers=self.authorization_header(self.merchant_a),
            json={"razorpay_account_id": "  acc_merchantone123  "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["razorpay_account_id"], "acc_merchantone123")
        self.assertEqual(self.merchant_a.razorpay_account_id, "acc_merchantone123")

    def test_empty_account_id_is_rejected(self) -> None:
        response = self.client.put(
            "/api/v1/merchant/razorpay-account",
            headers=self.authorization_header(self.merchant_a),
            json={"razorpay_account_id": "   "},
        )

        self.assertEqual(response.status_code, 422)

    def test_second_merchant_cannot_claim_assigned_account_id(self) -> None:
        self.merchant_a.razorpay_account_id = "acc_assigned123"
        response = self.client.put(
            "/api/v1/merchant/razorpay-account",
            headers=self.authorization_header(self.merchant_b),
            json={"razorpay_account_id": "acc_assigned123"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.merchant_b.razorpay_account_id)

    def test_merchant_a_cannot_modify_merchant_b(self) -> None:
        self.merchant_b.razorpay_account_id = "acc_merchantb123"
        response = self.client.put(
            "/api/v1/merchant/razorpay-account",
            headers=self.authorization_header(self.merchant_a),
            json={"razorpay_account_id": "acc_merchanta123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.merchant_a.razorpay_account_id, "acc_merchanta123")
        self.assertEqual(self.merchant_b.razorpay_account_id, "acc_merchantb123")

    def test_reusing_the_current_merchants_account_id_is_idempotent(self) -> None:
        self.merchant_a.razorpay_account_id = "acc_merchanta123"
        response = self.client.put(
            "/api/v1/merchant/razorpay-account",
            headers=self.authorization_header(self.merchant_a),
            json={"razorpay_account_id": "acc_merchanta123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.merchant_a.razorpay_account_id, "acc_merchanta123")
        self.assertEqual(self.db.commits, 0)


if __name__ == "__main__":
    unittest.main()

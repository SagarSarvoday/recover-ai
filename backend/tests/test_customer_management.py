import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_merchant
from app.main import app
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.transaction import Transaction
from app.services.razorpay_webhooks import (
    RazorpayWebhookEnvelope,
    extract_failed_payment_data,
    resolve_customer_identity,
)


class FakeDb:
    def __init__(self) -> None:
        self.merchants: dict[object, Merchant] = {}
        self.customers: dict[object, Customer] = {}
        self.payments: dict[object, Payment] = {}
        self.transactions: dict[object, Transaction] = {}
        self.recovery_cases: dict[object, RecoveryCase] = {}
        self.audit_logs: list[object] = []

    def get(self, model: object, obj_id: object) -> object | None:
        if model is Customer:
            return self.customers.get(obj_id)
        if model is Merchant:
            return self.merchants.get(obj_id)
        if model is Payment:
            return self.payments.get(obj_id)
        if model is RecoveryCase:
            return self.recovery_cases.get(obj_id)
        if model is Transaction:
            return self.transactions.get(obj_id)
        return None

    def scalar(self, statement: object) -> object | None:
        stmt_str = str(statement)
        if "customers" in stmt_str:
            # check email match or id match
            for c in self.customers.values():
                return c
        if "payments" in stmt_str:
            for p in self.payments.values():
                return p
        if "transactions" in stmt_str:
            for t in self.transactions.values():
                return t
        return None

    def scalars(self, statement: object) -> object:
        class MockResult:
            def __init__(self, items: list[object]) -> None:
                self.items = items

            def all(self) -> list[object]:
                return self.items

        stmt_str = str(statement)
        if "customers" in stmt_str:
            return MockResult(list(self.customers.values()))
        if "payments" in stmt_str:
            return MockResult(list(self.payments.values()))
        if "recovery_cases" in stmt_str:
            return MockResult(list(self.recovery_cases.values()))
        return MockResult([])

    def add(self, item: object) -> None:
        if isinstance(item, Customer):
            if not getattr(item, "id", None):
                item.id = uuid4()
            self.customers[item.id] = item
        elif isinstance(item, Payment):
            self.payments[item.id] = item
        elif isinstance(item, RecoveryCase):
            self.recovery_cases[item.id] = item
        elif isinstance(item, Transaction):
            self.transactions[item.id] = item
        else:
            self.audit_logs.append(item)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def refresh(self, item: object) -> None:
        pass

    def delete(self, item: object) -> None:
        if isinstance(item, Customer) and item.id in self.customers:
            del self.customers[item.id]


class CustomerManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_1 = Merchant(
            id=uuid4(),
            name="Alpha Merchant",
            email="alpha@store.local",
            razorpay_account_id="acc_alpha123",
        )
        self.merchant_2 = Merchant(
            id=uuid4(),
            name="Beta Merchant",
            email="beta@store.local",
            razorpay_account_id="acc_beta456",
        )
        self.db = FakeDb()
        self.db.merchants[self.merchant_1.id] = self.merchant_1
        self.db.merchants[self.merchant_2.id] = self.merchant_2

        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_merchant] = lambda: self.merchant_1

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_create_customer_success(self) -> None:
        response = self.client.post(
            "/api/v1/customers",
            json={
                "name": "Rohan Gupta",
                "email": "rohan@example.com",
                "phone": "+919876543210",
            },
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["name"], "Rohan Gupta")
        self.assertEqual(data["email"], "rohan@example.com")
        self.assertEqual(data["merchant_id"], str(self.merchant_1.id))
        self.assertIn("created_at", data)
        self.assertIn("updated_at", data)

    def test_create_customer_duplicate_email_within_merchant_fails(self) -> None:
        # Given an existing customer for merchant 1
        existing_cust = Customer(
            id=uuid4(),
            merchant_id=self.merchant_1.id,
            name="Existing Customer",
            email="duplicate@example.com",
            phone="+919876543211",
        )
        self.db.customers[existing_cust.id] = existing_cust

        # Mock scalar finding the existing email for this merchant
        with patch.object(self.db, "scalar", return_value=existing_cust):
            response = self.client.post(
                "/api/v1/customers",
                json={
                    "name": "New Customer Same Email",
                    "email": "duplicate@example.com",
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertIn("already exists", response.json()["detail"])

    def test_merchant_isolation_cross_access_prevented(self) -> None:
        # Customer belonging to merchant 2
        cust_m2 = Customer(
            id=uuid4(),
            merchant_id=self.merchant_2.id,
            name="Beta Customer",
            email="customer@beta.local",
            phone="+919876543212",
        )
        self.db.customers[cust_m2.id] = cust_m2

        # Authenticated as merchant 1, try to GET customer of merchant 2
        with patch.object(self.db, "scalar", return_value=None):
            response = self.client.get(f"/api/v1/customers/{cust_m2.id}")
        self.assertEqual(response.status_code, 404)

        # Authenticated as merchant 1, try to UPDATE customer of merchant 2
        with patch.object(self.db, "scalar", return_value=None):
            response = self.client.put(
                f"/api/v1/customers/{cust_m2.id}",
                json={"name": "Hacked Name"},
            )
        self.assertEqual(response.status_code, 404)

        # Authenticated as merchant 1, try to DELETE customer of merchant 2
        with patch.object(self.db, "scalar", return_value=None):
            response = self.client.delete(f"/api/v1/customers/{cust_m2.id}")
        self.assertEqual(response.status_code, 404)

    def test_delete_customer_with_payments_blocked(self) -> None:
        cust = Customer(
            id=uuid4(),
            merchant_id=self.merchant_1.id,
            name="Active Customer",
            email="active@example.com",
        )
        self.db.customers[cust.id] = cust

        def mock_scalar(statement):
            stmt_str = str(statement)
            if "customers" in stmt_str:
                return cust
            if "payments" in stmt_str:
                return uuid4()  # has payments
            return None

        with patch.object(self.db, "scalar", side_effect=mock_scalar):
            response = self.client.delete(f"/api/v1/customers/{cust.id}")
        self.assertEqual(response.status_code, 409)
        self.assertIn("Cannot delete customer with existing", response.json()["detail"])

    def test_placeholder_email_void_at_razorpay_is_not_stored_as_customer_email(self) -> None:
        envelope = RazorpayWebhookEnvelope(
            account_id="acc_alpha123",
            event="payment.failed",
            payload={
                "payment": {
                    "entity": {
                        "id": "pay_void_test",
                        "amount": 250000,
                        "currency": "INR",
                        "email": "void@razorpay.com",
                        "contact": "+919999999999",
                        "notes": {"name": "Void User"},
                    }
                }
            },
        )
        data = extract_failed_payment_data(envelope)
        self.assertIsNone(data.customer_email)
        self.assertEqual(data.customer_contact, "+919999999999")

    def test_find_or_create_customer_reuses_existing_by_email(self) -> None:
        existing = Customer(
            id=uuid4(),
            merchant_id=self.merchant_1.id,
            name="Existing User",
            email="existing@example.com",
            phone="+919876543200",
        )
        envelope = RazorpayWebhookEnvelope(
            account_id="acc_alpha123",
            event="payment.failed",
            payload={
                "payment": {
                    "entity": {
                        "id": "pay_reuse_test",
                        "amount": 100000,
                        "currency": "INR",
                        "email": "existing@example.com",
                    }
                }
            },
        )
        data = extract_failed_payment_data(envelope)
        with patch.object(self.db, "scalar", return_value=existing):
            resolved = resolve_customer_identity(self.db, self.merchant_1.id, data)
        self.assertEqual(resolved.id, existing.id)
        self.assertEqual(resolved.email, "existing@example.com")


if __name__ == "__main__":
    unittest.main()

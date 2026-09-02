import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.api.v1.transactions import create_transaction, get_transaction, list_transactions
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.transaction import Transaction
from app.schemas.razorpay import RazorpayOrderResult
from app.schemas.transaction import TransactionCreateRequest


class FakeScalars:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class FakeSession:
    def __init__(self, customers, transactions=None):
        self.customers = customers
        self.transactions = transactions or []
        self.commits = 0

    def get(self, model, object_id):
        rows = self.customers if model is Customer else self.transactions if model is Transaction else []
        return next((row for row in rows if row.id == object_id), None)

    def scalar(self, statement):
        values = set(statement.compile().params.values())
        return next((item for item in self.transactions if item.merchant_id in values and item.merchant_transaction_id in values), None)

    def scalars(self, statement):
        values = set(statement.compile().params.values())
        return FakeScalars([item for item in self.transactions if item.merchant_id in values])
    def add(self, transaction):
        transaction.id = uuid4()
        transaction.created_at = datetime.now(timezone.utc)
        transaction.updated_at = transaction.created_at
        self.transactions.append(transaction)
    def commit(self): self.commits += 1
    def refresh(self, _transaction): pass
    def rollback(self): pass


class FakeRazorpayService:
    def create_order(self, request):
        return RazorpayOrderResult(id="order_test_123", amount=request.amount, currency=request.currency, status="created", raw_response={})


class TransactionApiTests(unittest.TestCase):
    def setUp(self):
        self.merchant_a = Merchant(id=uuid4(), name="A", email="a@example.com")
        self.merchant_b = Merchant(id=uuid4(), name="B", email="b@example.com")
        self.customer_a = Customer(id=uuid4(), merchant_id=self.merchant_a.id, name="Customer A")
        self.customer_b = Customer(id=uuid4(), merchant_id=self.merchant_b.id, name="Customer B")
        self.db = FakeSession([self.customer_a, self.customer_b])
        self.request = TransactionCreateRequest(merchant_transaction_id="ORDER_123", customer_id=self.customer_a.id, amount=Decimal("1999.00"), currency="INR")

    def create(self):
        with patch("app.api.v1.transactions.RazorpayService", return_value=FakeRazorpayService()):
            return create_transaction(self.request, self.db, self.merchant_a)

    def test_merchant_creates_transaction_and_stores_razorpay_order(self):
        transaction = self.create()
        self.assertEqual(transaction.merchant_id, self.merchant_a.id)
        self.assertEqual(transaction.customer_id, self.customer_a.id)
        self.assertEqual(transaction.razorpay_order_id, "order_test_123")
        self.assertEqual(transaction.status, "pending")

    def test_customer_must_belong_to_authenticated_merchant(self):
        request = self.request.model_copy(update={"customer_id": self.customer_b.id})
        with self.assertRaises(HTTPException) as error:
            create_transaction(request, self.db, self.merchant_a)
        self.assertEqual(error.exception.status_code, 404)

    def test_duplicate_is_scoped_to_merchant(self):
        self.create()
        with self.assertRaises(HTTPException) as error:
            self.create()
        self.assertEqual(error.exception.status_code, 409)

        second_request = self.request.model_copy(update={"customer_id": self.customer_b.id})
        with patch("app.api.v1.transactions.RazorpayService", return_value=FakeRazorpayService()):
            second = create_transaction(second_request, self.db, self.merchant_b)
        self.assertEqual(second.merchant_id, self.merchant_b.id)

    def test_transaction_lookup_is_merchant_scoped(self):
        transaction = self.create()
        self.assertEqual(get_transaction(transaction.id, self.db, self.merchant_a).id, transaction.id)
        with self.assertRaises(HTTPException) as error:
            get_transaction(transaction.id, self.db, self.merchant_b)
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(list_transactions(self.db, self.merchant_a), self.db.transactions)

        second_request = self.request.model_copy(update={"customer_id": self.customer_b.id, "merchant_transaction_id": "ORDER_456"})
        with patch("app.api.v1.transactions.RazorpayService", return_value=FakeRazorpayService()):
            create_transaction(second_request, self.db, self.merchant_b)
        self.assertEqual(list_transactions(self.db, self.merchant_a), [transaction])

import asyncio
import hashlib
import hmac
import json
import unittest
from uuid import uuid4

from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.webhooks import receive_razorpay_webhook
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


class FakeRazorpayService:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.received_raw_body: bytes | None = None

    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        self.received_raw_body = raw_body
        return self.valid


class FakeExecutionResult:
    def __init__(self, event_id: str | None) -> None:
        self.event_id = event_id

    def scalar_one_or_none(self) -> str | None:
        return self.event_id


class FakeSession:
    def __init__(self) -> None:
        self.event_ids: set[str] = set()
        self.pending_event_ids: set[str] = set()
        self.customers: list[Customer] = []
        self.payments: list[Payment] = []
        self.recovery_cases: list[RecoveryCase] = []
        self.audit_logs: list[AuditLog] = []
        self.commits = 0

    def execute(self, statement: object) -> FakeExecutionResult:
        event_id = statement.compile().params["razorpay_event_id"]
        if event_id in self.event_ids or event_id in self.pending_event_ids:
            return FakeExecutionResult(None)
        self.pending_event_ids.add(event_id)
        return FakeExecutionResult(event_id)

    def scalar(self, statement: object):
        entity = statement.column_descriptions[0]["entity"]
        values = set(statement.compile().params.values())
        if entity is Customer:
            return next(
                (
                    customer
                    for customer in self.customers
                    if customer.razorpay_customer_id in values or customer.email in values
                ),
                None,
            )
        if entity is Payment:
            return next(
                (payment for payment in self.payments if payment.razorpay_payment_id in values),
                None,
            )
        if entity is RecoveryCase:
            return next(
                (
                    case
                    for case in self.recovery_cases
                    if case.payment_id in values or case.razorpay_payment_link_id in values
                ),
                None,
            )
        return None

    def get(self, model: object, object_id: object):
        if model is Payment:
            return next((payment for payment in self.payments if payment.id == object_id), None)
        return None

    def add(self, item: object) -> None:
        if isinstance(item, Customer):
            item.id = uuid4()
            self.customers.append(item)
        elif isinstance(item, Payment):
            item.id = uuid4()
            self.payments.append(item)
        elif isinstance(item, RecoveryCase):
            item.id = uuid4()
            self.recovery_cases.append(item)
        elif isinstance(item, AuditLog):
            self.audit_logs.append(item)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        self.event_ids.update(self.pending_event_ids)
        self.pending_event_ids.clear()
        self.commits += 1

    def rollback(self) -> None:
        self.pending_event_ids.clear()


def make_request(body: bytes, headers: dict[str, str]) -> Request:
    encoded_headers = [(key.lower().encode(), value.encode()) for key, value in headers.items()]

    async def receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "headers": encoded_headers}, receive)


def webhook_body(event: str) -> bytes:
    if event == "payment.failed":
        payload = {
            "event": event,
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_1",
                        "customer_id": "cust_test_1",
                        "email": "customer@example.com",
                        "contact": "+919999999999",
                        "notes": {"customer_name": "Test Customer"},
                        "amount": 50000,
                        "currency": "INR",
                        "error_description": "Bank declined the payment",
                    }
                }
            },
        }
    elif event == "payment_link.paid":
        payload = {
            "event": event,
            "payload": {
                "payment_link": {"entity": {"id": "plink_test_1"}},
                "payment": {"entity": {"id": "pay_success_1", "amount": 50000}},
            },
        }
    else:
        payload = {"event": event, "payload": {}}
    return json.dumps(payload, separators=(",", ":")).encode()


class RazorpayWebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = FakeSession()
        self.service = FakeRazorpayService()

    def post(self, body: bytes, headers: dict[str, str]):
        return asyncio.run(receive_razorpay_webhook(make_request(body, headers), self.db, self.service))

    def test_valid_signature_processes_payment_failed_and_uses_raw_body(self) -> None:
        body = webhook_body("payment.failed")
        response = self.post(body, {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_1"})

        self.assertEqual(response.status, "processed")
        self.assertEqual(response.event_type, "payment.failed")
        self.assertEqual(self.service.received_raw_body, body)
        self.assertEqual(len(self.db.customers), 1)
        self.assertEqual(len(self.db.payments), 1)
        self.assertEqual(len(self.db.recovery_cases), 1)
        self.assertEqual(self.db.payments[0].razorpay_payment_id, "pay_test_1")
        self.assertEqual(str(self.db.payments[0].amount), "500")

    def create_mapped_recovery_case(self) -> None:
        self.post(
            webhook_body("payment.failed"),
            {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_setup"},
        )
        self.db.recovery_cases[0].razorpay_payment_link_id = "plink_test_1"

    def test_payment_link_paid_recovers_matching_case(self) -> None:
        self.create_mapped_recovery_case()
        response = self.post(
            webhook_body("payment_link.paid"),
            {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_2"},
        )

        self.assertEqual(response.status, "processed")
        self.assertEqual(response.event_type, "payment_link.paid")
        self.assertEqual(self.db.payments[0].status, "succeeded")
        self.assertIsNone(self.db.payments[0].failure_reason)
        self.assertEqual(self.db.payments[0].razorpay_success_payment_id, "pay_success_1")
        self.assertIsNotNone(self.db.payments[0].paid_at)
        self.assertEqual(self.db.recovery_cases[0].status, "recovered")
        self.assertEqual(self.db.recovery_cases[0].amount_recovered, self.db.recovery_cases[0].amount_at_risk)

    def test_invalid_signature_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as error:
            asyncio.run(
                receive_razorpay_webhook(
                    make_request(
                        webhook_body("payment.failed"),
                        {"X-Razorpay-Signature": "invalid", "X-Razorpay-Event-Id": "evt_3"},
                    ),
                    self.db,
                    FakeRazorpayService(valid=False),
                )
            )

        self.assertEqual(error.exception.status_code, 401)

    def test_missing_signature_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as error:
            self.post(webhook_body("payment.failed"), {"X-Razorpay-Event-Id": "evt_4"})

        self.assertEqual(error.exception.status_code, 401)

    def test_unsupported_event_is_ignored(self) -> None:
        response = self.post(
            webhook_body("refund.created"),
            {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_5"},
        )

        self.assertEqual(response.status, "ignored")

    def test_duplicate_event_has_no_duplicate_side_effect(self) -> None:
        body = webhook_body("payment.failed")
        headers = {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_6"}

        first = self.post(body, headers)
        second = self.post(body, headers)

        self.assertEqual(first.status, "processed")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(self.db.commits, 1)

    def test_same_payment_with_a_new_event_does_not_duplicate_recovery_case(self) -> None:
        body = webhook_body("payment.failed")
        first = self.post(body, {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_7"})
        second = self.post(body, {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_8"})

        self.assertEqual(first.status, "processed")
        self.assertEqual(second.status, "processed")
        self.assertEqual(len(self.db.customers), 1)
        self.assertEqual(len(self.db.payments), 1)
        self.assertEqual(len(self.db.recovery_cases), 1)

    def test_incomplete_payment_payload_is_ignored_without_creating_records(self) -> None:
        payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_incomplete",
                        "email": "missing-name@example.com",
                        "amount": 1000,
                        "currency": "INR",
                        "error_reason": "payment_failed",
                    }
                }
            },
        }
        response = self.post(
            json.dumps(payload).encode(),
            {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_9"},
        )

        self.assertEqual(response.status, "ignored")
        self.assertEqual(len(self.db.customers), 0)
        self.assertEqual(len(self.db.payments), 0)
        self.assertEqual(len(self.db.recovery_cases), 0)

    def test_duplicate_paid_link_event_does_not_recover_twice(self) -> None:
        self.create_mapped_recovery_case()
        body = webhook_body("payment_link.paid")
        headers = {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_paid_duplicate"}

        first = self.post(body, headers)
        audit_count = len(self.db.audit_logs)
        second = self.post(body, headers)

        self.assertEqual(first.status, "processed")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(self.db.recovery_cases[0].amount_recovered, self.db.recovery_cases[0].amount_at_risk)
        self.assertEqual(len(self.db.audit_logs), audit_count)

    def test_unknown_payment_link_is_ignored_without_creating_records(self) -> None:
        response = self.post(
            webhook_body("payment_link.paid"),
            {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_unknown_link"},
        )

        self.assertEqual(response.status, "ignored")
        self.assertEqual(len(self.db.customers), 0)
        self.assertEqual(len(self.db.payments), 0)
        self.assertEqual(len(self.db.recovery_cases), 0)

    def test_already_recovered_case_is_not_recovered_again(self) -> None:
        self.create_mapped_recovery_case()
        case = self.db.recovery_cases[0]
        case.status = "recovered"
        case.amount_recovered = case.amount_at_risk

        response = self.post(
            webhook_body("payment_link.paid"),
            {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_already_recovered"},
        )

        self.assertEqual(response.status, "processed")
        self.assertEqual(case.amount_recovered, case.amount_at_risk)
        self.assertEqual(case.status, "recovered")

    def test_paid_link_recovery_never_exceeds_amount_at_risk(self) -> None:
        self.create_mapped_recovery_case()
        payload = json.loads(webhook_body("payment_link.paid"))
        payload["payload"]["payment"]["entity"]["amount"] = 100000

        response = self.post(
            json.dumps(payload).encode(),
            {"X-Razorpay-Signature": "valid", "X-Razorpay-Event-Id": "evt_overpayment"},
        )

        self.assertEqual(response.status, "processed")
        self.assertEqual(
            self.db.recovery_cases[0].amount_recovered,
            self.db.recovery_cases[0].amount_at_risk,
        )


if __name__ == "__main__":
    unittest.main()

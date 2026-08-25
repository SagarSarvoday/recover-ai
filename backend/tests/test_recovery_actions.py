import unittest
from types import SimpleNamespace
from uuid import uuid4
from decimal import Decimal
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.customer import Customer
from app.schemas.razorpay import RazorpayPaymentLinkResult
from app.services.recovery_actions import (
    InvalidRecoveryActionError,
    RecoveryActionExecutionContext,
    execute_recovery_action,
)


class FakeSession:
    def __init__(self, case: object, payment: object, customer: object) -> None:
        self.case = case
        self.payment = payment
        self.customer = customer
        self.audit_logs: list[object] = []
        self.commit_count = 0

    def get(self, model: object, object_id: object) -> object | None:
        if model is RecoveryCase and object_id == self.case.id:
            return self.case
        if model is Payment and object_id == self.payment.id:
            return self.payment
        if model is Customer and object_id == self.customer.id:
            return self.customer
        return None

    def add(self, item: object) -> None:
        self.audit_logs.append(item)

    def commit(self) -> None:
        self.commit_count += 1


class FakeRazorpayService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[object] = []

    def create_payment_link(self, request: object) -> RazorpayPaymentLinkResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return RazorpayPaymentLinkResult(
            id="plink_test_recovery_123",
            short_url="https://rzp.io/i/recovery-test",
            status="created",
            amount=request.amount,  # type: ignore[attr-defined]
            currency=request.currency,  # type: ignore[attr-defined]
            raw_response={},
        )


class RecoveryActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case_id = uuid4()
        self.customer_id = uuid4()
        self.payment_id = uuid4()
        self.case = SimpleNamespace(
            id=self.case_id,
            payment_id=self.payment_id,
            customer_id=self.customer_id,
            amount_at_risk=Decimal("499.00"),
            amount_recovered=Decimal("0.00"),
            attempt_count=0,
            last_attempt_at=None,
            status="open",
            razorpay_payment_link_id=None,
        )
        self.payment = SimpleNamespace(
            id=self.payment_id,
            customer_id=self.customer_id,
            failure_reason="temporary bank error",
            amount=Decimal("499.00"),
            currency="INR",
            status="failed",
        )
        self.customer = SimpleNamespace(
            id=self.customer_id,
            name="Test Customer",
            email="customer@example.com",
            phone="9876543210",
        )
        self.db = FakeSession(self.case, self.payment, self.customer)
        self.context = RecoveryActionExecutionContext(db=self.db, max_recovery_attempts=3)

    def test_allowed_retry(self) -> None:
        result = execute_recovery_action("retry", self.case_id, self.context)

        self.assertTrue(result.success)
        self.assertEqual(result.action, "retry_payment")
        self.assertEqual(len(self.db.audit_logs), 1)

    def test_retry_is_blocked_at_max_attempts(self) -> None:
        self.case.attempt_count = 3

        result = execute_recovery_action("retry", self.case_id, self.context)

        self.assertFalse(result.success)
        self.assertIn("maximum_recovery_attempts_reached", result.guardrails_applied)

    def test_action_is_blocked_for_recovered_case(self) -> None:
        self.case.status = "recovered"

        result = execute_recovery_action("wait", self.case_id, self.context)

        self.assertFalse(result.success)
        self.assertIn("case_already_recovered", result.guardrails_applied)

    def test_action_is_blocked_for_closed_case(self) -> None:
        self.case.status = "closed"

        result = execute_recovery_action("wait", self.case_id, self.context)

        self.assertFalse(result.success)
        self.assertIn("case_already_closed", result.guardrails_applied)

    def test_wait_schedules_followup(self) -> None:
        result = execute_recovery_action("wait", self.case_id, self.context)

        self.assertTrue(result.success)
        self.assertEqual(result.action, "schedule_followup")

    def test_stop_recovery_closes_case(self) -> None:
        result = execute_recovery_action("close", self.case_id, self.context)

        self.assertTrue(result.success)
        self.assertEqual(result.action, "stop_recovery")
        self.assertEqual(self.case.status, "closed")

    def test_invalid_action_is_rejected(self) -> None:
        with self.assertRaises(InvalidRecoveryActionError):
            execute_recovery_action("arbitrary_database_operation", self.case_id, self.context)

    def test_contact_creates_and_persists_real_payment_link(self) -> None:
        razorpay_service = FakeRazorpayService()
        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )

        result = execute_recovery_action("contact", self.case_id, context)

        self.assertTrue(result.success)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.payment_link_id, "plink_test_recovery_123")
        self.assertEqual(result.payment_link_url, "https://rzp.io/i/recovery-test")
        self.assertEqual(self.case.razorpay_payment_link_id, "plink_test_recovery_123")
        self.assertEqual(len(razorpay_service.requests), 1)
        request = razorpay_service.requests[0]
        self.assertEqual(request.amount, 49900)  # type: ignore[attr-defined]
        self.assertEqual(request.currency, "INR")  # type: ignore[attr-defined]
        self.assertEqual(request.customer_email, "customer@example.com")  # type: ignore[attr-defined]

    def test_contact_uses_only_outstanding_amount_in_paise(self) -> None:
        self.case.amount_recovered = Decimal("149.00")
        razorpay_service = FakeRazorpayService()
        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )

        execute_recovery_action("contact", self.case_id, context)

        self.assertEqual(razorpay_service.requests[0].amount, 35000)  # type: ignore[attr-defined]

    def test_repeated_contact_reuses_existing_payment_link(self) -> None:
        self.case.razorpay_payment_link_id = "plink_existing"
        razorpay_service = FakeRazorpayService()
        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )

        result = execute_recovery_action("contact", self.case_id, context)

        self.assertTrue(result.success)
        self.assertEqual(result.payment_link_id, "plink_existing")
        self.assertEqual(razorpay_service.requests, [])

    def test_razorpay_failure_does_not_persist_a_payment_link(self) -> None:
        razorpay_service = FakeRazorpayService(error=RuntimeError("provider unavailable"))
        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )

        result = execute_recovery_action("contact", self.case_id, context)

        self.assertFalse(result.success)
        self.assertEqual(result.status, "failed")
        self.assertIsNone(self.case.razorpay_payment_link_id)
        self.assertNotEqual(self.case.status, "recovered")

    def test_recovered_case_does_not_create_a_payment_link(self) -> None:
        self.case.status = "recovered"
        razorpay_service = FakeRazorpayService()
        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )

        result = execute_recovery_action("contact", self.case_id, context)

        self.assertFalse(result.success)
        self.assertIn("case_already_recovered", result.guardrails_applied)
        self.assertEqual(razorpay_service.requests, [])


if __name__ == "__main__":
    unittest.main()

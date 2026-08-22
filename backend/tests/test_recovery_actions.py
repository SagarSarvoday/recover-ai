import unittest
from types import SimpleNamespace
from uuid import uuid4
from decimal import Decimal
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.services.recovery_actions import (
    InvalidRecoveryActionError,
    RecoveryActionExecutionContext,
    execute_recovery_action,
)


class FakeSession:
    def __init__(self, case: object, payment: object) -> None:
        self.case = case
        self.payment = payment
        self.audit_logs: list[object] = []
        self.commit_count = 0

    def get(self, model: object, object_id: object) -> object | None:
        if model is RecoveryCase and object_id == self.case.id:
            return self.case
        if model is Payment and object_id == self.payment.id:
            return self.payment
        return None

    def add(self, item: object) -> None:
        self.audit_logs.append(item)

    def commit(self) -> None:
        self.commit_count += 1


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
    )
        self.payment = SimpleNamespace(
        id=self.payment_id,
        customer_id=self.customer_id,
        failure_reason="temporary bank error",
        amount=Decimal("499.00"),
        status="failed",
    )
        self.db = FakeSession(self.case, self.payment)
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


if __name__ == "__main__":
    unittest.main()

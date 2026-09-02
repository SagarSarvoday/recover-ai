import unittest
from types import SimpleNamespace
from uuid import uuid4
from decimal import Decimal 
from fastapi import HTTPException

from app.api.v1.recovery_cases import execute_recovery_case
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


class FakeSession:
    def __init__(self, case: object, payment: object) -> None:
        self.case = case
        self.payment = payment
        self.audit_logs: list[object] = []

    def get(self, model: object, object_id: object) -> object | None:
        if model is RecoveryCase and object_id == self.case.id:
            return self.case
        if model is Payment and object_id == self.payment.id:
            return self.payment
        return None

    def add(self, item: object) -> None:
        self.audit_logs.append(item)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class RecoveryExecutionEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case_id = uuid4()
        self.customer_id = uuid4()
        self.merchant_id = uuid4()
        self.payment_id = uuid4()
        self.case = SimpleNamespace(
            amount_at_risk=Decimal("499.00"),
            amount_recovered=Decimal("0.00"),
            last_attempt_at=None,
            id=self.case_id,
            merchant_id=self.merchant_id,
            customer_id=self.customer_id,
            payment_id=self.payment_id,
            status="open",
            attempt_count=0,
            ai_decision="retry",
            ai_decision_note="The payment can be retried once.",
            razorpay_payment_link_id=None,
        )
        self.payment = SimpleNamespace(
        id=self.payment_id,
        customer_id=self.customer_id,
        failure_reason="temporary bank error",
        amount=Decimal("499.00"),
        status="failed",
    )
        self.db = FakeSession(self.case, self.payment)
        self.merchant = SimpleNamespace(id=self.merchant_id)

    def execute_persisted_decision(self, action: str):
        self.case.ai_decision = action
        return execute_recovery_case(self.case_id, self.db, self.merchant)

    def test_analyzed_retry_executes_retry_tool(self) -> None:
        response = self.execute_persisted_decision("retry")

        self.assertEqual(response.ai_recommendation.action, "retry")
        self.assertEqual(response.action_execution_result.action, "retry_payment")
        self.assertTrue(response.action_execution_result.success)

    def test_analyzed_contact_executes_payment_link_tool(self) -> None:
        # This endpoint test verifies the persisted-action dispatch without performing
        # an external provider call. Dedicated action tests cover link creation.
        self.case.razorpay_payment_link_id = "plink_existing"
        response = self.execute_persisted_decision("contact")

        self.assertEqual(response.action_execution_result.action, "create_payment_link")
        self.assertTrue(response.action_execution_result.success)

    def test_analyzed_wait_executes_followup_tool(self) -> None:
        self.case.ai_wait_minutes = 60
        response = self.execute_persisted_decision("wait")

        self.assertEqual(response.action_execution_result.action, "schedule_followup")
        self.assertTrue(response.action_execution_result.success)

    def test_analyzed_close_executes_stop_tool(self) -> None:
        response = self.execute_persisted_decision("close")

        self.assertEqual(response.action_execution_result.action, "stop_recovery")
        self.assertTrue(response.action_execution_result.success)
        self.assertEqual(self.case.status, "closed")

    def test_execution_requires_prior_ai_decision(self) -> None:
        self.case.ai_decision = None

        with self.assertRaises(HTTPException) as error:
            execute_recovery_case(self.case_id, self.db, self.merchant)

        self.assertEqual(error.exception.status_code, 400)

    def test_execution_is_blocked_at_max_attempts(self) -> None:
        self.case.attempt_count = 3

        response = self.execute_persisted_decision("retry")

        self.assertFalse(response.action_execution_result.success)
        self.assertIn(
            "maximum_recovery_attempts_reached",
            response.action_execution_result.guardrails_applied,
        )

    def test_execution_is_blocked_for_recovered_case(self) -> None:
        self.case.status = "recovered"

        response = self.execute_persisted_decision("retry")

        self.assertFalse(response.action_execution_result.success)
        self.assertIn(
            "case_already_recovered",
            response.action_execution_result.guardrails_applied,
        )

    def test_invalid_persisted_action_is_rejected(self) -> None:
        self.case.ai_decision = "arbitrary_function"

        with self.assertRaises(HTTPException) as error:
            execute_recovery_case(self.case_id, self.db, self.merchant)

        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()

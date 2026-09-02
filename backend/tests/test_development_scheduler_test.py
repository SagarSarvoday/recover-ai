import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.services.development_scheduler_test import (
    DevelopmentSchedulerTestError,
    schedule_development_wait_retry,
)
from app.services.legacy_merchant_bootstrap import LEGACY_MERCHANT_ID


class FakeSession:
    def __init__(self, case, payment, active_job=None):
        self.case = case
        self.payment = payment
        self.active_job = active_job
        self.added = []
        self.commits = 0

    def get(self, model, object_id):
        if model is RecoveryCase and object_id == self.case.id:
            return self.case
        if model is Payment and object_id == self.payment.id:
            return self.payment
        return None

    def scalar(self, _statement):
        return self.active_job

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commits += 1


class DevelopmentSchedulerTestTests(unittest.TestCase):
    def setUp(self):
        self.case = SimpleNamespace(
            id=uuid4(),
            merchant_id=LEGACY_MERCHANT_ID,
            customer_id=uuid4(),
            payment_id=uuid4(),
            status="open",
            attempt_count=0,
            amount_at_risk=Decimal("99.00"),
            amount_recovered=Decimal("0.00"),
            razorpay_payment_link_id=None,
            last_attempt_at=None,
            next_action_at=None,
            scheduled_action=None,
        )
        self.payment = SimpleNamespace(
            id=self.case.payment_id,
            customer_id=self.case.customer_id,
            amount=Decimal("99.00"),
            currency="INR",
            status="failed",
            failure_reason="bank declined",
            paid_at=None,
        )
        self.db = FakeSession(self.case, self.payment)

    def test_schedules_one_minute_wait_via_normal_wait_dispatcher(self):
        before = datetime.now(timezone.utc)
        result = schedule_development_wait_retry(
            self.db, self.case.id, app_environment="development", max_recovery_attempts=3
        )

        jobs = [item for item in self.db.added if isinstance(item, ScheduledRecoveryAction)]
        self.assertTrue(result.success)
        self.assertEqual(result.scheduled_action, "retry")
        self.assertGreaterEqual(result.scheduled_at, before)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].action, "retry")
        self.assertEqual(jobs[0].merchant_id, LEGACY_MERCHANT_ID)
        self.assertEqual(self.case.next_action_at, result.scheduled_at)

    def test_refuses_non_test_environment(self):
        with self.assertRaises(DevelopmentSchedulerTestError):
            schedule_development_wait_retry(
                self.db, self.case.id, app_environment="production", max_recovery_attempts=3
            )

    def test_refuses_already_scheduled_case(self):
        self.case.next_action_at = datetime.now(timezone.utc)
        with self.assertRaises(DevelopmentSchedulerTestError):
            schedule_development_wait_retry(
                self.db, self.case.id, app_environment="development", max_recovery_attempts=3
            )

    def test_refuses_recoverable_retry_and_invalid_ownership(self):
        self.payment.failure_reason = "temporary network failure"
        with self.assertRaises(DevelopmentSchedulerTestError):
            schedule_development_wait_retry(
                self.db, self.case.id, app_environment="test", max_recovery_attempts=3
            )

        self.payment.failure_reason = "bank declined"
        self.case.merchant_id = uuid4()
        with self.assertRaises(DevelopmentSchedulerTestError):
            schedule_development_wait_retry(
                self.db, self.case.id, app_environment="test", max_recovery_attempts=3
            )

    def test_refuses_attempt_limit_and_active_retry(self):
        self.case.attempt_count = 3
        with self.assertRaises(DevelopmentSchedulerTestError):
            schedule_development_wait_retry(
                self.db, self.case.id, app_environment="development", max_recovery_attempts=3
            )

        self.case.attempt_count = 0
        self.db.active_job = uuid4()
        with self.assertRaises(DevelopmentSchedulerTestError):
            schedule_development_wait_retry(
                self.db, self.case.id, app_environment="development", max_recovery_attempts=3
            )


if __name__ == "__main__":
    unittest.main()

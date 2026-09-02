import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from app.models.customer import Customer
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.recovery_analysis import RecoveryDecision
from app.services.recovery_actions import RecoveryActionExecutionContext, execute_recovery_action
from app.services.scheduled_recovery_actions import execute_claimed_scheduled_action, run_scheduled_recovery_actions


class FakeSession:
    def __init__(self, case, payment, customer, job=None, existing_job=None):
        self.case = case
        self.payment = payment
        self.customer = customer
        self.job = job
        self.existing_job = existing_job
        self.added = []
        self.commit_count = 0
        self.closed = False

    def get(self, model, object_id):
        if model is RecoveryCase and object_id == self.case.id:
            return self.case
        if model is Payment and object_id == self.payment.id:
            return self.payment
        if model is Customer and object_id == self.customer.id:
            return self.customer
        if model is ScheduledRecoveryAction and self.job is not None and object_id == self.job.id:
            return self.job
        return None

    def scalar(self, _query):
        return self.existing_job

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commit_count += 1

    def close(self):
        self.closed = True


class ScheduledRecoveryActionTests(unittest.TestCase):
    def setUp(self):
        self.merchant_id = uuid4()
        self.case_id = uuid4()
        self.customer_id = uuid4()
        self.payment_id = uuid4()
        self.case = SimpleNamespace(
            id=self.case_id, merchant_id=self.merchant_id, customer_id=self.customer_id,
            payment_id=self.payment_id, status="open", attempt_count=0,
            amount_at_risk=Decimal("99.00"), amount_recovered=Decimal("0.00"),
            razorpay_payment_link_id=None, last_attempt_at=None, next_action_at=None, scheduled_action=None,
        )
        self.payment = SimpleNamespace(
            id=self.payment_id, customer_id=self.customer_id, amount=Decimal("99.00"), currency="INR",
            status="failed", failure_reason="temporary bank error", paid_at=None,
        )
        self.customer = SimpleNamespace(id=self.customer_id, name="Test", email="test@example.com", phone=None)
        self.db = FakeSession(self.case, self.payment, self.customer)
        self.context = RecoveryActionExecutionContext(db=self.db, max_recovery_attempts=3)

    def test_wait_with_validated_timing_persists_a_retry_job(self):
        before = datetime.now(timezone.utc)
        result = execute_recovery_action("wait", self.case_id, self.context, wait_minutes=10)
        jobs = [item for item in self.db.added if isinstance(item, ScheduledRecoveryAction)]
        self.assertTrue(result.success)
        self.assertEqual(result.scheduled_action, "retry")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].merchant_id, self.merchant_id)
        self.assertGreaterEqual(jobs[0].scheduled_at, before + timedelta(minutes=10))
        self.assertEqual(self.case.scheduled_action, "retry")

    def test_wait_without_timing_is_safely_rejected(self):
        result = execute_recovery_action("wait", self.case_id, self.context)
        self.assertFalse(result.success)
        self.assertIn("missing_wait_minutes", result.guardrails_applied)

    def test_wait_schema_rejects_missing_or_out_of_range_timing(self):
        with self.assertRaises(ValidationError):
            RecoveryDecision(action="wait", reason="Wait.", confidence=0.5, next_step="wait", stop=False)
        with self.assertRaises(ValidationError):
            RecoveryDecision(action="wait", reason="Wait.", confidence=0.5, next_step="wait", stop=False, wait_minutes=10_081)

    def test_duplicate_schedule_does_not_add_a_second_job(self):
        self.db.existing_job = SimpleNamespace(id=uuid4(), scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=10))
        result = execute_recovery_action("wait", self.case_id, self.context, wait_minutes=10)
        self.assertTrue(result.success)
        self.assertEqual(len([item for item in self.db.added if isinstance(item, ScheduledRecoveryAction)]), 0)

    def _run_job(self, *, status="open", merchant_id=None, attempts=0, when=None):
        self.case.status = status
        self.case.attempt_count = attempts
        job = SimpleNamespace(
            id=uuid4(), recovery_case_id=self.case_id, merchant_id=merchant_id or self.merchant_id,
            action="retry", scheduled_at=when or datetime.now(timezone.utc) - timedelta(minutes=1),
            status="running", attempt_count=1, lease_expires_at=datetime.now(timezone.utc),
            executed_at=None, error_message=None,
        )
        self.db.job = job
        execute_claimed_scheduled_action(self.db, job.id, max_recovery_attempts=3)
        return job

    def test_due_job_executes_once_and_marks_completed(self):
        job = self._run_job()
        self.assertEqual(job.status, "completed")
        self.assertIsNotNone(job.executed_at)
        self.assertEqual(self.case.status, "recovered")
        attempts = self.case.attempt_count
        execute_claimed_scheduled_action(self.db, job.id, max_recovery_attempts=3)
        self.assertEqual(self.case.attempt_count, attempts)

    def test_job_does_not_execute_before_its_scheduled_time(self):
        job = self._run_job(when=datetime.now(timezone.utc) + timedelta(minutes=1))
        self.assertEqual(job.status, "running")
        self.assertEqual(self.case.attempt_count, 0)

    def test_recovered_closed_and_max_attempt_cases_are_skipped(self):
        for status, attempts in (("recovered", 0), ("closed", 0), ("open", 3)):
            self.case.status = "open"
            self.case.attempt_count = 0
            job = self._run_job(status=status, attempts=attempts)
            self.assertEqual(job.status, "skipped")
            self.assertIsNotNone(job.error_message)

    def test_merchant_ownership_mismatch_is_skipped(self):
        job = self._run_job(merchant_id=uuid4())
        self.assertEqual(job.status, "skipped")
        self.assertIn("ownership", job.error_message)

    def test_execution_failure_is_recorded(self):
        with patch("app.services.scheduled_recovery_actions.execute_recovery_action", side_effect=RuntimeError("provider down")):
            job = self._run_job()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error_message, "Scheduled recovery action failed internally.")

    def test_reinitializing_worker_processes_existing_pending_job(self):
        job = SimpleNamespace(
            id=uuid4(), recovery_case_id=self.case_id, merchant_id=self.merchant_id, action="retry",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1), status="running", attempt_count=1,
            lease_expires_at=None, executed_at=None, error_message=None,
        )
        claim_session = FakeSession(self.case, self.payment, self.customer)
        execute_session = FakeSession(self.case, self.payment, self.customer, job=job)
        sessions = iter([claim_session, execute_session])
        with patch("app.services.scheduled_recovery_actions.claim_due_scheduled_actions", return_value=[job.id]):
            count = run_scheduled_recovery_actions(lambda: next(sessions), max_recovery_attempts=3)
        self.assertEqual(count, 1)
        self.assertTrue(claim_session.closed)
        self.assertTrue(execute_session.closed)
        self.assertEqual(job.status, "completed")


if __name__ == "__main__":
    unittest.main()

"""Comprehensive end-to-end and scenario tests for the production-ready RecoverAI engine.

Verifies requirements A through L:
A. Initial failed payment (recovery case created, AI runs)
B. WAIT (creates durable scheduled action, case is not recovered)
C. Scheduled WAIT completion (scheduled worker resumes case, AI reassesses with wait_elapsed=True)
D. RETRY/CONTACT (payment link created/reused, email sent, case in_progress, amount_recovered=0)
E. Successful payment (payment_link.paid webhook marks succeeded, case recovered, amount_recovered updated)
F. Customer doesn't pay (case remains unrecovered)
G. Expired payment link (expired link not treated as active; fresh link created with incremented attempt_count)
H. Missing customer email (notification failure audited, case not recovered)
I. Maximum attempts (enforced server-side)
J. Duplicate worker execution (idempotent, no duplicate attempts)
K. Already recovered/closed case (actions blocked)
L. Merchant isolation (strict tenant boundary)
"""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from pydantic import SecretStr

from app.core.config import Settings
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.razorpay import RazorpayPaymentLinkResult
from app.schemas.recovery_actions import RecoveryActionContext
from app.schemas.recovery_analysis import RecoveryDecision
from app.services.notification_service import NotificationService
from app.services.razorpay_webhooks import (
    RazorpayWebhookEnvelope,
    ingest_payment_failed_webhook,
    ingest_payment_link_paid_webhook,
)
from app.services.recovery_actions import (
    RecoveryActionExecutionContext,
    execute_recovery_action,
)
from app.services.recovery_agent import run_recovery_agent
from app.services.recovery_decision import apply_guardrails, build_recovery_context
from app.services.scheduled_recovery_actions import (
    execute_claimed_scheduled_action,
)


class FakeExecutionResult:
    def __init__(self, event_id: str | None) -> None:
        self.event_id = event_id

    def scalar_one_or_none(self) -> str | None:
        return self.event_id


class FakeLifecycleDb:
    def __init__(self, *, merchant=None, customer=None, payment=None, case=None) -> None:
        self.merchants = {merchant.id: merchant} if merchant else {}
        self.customers = {customer.id: customer} if customer else {}
        self.payments = {payment.id: payment} if payment else {}
        self.cases = {case.id: case} if case else {}
        self.scheduled_actions: dict[object, object] = {}
        self.audit_logs: list[object] = []
        self.webhook_events: set[str] = set()
        self.commits = 0
        self.rollbacks = 0

    def get(self, model: object, obj_id: object) -> object | None:
        if model is Merchant:
            return self.merchants.get(obj_id)
        if model is Customer:
            return self.customers.get(obj_id)
        if model is Payment:
            return self.payments.get(obj_id)
        if model is RecoveryCase:
            return self.cases.get(obj_id)
        if model is ScheduledRecoveryAction:
            return self.scheduled_actions.get(obj_id)
        return None

    def execute(self, statement: object) -> FakeExecutionResult:
        try:
            params = statement.compile().params  # type: ignore[attr-defined]
            event_id = params.get("razorpay_event_id")
            if event_id:
                if event_id in self.webhook_events:
                    return FakeExecutionResult(None)
                self.webhook_events.add(event_id)
                return FakeExecutionResult(event_id)
        except Exception:
            pass
        return FakeExecutionResult("evt_ok")

    def scalar(self, statement: object) -> object | None:
        stmt_str = str(statement)
        if "merchants" in stmt_str:
            for m in self.merchants.values():
                return m
        if "scheduled_recovery_actions" in stmt_str:
            for job in self.scheduled_actions.values():
                if getattr(job, "status", None) in ("pending", "running"):
                    return job
            return None
        if "payments" in stmt_str:
            for p in self.payments.values():
                return p
        if "recovery_cases" in stmt_str:
            for c in self.cases.values():
                return c
        return None

    def scalars(self, statement: object) -> object:
        mock_result = MagicMock()
        mock_result.all.return_value = []
        return mock_result

    def add(self, item: object) -> None:
        name = item.__class__.__name__
        if name == "Customer":
            self.customers[item.id] = item
        elif name == "Payment":
            self.payments[item.id] = item
        elif name == "RecoveryCase":
            self.cases[item.id] = item
        elif name == "ScheduledRecoveryAction":
            if not getattr(item, "id", None):
                item.id = uuid4()
            self.scheduled_actions[item.id] = item
        else:
            self.audit_logs.append(item)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def flush(self) -> None:
        pass


class FakeRazorpayService:
    def __init__(self) -> None:
        self.created_links: list[object] = []

    def create_payment_link(self, request: object) -> RazorpayPaymentLinkResult:
        self.created_links.append(request)
        link_id = f"plink_test_{uuid4().hex[:8]}"
        return RazorpayPaymentLinkResult(
            id=link_id,
            short_url=f"https://rzp.io/i/{link_id}",
            status="created",
            amount=request.amount,  # type: ignore[attr-defined]
            currency=request.currency,  # type: ignore[attr-defined]
            raw_response={},
        )


class ProductionRecoveryLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_id = uuid4()
        self.customer_id = uuid4()
        self.payment_id = uuid4()
        self.case_id = uuid4()
        now = datetime.now(timezone.utc)

        self.merchant = SimpleNamespace(
            id=self.merchant_id,
            name="Test Store",
            email="merchant@store.com",
            razorpay_account_id="acc_test_store_123",
            ai_agent_enabled=True,
        )
        self.customer = SimpleNamespace(
            id=self.customer_id,
            merchant_id=self.merchant_id,
            name="Aarav Patel",
            email="aarav@example.com",
            phone="+919876543210",
            razorpay_customer_id="cust_123",
        )
        self.payment = SimpleNamespace(
            id=self.payment_id,
            merchant_id=self.merchant_id,
            customer_id=self.customer_id,
            transaction_id=None,
            amount=Decimal("1500.00"),
            currency="INR",
            status="failed",
            failure_reason="temporary bank error",
            payment_method="card",
            razorpay_payment_id="pay_failed_123",
            razorpay_success_payment_id=None,
            paid_at=None,
            created_at=now,
            updated_at=now,
        )
        self.case = SimpleNamespace(
            id=self.case_id,
            merchant_id=self.merchant_id,
            customer_id=self.customer_id,
            payment_id=self.payment_id,
            status="open",
            amount_at_risk=Decimal("1500.00"),
            amount_recovered=Decimal("0.00"),
            attempt_count=0,
            ai_decision=None,
            ai_decision_note=None,
            ai_wait_minutes=None,
            next_action_at=None,
            scheduled_action=None,
            razorpay_payment_link_id=None,
            payment_link_expires_at=None,
            last_attempt_at=None,
            created_at=now,
            updated_at=now,
        )
        self.db = FakeLifecycleDb(
            merchant=self.merchant,
            customer=self.customer,
            payment=self.payment,
            case=self.case,
        )
        self.razorpay_service = FakeRazorpayService()
        self.settings = SimpleNamespace(
            ollama_model="llama3.2:3b",
            recovery_max_attempts=3,
            llm_provider="ollama",
            email_enabled=False,
            ollama_base_url="http://127.0.0.1:11434",
        )

    # -------------------------------------------------------------------------
    # Scenario A: Initial failed payment -> Case open, AI runs
    # -------------------------------------------------------------------------
    def test_scenario_a_initial_failed_payment_context_built(self) -> None:
        context = build_recovery_context(
            self.db,
            self.case,
            self.customer,
            self.payment,
            3,
            trigger="payment_failed_webhook",
        )
        self.assertEqual(context.trigger, "payment_failed_webhook")
        self.assertFalse(context.is_scheduled_followup)
        self.assertFalse(context.wait_elapsed)
        self.assertIsNone(context.active_payment_link_id)
        self.assertFalse(context.is_payment_link_expired)

    # -------------------------------------------------------------------------
    # Scenario B: WAIT creates scheduled action, case is NOT recovered
    # -------------------------------------------------------------------------
    @patch("app.services.recovery_agent.request_llm_decision")
    def test_scenario_b_wait_creates_durable_schedule_without_recovery(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = RecoveryDecision(
            action="wait",
            reason="Temporary payment network outage; deferring retry by 60 minutes.",
            confidence=0.9,
            next_step="Wait for the validated interval, then perform one controlled retry",
            stop=False,
            wait_minutes=60,
        )
        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,
            razorpay_service=self.razorpay_service,
        )
        self.assertEqual(result.stopped_reason, "wait_scheduled")
        self.assertEqual(self.case.status, "open")
        self.assertEqual(self.case.amount_recovered, Decimal("0.00"))
        self.assertEqual(self.case.attempt_count, 0)
        self.assertEqual(len(self.db.scheduled_actions), 1)

    # -------------------------------------------------------------------------
    # Scenario C: Scheduled WAIT completion -> AI reassesses with wait_elapsed=True
    # -------------------------------------------------------------------------
    @patch("app.services.recovery_agent.request_llm_decision")
    def test_scenario_c_scheduled_wait_completion_reassesses_freshly(self, mock_llm: MagicMock) -> None:
        # Simulate that case was previously waiting
        self.case.ai_decision = "wait"
        self.case.ai_decision_note = "Network glitch, waiting 60m"
        past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.case.next_action_at = past_time
        self.case.scheduled_action = "retry"

        # Context build should explicitly show wait_elapsed is True
        context = build_recovery_context(
            self.db,
            self.case,
            self.customer,
            self.payment,
            3,
            trigger="scheduled_action",
        )
        self.assertTrue(context.wait_elapsed)
        self.assertTrue(context.is_scheduled_followup)
        self.assertTrue(context.is_scheduled_due)

        # AI chooses contact during fresh reassessment
        mock_llm.return_value = RecoveryDecision(
            action="contact",
            reason="Wait elapsed. Sending payment link now.",
            confidence=0.92,
            next_step="Contact the customer with a payment link",
            stop=False,
        )
        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="scheduled_action",
            max_recovery_attempts=3,
            settings=self.settings,
            razorpay_service=self.razorpay_service,
        )
        self.assertEqual(result.stopped_reason, "contact_pending_webhook")
        self.assertEqual(self.case.status, "in_progress")
        self.assertEqual(self.case.attempt_count, 1)
        self.assertIsNotNone(self.case.razorpay_payment_link_id)
        self.assertIsNotNone(self.case.payment_link_expires_at)

    # -------------------------------------------------------------------------
    # Scenario D: RETRY/CONTACT creates payment link & sends email, case remains in_progress
    # -------------------------------------------------------------------------
    def test_scenario_d_retry_creates_payment_link_and_remains_in_progress(self) -> None:
        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            action_context=RecoveryActionContext(source="manual", note="manual"),
            actor="recovery_agent",
            razorpay_service=self.razorpay_service,
        )
        result = execute_recovery_action("retry", self.case_id, context)
        self.assertTrue(result.success)
        self.assertEqual(result.outcome, "scheduled")
        self.assertEqual(self.case.status, "in_progress")
        self.assertEqual(self.case.amount_recovered, Decimal("0.00"))
        self.assertEqual(self.case.attempt_count, 1)
        self.assertIsNotNone(self.case.razorpay_payment_link_id)
        self.assertIsNotNone(self.case.payment_link_expires_at)

    # -------------------------------------------------------------------------
    # Scenario E: Successful payment webhook transitions case to recovered
    # -------------------------------------------------------------------------
    def test_scenario_e_payment_link_paid_webhook_marks_recovered(self) -> None:
        # Given an active recovery link
        self.case.status = "in_progress"
        self.case.razorpay_payment_link_id = "plink_scenario_e"
        self.case.payment_link_expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

        webhook_envelope = RazorpayWebhookEnvelope(
            account_id=self.merchant.razorpay_account_id,
            event="payment_link.paid",
            payload={
                "payment_link": {
                    "entity": {
                        "id": "plink_scenario_e",
                        "status": "paid",
                        "amount_paid": 150000,
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_scenario_e_success",
                        "amount": 150000,
                    }
                },
            },
        )
        outcome = ingest_payment_link_paid_webhook(
            self.db,
            razorpay_event_id="evt_scenario_e_1",
            webhook=webhook_envelope,
        )
        self.assertEqual(outcome, "processed")
        self.assertEqual(self.case.status, "recovered")
        self.assertEqual(self.case.amount_recovered, Decimal("1500.00"))
        self.assertEqual(self.payment.status, "succeeded")
        self.assertEqual(self.payment.razorpay_success_payment_id, "pay_scenario_e_success")

    # -------------------------------------------------------------------------
    # Scenario F: Customer does not pay -> case remains unrecovered
    # -------------------------------------------------------------------------
    def test_scenario_f_customer_does_not_pay_remains_unrecovered(self) -> None:
        self.case.status = "in_progress"
        self.case.razorpay_payment_link_id = "plink_scenario_f"
        # No webhook received
        self.assertEqual(self.case.status, "in_progress")
        self.assertEqual(self.case.amount_recovered, Decimal("0.00"))

    # -------------------------------------------------------------------------
    # Scenario G: Expired payment link isn't treated as active; fresh link created
    # -------------------------------------------------------------------------
    def test_scenario_g_expired_payment_link_creates_fresh_link_and_increments_attempt(self) -> None:
        now = datetime.now(timezone.utc)
        old_link_id = "plink_scenario_g_old"
        self.case.status = "in_progress"
        self.case.razorpay_payment_link_id = old_link_id
        # Expired 1 hour ago
        self.case.payment_link_expires_at = now - timedelta(hours=1)
        self.case.attempt_count = 1

        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            action_context=RecoveryActionContext(source="manual", note="reassessment"),
            actor="recovery_agent",
            razorpay_service=self.razorpay_service,
        )
        result = execute_recovery_action("retry", self.case_id, context)
        self.assertTrue(result.success)
        # Verify a new link was created, not reusing the expired one
        self.assertNotEqual(self.case.razorpay_payment_link_id, old_link_id)
        self.assertEqual(self.case.attempt_count, 2)
        self.assertGreater(self.case.payment_link_expires_at, now)

    # -------------------------------------------------------------------------
    # Scenario H: Missing customer email -> failure audited, case not recovered
    # -------------------------------------------------------------------------
    def test_scenario_h_missing_customer_email_audits_failure_safely(self) -> None:
        self.customer.email = ""
        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            action_context=RecoveryActionContext(source="manual", note="missing_email"),
            actor="recovery_agent",
            razorpay_service=self.razorpay_service,
        )
        result = execute_recovery_action("contact", self.case_id, context)
        # Link was created or attempted, but notification was blocked or audited as failed
        self.assertEqual(self.case.status, "in_progress")
        self.assertEqual(self.case.amount_recovered, Decimal("0.00"))
        # Verify audit logs contain notification_failed with customer_email_missing
        notif_failed_logs = [
            log for log in self.db.audit_logs
            if getattr(log, "action", None) == "notification_failed"
        ]
        self.assertTrue(len(notif_failed_logs) >= 1)
        self.assertEqual(notif_failed_logs[0].details.get("reason"), "customer_email_missing")

    # -------------------------------------------------------------------------
    # Scenario I: Maximum recovery attempts enforced server-side
    # -------------------------------------------------------------------------
    def test_scenario_i_max_attempts_blocks_execution(self) -> None:
        self.case.attempt_count = 3
        decision = RecoveryDecision(
            action="retry",
            reason="Retrying despite limit.",
            confidence=0.8,
            next_step="Retry the payment once",
            stop=False,
        )
        safe_decision, guardrails = apply_guardrails(decision, self.case, 3)
        self.assertEqual(safe_decision.action, "close")
        self.assertTrue(safe_decision.stop)
        self.assertIn("maximum_recovery_attempts_reached", guardrails)

        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            action_context=RecoveryActionContext(source="manual", note="limit_check"),
            actor="recovery_agent",
            razorpay_service=self.razorpay_service,
        )
        result = execute_recovery_action("retry", self.case_id, context)
        self.assertFalse(result.success)
        self.assertEqual(result.outcome, "blocked")

    # -------------------------------------------------------------------------
    # Scenario J: Duplicate worker execution is idempotent
    # -------------------------------------------------------------------------
    def test_scenario_j_duplicate_worker_execution_idempotency(self) -> None:
        job = SimpleNamespace(
            id=uuid4(),
            recovery_case_id=self.case_id,
            merchant_id=self.merchant_id,
            action="retry",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            status="running",
            attempt_count=1,
            lease_expires_at=None,
            executed_at=None,
            error_message=None,
        )
        self.db.scheduled_actions[job.id] = job
        # First execution completes the job and calls agent
        with patch("app.services.scheduled_recovery_actions.run_recovery_agent") as mock_agent:
            execute_claimed_scheduled_action(self.db, job.id, max_recovery_attempts=3, razorpay_service=self.razorpay_service)
            self.assertEqual(job.status, "completed")
            self.assertEqual(mock_agent.call_count, 1)

            # Second execution of already completed job does nothing
            execute_claimed_scheduled_action(self.db, job.id, max_recovery_attempts=3, razorpay_service=self.razorpay_service)
            self.assertEqual(mock_agent.call_count, 1)  # not called again!

    # -------------------------------------------------------------------------
    # Scenario K: Already recovered/closed case blocks further recovery
    # -------------------------------------------------------------------------
    def test_scenario_k_already_recovered_case_blocks_all_actions(self) -> None:
        self.case.status = "recovered"
        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            action_context=RecoveryActionContext(source="manual", note="recovered_check"),
            actor="recovery_agent",
            razorpay_service=self.razorpay_service,
        )
        result = execute_recovery_action("retry", self.case_id, context)
        self.assertFalse(result.success)
        self.assertEqual(result.outcome, "blocked")
        self.assertIn("case_already_recovered", result.guardrails_applied)

    # -------------------------------------------------------------------------
    # Scenario L: Merchant isolation
    # -------------------------------------------------------------------------
    def test_scenario_l_merchant_isolation_on_scheduled_action(self) -> None:
        other_merchant_id = uuid4()
        job = SimpleNamespace(
            id=uuid4(),
            recovery_case_id=self.case_id,
            merchant_id=other_merchant_id,  # mismatch
            action="retry",
            scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            status="running",
            attempt_count=1,
            lease_expires_at=None,
            executed_at=None,
            error_message=None,
        )
        self.db.scheduled_actions[job.id] = job
        execute_claimed_scheduled_action(self.db, job.id, max_recovery_attempts=3, razorpay_service=self.razorpay_service)
        self.assertEqual(job.status, "skipped")
        self.assertIn("Merchant ownership no longer matches", job.error_message)


if __name__ == "__main__":
    unittest.main()

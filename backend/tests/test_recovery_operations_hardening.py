"""Tests for production hardening and recovery operations across Scenarios A through P.

Scenarios:
- Scenario A: Full state machine happy path (open -> in_progress -> waiting -> in_progress -> payment_link_active -> recovered)
- Scenario B: Invalid state transitions rejected with InvalidStateTransitionError
- Scenario C: Terminal state immutability (recovered and closed are permanently terminal)
- Scenario D: Payment link creation sets payment_link_active, increments attempt_count, records timestamps
- Scenario E: Reusing active payment link does NOT increment attempt_count, updates payment_link_sent_at
- Scenario F: Retrying expired payment link creates fresh link and increments attempt_count
- Scenario G: Notification failure audits notification_failed, leaves link valid, case not recovered
- Scenario H: Razorpay webhook signature verification (valid HMAC succeeds, tampered fails)
- Scenario I: Webhook idempotency (duplicate payment_link.paid does not double-count amount)
- Scenario J: Webhook rejection on customer mismatch or corrupted payload
- Scenario K: Scheduled recovery worker atomic claiming, lease timeout handling, and crash recovery
- Scenario L: Scheduled recovery worker skips recovered, closed, and max-attempt cases
- Scenario M: Scheduled worker on wait expiry transitions case to in_progress and triggers reassessment with wait_elapsed=True
- Scenario N: AI decision context includes full history, notification delivery status, preventing infinite wait loops
- Scenario O: Max attempts enforcement (attempt_count >= 3 strictly forces case closure)
- Scenario P: Strict merchant isolation across all recovery operations, customer data, and dashboard queries
"""

import hashlib
import hmac
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from pydantic import SecretStr

from app.api.v1.customers import get_customer_details
from app.api.v1.recovery_cases import list_recovery_cases
from app.core.config import Settings
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.razorpay import RazorpayPaymentLinkResult
from app.schemas.recovery_actions import RecoveryActionContext
from app.schemas.recovery_analysis import RecoveryDecision, RecoveryDecisionContext
from app.services.notification_service import NotificationResult, NotificationService
from app.services.razorpay_service import RazorpayService
from app.services.razorpay_webhooks import (
    RazorpayWebhookEnvelope,
    ingest_payment_link_paid_webhook,
)
from app.services.recovery_actions import (
    RecoveryActionExecutionContext,
    execute_recovery_action,
)
from app.services.recovery_decision import apply_guardrails, build_recovery_context
from app.services.recovery_state_machine import (
    InvalidStateTransitionError,
    RecoveryCaseStatus,
    RecoveryState,
    is_valid_transition,
    transition_case_status,
)
from app.services.scheduled_recovery_actions import (
    claim_due_scheduled_actions,
    execute_claimed_scheduled_action,
)


class FakeDbSession:
    def __init__(self, *, merchant=None, customer=None, payment=None, case=None) -> None:
        self.merchants = {merchant.id: merchant} if merchant else {}
        self.customers = {customer.id: customer} if customer else {}
        self.payments = {payment.id: payment} if payment else {}
        self.cases = {case.id: case} if case else {}
        self.scheduled_actions: dict[object, object] = {}
        self.audit_logs: list[object] = []
        self.webhook_events: set[str] = set()
        self.commits = 0

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
        pass

    def execute(self, statement: object) -> object:
        mock_result = MagicMock()
        try:
            params = statement.compile().params  # type: ignore[attr-defined]
            event_id = params.get("razorpay_event_id")
            if event_id:
                if event_id in self.webhook_events:
                    mock_result.scalar_one_or_none.return_value = None
                    return mock_result
                self.webhook_events.add(event_id)
                mock_result.scalar_one_or_none.return_value = event_id
                return mock_result
        except Exception:
            pass
        mock_result.scalar_one_or_none.return_value = "ok"
        mock_result.all.return_value = []
        return mock_result

    def scalars(self, stmt: object) -> object:
        mock_result = MagicMock()
        mock_result.all.return_value = list(self.audit_logs)
        return mock_result

    def scalar(self, stmt: object) -> object | None:
        stmt_str = str(stmt)
        if "recovery_cases" in stmt_str:
            for c in self.cases.values():
                return c
        if "merchants" in stmt_str:
            for m in self.merchants.values():
                return m
        if "customers" in stmt_str:
            for cust in self.customers.values():
                return cust
        if "payments" in stmt_str:
            for p in self.payments.values():
                return p
        return None


class RecoveryOperationsHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_id = uuid4()
        self.merchant = Merchant(
            id=self.merchant_id,
            name="Apex Retailers",
            email="ops@apex.com",
            razorpay_account_id="acc_apex_001",
            ai_agent_enabled=True,
        )
        self.customer = Customer(
            id=uuid4(),
            merchant_id=self.merchant_id,
            name="Rahul Varma",
            email="rahul@example.com",
            phone="+919876543210",
        )
        self.payment = Payment(
            id=uuid4(),
            merchant_id=self.merchant_id,
            customer_id=self.customer.id,
            amount=Decimal("1500.00"),
            currency="INR",
            status="failed",
            razorpay_payment_id="pay_apex_test_001",
            created_at=datetime.now(timezone.utc),
        )
        self.case = RecoveryCase(
            id=uuid4(),
            merchant_id=self.merchant_id,
            customer_id=self.customer.id,
            payment_id=self.payment.id,
            status=RecoveryState.OPEN,
            amount_at_risk=Decimal("1500.00"),
            amount_recovered=Decimal("0.00"),
            attempt_count=0,
            created_at=datetime.now(timezone.utc),
        )
        self.db = FakeDbSession(
            merchant=self.merchant,
            customer=self.customer,
            payment=self.payment,
            case=self.case,
        )

    # =========================================================================
    # Scenario A: Full state machine happy path
    # =========================================================================
    def test_scenario_a_state_machine_happy_path(self) -> None:
        case = self.case
        self.assertEqual(case.status, RecoveryState.OPEN)

        # open -> in_progress
        transition_case_status(case, RecoveryState.IN_PROGRESS)
        self.assertEqual(case.status, RecoveryState.IN_PROGRESS)

        # in_progress -> waiting
        transition_case_status(case, RecoveryState.WAITING)
        self.assertEqual(case.status, RecoveryState.WAITING)

        # waiting -> in_progress (e.g. wait expired)
        transition_case_status(case, RecoveryState.IN_PROGRESS)
        self.assertEqual(case.status, RecoveryState.IN_PROGRESS)

        # in_progress -> payment_link_active
        transition_case_status(case, RecoveryState.PAYMENT_LINK_ACTIVE)
        self.assertEqual(case.status, RecoveryState.PAYMENT_LINK_ACTIVE)

        # payment_link_active -> recovered (webhook arrives)
        transition_case_status(case, RecoveryState.RECOVERED)
        self.assertEqual(case.status, RecoveryState.RECOVERED)

    # =========================================================================
    # Scenario B: Invalid state transitions rejected
    # =========================================================================
    def test_scenario_b_invalid_state_transitions_rejected(self) -> None:
        # open -> recovered directly is forbidden
        case = RecoveryCase(
            id=uuid4(),
            merchant_id=self.merchant_id,
            customer_id=self.customer.id,
            payment_id=self.payment.id,
            status=RecoveryState.OPEN,
            amount_at_risk=Decimal("1500.00"),
            amount_recovered=Decimal("0.00"),
        )
        with self.assertRaises(InvalidStateTransitionError) as ctx:
            transition_case_status(case, RecoveryState.RECOVERED)
        self.assertIn("Invalid state transition", str(ctx.exception))

        # closed -> in_progress is forbidden
        case.status = RecoveryState.CLOSED
        with self.assertRaises(InvalidStateTransitionError):
            transition_case_status(case, RecoveryState.IN_PROGRESS)

        # recovered -> in_progress is forbidden
        case.status = RecoveryState.RECOVERED
        with self.assertRaises(InvalidStateTransitionError):
            transition_case_status(case, RecoveryState.IN_PROGRESS)

    # =========================================================================
    # Scenario C: Terminal state immutability
    # =========================================================================
    def test_scenario_c_terminal_state_immutability(self) -> None:
        case = self.case
        case.status = RecoveryState.RECOVERED

        # Recovered cannot transition anywhere
        for target in [
            RecoveryState.OPEN,
            RecoveryState.IN_PROGRESS,
            RecoveryState.WAITING,
            RecoveryState.PAYMENT_LINK_ACTIVE,
            RecoveryState.CLOSED,
        ]:
            self.assertFalse(is_valid_transition(RecoveryState.RECOVERED, target))
            with self.assertRaises(InvalidStateTransitionError):
                transition_case_status(case, target)

        # Closed cannot transition anywhere
        case.status = RecoveryState.CLOSED
        for target in [
            RecoveryState.OPEN,
            RecoveryState.IN_PROGRESS,
            RecoveryState.WAITING,
            RecoveryState.PAYMENT_LINK_ACTIVE,
            RecoveryState.RECOVERED,
        ]:
            self.assertFalse(is_valid_transition(RecoveryState.CLOSED, target))
            with self.assertRaises(InvalidStateTransitionError):
                transition_case_status(case, target)

    # =========================================================================
    # Scenario D: Payment link creation sets timestamps and increments attempts
    # =========================================================================
    def test_scenario_d_payment_link_creation_lifecycle(self) -> None:
        case = self.case
        case.status = RecoveryState.IN_PROGRESS
        mock_razorpay = MagicMock()
        mock_notification = MagicMock()

        mock_razorpay.create_payment_link.return_value = SimpleNamespace(
            id="plink_new_123",
            short_url="https://rzp.io/i/plink_new_123",
            amount=Decimal("1500.00"),
            currency="INR",
            status="created",
            raw_response={},
        )
        mock_notification.send_recovery_payment_email.return_value = NotificationResult(
            success=True,
            status="sent",
        )

        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=mock_razorpay,
            notification_service=mock_notification,
        )

        result = execute_recovery_action("retry", case.id, context)
        self.assertTrue(result.success)
        self.assertEqual(case.attempt_count, 1)
        self.assertEqual(case.razorpay_payment_link_id, "plink_new_123")
        self.assertIsNotNone(case.payment_link_created_at)
        self.assertIsNotNone(case.payment_link_sent_at)
        self.assertIsNotNone(case.last_attempt_at)
        self.assertEqual(case.status, RecoveryState.PAYMENT_LINK_ACTIVE)
        self.assertEqual(case.amount_recovered, Decimal("0.00"))

    # =========================================================================
    # Scenario E: Reusing active payment link does NOT increment attempt_count
    # =========================================================================
    def test_scenario_e_reusing_active_payment_link_no_increment(self) -> None:
        case = self.case
        case.status = RecoveryState.PAYMENT_LINK_ACTIVE
        case.attempt_count = 1
        case.razorpay_payment_link_id = "plink_active_existing"
        case.payment_link_status = "created"
        case.payment_link_created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        case.payment_link_expires_at = datetime.now(timezone.utc) + timedelta(hours=22)

        mock_razorpay = MagicMock()
        mock_notification = MagicMock()
        mock_notification.send_recovery_payment_email.return_value = NotificationResult(
            success=True,
            status="sent",
        )

        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=mock_razorpay,
            notification_service=mock_notification,
        )

        result = execute_recovery_action("contact", case.id, context)
        self.assertTrue(result.success)
        mock_razorpay.create_payment_link.assert_not_called()
        self.assertEqual(case.attempt_count, 1, "Attempt count must NOT increment on link reuse")
        self.assertIsNotNone(case.payment_link_sent_at)
        self.assertEqual(case.status, RecoveryState.PAYMENT_LINK_ACTIVE)

    # =========================================================================
    # Scenario F: Retrying expired payment link creates fresh link & increments
    # =========================================================================
    def test_scenario_f_retrying_expired_payment_link(self) -> None:
        case = self.case
        case.status = RecoveryState.PAYMENT_LINK_ACTIVE
        case.attempt_count = 1
        case.razorpay_payment_link_id = "plink_old_expired"
        case.payment_link_status = "created"
        # Expired 1 hour ago
        case.payment_link_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

        mock_razorpay = MagicMock()
        mock_notification = MagicMock()

        mock_razorpay.create_payment_link.return_value = SimpleNamespace(
            id="plink_fresh_456",
            short_url="https://rzp.io/i/plink_fresh_456",
            amount=Decimal("1500.00"),
            currency="INR",
            status="created",
            raw_response={},
        )
        mock_notification.send_recovery_payment_email.return_value = NotificationResult(
            success=True,
            status="sent",
        )

        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=mock_razorpay,
            notification_service=mock_notification,
        )

        result = execute_recovery_action("retry", case.id, context)
        self.assertTrue(result.success)
        mock_razorpay.create_payment_link.assert_called_once()
        self.assertEqual(case.attempt_count, 2, "Attempt count must increment on fresh link")
        self.assertEqual(case.razorpay_payment_link_id, "plink_fresh_456")

    # =========================================================================
    # Scenario G: Notification failure audits failure, link valid, NOT recovered
    # =========================================================================
    def test_scenario_g_notification_failure_handling(self) -> None:
        case = self.case
        case.status = RecoveryState.IN_PROGRESS
        mock_razorpay = MagicMock()
        mock_notification = MagicMock()

        mock_razorpay.create_payment_link.return_value = SimpleNamespace(
            id="plink_notif_fail",
            short_url="https://rzp.io/i/plink_notif_fail",
            amount=Decimal("1500.00"),
            currency="INR",
            status="created",
            raw_response={},
        )
        # Notification fails due to SMTP/network issue
        mock_notification.send_recovery_payment_email.return_value = NotificationResult(
            success=False,
            status="failed",
            reason="smtp_failure",
        )

        context = RecoveryActionExecutionContext(
            db=self.db,
            max_recovery_attempts=3,
            razorpay_service=mock_razorpay,
            notification_service=mock_notification,
        )

        result = execute_recovery_action("contact", case.id, context)
        self.assertTrue(result.success)
        # Link was still created and remains active
        self.assertEqual(case.razorpay_payment_link_id, "plink_notif_fail")
        self.assertEqual(case.status, RecoveryState.PAYMENT_LINK_ACTIVE)
        # Invariant: Not recovered!
        self.assertEqual(case.amount_recovered, Decimal("0.00"))
        self.assertNotEqual(case.status, RecoveryState.RECOVERED)

    # =========================================================================
    # Scenario H: Webhook signature verification
    # =========================================================================
    def test_scenario_h_webhook_signature_verification(self) -> None:
        secret = "super_secret_webhook_key_123"
        settings = Settings(
            razorpay_webhook_secret=SecretStr(secret),
            database_url="sqlite:///:memory:",
            secret_key=SecretStr("test-secret-key-that-is-at-least-32-chars-long"),
        )
        service = RazorpayService(settings)

        payload = b'{"event":"payment_link.paid","payload":{"payment_link":{"entity":{"id":"plink_1"}}}}'
        valid_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        self.assertTrue(service.verify_webhook_signature(payload, valid_sig))
        self.assertFalse(service.verify_webhook_signature(payload, "invalid_or_tampered_sig"))
        self.assertFalse(service.verify_webhook_signature(payload + b"tampered", valid_sig))

    # =========================================================================
    # Scenario I: Webhook idempotency (no double-counting)
    # =========================================================================
    def test_scenario_i_webhook_idempotency_no_double_counting(self) -> None:
        case = self.case
        case.status = RecoveryState.PAYMENT_LINK_ACTIVE
        case.razorpay_payment_link_id = "plink_idem_123"
        case.amount_recovered = Decimal("0.00")

        envelope = RazorpayWebhookEnvelope(
            entity="event",
            account_id="acc_apex_001",
            event="payment_link.paid",
            contains=["payment_link", "payment"],
            payload={
                "payment_link": {
                    "entity": {
                        "id": "plink_idem_123",
                        "status": "paid",
                        "amount_paid": 150000,
                        "customer": {
                            "email": self.customer.email,
                        },
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_settled_123",
                        "status": "captured",
                        "amount": 150000,
                    }
                },
            },
        )

        # Delivery 1: Case recovers
        status1 = ingest_payment_link_paid_webhook(self.db, razorpay_event_id="evt_idem_1", webhook=envelope)
        self.assertEqual(status1, "processed")
        self.assertEqual(case.status, RecoveryState.RECOVERED)
        self.assertEqual(case.amount_recovered, Decimal("1500.00"))
        self.assertIsNotNone(case.payment_link_paid_at)

        # Delivery 2: Duplicate delivery should be idempotent
        status2 = ingest_payment_link_paid_webhook(self.db, razorpay_event_id="evt_idem_1", webhook=envelope)
        self.assertEqual(status2, "duplicate")
        self.assertEqual(case.status, RecoveryState.RECOVERED)
        # CRITICAL: Amount recovered remains exactly 1500.00, NOT 3000.00
        self.assertEqual(case.amount_recovered, Decimal("1500.00"))

    # =========================================================================
    # Scenario J: Webhook rejection on customer mismatch
    # =========================================================================
    def test_scenario_j_webhook_rejection_on_customer_mismatch(self) -> None:
        case = self.case
        case.status = RecoveryState.PAYMENT_LINK_ACTIVE
        case.razorpay_payment_link_id = "plink_mismatch_123"

        # Payload contains an imposter email
        envelope = RazorpayWebhookEnvelope(
            entity="event",
            account_id="acc_apex_001",
            event="payment_link.paid",
            contains=["payment_link", "payment"],
            payload={
                "payment_link": {
                    "entity": {
                        "id": "plink_mismatch_123",
                        "status": "paid",
                        "amount_paid": 150000,
                        "customer": {
                            "email": "imposter@attacker.com",
                        },
                    }
                },
                "payment": {
                    "entity": {
                        "id": "pay_mismatch_123",
                        "status": "captured",
                        "amount": 150000,
                    }
                },
            },
        )

        status = ingest_payment_link_paid_webhook(self.db, razorpay_event_id="evt_mismatch_1", webhook=envelope)
        self.assertEqual(status, "ignored")
        # Invariant preserved: case is not recovered
        self.assertEqual(case.amount_recovered, Decimal("0.00"))
        self.assertNotEqual(case.status, RecoveryState.RECOVERED)

    # =========================================================================
    # Scenario K: Scheduled recovery worker atomic claiming & lease timeout
    # =========================================================================
    def test_scenario_k_scheduled_worker_atomic_claiming_and_lease(self) -> None:
        now = datetime.now(timezone.utc)

        # Stale action running past its lease timeout
        stale_action = ScheduledRecoveryAction(
            id=uuid4(),
            merchant_id=self.merchant_id,
            recovery_case_id=self.case.id,
            action="wait",
            status="running",
            scheduled_at=now - timedelta(minutes=30),
            lease_expires_at=now - timedelta(minutes=5),  # lease expired
            attempt_count=1,
        )

        mock_db = MagicMock()
        mock_query = mock_db.scalars.return_value
        mock_query.all.return_value = [stale_action]

        job_ids = claim_due_scheduled_actions(mock_db)

        self.assertEqual(job_ids, [stale_action.id])
        self.assertEqual(stale_action.status, "running")
        self.assertEqual(stale_action.attempt_count, 2)
        self.assertGreater(stale_action.lease_expires_at, now)

    # =========================================================================
    # Scenario L: Scheduled recovery worker skips recovered/closed/max-attempts
    # =========================================================================
    def test_scenario_l_scheduled_worker_skips_ineligible_cases(self) -> None:
        now = datetime.now(timezone.utc)

        # Test with recovered case
        self.case.status = RecoveryState.RECOVERED
        action_recovered = ScheduledRecoveryAction(
            id=uuid4(),
            merchant_id=self.merchant_id,
            recovery_case_id=self.case.id,
            action="wait",
            status="running",
            scheduled_at=now - timedelta(minutes=5),
        )

        mock_db = MagicMock()
        def mock_get(model, obj_id):
            if model is ScheduledRecoveryAction and obj_id == action_recovered.id:
                return action_recovered
            if model is RecoveryCase and obj_id == self.case.id:
                return self.case
            return None
        mock_db.get.side_effect = mock_get

        execute_claimed_scheduled_action(
            mock_db,
            action_recovered.id,
            max_recovery_attempts=3,
            now=now,
        )
        self.assertEqual(action_recovered.status, "skipped")
        self.assertIn("already recovered", action_recovered.error_message)

        # Test with max-attempts reached
        self.case.status = RecoveryState.IN_PROGRESS
        self.case.attempt_count = 3
        action_max_attempts = ScheduledRecoveryAction(
            id=uuid4(),
            merchant_id=self.merchant_id,
            recovery_case_id=self.case.id,
            action="wait",
            status="running",
            scheduled_at=now - timedelta(minutes=5),
        )

        def mock_get2(model, obj_id):
            if model is ScheduledRecoveryAction and obj_id == action_max_attempts.id:
                return action_max_attempts
            if model is RecoveryCase and obj_id == self.case.id:
                return self.case
            return None
        mock_db.get.side_effect = mock_get2

        execute_claimed_scheduled_action(
            mock_db,
            action_max_attempts.id,
            max_recovery_attempts=3,
            now=now,
        )
        self.assertEqual(action_max_attempts.status, "skipped")
        self.assertIn("Maximum recovery attempts", action_max_attempts.error_message)

    # =========================================================================
    # Scenario M: Scheduled worker on wait expiry transitions to in_progress
    # =========================================================================
    @patch("app.services.scheduled_recovery_actions.run_recovery_agent")
    def test_scenario_m_wait_expiry_transitions_to_in_progress_and_reassesses(
        self, mock_run_agent: MagicMock
    ) -> None:
        self.case.status = RecoveryState.WAITING
        self.case.attempt_count = 1
        now = datetime.now(timezone.utc)

        action = ScheduledRecoveryAction(
            id=uuid4(),
            merchant_id=self.merchant_id,
            recovery_case_id=self.case.id,
            action="wait",
            status="running",
            scheduled_at=now - timedelta(minutes=5),
        )

        mock_db = MagicMock()
        def mock_get(model, obj_id):
            if model is ScheduledRecoveryAction and obj_id == action.id:
                return action
            if model is RecoveryCase and obj_id == self.case.id:
                return self.case
            if model is Merchant and obj_id == self.merchant.id:
                return self.merchant
            return None
        mock_db.get.side_effect = mock_get

        execute_claimed_scheduled_action(
            mock_db,
            action.id,
            max_recovery_attempts=3,
            now=now,
        )

        self.assertEqual(action.status, "completed")
        mock_run_agent.assert_called_once()
        _, kwargs = mock_run_agent.call_args
        self.assertEqual(kwargs.get("trigger"), "scheduled_action")
        # Verify case was transitioned from waiting to in_progress before agent evaluation
        self.assertEqual(str(self.case.status), "in_progress")

    # =========================================================================
    # Scenario N: AI decision context and loop guardrails
    # =========================================================================
    def test_scenario_n_ai_decision_context_and_guardrails(self) -> None:
        case = self.case
        case.status = RecoveryState.IN_PROGRESS
        case.attempt_count = 1

        mock_db = MagicMock()
        audit_mock = MagicMock()
        audit_mock.action = "notification_failed"
        audit_mock.actor = "notification_service"
        audit_mock.details = {"reason": "Invalid email host", "error_message": "Invalid email host"}
        audit_mock.created_at = datetime.now(timezone.utc)

        def mock_scalars(stmt: object) -> MagicMock:
            res = MagicMock()
            stmt_str = str(stmt)
            if "audit_logs" in stmt_str:
                res.all.return_value = [audit_mock]
            else:
                res.all.return_value = []
            return res

        mock_db.scalars.side_effect = mock_scalars

        context = build_recovery_context(
            db=mock_db,
            case=case,
            customer=self.customer,
            payment=self.payment,
            max_recovery_attempts=3,
            trigger="scheduled_action",
        )

        self.assertEqual(context.trigger, "scheduled_action")
        self.assertTrue(context.wait_elapsed)
        self.assertEqual(context.last_notification_status, "failed")
        self.assertEqual(context.last_notification_failure_reason, "Invalid email host")

        # Guardrail test: AI proposing 'wait' when wait_elapsed is True must be overridden to 'retry'
        bad_ai_decision = RecoveryDecision(
            action="wait",
            reason="Let us wait again",
            confidence=0.6,
            next_step="Wait for the validated interval, then perform one controlled retry",
            stop=False,
            wait_minutes=30,
        )
        safe_decision, guardrails = apply_guardrails(
            bad_ai_decision,
            case=case,
            max_recovery_attempts=3,
            context=context,
        )
        self.assertEqual(
            safe_decision.action,
            "retry",
            "Guardrails must prevent infinite wait loops after wait has already elapsed",
        )
        self.assertIn("infinite_wait_loop_prevented", guardrails)

    # =========================================================================
    # Scenario O: Max attempts enforcement (attempt_count >= 3 forces close)
    # =========================================================================
    def test_scenario_o_max_attempts_enforcement(self) -> None:
        case = self.case
        case.status = RecoveryState.IN_PROGRESS
        case.attempt_count = 3

        decision_retry = RecoveryDecision(
            action="retry",
            reason="Another retry attempt",
            confidence=0.9,
            next_step="Retry the payment once",
            stop=False,
        )

        safe_decision, guardrails = apply_guardrails(
            decision_retry,
            case=case,
            max_recovery_attempts=3,
        )
        self.assertEqual(
            safe_decision.action,
            "close",
            "Reaching max attempts must force action to close",
        )
        self.assertIn("maximum_recovery_attempts_reached", guardrails)

    # =========================================================================
    # Scenario P: Merchant isolation on cases, queries, and customer profiles
    # =========================================================================
    def test_scenario_p_merchant_isolation(self) -> None:
        other_merchant = Merchant(
            id=uuid4(),
            name="Competitor Store",
            email="ops@competitor.com",
            razorpay_account_id="acc_competitor",
        )

        mock_db = MagicMock()
        mock_db.execute.return_value.all.return_value = []

        # Calling list_recovery_cases scopes by current_merchant
        list_recovery_cases(
            db=mock_db,
            current_merchant=self.merchant,
        )
        # Verify db.execute was called and query constrained to current_merchant
        self.assertTrue(mock_db.execute.called)
        stmt = mock_db.execute.call_args[0][0]
        compiled = str(stmt.compile())
        self.assertIn("recovery_cases.merchant_id =", compiled)

        # Cross-tenant customer access check
        from fastapi import HTTPException
        # Querying customer belonging to self.merchant from other_merchant returns None -> 404
        mock_db.scalar.return_value = None

        with self.assertRaises(HTTPException) as ctx:
            get_customer_details(
                customer_id=self.customer.id,
                db=mock_db,
                current_merchant=other_merchant,  # unauthorized merchant
            )
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

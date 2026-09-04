import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.razorpay import RazorpayPaymentLinkResult
from app.schemas.recovery_analysis import RecoveryDecision
from app.services.recovery_agent import (
    LLMServiceError,
    RecoveryAgentResult,
    run_recovery_agent,
)


class FakeAgentSession:
    """In-memory session fake supporting get, scalar, scalars, add, commit, rollback."""

    def __init__(self, case: object, payment: object, customer: object | None) -> None:
        self.case = case
        self.payment = payment
        self.customer = customer
        self.audit_logs: list[object] = []
        self.scheduled_actions: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def get(self, model: object, object_id: object) -> object | None:
        if model is RecoveryCase and getattr(self.case, "id", None) == object_id:
            return self.case
        if model is Payment and getattr(self.payment, "id", None) == object_id:
            return self.payment
        if model is Customer and self.customer is not None and getattr(self.customer, "id", None) == object_id:
            return self.customer
        return None

    def scalar(self, statement: object) -> object | None:
        # Check if querying for ScheduledRecoveryAction
        stmt_str = str(statement)
        if "scheduled_recovery_actions" in stmt_str:
            for job in self.scheduled_actions:
                if getattr(job, "status", None) in ("pending", "running"):
                    return job
            return None
        return None

    def scalars(self, statement: object) -> object:
        mock_result = MagicMock()
        mock_result.all.return_value = []
        return mock_result

    def add(self, item: object) -> None:
        if item.__class__.__name__ == "ScheduledRecoveryAction":
            self.scheduled_actions.append(item)
        else:
            self.audit_logs.append(item)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeRazorpayServiceForAgent:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[object] = []

    def create_payment_link(self, request: object) -> RazorpayPaymentLinkResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return RazorpayPaymentLinkResult(
            id="plink_agent_test_999",
            short_url="https://rzp.io/i/agent-test",
            status="created",
            amount=request.amount,  # type: ignore[attr-defined]
            currency=request.currency,  # type: ignore[attr-defined]
            raw_response={},
        )


class AutonomousRecoveryAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case_id = uuid4()
        self.merchant_id = uuid4()
        self.customer_id = uuid4()
        self.payment_id = uuid4()
        now = datetime.now(timezone.utc)

        self.case = SimpleNamespace(
            id=self.case_id,
            merchant_id=self.merchant_id,
            payment_id=self.payment_id,
            customer_id=self.customer_id,
            amount_at_risk=Decimal("500.00"),
            amount_recovered=Decimal("0.00"),
            attempt_count=0,
            last_attempt_at=None,
            status="open",
            ai_decision=None,
            ai_decision_note=None,
            ai_wait_minutes=None,
            next_action_at=None,
            scheduled_action=None,
            razorpay_payment_link_id=None,
            created_at=now,
            updated_at=now,
        )

        self.payment = SimpleNamespace(
            id=self.payment_id,
            customer_id=self.customer_id,
            merchant_id=self.merchant_id,
            failure_reason="temporary bank error",
            amount=Decimal("500.00"),
            currency="INR",
            status="failed",
            paid_at=None,
            created_at=now,
            updated_at=now,
        )

        self.customer = SimpleNamespace(
            id=self.customer_id,
            merchant_id=self.merchant_id,
            name="Alice Agent",
            email="alice@example.com",
            phone="+919876543210",
        )

        self.db = FakeAgentSession(self.case, self.payment, self.customer)

        self.settings = SimpleNamespace(
            ollama_model="llama3.2:3b",
            recovery_max_attempts=3,
            llm_provider="ollama",
            ollama_base_url="http://127.0.0.1:11434",
        )

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_agent_starts_from_payment_failed_trigger_and_builds_fresh_context(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = RecoveryDecision(
            action="retry",
            reason="Customer has past successful payments and failure is transient network issue.",
            confidence=0.9,
            next_step="Retry the payment once",
            stop=False,
        )

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )

        self.assertEqual(result.stopped_reason, "recovered")
        self.assertEqual(result.cycles_executed, 1)
        self.assertEqual(result.final_action, "retry")
        self.assertEqual(result.final_outcome, "recovered")
        self.assertTrue(mock_llm.called)
        # Verify context built and passed to LLM
        context_arg = mock_llm.call_args[0][0]
        self.assertEqual(context_arg.case_id, self.case_id)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_ai_reasoning_reflects_context_not_just_failure_reason(self, mock_llm: MagicMock) -> None:
        good_reason = (
            "This customer has a history of successful payments and the current failure appears transient. "
            "There have been no prior recovery attempts, so an immediate customer message is unnecessary."
        )
        mock_llm.return_value = RecoveryDecision(
            action="wait",
            reason=good_reason,
            confidence=0.85,
            next_step="Wait for the validated interval, then perform one controlled retry",
            stop=False,
            wait_minutes=60,
        )

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )

        self.assertEqual(result.ai_reasoning, good_reason)
        self.assertNotIn("temporary bank error", result.ai_reasoning)
        self.assertIn("history of successful payments", result.ai_reasoning)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_contact_action_stops_and_waits_for_webhook(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = RecoveryDecision(
            action="contact",
            reason="Failure requires manual customer re-entry. Sending payment link.",
            confidence=0.8,
            next_step="Contact the customer with a payment link",
            stop=False,
        )
        razorpay_service = FakeRazorpayServiceForAgent()

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )

        self.assertEqual(result.stopped_reason, "contact_pending_webhook")
        self.assertEqual(result.cycles_executed, 1)
        self.assertEqual(result.final_action, "contact")
        self.assertEqual(self.case.razorpay_payment_link_id, "plink_agent_test_999")
        self.assertEqual(self.case.status, "in_progress")

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_wait_action_creates_durable_schedule_and_stops(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = RecoveryDecision(
            action="wait",
            reason="Banking network issue expected to resolve within 120 minutes.",
            confidence=0.88,
            next_step="Wait for the validated interval, then perform one controlled retry",
            stop=False,
            wait_minutes=120,
        )

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )

        self.assertEqual(result.stopped_reason, "wait_scheduled")
        self.assertEqual(result.cycles_executed, 1)
        self.assertEqual(result.final_action, "wait")
        self.assertEqual(self.case.scheduled_action, "retry")
        self.assertIsNotNone(self.case.next_action_at)
        self.assertEqual(len(self.db.scheduled_actions), 1)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_retry_recovered_result_stops_agent(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = RecoveryDecision(
            action="retry",
            reason="Transient network failure detected.",
            confidence=0.95,
            next_step="Retry the payment once",
            stop=False,
        )

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )

        self.assertEqual(result.stopped_reason, "recovered")
        self.assertEqual(self.case.status, "recovered")
        self.assertEqual(self.case.amount_recovered, Decimal("500.00"))

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_retry_failed_result_triggers_another_bounded_cycle_when_allowed(self, mock_llm: MagicMock) -> None:
        # First retry fails (non-transient failure reason), second analysis recommends contact
        self.payment.failure_reason = "insufficient funds"

        mock_llm.side_effect = [
            RecoveryDecision(
                action="retry",
                reason="Attempting retry for account failure.",
                confidence=0.7,
                next_step="Retry the payment once",
                stop=False,
            ),
            RecoveryDecision(
                action="contact",
                reason="First retry failed due to insufficient funds. Contacting customer with payment link.",
                confidence=0.9,
                next_step="Contact the customer with a payment link",
                stop=False,
            ),
        ]
        razorpay_service = FakeRazorpayServiceForAgent()

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )

        self.assertEqual(result.cycles_executed, 2)
        self.assertEqual(result.final_action, "contact")
        self.assertEqual(result.stopped_reason, "contact_pending_webhook")
        self.assertEqual(self.case.attempt_count, 2)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_maximum_attempts_stops_further_cycles(self, mock_llm: MagicMock) -> None:
        self.case.attempt_count = 3

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )

        self.assertEqual(result.stopped_reason, "max_attempts_reached")
        self.assertEqual(result.cycles_executed, 0)
        self.assertFalse(mock_llm.called)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_guardrails_override_unsafe_decisions_at_max_attempts(self, mock_llm: MagicMock) -> None:
        # Suppose attempt count is 2 (below max 3), LLM returns retry. Retry fails, attempt count becomes 3.
        # Next cycle guardrails kick in and force close.
        self.payment.failure_reason = "insufficient funds"
        mock_llm.return_value = RecoveryDecision(
            action="retry",
            reason="Retrying payment.",
            confidence=0.7,
            next_step="Retry the payment once",
            stop=False,
        )

        # Set max_recovery_attempts = 1
        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=1,
            settings=self.settings,  # type: ignore[arg-type]
        )

        # Cycle 1: retry (fails, attempt_count becomes 1).
        # Cycle 2: attempt_count (1) >= max_attempts (1) -> stopped_reason max_attempts_reached
        self.assertEqual(result.stopped_reason, "max_attempts_reached")
        self.assertEqual(result.cycles_executed, 1)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_recovered_or_closed_case_cannot_be_acted_upon(self, mock_llm: MagicMock) -> None:
        self.case.status = "recovered"

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )

        self.assertEqual(result.stopped_reason, "case_already_recovered")
        self.assertEqual(result.cycles_executed, 0)
        self.assertFalse(mock_llm.called)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_llm_error_leaves_auditable_state_and_stops_gracefully(self, mock_llm: MagicMock) -> None:
        mock_llm.side_effect = LLMServiceError("Ollama host unavailable")

        result = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )

        self.assertEqual(result.stopped_reason, "llm_error")
        self.assertEqual(result.cycles_executed, 0)
        # Check audit log recorded stop with llm_error detail
        actions = [getattr(log, "action", None) for log in self.db.audit_logs]
        self.assertIn("recovery_agent_stopped", actions)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_duplicate_triggers_do_not_double_increment_attempts_on_contact(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = RecoveryDecision(
            action="contact",
            reason="Contacting customer.",
            confidence=0.8,
            next_step="Contact the customer with a payment link",
            stop=False,
        )
        razorpay_service = FakeRazorpayServiceForAgent()

        # Run 1
        res1 = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )
        self.assertEqual(self.case.attempt_count, 1)

        # Duplicate run (e.g. re-triggered)
        res2 = run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
            razorpay_service=razorpay_service,  # type: ignore[arg-type]
        )
        # Reuses existing payment link, does not double-increment attempt count!
        self.assertEqual(self.case.attempt_count, 1)

    @patch("app.services.recovery_agent.request_llm_decision")
    def test_duplicate_wait_schedule_is_not_created(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = RecoveryDecision(
            action="wait",
            reason="Wait for retry.",
            confidence=0.8,
            next_step="Wait for the validated interval, then perform one controlled retry",
            stop=False,
            wait_minutes=30,
        )

        run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )
        self.assertEqual(len(self.db.scheduled_actions), 1)

        run_recovery_agent(
            self.db,
            self.case_id,
            trigger="payment_failed_webhook",
            max_recovery_attempts=3,
            settings=self.settings,  # type: ignore[arg-type]
        )
        # Schedule reused, no duplicate job added
        self.assertEqual(len(self.db.scheduled_actions), 1)


if __name__ == "__main__":
    unittest.main()

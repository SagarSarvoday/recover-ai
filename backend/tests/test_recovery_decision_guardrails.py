import unittest
from types import SimpleNamespace

from app.schemas.recovery_analysis import RecoveryDecision
from app.services.recovery_decision import apply_guardrails, ensure_action_consistent_next_step


class RecoveryDecisionGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model_decision = RecoveryDecision(
            action="retry",
            reason="A retry may be appropriate.",
            confidence=0.7,
            next_step="Schedule one controlled retry.",
            stop=False,
        )

    def test_resolved_case_is_forced_to_close(self) -> None:
        decision, guardrails = apply_guardrails(
            self.model_decision,
            SimpleNamespace(status="recovered", attempt_count=0),
            max_recovery_attempts=3,
        )

        self.assertEqual(decision.action, "close")
        self.assertTrue(decision.stop)
        self.assertIn("case_already_resolved", guardrails)

    def test_attempt_limit_is_forced_to_close(self) -> None:
        decision, guardrails = apply_guardrails(
            self.model_decision,
            SimpleNamespace(status="open", attempt_count=3),
            max_recovery_attempts=3,
        )

        self.assertEqual(decision.action, "close")
        self.assertTrue(decision.stop)
        self.assertIn("maximum_recovery_attempts_reached", guardrails)

    def test_wait_action_never_returns_a_retry_next_step(self) -> None:
        decision = RecoveryDecision(
            action="wait",
            reason="The customer has paid successfully before.",
            confidence=0.8,
            next_step="retry",
            stop=False,
        )

        validated = ensure_action_consistent_next_step(decision)

        self.assertEqual(validated.next_step, "Wait 24 hours, then reassess")


if __name__ == "__main__":
    unittest.main()

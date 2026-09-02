import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.schemas.recovery_analysis import RecoveryDecision
from app.services.recovery_decision import apply_guardrails, ensure_action_consistent_next_step, persist_analysis


class SchedulingSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.active_job: object | None = None
        self.commit_count = 0

    def scalar(self, _statement: object) -> object | None:
        return self.active_job

    def add(self, item: object) -> None:
        self.added.append(item)
        if isinstance(item, ScheduledRecoveryAction):
            self.active_job = item

    def commit(self) -> None:
        self.commit_count += 1


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
            wait_minutes=60,
        )

        validated = ensure_action_consistent_next_step(decision)

        self.assertEqual(validated.next_step, "Wait for the validated interval, then perform one controlled retry")

    def test_guarded_wait_analysis_persists_a_pending_durable_retry(self) -> None:
        db = SchedulingSession()
        case = SimpleNamespace(
            id=uuid4(), merchant_id=uuid4(), ai_decision=None, ai_decision_note=None,
            ai_wait_minutes=None, next_action_at=None, scheduled_action=None,
        )
        decision = RecoveryDecision(
            action="wait", reason="Allow the bank timeout to clear.", confidence=0.8,
            next_step="Wait for the validated interval, then perform one controlled retry",
            stop=False, wait_minutes=60,
        )
        before = datetime.now(timezone.utc)

        persist_analysis(db, case, decision, [], "test-model")

        jobs = [item for item in db.added if isinstance(item, ScheduledRecoveryAction)]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].merchant_id, case.merchant_id)
        self.assertEqual(jobs[0].recovery_case_id, case.id)
        self.assertEqual(jobs[0].action, "retry")
        self.assertEqual(jobs[0].status, "pending")
        self.assertGreaterEqual(jobs[0].scheduled_at, before + timedelta(minutes=60))
        self.assertEqual(case.next_action_at, jobs[0].scheduled_at)
        self.assertEqual(case.scheduled_action, "retry")
        self.assertEqual(db.commit_count, 1)

    def test_repeated_wait_analysis_reuses_the_active_schedule(self) -> None:
        db = SchedulingSession()
        case = SimpleNamespace(
            id=uuid4(), merchant_id=uuid4(), ai_decision=None, ai_decision_note=None,
            ai_wait_minutes=None, next_action_at=None, scheduled_action=None,
        )
        decision = RecoveryDecision(
            action="wait", reason="Wait.", confidence=0.8,
            next_step="Wait for the validated interval, then perform one controlled retry",
            stop=False, wait_minutes=60,
        )

        persist_analysis(db, case, decision, [], "test-model")
        first_scheduled_at = case.next_action_at
        persist_analysis(db, case, decision, [], "test-model")

        jobs = [item for item in db.added if isinstance(item, ScheduledRecoveryAction)]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(case.next_action_at, first_scheduled_at)
        self.assertEqual(case.scheduled_action, "retry")

    def test_guarded_close_does_not_leave_or_create_a_schedule(self) -> None:
        db = SchedulingSession()
        case = SimpleNamespace(
            id=uuid4(), merchant_id=uuid4(), status="closed", attempt_count=0,
            ai_decision=None, ai_decision_note=None, ai_wait_minutes=None,
            next_action_at=None, scheduled_action=None,
        )
        guarded_decision, guardrails = apply_guardrails(
            RecoveryDecision(
                action="wait", reason="Wait.", confidence=0.8,
                next_step="Wait for the validated interval, then perform one controlled retry",
                stop=False, wait_minutes=60,
            ),
            case,
            max_recovery_attempts=3,
        )

        persist_analysis(db, case, guarded_decision, guardrails, "test-model")

        self.assertEqual(guarded_decision.action, "close")
        self.assertEqual([item for item in db.added if isinstance(item, ScheduledRecoveryAction)], [])
        self.assertIsNone(case.next_action_at)
        self.assertIsNone(case.scheduled_action)

    def test_unvalidated_out_of_range_wait_cannot_be_scheduled(self) -> None:
        db = SchedulingSession()
        case = SimpleNamespace(
            id=uuid4(), merchant_id=uuid4(), ai_decision=None, ai_decision_note=None,
            ai_wait_minutes=None, next_action_at=None, scheduled_action=None,
        )
        invalid_decision = RecoveryDecision.model_construct(
            action="wait", reason="Wait.", confidence=0.8,
            next_step="Wait for the validated interval, then perform one controlled retry",
            stop=False, wait_minutes=10_081,
        )

        with self.assertRaises(ValueError):
            persist_analysis(db, case, invalid_decision, [], "test-model")

        self.assertEqual([item for item in db.added if isinstance(item, ScheduledRecoveryAction)], [])


if __name__ == "__main__":
    unittest.main()

import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.core.config import Settings
from app.models.audit_log import AuditLog
from app.models.merchant import Merchant
from app.models.recovery_case import RecoveryCase
from app.models.scheduled_recovery_action import ScheduledRecoveryAction
from app.services.recovery_agent_worker import (
    claim_case_in_flight,
    find_eligible_cases_for_merchant,
    is_case_in_flight,
    process_eligible_case,
    release_case_in_flight,
    run_continuous_recovery_agent_cycle,
)


class FakeSession:
    def __init__(
        self,
        merchants: list[Merchant] | None = None,
        cases: list[RecoveryCase] | None = None,
        scheduled_actions: list[ScheduledRecoveryAction] | None = None,
    ) -> None:
        self.merchants = merchants or []
        self.cases = cases or []
        self.scheduled_actions = scheduled_actions or []
        self.audit_logs: list[AuditLog] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def get(self, model: object, entity_id: object) -> object | None:
        if model is Merchant:
            return next((m for m in self.merchants if m.id == entity_id), None)
        if model is RecoveryCase:
            return next((c for c in self.cases if c.id == entity_id), None)
        if model is ScheduledRecoveryAction:
            return next((s for s in self.scheduled_actions if s.id == entity_id), None)
        return None

    def scalar(self, statement: object) -> object:
        statement_str = str(statement).lower()
        if "scheduled_recovery_actions" in statement_str:
            params = getattr(statement.compile(), "params", {})
            case_id = params.get("recovery_case_id_1") or params.get("recovery_case_id")
            for action in self.scheduled_actions:
                if (case_id is None or action.recovery_case_id == case_id) and action.status in ("pending", "running"):
                    return action.id
            return None
        return None

    def scalars(self, statement: object) -> SimpleNamespace:
        statement_str = str(statement).lower()
        if "from merchants" in statement_str:
            enabled = [m for m in self.merchants if m.ai_agent_enabled]
            return SimpleNamespace(all=lambda: enabled)
        if "from recovery_cases" in statement_str:
            params = getattr(statement.compile(), "params", {})
            merchant_id = params.get("merchant_id_1") or params.get("merchant_id")
            matching = [
                c for c in self.cases
                if (merchant_id is None or c.merchant_id == merchant_id)
                and c.status in ("open", "in_progress")
                and c.attempt_count < 3
            ]
            return SimpleNamespace(all=lambda: matching)
        return SimpleNamespace(all=lambda: [])

    def add(self, item: object) -> None:
        if isinstance(item, AuditLog):
            self.audit_logs.append(item)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def make_merchant(ai_agent_enabled: bool = True) -> Merchant:
    now = datetime.now(timezone.utc)
    return Merchant(
        id=uuid4(),
        name="Test Merchant",
        email=f"merchant_{uuid4().hex[:8]}@example.com",
        ai_agent_enabled=ai_agent_enabled,
        ai_agent_started_at=now if ai_agent_enabled else None,
        created_at=now,
        updated_at=now,
    )


def make_case(
    merchant_id: object,
    status: str = "open",
    attempt_count: int = 0,
    payment_link_id: str | None = None,
    ai_decision: str | None = None,
    last_attempt_at: datetime | None = None,
) -> RecoveryCase:
    now = datetime.now(timezone.utc)
    return RecoveryCase(
        id=uuid4(),
        merchant_id=merchant_id,
        payment_id=uuid4(),
        status=status,
        amount_at_risk=Decimal("500.00"),
        amount_recovered=Decimal("0.00"),
        attempt_count=attempt_count,
        razorpay_payment_link_id=payment_link_id,
        ai_decision=ai_decision,
        last_attempt_at=last_attempt_at,
        created_at=now,
        updated_at=now,
    )


class ContinuousRecoveryAgentWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            database_url="postgresql://test:test@localhost/test",
            recovery_agent_worker_enabled=True,
            recovery_agent_poll_seconds=1,
            recovery_max_attempts=3,
        )

    def test_in_flight_lock_prevents_concurrent_claims(self) -> None:
        case_id = uuid4()
        self.assertFalse(is_case_in_flight(case_id))

        self.assertTrue(claim_case_in_flight(case_id))
        self.assertTrue(is_case_in_flight(case_id))
        # Second claim must fail
        self.assertFalse(claim_case_in_flight(case_id))

        release_case_in_flight(case_id)
        self.assertFalse(is_case_in_flight(case_id))
        self.assertTrue(claim_case_in_flight(case_id))
        release_case_in_flight(case_id)

    def test_worker_only_processes_enabled_merchants(self) -> None:
        merchant_enabled = make_merchant(ai_agent_enabled=True)
        merchant_disabled = make_merchant(ai_agent_enabled=False)

        case_enabled = make_case(merchant_enabled.id)
        case_disabled = make_case(merchant_disabled.id)

        session = FakeSession(
            merchants=[merchant_enabled, merchant_disabled],
            cases=[case_enabled, case_disabled],
        )

        with patch("app.services.recovery_agent_worker.run_recovery_agent") as mock_run:
            processed = run_continuous_recovery_agent_cycle(
                lambda: session,
                max_recovery_attempts=3,
                settings=self.settings,
            )

        self.assertEqual(processed, 1)
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][1], case_enabled.id)
        self.assertEqual(mock_run.call_args[1]["trigger"], "continuous_worker")

    def test_worker_ignores_disabled_merchants(self) -> None:
        merchant_disabled = make_merchant(ai_agent_enabled=False)
        case = make_case(merchant_disabled.id)
        session = FakeSession(merchants=[merchant_disabled], cases=[case])

        with patch("app.services.recovery_agent_worker.run_recovery_agent") as mock_run:
            processed = run_continuous_recovery_agent_cycle(
                lambda: session,
                max_recovery_attempts=3,
                settings=self.settings,
            )

        self.assertEqual(processed, 0)
        mock_run.assert_not_called()

    def test_worker_skips_cases_with_active_scheduled_actions(self) -> None:
        merchant = make_merchant(ai_agent_enabled=True)
        case = make_case(merchant.id)
        scheduled_action = ScheduledRecoveryAction(
            id=uuid4(),
            recovery_case_id=case.id,
            merchant_id=merchant.id,
            action="retry",
            status="pending",
            scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        session = FakeSession(
            merchants=[merchant],
            cases=[case],
            scheduled_actions=[scheduled_action],
        )

        eligible, skipped = find_eligible_cases_for_merchant(
            session,
            merchant.id,
            max_recovery_attempts=3,
        )
        self.assertEqual(len(eligible), 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][1], "active_schedule_exists")

    def test_worker_skips_cases_awaiting_payment_link_webhook(self) -> None:
        merchant = make_merchant(ai_agent_enabled=True)
        case = make_case(merchant.id, payment_link_id="plink_waiting_123")
        session = FakeSession(merchants=[merchant], cases=[case])

        eligible, skipped = find_eligible_cases_for_merchant(
            session,
            merchant.id,
            max_recovery_attempts=3,
        )
        self.assertEqual(len(eligible), 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][1], "awaiting_payment_link_webhook")

    def test_worker_skips_cases_under_cooldown(self) -> None:
        merchant = make_merchant(ai_agent_enabled=True)
        now = datetime.now(timezone.utc)
        # Attempt count 1 and last attempt 10 seconds ago (cooldown is 60s)
        case = make_case(
            merchant.id,
            attempt_count=1,
            ai_decision="retry",
            last_attempt_at=now - timedelta(seconds=10),
        )
        session = FakeSession(merchants=[merchant], cases=[case])

        eligible, skipped = find_eligible_cases_for_merchant(
            session,
            merchant.id,
            max_recovery_attempts=3,
            cooldown_seconds=60,
            now=now,
        )
        self.assertEqual(len(eligible), 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0][1], "cooldown_active")

    def test_worker_skips_already_in_flight_case(self) -> None:
        merchant = make_merchant(ai_agent_enabled=True)
        case = make_case(merchant.id)
        session = FakeSession(merchants=[merchant], cases=[case])

        claim_case_in_flight(case.id)
        try:
            eligible, skipped = find_eligible_cases_for_merchant(
                session,
                merchant.id,
                max_recovery_attempts=3,
            )
            self.assertEqual(len(eligible), 0)
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0][1], "already_in_flight")
        finally:
            release_case_in_flight(case.id)

    def test_worker_survives_individual_agent_exception(self) -> None:
        merchant = make_merchant(ai_agent_enabled=True)
        case_fail = make_case(merchant.id)
        case_ok = make_case(merchant.id)

        session = FakeSession(merchants=[merchant], cases=[case_fail, case_ok])

        call_count = 0

        def side_effect(db, case_id, **kwargs):
            nonlocal call_count
            call_count += 1
            if case_id == case_fail.id:
                raise RuntimeError("LLM connection timeout")
            return SimpleNamespace(case_id=case_id)

        with patch("app.services.recovery_agent_worker.run_recovery_agent", side_effect=side_effect):
            processed = run_continuous_recovery_agent_cycle(
                lambda: session,
                max_recovery_attempts=3,
                settings=self.settings,
            )

        # case_fail threw error but was caught and logged; case_ok processed successfully
        self.assertEqual(call_count, 2)
        self.assertEqual(processed, 1)

    def test_clean_lifespan_shutdown(self) -> None:
        from app.main import lifespan
        from fastapi import FastAPI

        test_app = FastAPI()

        with patch("app.main.settings.recovery_scheduler_enabled", True), \
             patch("app.main.settings.recovery_agent_worker_enabled", True), \
             patch("app.main.settings.recovery_scheduler_poll_seconds", 1), \
             patch("app.main.settings.recovery_agent_poll_seconds", 1), \
             patch("app.main.run_scheduled_recovery_actions"), \
             patch("app.main.run_continuous_recovery_agent_cycle"):

            async def run_context():
                async with lifespan(test_app):
                    # Verify threads are active
                    thread_names = [t.name for t in threading.enumerate()]
                    self.assertIn("recovery-scheduler", thread_names)
                    self.assertIn("recovery-agent-worker", thread_names)

            import asyncio
            asyncio.run(run_context())

            # Verify threads shut down cleanly
            thread_names_after = [t.name for t in threading.enumerate()]
            self.assertNotIn("recovery-scheduler", thread_names_after)
            self.assertNotIn("recovery-agent-worker", thread_names_after)


if __name__ == "__main__":
    unittest.main()

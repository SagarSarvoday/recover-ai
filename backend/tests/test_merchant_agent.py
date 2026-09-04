import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.audit_log import AuditLog
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


class FakeQueryResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar(self) -> object:
        return self._value


class FakeSession:
    def __init__(self, merchants: list[Merchant]) -> None:
        self.merchants = merchants
        self.recovery_cases: list[RecoveryCase] = []
        self.payments: list[Payment] = []
        self.audit_logs: list[AuditLog] = []
        self.commits = 0
        self.rollbacks = 0

    def get(self, model: object, entity_id: object) -> object | None:
        if model is Merchant:
            return next((m for m in self.merchants if m.id == entity_id), None)
        if model is RecoveryCase:
            return next((c for c in self.recovery_cases if c.id == entity_id), None)
        if model is Payment:
            return next((p for p in self.payments if p.id == entity_id), None)
        return None

    def scalar(self, statement: object) -> object:
        statement_str = str(statement).lower()
        # Active cases count
        if "count" in statement_str and "recovery_cases" in statement_str:
            params = getattr(statement.compile(), "params", {})
            merchant_id = params.get("merchant_id_1") or params.get("merchant_id")
            matching = [
                c for c in self.recovery_cases
                if (merchant_id is None or c.merchant_id == merchant_id) and c.status in ("open", "in_progress")
            ]
            return len(matching)
        # Succeeded payments sum
        if "coalesce" in statement_str and "payments" in statement_str:
            params = getattr(statement.compile(), "params", {})
            merchant_id = params.get("merchant_id_1") or params.get("merchant_id")
            total = sum(
                (p.amount for p in self.payments if (merchant_id is None or p.merchant_id == merchant_id) and p.status == "succeeded"),
                Decimal("0.00"),
            )
            return total
        # Actions today count
        if "count" in statement_str and "audit_logs" in statement_str:
            params = getattr(statement.compile(), "params", {})
            merchant_id = params.get("merchant_id_1") or params.get("merchant_id")
            matching_cases = {c.id for c in self.recovery_cases if merchant_id is None or c.merchant_id == merchant_id}
            actions = [
                log for log in self.audit_logs
                if log.entity_type == "recovery_case" and log.entity_id in matching_cases
            ]
            return len(actions)
        # Max created_at
        if "max" in statement_str and "audit_logs" in statement_str:
            matching_logs = [log.created_at for log in self.audit_logs if log.created_at is not None]
            return max(matching_logs) if matching_logs else None
        return None

    def add(self, item: object) -> None:
        if isinstance(item, AuditLog):
            if item.created_at is None:
                item.created_at = datetime.now(timezone.utc)
            self.audit_logs.append(item)
        elif isinstance(item, RecoveryCase):
            self.recovery_cases.append(item)
        elif isinstance(item, Payment):
            self.payments.append(item)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def refresh(self, merchant: Merchant) -> None:
        merchant.updated_at = datetime.now(timezone.utc)


def make_merchant(email: str, ai_agent_enabled: bool = False) -> Merchant:
    now = datetime.now(timezone.utc)
    return Merchant(
        id=uuid4(),
        name=email.split("@", 1)[0],
        email=email,
        password_hash="hashed_pw",
        razorpay_account_id=f"acc_{email.split('@', 1)[0]}",
        ai_agent_enabled=ai_agent_enabled,
        ai_agent_started_at=now if ai_agent_enabled else None,
        ai_agent_updated_at=now if ai_agent_enabled else None,
        created_at=now,
        updated_at=now,
    )


class MerchantAgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_a = make_merchant("merchant-a@example.com", ai_agent_enabled=False)
        self.merchant_b = make_merchant("merchant-b@example.com", ai_agent_enabled=True)
        self.db = FakeSession([self.merchant_a, self.merchant_b])
        app.dependency_overrides[get_db] = lambda: self.db
        self.jwt_secret_patch = patch(
            "app.core.security.settings.jwt_secret_key",
            SecretStr("test-merchant-agent-secret-key-32-bytes"),
        )
        self.jwt_secret_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.jwt_secret_patch.stop()
        app.dependency_overrides.clear()
        self.client.close()

    def auth_headers(self, merchant: Merchant) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(merchant.id)}"}

    def test_default_agent_disabled_for_merchant(self) -> None:
        merchant = make_merchant("new-merchant@example.com")
        self.assertFalse(merchant.ai_agent_enabled)
        self.assertIsNone(merchant.ai_agent_started_at)
        self.assertIsNone(merchant.ai_agent_updated_at)

    def test_unauthenticated_agent_endpoints_return_401(self) -> None:
        for path in ("/api/v1/merchant/agent", "/api/v1/merchant/agent/start", "/api/v1/merchant/agent/stop"):
            with self.subTest(path=path):
                response = self.client.get(path) if path.endswith("agent") else self.client.post(path)
                self.assertEqual(response.status_code, 401)

    def test_get_agent_status_when_stopped(self) -> None:
        response = self.client.get("/api/v1/merchant/agent", headers=self.auth_headers(self.merchant_a))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["enabled"])
        self.assertEqual(body["status"], "stopped")
        self.assertEqual(body["active_cases"], 0)
        self.assertEqual(body["actions_today"], 0)
        self.assertEqual(float(body["recovered_today"]), 0.0)

    def test_get_agent_status_when_running(self) -> None:
        response = self.client.get("/api/v1/merchant/agent", headers=self.auth_headers(self.merchant_b))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["status"], "running")
        self.assertIsNotNone(body["started_at"])

    def test_start_endpoint_enables_agent_and_logs_audit(self) -> None:
        self.assertFalse(self.merchant_a.ai_agent_enabled)

        response = self.client.post(
            "/api/v1/merchant/agent/start",
            headers=self.auth_headers(self.merchant_a),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["status"], "running")
        self.assertTrue(self.merchant_a.ai_agent_enabled)
        self.assertIsNotNone(self.merchant_a.ai_agent_started_at)

        # Verify audit log was recorded
        audit = next((a for a in self.db.audit_logs if a.action == "recovery_agent_enabled"), None)
        self.assertIsNotNone(audit)
        self.assertEqual(audit.entity_type, "merchant")
        self.assertEqual(audit.entity_id, self.merchant_a.id)
        self.assertEqual(audit.actor, "merchant")

    def test_stop_endpoint_disables_agent_and_logs_audit(self) -> None:
        self.assertTrue(self.merchant_b.ai_agent_enabled)

        response = self.client.post(
            "/api/v1/merchant/agent/stop",
            headers=self.auth_headers(self.merchant_b),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["enabled"])
        self.assertEqual(body["status"], "stopped")
        self.assertFalse(self.merchant_b.ai_agent_enabled)
        self.assertIsNotNone(self.merchant_b.ai_agent_updated_at)

        # Verify audit log was recorded
        audit = next((a for a in self.db.audit_logs if a.action == "recovery_agent_disabled"), None)
        self.assertIsNotNone(audit)
        self.assertEqual(audit.entity_type, "merchant")
        self.assertEqual(audit.entity_id, self.merchant_b.id)
        self.assertEqual(audit.actor, "merchant")

    def test_start_and_stop_endpoints_are_idempotent(self) -> None:
        # Start already-running merchant B
        self.assertTrue(self.merchant_b.ai_agent_enabled)
        initial_started = self.merchant_b.ai_agent_started_at
        initial_commits = self.db.commits

        response = self.client.post(
            "/api/v1/merchant/agent/start",
            headers=self.auth_headers(self.merchant_b),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["enabled"])
        self.assertEqual(self.merchant_b.ai_agent_started_at, initial_started)
        self.assertEqual(self.db.commits, initial_commits)

        # Stop already-stopped merchant A
        self.assertFalse(self.merchant_a.ai_agent_enabled)
        response_stop = self.client.post(
            "/api/v1/merchant/agent/stop",
            headers=self.auth_headers(self.merchant_a),
        )
        self.assertEqual(response_stop.status_code, 200)
        self.assertFalse(response_stop.json()["enabled"])

    def test_cross_merchant_access_is_blocked(self) -> None:
        # Merchant A toggles start, merchant B remains unaffected
        self.client.post("/api/v1/merchant/agent/start", headers=self.auth_headers(self.merchant_a))
        self.assertTrue(self.merchant_a.ai_agent_enabled)

        # Merchant B stops its agent, merchant A remains enabled
        self.client.post("/api/v1/merchant/agent/stop", headers=self.auth_headers(self.merchant_b))
        self.assertFalse(self.merchant_b.ai_agent_enabled)
        self.assertTrue(self.merchant_a.ai_agent_enabled)


if __name__ == "__main__":
    unittest.main()

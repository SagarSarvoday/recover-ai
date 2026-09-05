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
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.models.transaction import Transaction


class FakeQueryResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def one(self):
        return self.rows[0] if self.rows else (Decimal("0.00"), Decimal("0.00"))


class ComprehensiveFakeDb:
    def __init__(self, merchants, customers=None, payments=None, cases=None, transactions=None):
        self.merchants = {m.id: m for m in merchants}
        self.customers = {c.id: c for c in (customers or [])}
        self.payments = {p.id: p for p in (payments or [])}
        self.cases = {c.id: c for c in (cases or [])}
        self.transactions = {t.id: t for t in (transactions or [])}
        self.audit_logs = []
        self.commits = 0

    def get(self, model, obj_id):
        if model is Merchant:
            return self.merchants.get(obj_id)
        if model is Customer:
            return self.customers.get(obj_id)
        if model is Payment:
            return self.payments.get(obj_id)
        if model is RecoveryCase:
            return self.cases.get(obj_id)
        if model is Transaction:
            return self.transactions.get(obj_id)
        return None

    def _extract_values(self, statement):
        params = statement.compile().params
        values = set()
        for v in params.values():
            if isinstance(v, (list, tuple, set)):
                values.update(v)
            else:
                values.add(v)
        return params, values

    def scalar(self, statement):
        stmt_str = str(statement).lower()
        params, values = self._extract_values(statement)

        if "merchants" in stmt_str and "razorpay_account_id" in stmt_str:
            acc_id = params.get("razorpay_account_id_1") or params.get("razorpay_account_id")
            for m in self.merchants.values():
                if m.razorpay_account_id == acc_id:
                    return m
            return None

        if "customers" in stmt_str and "email" in stmt_str:
            merch_id = params.get("merchant_id_1") or params.get("merchant_id")
            email = params.get("email_1") or params.get("email")
            for c in self.customers.values():
                if c.merchant_id == merch_id and c.email == email:
                    return c
            return None

        if "count(customers.id)" in stmt_str:
            merch_id = next((v for v in values if v in self.merchants), None)
            return len([c for c in self.customers.values() if c.merchant_id == merch_id])

        if "count(recovery_cases.id)" in stmt_str:
            merch_id = next((v for v in values if v in self.merchants), None)
            return len([c for c in self.cases.values() if c.merchant_id == merch_id])

        if "count(payments.id)" in stmt_str:
            merch_id = next((v for v in values if v in self.merchants), None)
            return len([p for p in self.payments.values() if p.merchant_id == merch_id])

        if "sum(" in stmt_str:
            return Decimal("0.00")

        if "count(audit_logs.id)" in stmt_str or "count(audit_log.id)" in stmt_str:
            return len(self.audit_logs)

        if "transactions" in stmt_str:
            merch_id = next((v for v in values if v in self.merchants), None)
            tx_id = params.get("merchant_transaction_id_1")
            for t in self.transactions.values():
                if t.merchant_id == merch_id and t.merchant_transaction_id == tx_id:
                    return t
            return None

        return None

    def scalars(self, statement):
        stmt_str = str(statement).lower()
        _, values = self._extract_values(statement)
        merch_id = next((v for v in values if v in self.merchants), None)

        if "customers" in stmt_str:
            rows = [c for c in self.customers.values() if c.merchant_id == merch_id]
            return FakeQueryResult(rows)
        if "recovery_cases" in stmt_str:
            rows = [c for c in self.cases.values() if c.merchant_id == merch_id]
            return FakeQueryResult(rows)
        if "transactions" in stmt_str:
            rows = [t for t in self.transactions.values() if t.merchant_id == merch_id]
            return FakeQueryResult(rows)
        if "audit_logs" in stmt_str:
            return FakeQueryResult(self.audit_logs)

        return FakeQueryResult([])

    def execute(self, statement):
        stmt_str = str(statement).lower()
        values = set(statement.compile().params.values())
        merch_id = next((v for v in values if v in self.merchants), None)

        if "sum(recovery_cases.amount_at_risk)" in stmt_str:
            merch_cases = [c for c in self.cases.values() if c.merchant_id == merch_id]
            at_risk = sum((c.amount_at_risk for c in merch_cases), Decimal("0.00"))
            recovered = sum((c.amount_recovered for c in merch_cases), Decimal("0.00"))
            return FakeQueryResult([(at_risk, recovered)])

        if "from recovery_cases" in stmt_str:
            rows = [
                (c, self.customers.get(c.customer_id), self.payments.get(c.payment_id))
                for c in self.cases.values()
                if c.merchant_id == merch_id
            ]
            return FakeQueryResult(rows)

        return FakeQueryResult([])

    def add(self, entity):
        if isinstance(entity, Customer):
            if not getattr(entity, "id", None):
                entity.id = uuid4()
            self.customers[entity.id] = entity
        elif isinstance(entity, Transaction):
            if not getattr(entity, "id", None):
                entity.id = uuid4()
            self.transactions[entity.id] = entity
        elif isinstance(entity, AuditLog):
            if not getattr(entity, "id", None):
                entity.id = uuid4()
            self.audit_logs.append(entity)

    def delete(self, entity):
        if isinstance(entity, Customer):
            self.customers.pop(entity.id, None)

    def commit(self):
        self.commits += 1

    def refresh(self, entity):
        pass

    def rollback(self):
        pass


def make_merchant(email: str, name: str, account_id: str | None = None) -> Merchant:
    now = datetime.now(timezone.utc)
    return Merchant(
        id=uuid4(),
        name=name,
        email=email,
        business_name=f"{name} Store",
        support_email=f"support@{email.split('@')[1]}",
        support_phone="+919876543210",
        password_hash="fake-hash",
        razorpay_account_id=account_id,
        ai_agent_enabled=False,
        max_recovery_attempts=3,
        default_payment_link_expiry_hours=48,
        default_wait_minutes=60,
        auto_notify_customer=True,
        created_at=now,
        updated_at=now,
    )


class MerchantIsolationAndIDORTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_a = make_merchant("alpha@example.com", "Alpha Merchant", "acc_alpha123456")
        self.merchant_b = make_merchant("beta@example.com", "Beta Merchant", "acc_beta654321")

        now = datetime.now(timezone.utc)
        self.customer_a = Customer(
            id=uuid4(),
            merchant_id=self.merchant_a.id,
            name="Alpha Customer",
            email="cust.a@example.com",
            phone="+919111111111",
            created_at=now,
            updated_at=now,
        )
        self.customer_b = Customer(
            id=uuid4(),
            merchant_id=self.merchant_b.id,
            name="Beta Customer",
            email="cust.b@example.com",
            phone="+919222222222",
            created_at=now,
            updated_at=now,
        )

        self.payment_a = Payment(
            id=uuid4(),
            merchant_id=self.merchant_a.id,
            customer_id=self.customer_a.id,
            amount=Decimal("1500.00"),
            currency="INR",
            status="failed",
            failure_reason="Insufficient funds",
            created_at=now,
        )
        self.payment_b = Payment(
            id=uuid4(),
            merchant_id=self.merchant_b.id,
            customer_id=self.customer_b.id,
            amount=Decimal("2500.00"),
            currency="INR",
            status="failed",
            failure_reason="Card expired",
            created_at=now,
        )

        self.case_a = RecoveryCase(
            id=uuid4(),
            merchant_id=self.merchant_a.id,
            customer_id=self.customer_a.id,
            payment_id=self.payment_a.id,
            status="open",
            attempt_count=0,
            amount_at_risk=Decimal("1500.00"),
            amount_recovered=Decimal("0.00"),
            created_at=now,
            updated_at=now,
        )
        self.case_b = RecoveryCase(
            id=uuid4(),
            merchant_id=self.merchant_b.id,
            customer_id=self.customer_b.id,
            payment_id=self.payment_b.id,
            status="open",
            attempt_count=0,
            amount_at_risk=Decimal("2500.00"),
            amount_recovered=Decimal("0.00"),
            created_at=now,
            updated_at=now,
        )

        self.transaction_a = Transaction(
            id=uuid4(),
            merchant_id=self.merchant_a.id,
            customer_id=self.customer_a.id,
            merchant_transaction_id="tx_alpha_001",
            amount=Decimal("1500.00"),
            currency="INR",
            status="created",
            created_at=now,
            updated_at=now,
        )
        self.transaction_b = Transaction(
            id=uuid4(),
            merchant_id=self.merchant_b.id,
            customer_id=self.customer_b.id,
            merchant_transaction_id="tx_beta_001",
            amount=Decimal("2500.00"),
            currency="INR",
            status="created",
            created_at=now,
            updated_at=now,
        )

        self.db = ComprehensiveFakeDb(
            merchants=[self.merchant_a, self.merchant_b],
            customers=[self.customer_a, self.customer_b],
            payments=[self.payment_a, self.payment_b],
            cases=[self.case_a, self.case_b],
            transactions=[self.transaction_a, self.transaction_b],
        )

        app.dependency_overrides[get_db] = lambda: self.db
        self.jwt_secret_patch = patch(
            "app.core.security.settings.jwt_secret_key",
            SecretStr("idor-merchant-test-secret-key-32chars"),
        )
        self.jwt_secret_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.jwt_secret_patch.stop()
        app.dependency_overrides.clear()
        self.client.close()

    def auth_headers(self, merchant: Merchant) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(merchant.id)}"}

    # ── 1. UNAUTHENTICATED ACCESS (401) ──────────────────────────────
    def test_unauthenticated_endpoints_return_401(self) -> None:
        protected_endpoints = [
            ("GET", "/api/v1/customers"),
            ("POST", "/api/v1/customers"),
            ("GET", f"/api/v1/customers/{self.customer_a.id}"),
            ("GET", "/api/v1/recovery-cases"),
            ("POST", f"/api/v1/recovery-cases/{self.case_a.id}/analyze"),
            ("POST", f"/api/v1/recovery-cases/{self.case_a.id}/execute"),
            ("POST", f"/api/v1/recovery-cases/{self.case_a.id}/run"),
            ("POST", f"/api/v1/recovery-cases/{self.case_a.id}/actions/retry-link"),
            ("POST", f"/api/v1/recovery-cases/{self.case_a.id}/actions/close"),
            ("GET", f"/api/v1/recovery-cases/{self.case_a.id}/activity"),
            ("GET", "/api/v1/transactions"),
            ("GET", f"/api/v1/transactions/{self.transaction_a.id}"),
            ("GET", "/api/v1/merchant/me"),
            ("GET", "/api/v1/merchant/metrics"),
            ("GET", "/api/v1/merchant/settings"),
            ("PUT", "/api/v1/merchant/settings"),
        ]

        for method, endpoint in protected_endpoints:
            with self.subTest(method=method, endpoint=endpoint):
                res = self.client.request(method, endpoint, json={})
                self.assertEqual(res.status_code, 401, f"Expected 401 for unauthenticated {method} {endpoint}")

    # ── 2. CUSTOMER IDOR & ISOLATION (404) ───────────────────────────
    def test_customer_cross_tenant_idor_rejected(self) -> None:
        headers_a = self.auth_headers(self.merchant_a)

        # Merchant A cannot view Merchant B's customer
        res = self.client.get(f"/api/v1/customers/{self.customer_b.id}", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot update Merchant B's customer
        res = self.client.put(
            f"/api/v1/customers/{self.customer_b.id}",
            headers=headers_a,
            json={"name": "Hacked Customer"},
        )
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot delete Merchant B's customer
        res = self.client.delete(f"/api/v1/customers/{self.customer_b.id}", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A listing customers only receives customer_a
        res = self.client.get("/api/v1/customers", headers=headers_a)
        self.assertEqual(res.status_code, 200)
        customer_ids = [c["id"] for c in res.json()]
        self.assertIn(str(self.customer_a.id), customer_ids)
        self.assertNotIn(str(self.customer_b.id), customer_ids)

    # ── 3. RECOVERY CASE IDOR & ACTIONS (404) ────────────────────────
    def test_recovery_case_cross_tenant_idor_rejected(self) -> None:
        headers_a = self.auth_headers(self.merchant_a)

        # Merchant A cannot view Merchant B's recovery case
        res = self.client.get(f"/api/v1/recovery-cases/{self.case_b.id}", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot analyze Merchant B's recovery case
        res = self.client.post(f"/api/v1/recovery-cases/{self.case_b.id}/analyze", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot execute recommendation on Merchant B's recovery case
        res = self.client.post(f"/api/v1/recovery-cases/{self.case_b.id}/execute", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot run full workflow on Merchant B's recovery case
        res = self.client.post(f"/api/v1/recovery-cases/{self.case_b.id}/run", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot manually retry payment link on Merchant B's recovery case
        res = self.client.post(f"/api/v1/recovery-cases/{self.case_b.id}/actions/retry-link", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot manually close Merchant B's recovery case
        res = self.client.post(f"/api/v1/recovery-cases/{self.case_b.id}/actions/close", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot view activity history of Merchant B's recovery case
        res = self.client.get(f"/api/v1/recovery-cases/{self.case_b.id}/activity", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A listing recovery cases never sees case_b
        res = self.client.get("/api/v1/recovery-cases", headers=headers_a)
        self.assertEqual(res.status_code, 200)
        case_ids = [c["id"] for c in res.json()]
        self.assertIn(str(self.case_a.id), case_ids)
        self.assertNotIn(str(self.case_b.id), case_ids)

    # ── 4. TRANSACTION CROSS-TENANT ISOLATION (404) ──────────────────
    def test_transaction_cross_tenant_isolation(self) -> None:
        headers_a = self.auth_headers(self.merchant_a)

        # Merchant A cannot read Merchant B's transaction
        res = self.client.get(f"/api/v1/transactions/{self.transaction_b.id}", headers=headers_a)
        self.assertEqual(res.status_code, 404)

        # Merchant A cannot create a transaction referencing Merchant B's customer
        res = self.client.post(
            "/api/v1/transactions",
            headers=headers_a,
            json={
                "customer_id": str(self.customer_b.id),
                "merchant_transaction_id": "idor_tx_fail_001",
                "amount": "150.00",
                "currency": "INR",
            },
        )
        self.assertEqual(res.status_code, 404)

    # ── 5. MERCHANT SETTINGS & SECRET REDACTION ──────────────────────
    def test_merchant_settings_redacts_secrets_and_is_tenant_isolated(self) -> None:
        headers_a = self.auth_headers(self.merchant_a)

        res = self.client.get("/api/v1/merchant/settings", headers=headers_a)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Tenant isolation
        self.assertEqual(data["id"], str(self.merchant_a.id))
        self.assertEqual(data["email"], self.merchant_a.email)
        self.assertEqual(data["business_name"], self.merchant_a.business_name)

        # Zero secret exposure
        self.assertNotIn("password_hash", data)
        self.assertNotIn("razorpay_key_secret", data["integrations"])
        self.assertNotIn("smtp_password", data["integrations"])
        self.assertNotIn("secret_key", data)
        self.assertNotIn("secret", data["webhook"])
        self.assertIsInstance(data["integrations"]["razorpay_key_secret_configured"], bool)
        self.assertIsInstance(data["integrations"]["razorpay_webhook_secret_configured"], bool)
        self.assertIsInstance(data["webhook"]["secret_configured"], bool)

    # ── 6. SETTINGS UPDATE & CONFLICT HANDLING ───────────────────────
    def test_settings_update_rejects_duplicate_razorpay_account(self) -> None:
        headers_a = self.auth_headers(self.merchant_a)

        # Merchant A tries to take Merchant B's razorpay_account_id
        res = self.client.put(
            "/api/v1/merchant/settings",
            headers=headers_a,
            json={"razorpay_account_id": self.merchant_b.razorpay_account_id},
        )
        self.assertEqual(res.status_code, 409)
        self.assertIn("already assigned", res.json()["detail"])

    def test_settings_update_validates_input_bounds(self) -> None:
        headers_a = self.auth_headers(self.merchant_a)

        # Max recovery attempts exceeds 5
        res = self.client.put(
            "/api/v1/merchant/settings",
            headers=headers_a,
            json={"max_recovery_attempts": 10},
        )
        self.assertEqual(res.status_code, 422)

        # Expiry hours < 1
        res = self.client.put(
            "/api/v1/merchant/settings",
            headers=headers_a,
            json={"default_payment_link_expiry_hours": 0},
        )
        self.assertEqual(res.status_code, 422)

        # Wait minutes < 15
        res = self.client.put(
            "/api/v1/merchant/settings",
            headers=headers_a,
            json={"default_wait_minutes": 5},
        )
        self.assertEqual(res.status_code, 422)

        # Invalid support email
        res = self.client.put(
            "/api/v1/merchant/settings",
            headers=headers_a,
            json={"support_email": "not-an-email"},
        )
        self.assertEqual(res.status_code, 422)

    def test_valid_settings_update_succeeds_and_persists(self) -> None:
        headers_a = self.auth_headers(self.merchant_a)

        res = self.client.put(
            "/api/v1/merchant/settings",
            headers=headers_a,
            json={
                "business_name": "Alpha Corp International",
                "support_email": "helpdesk@alpha.com",
                "support_phone": "+919876543219",
                "max_recovery_attempts": 4,
                "default_payment_link_expiry_hours": 72,
                "default_wait_minutes": 120,
                "auto_notify_customer": False,
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["business_name"], "Alpha Corp International")
        self.assertEqual(data["support_email"], "helpdesk@alpha.com")
        self.assertEqual(data["recovery"]["max_recovery_attempts"], 4)
        self.assertEqual(data["recovery"]["default_payment_link_expiry_hours"], 72)
        self.assertEqual(data["recovery"]["default_wait_minutes"], 120)
        self.assertFalse(data["recovery"]["auto_notify_customer"])

        # Check internal merchant model updated
        self.assertEqual(self.merchant_a.business_name, "Alpha Corp International")
        self.assertEqual(self.merchant_a.support_email, "helpdesk@alpha.com")
        self.assertEqual(self.merchant_a.max_recovery_attempts, 4)
        self.assertEqual(self.merchant_a.default_payment_link_expiry_hours, 72)
        self.assertEqual(self.merchant_a.default_wait_minutes, 120)
        self.assertFalse(self.merchant_a.auto_notify_customer)

    # ── 7. MERCHANT METRICS ISOLATION ────────────────────────────────
    def test_merchant_metrics_are_tenant_isolated(self) -> None:
        headers_a = self.auth_headers(self.merchant_a)

        res = self.client.get("/api/v1/merchant/metrics", headers=headers_a)
        self.assertEqual(res.status_code, 200)
        data = res.json()

        # Merchant A has 1 failed payment of 1500.00 at risk, 0 recovered
        self.assertEqual(data["failed_payments"], 1)
        self.assertEqual(Decimal(data["total_value_at_risk"]), Decimal("1500.00"))
        self.assertEqual(Decimal(data["recovered_amount"]), Decimal("0.00"))
        self.assertEqual(data["recovery_rate"], 0.0)

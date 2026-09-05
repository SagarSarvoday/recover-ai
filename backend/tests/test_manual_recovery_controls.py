import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_merchant
from app.main import app
from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase
from app.schemas.recovery_actions import ActionResult


class ManualRecoveryControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant = Merchant(
            id=uuid4(),
            name="Alpha Store",
            email="alpha@store.com",
            razorpay_account_id="acc_alpha123",
            ai_agent_enabled=True,
        )
        self.other_merchant = Merchant(
            id=uuid4(),
            name="Beta Store",
            email="beta@store.com",
            razorpay_account_id="acc_beta456",
        )
        self.customer = Customer(
            id=uuid4(),
            merchant_id=self.merchant.id,
            name="Priya Sharma",
            email="priya@example.com",
            phone="+919876543210",
        )
        self.case = RecoveryCase(
            id=uuid4(),
            merchant_id=self.merchant.id,
            customer_id=self.customer.id,
            payment_id=uuid4(),
            status="in_progress",
            amount_at_risk=Decimal("2500.00"),
            amount_recovered=Decimal("0.00"),
            attempt_count=1,
            ai_decision="contact",
            ai_decision_note="Customer outreach recommended",
        )
        self.mock_db = MagicMock()
        self.mock_db.get.side_effect = self._mock_get

        self.client = TestClient(app)
        app.dependency_overrides[get_db] = lambda: self.mock_db
        app.dependency_overrides[get_current_merchant] = lambda: self.merchant

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def _mock_get(self, model: object, obj_id: object) -> object | None:
        if model is RecoveryCase and obj_id == self.case.id:
            return self.case
        if model is Customer and obj_id == self.customer.id:
            return self.customer
        if model is Merchant and obj_id == self.merchant.id:
            return self.merchant
        return None

    @patch("app.api.v1.recovery_cases.execute_recovery_action")
    def test_manual_retry_link_success(self, mock_execute: MagicMock) -> None:
        mock_execute.return_value = ActionResult(
            success=True,
            action="retry_payment",
            outcome="failed",
            case_id=self.case.id,
            message="Recovery payment link created and customer notified.",
        )
        response = self.client.post(f"/api/v1/recovery-cases/{self.case.id}/actions/retry-link")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["case_id"], str(self.case.id))
        self.assertTrue(data["action_execution_result"]["success"])
        mock_execute.assert_called_once()
        self.assertEqual(mock_execute.call_args[0][0], "retry")
        self.assertEqual(mock_execute.call_args[0][2].action_context.source, "manual")

    @patch("app.api.v1.recovery_cases.execute_recovery_action")
    def test_manual_close_success(self, mock_execute: MagicMock) -> None:
        mock_execute.return_value = ActionResult(
            success=True,
            action="stop_recovery",
            outcome="stopped",
            case_id=self.case.id,
            message="Recovery case closed.",
        )
        response = self.client.post(f"/api/v1/recovery-cases/{self.case.id}/actions/close")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["case_id"], str(self.case.id))
        self.assertTrue(data["action_execution_result"]["success"])
        mock_execute.assert_called_once()
        self.assertEqual(mock_execute.call_args[0][0], "close")
        self.assertEqual(mock_execute.call_args[0][2].action_context.source, "manual")

    def test_merchant_cannot_act_on_other_merchants_case(self) -> None:
        # Case belongs to other merchant
        other_case = RecoveryCase(
            id=uuid4(),
            merchant_id=self.other_merchant.id,
            payment_id=uuid4(),
            status="open",
            amount_at_risk=Decimal("1000.00"),
        )
        self.mock_db.get.side_effect = lambda m, i: other_case if i == other_case.id else None

        response = self.client.post(f"/api/v1/recovery-cases/{other_case.id}/actions/retry-link")
        self.assertEqual(response.status_code, 404)

        response = self.client.post(f"/api/v1/recovery-cases/{other_case.id}/actions/close")
        self.assertEqual(response.status_code, 404)

    def test_merchant_metrics_endpoint(self) -> None:
        # Mock database queries for /api/v1/merchant/metrics
        self.mock_db.scalar.side_effect = [
            10,  # failed_payments
            4,   # active_recoveries
            2,   # waiting_cases
            5,   # payment_links_sent
            1,   # expired_payment_links
            0,   # notification_failures
        ]
        mock_exec_result = MagicMock()
        mock_exec_result.one.return_value = (Decimal("10000.00"), Decimal("4000.00"))
        self.mock_db.execute.return_value = mock_exec_result

        response = self.client.get("/api/v1/merchant/metrics")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["failed_payments"], 10)
        self.assertEqual(data["total_value_at_risk"], "10000.00")
        self.assertEqual(data["recovered_amount"], "4000.00")
        self.assertEqual(data["recovery_rate"], 40.0)
        self.assertEqual(data["active_recoveries"], 4)
        self.assertEqual(data["waiting_cases"], 2)
        self.assertEqual(data["payment_links_sent"], 5)
        self.assertEqual(data["expired_payment_links"], 1)
        self.assertEqual(data["customer_notification_failures"], 0)


if __name__ == "__main__":
    unittest.main()

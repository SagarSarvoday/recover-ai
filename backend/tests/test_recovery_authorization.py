import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException

from app.api.v1.recovery_cases import (
    analyze_recovery_case,
    execute_recovery_case,
    list_recovery_cases,
    run_recovery_workflow,
)
from app.models.recovery_case import RecoveryCase


class FakeQueryResult:
    def __init__(self, rows: list[tuple[object, object | None, object]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[object, object | None, object]]:
        return self.rows


class FakeSession:
    def __init__(self, cases: list[object]) -> None:
        self.cases = cases
        self.last_statement: object | None = None

    def get(self, model: object, case_id: object) -> object | None:
        if model is RecoveryCase:
            return next((case for case in self.cases if case.id == case_id), None)
        return None

    def execute(self, statement: object) -> FakeQueryResult:
        self.last_statement = statement
        merchant_ids = set(statement.compile().params.values())
        rows = [
            (case, None, case.payment)
            for case in self.cases
            if case.merchant_id in merchant_ids
        ]
        return FakeQueryResult(rows)


def make_case(merchant_id: object) -> object:
    now = datetime.now(timezone.utc)
    payment = SimpleNamespace(
        id=uuid4(),
        status="failed",
        failure_reason="declined",
        amount=Decimal("99.00"),
    )
    return SimpleNamespace(
        id=uuid4(),
        merchant_id=merchant_id,
        customer_id=None,
        payment_id=payment.id,
        status="open",
        amount_at_risk=Decimal("99.00"),
        amount_recovered=Decimal("0.00"),
        attempt_count=0,
        ai_decision=None,
        ai_decision_note=None,
        last_attempt_at=None,
        created_at=now,
        updated_at=now,
        payment=payment,
    )


class RecoveryAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant_a = SimpleNamespace(id=uuid4())
        self.merchant_b = SimpleNamespace(id=uuid4())
        self.case_a = make_case(self.merchant_a.id)
        self.case_b = make_case(self.merchant_b.id)
        self.db = FakeSession([self.case_a, self.case_b])

    def test_merchants_list_only_their_own_cases(self) -> None:
        merchant_a_cases = list_recovery_cases(self.db, self.merchant_a)
        merchant_b_cases = list_recovery_cases(self.db, self.merchant_b)

        self.assertEqual([case.id for case in merchant_a_cases], [self.case_a.id])
        self.assertEqual([case.id for case in merchant_b_cases], [self.case_b.id])

    def test_merchant_cannot_analyze_execute_or_run_another_merchants_case(self) -> None:
        for endpoint in (analyze_recovery_case, execute_recovery_case, run_recovery_workflow):
            with self.subTest(endpoint=endpoint.__name__), self.assertRaises(HTTPException) as error:
                endpoint(self.case_b.id, self.db, self.merchant_a)
            self.assertEqual(error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()

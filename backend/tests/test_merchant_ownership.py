import unittest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import UniqueConstraint

from app.models.customer import Customer
from app.models.merchant import Merchant
from app.models.payment import Payment
from app.models.recovery_case import RecoveryCase


class MerchantOwnershipModelTests(unittest.TestCase):
    def test_merchant_owns_customers_payments_and_recovery_cases(self) -> None:
        merchant = Merchant(id=uuid4(), name="Merchant One", email="merchant@example.com")
        customer = Customer(merchant=merchant, name="Customer", email="customer@example.com")
        payment = Payment(
            merchant=merchant,
            customer=customer,
            amount=Decimal("100.00"),
            currency="INR",
            status="failed",
            failure_reason="declined",
        )
        recovery_case = RecoveryCase(
            merchant=merchant,
            customer=customer,
            payment=payment,
            status="open",
            amount_at_risk=Decimal("100.00"),
            amount_recovered=Decimal("0.00"),
            attempt_count=0,
        )

        self.assertIs(customer.merchant, merchant)
        self.assertIs(payment.merchant, merchant)
        self.assertIs(recovery_case.merchant, merchant)

    def test_customer_identity_uniqueness_is_scoped_to_merchant(self) -> None:
        unique_constraints = {
            tuple(constraint.columns.keys())
            for constraint in Customer.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }

        self.assertIn(("merchant_id", "email"), unique_constraints)
        self.assertIn(("merchant_id", "razorpay_customer_id"), unique_constraints)
        self.assertNotIn(("email",), unique_constraints)
        self.assertNotIn(("razorpay_customer_id",), unique_constraints)

        first_merchant = Merchant(id=uuid4(), name="One", email="one@example.com")
        second_merchant = Merchant(id=uuid4(), name="Two", email="two@example.com")
        first_customer = Customer(merchant=first_merchant, email="same@example.com", phone="+919999999999")
        second_customer = Customer(merchant=second_merchant, email="same@example.com", phone="+919999999999")

        self.assertIs(first_customer.merchant, first_merchant)
        self.assertIs(second_customer.merchant, second_merchant)
        self.assertEqual(first_customer.email, second_customer.email)
        self.assertEqual(first_customer.phone, second_customer.phone)

    def test_payment_only_recovery_case_keeps_merchant_ownership(self) -> None:
        merchant = Merchant(id=uuid4(), name="Merchant", email="merchant@example.com")
        payment = Payment(
            merchant=merchant,
            customer_id=None,
            amount=Decimal("250.00"),
            currency="INR",
            status="failed",
            failure_reason="declined",
        )
        recovery_case = RecoveryCase(
            merchant=merchant,
            customer_id=None,
            payment=payment,
            status="open",
            amount_at_risk=Decimal("250.00"),
            amount_recovered=Decimal("0.00"),
            attempt_count=0,
        )

        self.assertIsNone(payment.customer_id)
        self.assertIsNone(recovery_case.customer_id)
        self.assertIs(payment.merchant, merchant)
        self.assertIs(recovery_case.merchant, merchant)


if __name__ == "__main__":
    unittest.main()

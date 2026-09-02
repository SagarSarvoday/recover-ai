"""Development-only initialization of credentials for the migrated legacy merchant."""

from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.merchant import Merchant

LEGACY_MERCHANT_ID = UUID("00000000-0000-0000-0000-000000000001")
LEGACY_MERCHANT_EMAIL = "legacy@recoverai.local"
LEGACY_RAZORPAY_ACCOUNT_ID = "acc_TTZWM0fniZWAbi"


class LegacyMerchantBootstrapError(RuntimeError):
    """Raised when the one-time legacy credential bootstrap cannot proceed."""


def initialize_legacy_merchant_password(
    db: Session,
    password: str,
    *,
    app_environment: str,
) -> Merchant:
    """Set the legacy merchant's first password without touching ownership data.

    This intentionally rejects non-development use, a missing/mismatched legacy
    record, weak input, and an already-initialized password hash.
    """
    if app_environment.lower() != "development":
        raise LegacyMerchantBootstrapError("Legacy password bootstrap is development-only.")
    if not 8 <= len(password) <= 256:
        raise LegacyMerchantBootstrapError("Password must be between 8 and 256 characters.")

    merchant = db.get(Merchant, LEGACY_MERCHANT_ID)
    if merchant is None:
        raise LegacyMerchantBootstrapError("The fixed legacy merchant record was not found.")
    if merchant.email != LEGACY_MERCHANT_EMAIL:
        raise LegacyMerchantBootstrapError("The fixed legacy merchant email does not match.")
    if merchant.razorpay_account_id != LEGACY_RAZORPAY_ACCOUNT_ID:
        raise LegacyMerchantBootstrapError("The fixed legacy Razorpay account does not match.")
    if merchant.password_hash is not None:
        raise LegacyMerchantBootstrapError("The legacy merchant password is already initialized.")

    merchant.password_hash = hash_password(password)
    try:
        db.commit()
        db.refresh(merchant)
    except SQLAlchemyError as error:
        db.rollback()
        raise LegacyMerchantBootstrapError("Unable to store the legacy merchant password.") from error
    return merchant

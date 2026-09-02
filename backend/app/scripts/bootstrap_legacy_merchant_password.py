"""Set the migrated legacy merchant's initial password from an interactive terminal."""

from getpass import getpass

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.legacy_merchant_bootstrap import (
    LegacyMerchantBootstrapError,
    initialize_legacy_merchant_password,
)


def main() -> int:
    if not settings.legacy_password_bootstrap_enabled:
        print("Refusing to run: set LEGACY_PASSWORD_BOOTSTRAP_ENABLED=true temporarily in .env.")
        return 1
    if settings.app_environment.lower() != "development":
        print("Refusing to run: legacy password bootstrap is development-only.")
        return 1

    password = getpass("Initial password for legacy@recoverai.local: ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match; no change was made.")
        return 1

    db = SessionLocal()
    try:
        initialize_legacy_merchant_password(
            db, password, app_environment=settings.app_environment
        )
    except LegacyMerchantBootstrapError as error:
        print(f"Legacy password bootstrap failed: {error}")
        return 1
    finally:
        db.close()

    print("Legacy merchant password initialized. Remove LEGACY_PASSWORD_BOOTSTRAP_ENABLED from .env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

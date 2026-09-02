"""Schedule one safe, observable WAIT retry against a selected development case."""

import argparse
from uuid import UUID

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.development_scheduler_test import (
    DevelopmentSchedulerTestError,
    schedule_development_wait_retry,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", required=True, type=UUID, help="Legacy-owned open case to use")
    arguments = parser.parse_args()

    if not settings.development_scheduler_test_enabled:
        print("Refusing to run: set DEVELOPMENT_SCHEDULER_TEST_ENABLED=true temporarily in .env.")
        return 1

    db = SessionLocal()
    try:
        result = schedule_development_wait_retry(
            db,
            arguments.case_id,
            app_environment=settings.app_environment,
            max_recovery_attempts=settings.recovery_max_attempts,
        )
    except DevelopmentSchedulerTestError as error:
        print(f"Development scheduler test was not created: {error}")
        return 1
    finally:
        db.close()

    print(
        "Development scheduler test created: "
        f"case_id={result.case_id} action=retry scheduled_at={result.scheduled_at.isoformat()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.database import SessionLocal, engine  # noqa: F401 — initialize the SQLAlchemy engine
from app.models import Base  # noqa: F401 — register ORM models against the existing schema
from app.services.scheduled_recovery_actions import run_scheduled_recovery_actions


@asynccontextmanager
async def lifespan(_: FastAPI):
    stop_event = threading.Event()
    worker: threading.Thread | None = None
    if settings.recovery_scheduler_enabled:
        def poll() -> None:
            while not stop_event.is_set():
                try:
                    run_scheduled_recovery_actions(
                        SessionLocal, max_recovery_attempts=settings.recovery_max_attempts
                    )
                except Exception:
                    # The job rows remain pending (or are reclaimed after lease expiry).
                    import logging
                    logging.getLogger(__name__).exception("Scheduled recovery polling cycle failed.")
                stop_event.wait(settings.recovery_scheduler_poll_seconds)

        worker = threading.Thread(target=poll, name="recovery-scheduler", daemon=True)
        worker.start()
    try:
        yield
    finally:
        stop_event.set()
        if worker is not None:
            worker.join(timeout=settings.recovery_scheduler_poll_seconds + 1)

app = FastAPI(
    title="RecoverAI API",
    description="AI revenue recovery platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

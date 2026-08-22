from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.database import engine  # noqa: F401 — initialize the SQLAlchemy engine
from app.models import Base  # noqa: F401 — register ORM models against the existing schema

app = FastAPI(
    title="RecoverAI API",
    description="AI revenue recovery platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

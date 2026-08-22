from fastapi import APIRouter

from app.api.v1 import health, recovery_cases

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(recovery_cases.router, prefix="/api/v1")

# Future v1 routers (not implemented yet):
# api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
# api_router.include_router(agent.router, prefix="/agent", tags=["agent"])
# api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])

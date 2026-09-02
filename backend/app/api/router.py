from fastapi import APIRouter

from app.api.v1 import auth, health, merchants, recovery_cases, transactions, webhooks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/api/v1")
api_router.include_router(merchants.router, prefix="/api/v1")
api_router.include_router(recovery_cases.router, prefix="/api/v1")
api_router.include_router(transactions.router, prefix="/api/v1")
api_router.include_router(webhooks.router, prefix="/api/v1")

# Future v1 routers (not implemented yet):
# api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
# api_router.include_router(agent.router, prefix="/agent", tags=["agent"])

from fastapi import APIRouter

from app.api.v1.admin import campaigns, providers, system, users, workers

router = APIRouter(prefix="/admin")
router.include_router(system.router)
router.include_router(users.router)
router.include_router(providers.router)
router.include_router(campaigns.router)
router.include_router(workers.router)

from fastapi import APIRouter

from app.api.v1 import me, notifications, sources, surveillance

router = APIRouter(prefix="/api/v1")
router.include_router(me.router)
router.include_router(sources.router)
router.include_router(surveillance.router)
router.include_router(notifications.router)

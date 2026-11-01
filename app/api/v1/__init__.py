from fastapi import APIRouter

from app.api.v1 import me, sources, surveillance

router = APIRouter(prefix="/api/v1")
router.include_router(me.router)
router.include_router(sources.router)
router.include_router(surveillance.router)

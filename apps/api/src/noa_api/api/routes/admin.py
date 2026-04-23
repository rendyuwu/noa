from fastapi import APIRouter

from noa_api.api.admin.role_routes import role_router
from noa_api.api.admin.user_routes import user_router

router = APIRouter(prefix="/admin", tags=["admin"])
router.include_router(user_router)
router.include_router(role_router)

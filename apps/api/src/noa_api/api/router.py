from fastapi import APIRouter

from noa_api.api.routes.admin import router as admin_router
from noa_api.api.routes.audit_admin import router as audit_admin_router
from noa_api.api.routes.assistant import router as assistant_router
from noa_api.api.routes.auth import router as auth_router
from noa_api.api.routes.health import router as health_router
from noa_api.api.routes.proxmox_admin import router as proxmox_admin_router
from noa_api.api.routes.threads import router as threads_router
from noa_api.api.routes.whm_admin import router as whm_admin_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router)
api_router.include_router(admin_router)
api_router.include_router(audit_admin_router)
api_router.include_router(proxmox_admin_router)
api_router.include_router(whm_admin_router)
api_router.include_router(threads_router)
api_router.include_router(assistant_router)

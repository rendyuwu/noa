"""Shared persistence layer (C3, C12).

One DB, one schema, one Alembic history. Importing this package pulls in every
schema-v1 model so `Base.metadata` is complete for migration autogenerate.
"""

from core.db.base import Base
from core.db.models import (
    ADMIN_ROLE_NAME,
    INTERNAL_ROLE_PREFIX,
    McpToken,
    PMGServer,
    ProxmoxServer,
    Role,
    RoleToolPermission,
    User,
    UserRole,
    WHMServer,
    is_internal_role,
)

__all__ = [
    "ADMIN_ROLE_NAME",
    "INTERNAL_ROLE_PREFIX",
    "Base",
    "McpToken",
    "PMGServer",
    "ProxmoxServer",
    "Role",
    "RoleToolPermission",
    "User",
    "UserRole",
    "WHMServer",
    "is_internal_role",
]

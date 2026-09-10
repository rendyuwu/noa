"""Shared persistence layer.

One DB, one schema, one Alembic history. Importing this package pulls in every
schema-v1 model so `Base.metadata` is complete for migration autogenerate.
"""

from core.db.base import Base
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus
from core.db.models import (
    ADMIN_ROLE_NAME,
    INTERNAL_ROLE_PREFIX,
    ActionRequest,
    LoginRateLimit,
    McpToken,
    PMGServer,
    ProxmoxServer,
    Role,
    RoleToolPermission,
    ToolRun,
    User,
    UserRole,
    WHMServer,
    is_internal_role,
)
from core.db.session import create_engine, create_session_factory

__all__ = [
    "ADMIN_ROLE_NAME",
    "INTERNAL_ROLE_PREFIX",
    "ActionRequest",
    "ActionRequestStatus",
    "Base",
    "LoginRateLimit",
    "McpToken",
    "PMGServer",
    "ProxmoxServer",
    "Role",
    "RoleToolPermission",
    "ToolRisk",
    "ToolRun",
    "ToolRunStatus",
    "User",
    "UserRole",
    "WHMServer",
    "create_engine",
    "create_session_factory",
    "is_internal_role",
]

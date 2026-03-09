from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.auth.auth_service import AuthService
from noa_api.core.auth.deps import get_auth_service, get_jwt_service
from noa_api.core.auth.errors import AuthInvalidCredentialsError
from noa_api.core.auth.jwt_service import JWTService
from noa_api.storage.postgres.client import create_engine, create_session_factory
from noa_api.storage.postgres.models import AuditLog, Role, RoleToolPermission, User, UserRole

_engine = create_engine()
_session_factory = create_session_factory(_engine)
_bearer_scheme = HTTPBearer(auto_error=False)
KNOWN_TOOLS = ("search", "summarize")


class UnknownToolError(Exception):
    def __init__(self, unknown_tools: list[str]) -> None:
        self.unknown_tools = sorted({name.strip() for name in unknown_tools if name.strip()})
        super().__init__(f"Unknown tools: {', '.join(self.unknown_tools)}")


@dataclass
class AuthorizationUser:
    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str]
    tools: list[str]


class AuthorizationRepositoryProtocol(Protocol):
    async def get_role_tool_names(self, role_names: list[str]) -> list[str]: ...

    async def list_users(self) -> list[User]: ...

    async def get_user_by_id(self, user_id: UUID) -> User | None: ...

    async def update_user_active(self, user_id: UUID, *, is_active: bool) -> User | None: ...

    async def get_role_names(self, user_id: UUID) -> list[str]: ...

    async def ensure_role(self, name: str) -> str: ...

    async def assign_role(self, user_id: UUID, role_name: str) -> None: ...

    async def replace_role_tool_permissions(self, role_name: str, tool_names: list[str]) -> None: ...

    async def get_user_allowlist_tools(self, user_id: UUID) -> list[str]: ...

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None: ...


class SQLAuthorizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_role_tool_names(self, role_names: list[str]) -> list[str]:
        if not role_names:
            return []
        result = await self._session.execute(
            select(RoleToolPermission.tool_name)
            .join(Role, Role.id == RoleToolPermission.role_id)
            .where(Role.name.in_(role_names))
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def list_users(self) -> list[User]:
        result = await self._session.execute(select(User).order_by(User.email.asc()))
        return list(result.scalars().all())

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def update_user_active(self, user_id: UUID, *, is_active: bool) -> User | None:
        user = await self.get_user_by_id(user_id)
        if user is None:
            return None
        user.is_active = is_active
        await self._session.flush()
        return user

    async def get_role_names(self, user_id: UUID) -> list[str]:
        result = await self._session.execute(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def ensure_role(self, name: str) -> str:
        result = await self._session.execute(select(Role).where(Role.name == name))
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(name=name)
            self._session.add(role)
            await self._session.flush()
        return role.name

    async def assign_role(self, user_id: UUID, role_name: str) -> None:
        role_result = await self._session.execute(select(Role).where(Role.name == role_name))
        role = role_result.scalar_one_or_none()
        if role is None:
            return

        existing = await self._session.execute(
            select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role.id)
        )
        if existing.scalar_one_or_none() is None:
            self._session.add(UserRole(user_id=user_id, role_id=role.id))
            await self._session.flush()

    async def replace_role_tool_permissions(self, role_name: str, tool_names: list[str]) -> None:
        role_result = await self._session.execute(select(Role).where(Role.name == role_name))
        role = role_result.scalar_one_or_none()
        if role is None:
            return

        await self._session.execute(delete(RoleToolPermission).where(RoleToolPermission.role_id == role.id))
        for tool_name in sorted({name.strip() for name in tool_names if name.strip()}):
            self._session.add(RoleToolPermission(role_id=role.id, tool_name=tool_name))
        await self._session.flush()

    async def get_user_allowlist_tools(self, user_id: UUID) -> list[str]:
        role_name = f"user:{user_id}"
        result = await self._session.execute(
            select(RoleToolPermission.tool_name)
            .join(Role, Role.id == RoleToolPermission.role_id)
            .where(Role.name == role_name)
        )
        return sorted({str(name) for name in result.scalars().all()})

    async def create_audit_log(
        self,
        *,
        event_type: str,
        actor_email: str | None,
        tool_name: str | None,
        metadata: dict[str, object],
    ) -> None:
        self._session.add(
            AuditLog(
                event_type=event_type,
                user_email=actor_email,
                tool_name=tool_name,
                meta_data=metadata,
            )
        )
        await self._session.flush()


class AuthorizationService:
    def __init__(self, *, repository: AuthorizationRepositoryProtocol) -> None:
        self._repository = repository

    async def authorize_tool_access(self, user: AuthorizationUser, tool_name: str) -> bool:
        if not user.is_active:
            return False
        if "admin" in user.roles:
            return True
        role_tools = await self._repository.get_role_tool_names(user.roles)
        return tool_name in role_tools

    async def list_users(self) -> list[AuthorizationUser]:
        users = await self._repository.list_users()
        result: list[AuthorizationUser] = []
        for user in users:
            roles = await self._repository.get_role_names(user.id)
            tools = await self._repository.get_user_allowlist_tools(user.id)
            result.append(
                AuthorizationUser(
                    user_id=user.id,
                    email=user.email,
                    display_name=user.display_name,
                    is_active=user.is_active,
                    roles=roles,
                    tools=tools,
                )
            )
        return result

    async def set_user_active(self, user_id: UUID, *, is_active: bool, actor_email: str | None = None) -> AuthorizationUser | None:
        user = await self._repository.update_user_active(user_id, is_active=is_active)
        if user is None:
            return None
        roles = await self._repository.get_role_names(user.id)
        tools = await self._repository.get_user_allowlist_tools(user.id)
        await self._repository.create_audit_log(
            event_type="admin_user_status_updated",
            actor_email=actor_email,
            tool_name=None,
            metadata={"target_user_id": str(user.id), "is_active": is_active},
        )
        return AuthorizationUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=roles,
            tools=tools,
        )

    async def list_tools(self) -> list[str]:
        return list(KNOWN_TOOLS)

    async def set_user_tools(
        self,
        user_id: UUID,
        tool_names: list[str],
        *,
        actor_email: str | None = None,
    ) -> AuthorizationUser | None:
        user = await self._repository.get_user_by_id(user_id)
        if user is None:
            return None

        normalized = sorted({name.strip() for name in tool_names if name.strip()})
        unknown = [name for name in normalized if name not in KNOWN_TOOLS]
        if unknown:
            raise UnknownToolError(unknown)

        role_name = f"user:{user_id}"
        await self._repository.ensure_role(role_name)
        await self._repository.assign_role(user_id, role_name)
        await self._repository.replace_role_tool_permissions(role_name, normalized)

        roles = await self._repository.get_role_names(user.id)
        tools = await self._repository.get_user_allowlist_tools(user.id)
        await self._repository.create_audit_log(
            event_type="admin_user_tools_updated",
            actor_email=actor_email,
            tool_name=None,
            metadata={"target_user_id": str(user.id), "tools": tools},
        )
        return AuthorizationUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=roles,
            tools=tools,
        )


async def get_authorization_service() -> AsyncGenerator[AuthorizationService, None]:
    async with _session_factory() as session:
        repository = SQLAuthorizationRepository(session)
        service = AuthorizationService(repository=repository)
        try:
            yield service
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_current_auth_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    jwt_service: JWTService = Depends(get_jwt_service),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthorizationUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        payload = jwt_service.decode_token(credentials.credentials)
    except AuthInvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    try:
        user = await auth_service.get_user_by_email(email=subject)
    except AuthInvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    return AuthorizationUser(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        roles=user.roles,
        tools=[],
    )

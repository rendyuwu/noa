from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from noa_api.core.auth.errors import AuthInvalidCredentialsError, AuthPendingApprovalError
from noa_api.core.auth.jwt_service import JWTService
from noa_api.core.auth.ldap_service import LDAPService
from noa_api.storage.postgres.models import Role, User, UserRole


@dataclass
class AuthResult:
    access_token: str
    expires_in: int
    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str]


@dataclass
class AuthUser:
    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str]


class AuthUserRecord(Protocol):
    id: UUID
    email: str
    display_name: str | None
    is_active: bool


class AuthRepositoryProtocol(Protocol):
    async def get_user_by_email(self, email: str) -> AuthUserRecord | None: ...

    async def create_user(
        self,
        *,
        email: str,
        ldap_dn: str | None,
        display_name: str | None,
        is_active: bool,
    ) -> AuthUserRecord: ...

    async def update_user(
        self,
        user: Any,
        *,
        ldap_dn: str | None = None,
        display_name: str | None = None,
        is_active: bool | None = None,
    ) -> AuthUserRecord: ...

    async def ensure_role(self, name: str) -> str: ...

    async def assign_role(self, user_id: UUID, role_name: str) -> None: ...

    async def get_role_names(self, user_id: UUID) -> list[str]: ...


class SQLAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def create_user(self, *, email: str, ldap_dn: str | None, display_name: str | None, is_active: bool) -> User:
        user = User(email=email, ldap_dn=ldap_dn, display_name=display_name, is_active=is_active)
        self._session.add(user)
        await self._session.flush()
        return user

    async def update_user(
        self,
        user: User,
        *,
        ldap_dn: str | None = None,
        display_name: str | None = None,
        is_active: bool | None = None,
    ) -> User:
        if ldap_dn is not None:
            user.ldap_dn = ldap_dn
        if display_name is not None:
            user.display_name = display_name
        if is_active is not None:
            user.is_active = is_active
        await self._session.flush()
        return user

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

    async def get_role_names(self, user_id: UUID) -> list[str]:
        result = await self._session.execute(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
        )
        return sorted({str(name) for name in result.scalars().all()})


class AuthService:
    def __init__(
        self,
        *,
        auth_repository: AuthRepositoryProtocol,
        ldap_service: LDAPService,
        jwt_service: JWTService,
        bootstrap_admin_emails: set[str],
    ) -> None:
        self._auth_repository = auth_repository
        self._ldap_service = ldap_service
        self._jwt_service = jwt_service
        self._bootstrap_admin_emails = {email.lower() for email in bootstrap_admin_emails}

    async def authenticate(self, *, email: str, password: str) -> AuthResult:
        normalized_email = email.strip().lower()
        if not normalized_email or not password:
            raise AuthInvalidCredentialsError("Invalid credentials")

        ldap_user = await self._ldap_service.authenticate(normalized_email, password)
        user = await self._auth_repository.get_user_by_email(normalized_email)
        is_bootstrap_admin = normalized_email in self._bootstrap_admin_emails

        if user is None:
            user = await self._auth_repository.create_user(
                email=normalized_email,
                ldap_dn=ldap_user.dn,
                display_name=ldap_user.display_name,
                is_active=is_bootstrap_admin,
            )
        else:
            user = await self._auth_repository.update_user(
                user,
                ldap_dn=ldap_user.dn,
                display_name=ldap_user.display_name,
            )

        if is_bootstrap_admin:
            await self._auth_repository.ensure_role("admin")
            await self._auth_repository.assign_role(user.id, "admin")
            if not user.is_active:
                user = await self._auth_repository.update_user(user, is_active=True)

        if not user.is_active:
            raise AuthPendingApprovalError("User pending approval")

        role_names = await self._auth_repository.get_role_names(user.id)
        access_token, expires_in = self._jwt_service.create_access_token(user.email, user.id)
        return AuthResult(
            access_token=access_token,
            expires_in=expires_in,
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=role_names,
        )

    async def get_user_by_email(self, *, email: str) -> AuthUser:
        normalized_email = email.strip().lower()
        user = await self._auth_repository.get_user_by_email(normalized_email)
        if user is None:
            raise AuthInvalidCredentialsError("Invalid token")

        role_names = await self._auth_repository.get_role_names(user.id)
        return AuthUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=role_names,
        )

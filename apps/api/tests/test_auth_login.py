from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from noa_api.api.routes.auth import router as auth_router
from noa_api.core.auth.auth_service import AuthResult, AuthService
from noa_api.core.auth.errors import AuthInvalidCredentialsError, AuthPendingApprovalError
from noa_api.core.auth.ldap_service import LdapUser
from noa_api.core.auth.deps import get_auth_service


@dataclass
class _User:
    id: UUID
    email: str
    ldap_dn: str | None = None
    display_name: str | None = None
    is_active: bool = False


class _InMemoryAuthRepository:
    def __init__(self) -> None:
        self.users: dict[str, _User] = {}
        self.roles: set[str] = set()
        self.user_roles: dict[UUID, set[str]] = {}

    async def get_user_by_email(self, email: str) -> _User | None:
        return self.users.get(email)

    async def create_user(self, *, email: str, ldap_dn: str | None, display_name: str | None, is_active: bool) -> _User:
        user = _User(id=uuid4(), email=email, ldap_dn=ldap_dn, display_name=display_name, is_active=is_active)
        self.users[email] = user
        return user

    async def update_user(
        self,
        user: _User,
        *,
        ldap_dn: str | None = None,
        display_name: str | None = None,
        is_active: bool | None = None,
    ) -> _User:
        user.ldap_dn = ldap_dn if ldap_dn is not None else user.ldap_dn
        user.display_name = display_name if display_name is not None else user.display_name
        user.is_active = is_active if is_active is not None else user.is_active
        return user

    async def ensure_role(self, name: str) -> str:
        self.roles.add(name)
        return name

    async def assign_role(self, user_id: UUID, role_name: str) -> None:
        self.user_roles.setdefault(user_id, set()).add(role_name)

    async def get_role_names(self, user_id: UUID) -> list[str]:
        return sorted(self.user_roles.get(user_id, set()))


class _FakeLDAPService:
    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid

    async def authenticate(self, email: str, password: str) -> LdapUser:
        if self.invalid:
            raise AuthInvalidCredentialsError("Invalid credentials")
        return LdapUser(email=email, dn=f"CN={email}", display_name="Example User")


class _FakeJWTService:
    def create_access_token(self, email: str, user_id: UUID) -> tuple[str, int]:
        return f"token:{email}:{user_id}", 3600


class _FakeRouteAuthService:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode

    async def authenticate(self, *, email: str, password: str) -> AuthResult:
        if self.mode == "invalid":
            raise AuthInvalidCredentialsError("Invalid credentials")
        if self.mode == "pending":
            raise AuthPendingApprovalError("Pending approval")

        return AuthResult(
            access_token="jwt-token",
            expires_in=3600,
            user_id=uuid4(),
            email=email,
            display_name="Route User",
            is_active=True,
            roles=["admin"],
        )


async def test_auth_service_auto_provisions_pending_user() -> None:
    repo = _InMemoryAuthRepository()
    service = AuthService(
        auth_repository=repo,
        ldap_service=_FakeLDAPService(),
        jwt_service=_FakeJWTService(),
        bootstrap_admin_emails=set(),
    )

    try:
        await service.authenticate(email="new.user@example.com", password="secret")
        assert False, "Expected AuthPendingApprovalError"
    except AuthPendingApprovalError:
        created = repo.users["new.user@example.com"]
        assert created.is_active is False
        assert created.display_name == "Example User"


async def test_auth_service_bootstrap_admin_auto_active_and_issues_jwt() -> None:
    repo = _InMemoryAuthRepository()
    service = AuthService(
        auth_repository=repo,
        ldap_service=_FakeLDAPService(),
        jwt_service=_FakeJWTService(),
        bootstrap_admin_emails={"admin@example.com"},
    )

    result = await service.authenticate(email="admin@example.com", password="secret")

    assert result.access_token.startswith("token:admin@example.com:")
    assert result.is_active is True
    assert "admin" in result.roles
    assert repo.users["admin@example.com"].is_active is True


async def test_login_route_maps_auth_errors_and_success() -> None:
    app = FastAPI()
    app.include_router(auth_router)

    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(mode="invalid")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        invalid_response = await client.post("/auth/login", json={"email": "user@example.com", "password": "bad"})
    assert invalid_response.status_code == 401

    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(mode="pending")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pending_response = await client.post("/auth/login", json={"email": "user@example.com", "password": "ok"})
    assert pending_response.status_code == 403

    app.dependency_overrides[get_auth_service] = lambda: _FakeRouteAuthService(mode="ok")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        success_response = await client.post("/auth/login", json={"email": "user@example.com", "password": "ok"})
    assert success_response.status_code == 200
    payload = success_response.json()
    assert payload["access_token"] == "jwt-token"
    assert payload["token_type"] == "bearer"
    assert payload["user"]["email"] == "user@example.com"

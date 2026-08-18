"""An app carrying the real `/admin/users` routes, over in-memory doubles (T51).

Same split every route test in this suite uses: the router, `require_admin`,
`require_session_user`, the real `AuthService`, the real `AuthorizationService`, the real
`JWTService` and the shared error handler are all production code — only SQL and LDAP are faked.
So a 403 here is the shipped 403, a 409 is the shipped 409, and the V12 guards run for real.
`SQLAuthorizationRepository` gets its own live-database coverage in `test_rbac_repository.py`.

**Two repositories, one identity.** The session path resolves the caller through
`support.auth`'s `FakeAuthRepository`, while the admin routes read and write
`support.rbac`'s `FakeAuthorizationRepository` — the same split production has, where
`AuthService` and `AuthorizationService` hold different repositories over one session.
`sign_in` therefore writes the actor into *both*, under one id. Without that, `actor_user_id`
could never equal a target's id and V12's self-deactivate, self-delete and self-demote refusals
would be unreachable from HTTP — the tests would pass while asserting nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth.authorization_service import AuthorizationService
from core.auth.jwt_service import JWTService
from core.auth.tool_catalog import TOOL_CATALOG
from core.config import Settings
from core.db.models import ADMIN_ROLE_NAME
from noa_api.api.deps import (
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
    get_auth_service,
    get_authorization_service,
)
from noa_api.api.errors import install_error_handling
from noa_api.api.routes.admin_users import router as admin_users_router
from support.auth import (
    COOKIE_NAME,
    FakeAuthRepository,
    FakeUserRow,
    build_settings,
    override_auth_service_factory,
)
from support.rbac import FakeAuthorizationRepository, FakeUserRecord, RecordingAuditSink

ADMIN_EMAIL = "admin@example.com"
OPERATOR_EMAIL = "operator@example.com"

USERS_PATH = "/admin/users"


@dataclass
class SignedInUser:
    """One actor, as both doubles see them.

    Held together so a test that flips `is_active` knows which row it is flipping: `session` is
    what `require_session_user` re-reads (V6), `record` is what the admin routes read and write.
    """

    id: UUID
    email: str
    session: FakeUserRow
    record: FakeUserRecord


@dataclass
class AdminHarness:
    """A client plus every double behind it, so assertions read off one object."""

    client: TestClient
    app: FastAPI
    settings: Settings
    jwt_service: JWTService
    auth_repository: FakeAuthRepository
    repository: FakeAuthorizationRepository
    audit: RecordingAuditSink

    def sign_in(
        self,
        email: str = ADMIN_EMAIL,
        *,
        roles: tuple[str, ...] = (ADMIN_ROLE_NAME,),
        mcp_tokens: int = 0,
    ) -> SignedInUser:
        """Put an active user's session cookie on the client, mirrored into both doubles."""
        session_row = self.auth_repository.add_active_user(email, roles=roles)
        record = self.repository.add_user(
            email, user_id=session_row.id, roles=roles, mcp_tokens=mcp_tokens
        )
        issued = self.jwt_service.create_access_token(email=email, user_id=session_row.id)
        self.client.cookies.set(COOKIE_NAME, issued.token)
        return SignedInUser(id=session_row.id, email=email, session=session_row, record=record)

    def add_target(
        self,
        email: str = OPERATOR_EMAIL,
        *,
        is_active: bool = True,
        roles: tuple[str, ...] = (),
        mcp_tokens: int = 0,
    ) -> FakeUserRecord:
        """A user the admin routes act on. No session: they never sign in."""
        return self.repository.add_user(
            email, is_active=is_active, roles=roles, mcp_tokens=mcp_tokens
        )

    def grant(self, role_name: str, *tool_names: str) -> None:
        """Give a role tool grants, through the same shape the SQL stores them in."""
        self.repository.grant(role_name, *tool_names)

    def list_users(self) -> list[dict[str, object]]:
        """`GET /admin/users` → the `users` array, for tests that assert on the list."""
        response = self.client.get(USERS_PATH)
        assert response.status_code == 200, response.text
        users = response.json()["users"]
        assert isinstance(users, list)
        return users

    def user_in_list(self, email: str) -> dict[str, object]:
        return next(user for user in self.list_users() if user["email"] == email)


@contextmanager
def admin_harness(
    *,
    settings: Settings | None = None,
    known_tools: frozenset[str] = TOOL_CATALOG,
) -> Iterator[AdminHarness]:
    """An app with only the `/admin/users` routes, wired to in-memory doubles.

    Two dependency overrides and nothing else: `get_auth_service` (so the cookie resolves
    without Postgres or LDAP) and `get_authorization_service` (so the routes read the fake
    RBAC repository). `require_admin` itself is never overridden — it is the thing under test on
    every route.
    """
    resolved_settings = settings or build_settings()
    auth_repository = FakeAuthRepository()
    repository = FakeAuthorizationRepository()
    audit = RecordingAuditSink()
    jwt_service = JWTService(resolved_settings)

    app = FastAPI()
    install_error_handling(app)
    app.include_router(admin_users_router)

    # The same attributes `noa_api.main.lifespan` writes, minus the engine no test here needs.
    setattr(app.state, STATE_SETTINGS, resolved_settings)
    setattr(app.state, STATE_JWT_SERVICE, jwt_service)
    setattr(app.state, STATE_LDAP_SERVICE, None)
    setattr(app.state, STATE_SESSION_FACTORY, None)

    app.dependency_overrides[get_auth_service] = override_auth_service_factory(
        settings=resolved_settings, repository=auth_repository, jwt_service=jwt_service
    )
    app.dependency_overrides[get_authorization_service] = lambda: AuthorizationService(
        repository=repository, audit_sink=audit, known_tools=known_tools
    )

    with TestClient(app) as client:
        yield AdminHarness(
            client=client,
            app=app,
            settings=resolved_settings,
            jwt_service=jwt_service,
            auth_repository=auth_repository,
            repository=repository,
            audit=audit,
        )


__all__ = [
    "ADMIN_EMAIL",
    "OPERATOR_EMAIL",
    "USERS_PATH",
    "AdminHarness",
    "SignedInUser",
    "admin_harness",
]

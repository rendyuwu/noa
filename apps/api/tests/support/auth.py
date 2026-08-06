"""Doubles and an app builder for the `/auth` routes (T8).

Postgres is not required for any route test: the app is built with
`dependency_overrides` pointing `get_auth_service` at an `AuthService` whose repository
and rate-limit store are in-memory dicts. The *real* `JWTService` and the real
`AuthService` run — only I/O is faked, so the cookie, the activation gate (V7), the
limiter policy (V9) and the per-request row re-read (V6) are all exercised for real.

`SQLAuthRepository` / `SQLLoginRateLimitRepository` get their own coverage against a
live scratch database in `test_auth_repository.py`, skip-gated the way
`test_migrations.py` is.

Two settings choices worth knowing:

- `auth_session_cookie_domain=None`. Production scopes the cookie to `.noa.internal`
  (V40), which `TestClient` will not store for its `testserver` host — the cookie would
  silently vanish and every `/auth/me` assertion would pass for the wrong reason. Domain
  attributes themselves are asserted directly off `Set-Cookie` in `test_jwt_service.py`.
- `auth_dev_bypass_ldap=False`. The fake directory *is* the double here; the bypass would
  short-circuit the very error paths under test.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.auth.auth_service import AuthService
from core.auth.errors import AuthError, AuthInvalidCredentialsError
from core.auth.jwt_service import JWTService
from core.auth.ldap_service import LdapUser
from core.auth.login_rate_limiter import LoginRateLimitBucket, LoginRateLimiter
from core.config import Settings
from noa_api.api.deps import (
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
    get_auth_service,
)
from noa_api.api.errors import install_error_handler
from noa_api.api.routes.auth import router as auth_router

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_PASSWORD = "operator-password"
OPERATOR_DISPLAY_NAME = "Example Operator"

ADMIN_EMAIL = "bootstrap-admin@example.com"

JWT_SECRET = "s" * 64
COOKIE_NAME = "noa_session"


def build_settings(**overrides: object) -> Settings:
    """Settings from explicit values only — the developer's `.env` cannot leak in."""
    defaults: dict[str, object] = {
        "environment": "test",
        "auth_jwt_secret": JWT_SECRET,
        "auth_session_cookie_name": COOKIE_NAME,
        # See module docstring: `TestClient` drops a `.noa.internal` cookie.
        "auth_session_cookie_domain": None,
        "auth_dev_bypass_ldap": False,
        "auth_bootstrap_admin_emails": [],
    }
    return Settings(_env_file=None, **{**defaults, **overrides})  # type: ignore[arg-type]


# --- Repository double ---


@dataclass
class FakeUserRow:
    """Stands in for a `users` row. Mutable, like the ORM object it replaces."""

    id: UUID
    email: str
    ldap_dn: str | None = None
    display_name: str | None = None
    is_active: bool = False
    last_login_at: datetime | None = None


class FakeAuthRepository:
    """In-memory `AuthRepository`.

    `commit()` snapshots every row so the test can distinguish "written" from
    "committed". That distinction is the whole point of T8's explicit transaction
    boundary: V7 requires a first login's user row to survive the pending-approval
    raise, and V9 requires a recorded failure to survive the error path. Both are
    invisible to a double that just mutates a dict.
    """

    def __init__(self) -> None:
        self.users: dict[UUID, FakeUserRow] = {}
        self.roles: set[str] = set()
        self.user_roles: dict[UUID, set[str]] = {}
        self.commits = 0
        self.committed_users: dict[UUID, FakeUserRow] = {}
        self.committed_user_roles: dict[UUID, set[str]] = {}

    # --- Users ---

    async def get_user_by_email(self, email: str) -> FakeUserRow | None:
        return next((user for user in self.users.values() if user.email == email), None)

    async def get_user_by_id(self, user_id: UUID) -> FakeUserRow | None:
        return self.users.get(user_id)

    async def create_user(
        self,
        *,
        email: str,
        ldap_dn: str | None,
        display_name: str | None,
        is_active: bool,
    ) -> FakeUserRow:
        user = FakeUserRow(
            id=uuid4(),
            email=email,
            ldap_dn=ldap_dn,
            display_name=display_name,
            is_active=is_active,
        )
        self.users[user.id] = user
        return user

    async def update_user(
        self,
        user: FakeUserRow,
        *,
        ldap_dn: str | None = None,
        display_name: str | None = None,
        is_active: bool | None = None,
        last_login_at: datetime | None = None,
    ) -> FakeUserRow:
        if ldap_dn is not None:
            user.ldap_dn = ldap_dn
        if display_name is not None:
            user.display_name = display_name
        if is_active is not None:
            user.is_active = is_active
        if last_login_at is not None:
            user.last_login_at = last_login_at
        return user

    # --- Roles ---

    async def ensure_role(self, name: str) -> str:
        self.roles.add(name)
        return name

    async def assign_role(self, user_id: UUID, role_name: str) -> None:
        if role_name in self.roles:
            self.user_roles.setdefault(user_id, set()).add(role_name)

    async def get_role_names(self, user_id: UUID) -> list[str]:
        return sorted(self.user_roles.get(user_id, set()))

    async def commit(self) -> None:
        self.commits += 1
        self.committed_users = {user_id: replace(user) for user_id, user in self.users.items()}
        self.committed_user_roles = {
            user_id: set(names) for user_id, names in self.user_roles.items()
        }

    # --- Test helpers ---

    def add_active_user(self, email: str, *, roles: tuple[str, ...] = ()) -> FakeUserRow:
        user = FakeUserRow(
            id=uuid4(),
            email=email,
            ldap_dn=f"CN={email}",
            display_name=OPERATOR_DISPLAY_NAME,
            is_active=True,
        )
        self.users[user.id] = user
        for role in roles:
            self.roles.add(role)
            self.user_roles.setdefault(user.id, set()).add(role)
        return user

    def committed_user_by_email(self, email: str) -> FakeUserRow | None:
        return next((user for user in self.committed_users.values() if user.email == email), None)


# --- Directory double ---


@dataclass
class FakeDirectory:
    """Stands in for `LDAPService.authenticate`.

    `error` lets a test choose the failure class, which matters for the rate limiter:
    only `AuthInvalidCredentialsError` may move a counter, so `LdapUnavailableError` has
    to be injectable to prove it does not.
    """

    password: str = OPERATOR_PASSWORD
    error: AuthError | None = None
    display_name: str | None = OPERATOR_DISPLAY_NAME
    calls: list[str] = field(default_factory=list)

    async def authenticate(self, email: str, password: str) -> LdapUser:
        self.calls.append(email)
        if self.error is not None:
            raise self.error
        if password != self.password:
            raise AuthInvalidCredentialsError("fake directory rejected the bind")
        return LdapUser(
            email=email,
            dn=f"CN={email},OU=Staff,dc=example,dc=com",
            display_name=self.display_name,
        )


# --- Rate-limit store double ---


class FakeRateLimitRepository:
    """In-memory `LoginRateLimitRepository`.

    `cleared` records every `clear_bucket` call, so a test can assert a successful login
    reset the counters instead of inferring it from a later attempt succeeding.
    """

    def __init__(self) -> None:
        self.buckets: dict[tuple[str, str], LoginRateLimitBucket] = {}
        self.cleared: list[tuple[str, str]] = []

    async def get_bucket(self, scope: str, scope_key: str) -> LoginRateLimitBucket | None:
        return self.buckets.get((scope, scope_key))

    async def upsert_bucket(
        self,
        scope: str,
        scope_key: str,
        *,
        attempt_count: int,
        window_started_at: datetime,
        blocked_until: datetime | None,
    ) -> LoginRateLimitBucket:
        bucket = LoginRateLimitBucket(
            attempt_count=attempt_count,
            window_started_at=window_started_at,
            blocked_until=blocked_until,
        )
        self.buckets[(scope, scope_key)] = bucket
        return bucket

    async def clear_bucket(self, scope: str, scope_key: str) -> None:
        self.cleared.append((scope, scope_key))
        self.buckets.pop((scope, scope_key), None)


# --- Dependency override ---


def override_auth_service_factory(
    *,
    settings: Settings,
    repository: FakeAuthRepository,
    jwt_service: JWTService,
    directory: FakeDirectory | None = None,
    rate_limits: FakeRateLimitRepository | None = None,
) -> Callable[[], AuthService]:
    """A `get_auth_service` override wired to the doubles passed in.

    Extracted from `auth_harness` so T9's `support.rbac` probe app can put the same real
    `AuthService` behind `require_admin` without a second copy of this wiring (V66). The
    limiter policy is read off `settings`, never hardcoded, so a test that narrows the
    window still exercises the real thresholds.
    """
    resolved_directory = directory or FakeDirectory()
    resolved_rate_limits = rate_limits or FakeRateLimitRepository()

    def override() -> AuthService:
        return AuthService(
            repository=repository,
            directory=resolved_directory,
            jwt_service=jwt_service,
            rate_limiter=LoginRateLimiter(
                resolved_rate_limits,
                window_seconds=settings.auth_login_rate_limit_window_seconds,
                max_attempts=settings.auth_login_rate_limit_max_attempts,
                block_seconds=settings.auth_login_rate_limit_block_seconds,
            ),
            bootstrap_admin_emails=settings.auth_bootstrap_admin_emails,
        )

    return override


# --- App builder ---


@dataclass
class AuthHarness:
    """Everything a route test pokes at, so assertions read off one object."""

    client: TestClient
    app: FastAPI
    settings: Settings
    jwt_service: JWTService
    repository: FakeAuthRepository
    directory: FakeDirectory
    rate_limits: FakeRateLimitRepository

    def login(self, *, email: str = OPERATOR_EMAIL, password: str = OPERATOR_PASSWORD) -> Any:
        return self.client.post("/auth/login", json={"email": email, "password": password})

    def set_session_cookie_for(self, user_id: UUID, email: str) -> str:
        """Put a freshly minted cookie on the client without going through login.

        Lets a test hold a *valid* session and then change the world behind it — the
        shape V6's re-read rule is about.
        """
        issued = self.jwt_service.create_access_token(email=email, user_id=user_id)
        self.client.cookies.set(COOKIE_NAME, issued.token)
        return issued.token


@contextmanager
def auth_harness(
    *,
    settings: Settings | None = None,
    repository: FakeAuthRepository | None = None,
    directory: FakeDirectory | None = None,
    rate_limits: FakeRateLimitRepository | None = None,
) -> Iterator[AuthHarness]:
    """An app with only the `/auth` routes, wired to in-memory doubles.

    `get_auth_service` is the single override. Everything else — the router, the error
    handler, `JWTService`, `AuthService`, `LoginRateLimiter` — is production code.
    """
    resolved_settings = settings or build_settings()
    resolved_repository = repository or FakeAuthRepository()
    resolved_directory = directory or FakeDirectory()
    resolved_rate_limits = rate_limits or FakeRateLimitRepository()
    jwt_service = JWTService(resolved_settings)

    app = FastAPI()
    install_error_handler(app)
    app.include_router(auth_router)

    # Set directly rather than through a lifespan: these are the same attributes
    # `noa_api.main.lifespan` writes, minus the engine no test here needs.
    setattr(app.state, STATE_SETTINGS, resolved_settings)
    setattr(app.state, STATE_JWT_SERVICE, jwt_service)
    setattr(app.state, STATE_LDAP_SERVICE, None)
    setattr(app.state, STATE_SESSION_FACTORY, None)

    app.dependency_overrides[get_auth_service] = override_auth_service_factory(
        settings=resolved_settings,
        repository=resolved_repository,
        jwt_service=jwt_service,
        directory=resolved_directory,
        rate_limits=resolved_rate_limits,
    )

    with TestClient(app) as client:
        yield AuthHarness(
            client=client,
            app=app,
            settings=resolved_settings,
            jwt_service=jwt_service,
            repository=resolved_repository,
            directory=resolved_directory,
            rate_limits=resolved_rate_limits,
        )


__all__ = [
    "ADMIN_EMAIL",
    "COOKIE_NAME",
    "JWT_SECRET",
    "OPERATOR_DISPLAY_NAME",
    "OPERATOR_EMAIL",
    "OPERATOR_PASSWORD",
    "AuthHarness",
    "FakeAuthRepository",
    "FakeDirectory",
    "FakeRateLimitRepository",
    "FakeUserRow",
    "auth_harness",
    "build_settings",
    "override_auth_service_factory",
]

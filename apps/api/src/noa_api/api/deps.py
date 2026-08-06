"""Request-scoped dependencies (T8).

Long-lived objects — `Settings`, `JWTService`, `LDAPService`, the engine, the session
factory — are built once in the app lifespan and read off `app.state` here. T8 requires
that for `JWTService` specifically: its algorithm allowlist and key-length guards raise
at construction, so building it per request turns a configuration error into a 500 on
the first login instead of a failure to boot. The rest follow the same rule because a
per-request engine would open a fresh pool every request.

Per-request objects — the DB session, the repositories, the rate limiter, `AuthService`
— are built here, one set per request, and share a single transaction.

`require_session_user` is the important export. Every session-authenticated route in
this app depends on it, so the `users.is_active` re-read V6 demands happens once,
centrally, and a new route cannot forget it. `require_admin` (T9) layers on top of it, so
the role check always happens *after* that re-read — a disabled admin loses the panel on
their next request, not at cookie expiry.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated, Final, TypeVar, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.admin_events import StructlogAdminAuditSink
from core.auth.auth_repository import SQLAuthRepository, SQLLoginRateLimitRepository
from core.auth.auth_service import AuthService, SessionUser
from core.auth.authorization_errors import AdminAccessRequiredError
from core.auth.authorization_repository import SQLAuthorizationRepository
from core.auth.authorization_service import AuthorizationService
from core.auth.errors import AuthSessionInvalidError
from core.auth.jwt_service import JWTService
from core.auth.ldap_service import LDAPService
from core.auth.login_rate_limiter import LoginRateLimiter
from core.config import Settings
from core.db.models import ADMIN_ROLE_NAME

# `app.state` keys, written by the lifespan in `noa_api.main`.
STATE_SETTINGS: Final = "settings"
STATE_JWT_SERVICE: Final = "jwt_service"
STATE_LDAP_SERVICE: Final = "ldap_service"
STATE_SESSION_FACTORY: Final = "session_factory"

DETAIL_NO_SESSION_COOKIE = "no `noa_session` cookie on the request"

T = TypeVar("T")


def _from_state(request: Request, key: str, expected: type[T]) -> T:
    """Read a lifespan-built object off `app.state`, type-checked.

    Both failure messages exist because their absence costs real debugging time. A
    `TestClient(app)` used without entering its context manager never runs the lifespan
    and would otherwise fail with a bare `AttributeError` on an internal attribute name;
    an overridden dependency that returns the wrong type would surface much later, in
    whatever code first called a method on it.
    """
    try:
        value = getattr(request.app.state, key)
    except AttributeError:
        raise RuntimeError(
            f"app.state.{key} is missing — the app lifespan did not run. "
            "Use `with TestClient(app):` or an ASGI server that runs lifespan events."
        ) from None

    if not isinstance(value, expected):
        raise RuntimeError(f"app.state.{key} is {type(value).__name__}, not {expected.__name__}")
    return value


def get_settings_dep(request: Request) -> Settings:
    """Process settings, resolved once at startup (T5)."""
    return _from_state(request, STATE_SETTINGS, Settings)


def get_jwt_service(request: Request) -> JWTService:
    """The single `JWTService`, constructed at startup (T8, V6)."""
    return _from_state(request, STATE_JWT_SERVICE, JWTService)


def get_ldap_service(request: Request) -> LDAPService:
    """The single `LDAPService` (T6, C4)."""
    return _from_state(request, STATE_LDAP_SERVICE, LDAPService)


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the app's engine (C3)."""
    factory = _from_state(request, STATE_SESSION_FACTORY, async_sessionmaker)
    return cast("async_sessionmaker[AsyncSession]", factory)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """One session per request, rolled back if the handler raises.

    The rollback is not redundant with closing: it releases the transaction as the
    error propagates rather than at teardown, so a failed request cannot hold locks
    while exception handlers run.
    """
    async with get_session_factory(request)() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
JWTServiceDep = Annotated[JWTService, Depends(get_jwt_service)]
LDAPServiceDep = Annotated[LDAPService, Depends(get_ldap_service)]


def get_auth_service(
    session: SessionDep,
    settings: SettingsDep,
    jwt_service: JWTServiceDep,
    ldap_service: LDAPServiceDep,
) -> AuthService:
    """`AuthService` wired to this request's session (T8).

    Repositories and the rate limiter share that one session, so a recorded login
    failure and a provisioned user row commit or roll back together (V7, V9).
    """
    return AuthService(
        repository=SQLAuthRepository(session),
        directory=ldap_service,
        jwt_service=jwt_service,
        rate_limiter=LoginRateLimiter(
            SQLLoginRateLimitRepository(session),
            window_seconds=settings.auth_login_rate_limit_window_seconds,
            max_attempts=settings.auth_login_rate_limit_max_attempts,
            block_seconds=settings.auth_login_rate_limit_block_seconds,
        ),
        bootstrap_admin_emails=settings.auth_bootstrap_admin_emails,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def require_session_user(
    request: Request,
    jwt_service: JWTServiceDep,
    auth_service: AuthServiceDep,
) -> SessionUser:
    """Resolve the caller from the `noa_session` cookie, re-reading the row (V6).

    Three gates, all raising `AuthError` subclasses the shared handler turns into
    responses: no cookie → 401 `session_invalid`; cookie that fails signature/claim
    verification or is past `exp` → 401 (V79 pins zero clock leeway); row absent or
    `is_active=False` → 401 / 403.

    The third gate is the one V6 makes non-optional: the session JWT has no revocation
    path before `exp`, so this re-read is the only thing that stops a disabled
    operator's cookie from working for the rest of its TTL.
    """
    token = jwt_service.read_session_cookie(request.cookies)
    if token is None:
        raise AuthSessionInvalidError(DETAIL_NO_SESSION_COOKIE)

    claims = jwt_service.decode_token(token)
    return await auth_service.resolve_session_user(claims.user_id)


SessionUserDep = Annotated[SessionUser, Depends(require_session_user)]


def get_authorization_service(session: SessionDep) -> AuthorizationService:
    """The RBAC engine wired to this request's session (T9).

    Built per request, like `AuthService`, so a role change and its audit event share one
    transaction. The audit sink is constructed here rather than kept on `app.state` because
    `StructlogAdminAuditSink` holds only a logger; when a database-backed sink lands it will
    need this request's session anyway.

    The tool catalog is left at its default (`core.auth.tool_catalog.TOOL_CATALOG`, V10).
    T13 replaces that default with the live FastMCP registry.
    """
    return AuthorizationService(
        repository=SQLAuthorizationRepository(session),
        audit_sink=StructlogAdminAuditSink(),
    )


AuthorizationServiceDep = Annotated[AuthorizationService, Depends(get_authorization_service)]


async def require_admin(current_user: SessionUserDep) -> SessionUser:
    """Gate every `/admin` route on the `admin` role (V13).

    Depends on `require_session_user`, so the V6 row re-read runs first and the roles
    checked here are the ones in the database, never the cookie's claims — the session JWT
    carries no role claim precisely so this cannot be spoofed by an old cookie.

    403, not 404: hiding the admin surface from an authenticated operator buys nothing (the
    routes are in the OpenAPI schema) and would make a permission problem look like a
    broken deployment.
    """
    if ADMIN_ROLE_NAME not in current_user.roles:
        raise AdminAccessRequiredError(
            f"`{current_user.email}` holds roles {current_user.roles!r}, not `{ADMIN_ROLE_NAME}`"
        )
    return current_user


AdminUserDep = Annotated[SessionUser, Depends(require_admin)]


__all__ = [
    "AdminUserDep",
    "AuthServiceDep",
    "AuthorizationServiceDep",
    "JWTServiceDep",
    "LDAPServiceDep",
    "SessionDep",
    "SessionUserDep",
    "SettingsDep",
    "get_auth_service",
    "get_authorization_service",
    "get_db_session",
    "get_jwt_service",
    "get_ldap_service",
    "get_session_factory",
    "get_settings_dep",
    "require_admin",
    "require_session_user",
]

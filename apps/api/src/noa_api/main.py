"""FastAPI application factory (T3, T8).

Live routes: `/health` (V51) and `/auth` (T8). The MCP app is built in
`noa_api.mcp_server` but not mounted — mounting needs the `TokenVerifier` from T11, and
an unauthenticated `/mcp` would breach V1. T13 does the mount and will wrap this
lifespan with `fastmcp.utilities.lifespan.combine_lifespans` (R6).

Everything expensive or fail-fast is built in the lifespan, once, and read off
`app.state` by `noa_api.api.deps`:

- `JWTService` — T8 requires this specifically. Its algorithm allowlist and RFC 7518 key
  length check raise at construction, so a per-request build would turn a config error
  into a 500 on the first login attempt instead of a refusal to start.
- `LDAPService` — holds settings and a connect factory; no reason to rebuild per request.
- engine + session factory — a per-request engine would open a fresh connection pool on
  every request. The lifespan owns disposal, so connections cannot outlive the app.

The engine is created but never connected here: `create_async_engine` is lazy, so an app
pointed at an unreachable database still starts and answers `/health`. That is
deliberate — a liveness probe that needs Postgres cannot report "the API is up but the
database is not".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.auth.jwt_service import JWTService
from core.auth.ldap_service import LDAPService
from core.config import Settings, get_settings
from core.db.session import create_engine, create_session_factory
from noa_api import __version__
from noa_api.api.deps import (
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
)
from noa_api.api.errors import install_auth_error_handler
from noa_api.api.routes.auth import router as auth_router

TITLE = "NOA API"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the long-lived services, then dispose the engine on shutdown.

    A construction failure here is a boot failure, which is the point: bad
    `AUTH_JWT_ALGORITHM` or a secret too short for it should stop the process, not wait
    to surface as a 500 on someone's login.
    """
    settings: Settings = get_settings()
    engine = create_engine(settings)

    setattr(app.state, STATE_SETTINGS, settings)
    setattr(app.state, STATE_JWT_SERVICE, JWTService(settings))
    setattr(app.state, STATE_LDAP_SERVICE, LDAPService(settings))
    setattr(app.state, STATE_SESSION_FACTORY, create_session_factory(engine))

    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    """Build the FastAPI app.

    Router sets still to land: `/admin` (T51-T55), `/action-requests` (T37), `/mcp` (T13).
    """
    app = FastAPI(title=TITLE, version=__version__, lifespan=lifespan)

    install_auth_error_handler(app)
    app.include_router(auth_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe (V51). No dependencies: it must answer with Postgres down."""
        return {"status": "ok"}

    return app


app = create_app()

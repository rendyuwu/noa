"""FastAPI application factory (T3, T8, T13).

Live surfaces: `/health` (V51), `/auth` (T8) and the mounted MCP server at `/mcp` (T13).

**What is built where, and why it moved.** T8 put every long-lived object in the lifespan.
T13 splits that in two, because mounting the MCP app forces the order: `http_app()` reads
`self.auth` when it builds the authentication middleware, and the FastAPI app needs
`mcp_app.lifespan` at construction (R6). So the verifier — and therefore the session
factory and the directory it resolves identities against — must exist *before* `FastAPI(...)`
is called. `AppRuntime` holds those, built in `create_app`:

- engine + session factory — one pool per app, as before. `create_async_engine` is lazy, so
  an app pointed at an unreachable database still starts and answers `/health`. That is
  deliberate: a liveness probe that needs Postgres cannot report "the API is up but the
  database is not".
- `LDAPService` — holds settings and a connect factory; nothing to fail at construction.

`JWTService` stays in the lifespan, and that is not a leftover. T8 requires it specifically:
its algorithm allowlist and RFC 7518 key-length check raise at construction, so building it
here would turn a configuration error into a failure at import time of this module rather
than a clean startup failure the server reports. The lifespan also still owns engine
disposal, so connections cannot outlive the app.

`noa_api.api.deps` reads all of it off `app.state`, which the lifespan writes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from fastmcp.utilities.lifespan import combine_lifespans
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
from noa_api.api.errors import install_error_handler
from noa_api.api.routes.auth import router as auth_router
from noa_api.mcp_request_auth import build_mcp_auth_context
from noa_api.mcp_server import MCP_MOUNT_PATH, build_mcp_http_app
from noa_api.mcp_tools.context import build_mcp_tool_context

TITLE = "NOA API"


@dataclass(frozen=True)
class AppRuntime:
    """The objects one app instance owns for its whole life.

    A value object rather than four locals because two places need the same four: the MCP
    verifier's auth context (built before the app exists) and the lifespan (which publishes
    them on `app.state` and disposes the engine). Passing the group around is what keeps the
    MCP path and the FastAPI dependency graph reading the *same* session factory and the
    same directory, rather than two that happen to be configured alike.
    """

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    ldap_service: LDAPService


def build_runtime(settings: Settings) -> AppRuntime:
    """Construct one app's long-lived objects. Opens no connection (the engine is lazy)."""
    engine = create_engine(settings)
    return AppRuntime(
        settings=settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        ldap_service=LDAPService(settings),
    )


def build_lifespan(
    runtime: AppRuntime,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The app's own lifespan: publish `app.state`, then dispose the engine on shutdown.

    `JWTService` is constructed inside, once (T8). A construction failure here is a boot
    failure, which is the point: a bad `AUTH_JWT_ALGORITHM`, or a secret too short for it,
    should stop the process rather than wait to surface as a 500 on someone's login.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setattr(app.state, STATE_SETTINGS, runtime.settings)
        setattr(app.state, STATE_JWT_SERVICE, JWTService(runtime.settings))
        setattr(app.state, STATE_LDAP_SERVICE, runtime.ldap_service)
        setattr(app.state, STATE_SESSION_FACTORY, runtime.session_factory)

        try:
            yield
        finally:
            await runtime.engine.dispose()

    return lifespan


def create_app() -> FastAPI:
    """Build the FastAPI app with the MCP server mounted at `/mcp` (T13, I.mcp).

    Router sets still to land: `/admin` (T51-T55) and `/action-requests` (T37).

    Three things about the mount are load-bearing:

    - **The verifier is built here, not in the lifespan.** `http_app()` snapshots
      `self.auth` into the authentication middleware, so an app assembled without one would
      serve `/mcp` unauthenticated for its whole life (V1).
    - **`combine_lifespans` runs both** (R6). The MCP app's lifespan starts the
      streamable-HTTP session manager; skip it and every MCP request fails at the transport
      while `/health` looks fine.
    - **`/mcp` versus `/mcp/`.** Starlette's mount matches the sub-app at the stripped path,
      so the endpoint answers at `/mcp/` and a request to `/mcp` gets a 307 to it. 307
      preserves method and body, so a redirect-following client works either way; T57 should
      still configure the trailing-slash URL so the hot path costs no extra round trip.
    """
    runtime = build_runtime(get_settings())

    # T12's production wiring: the same session factory and directory the rest of the app
    # uses, so an operator disabled through `/admin` is refused on their next MCP call by
    # the row this reads (V1), not by a second copy of the world. T19's tool context reads
    # that same factory for the same reason — the RBAC gate in front of every tool has to
    # see the grant an admin wrote a moment ago, not a pool of its own.
    mcp_app = build_mcp_http_app(
        auth_context=build_mcp_auth_context(
            session_factory=runtime.session_factory,
            directory=runtime.ldap_service,
            settings=runtime.settings,
        ),
        tool_context=build_mcp_tool_context(session_factory=runtime.session_factory),
    )

    app = FastAPI(
        title=TITLE,
        version=__version__,
        lifespan=combine_lifespans(build_lifespan(runtime), mcp_app.lifespan),
    )

    install_error_handler(app)
    app.include_router(auth_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe (V51). No dependencies: it must answer with Postgres down."""
        return {"status": "ok"}

    # Mounted last: the sub-app has no NOA exception handlers and no OpenAPI presence, so
    # anything registered after it would be easy to mistake for part of the admin surface.
    app.mount(MCP_MOUNT_PATH, mcp_app)

    return app


app = create_app()

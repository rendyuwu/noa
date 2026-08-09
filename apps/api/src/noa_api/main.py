"""FastAPI application factory (T3, T8, T13, T37).

Live surfaces: `/health` (V51), `/auth` (T8), `/action-requests` (T37) and the mounted MCP
server at `/mcp` (T13).

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
- `SecretCipher` — the one instance, from `NOA_SECRET_ENCRYPTION_KEY` (C7, T21). Here rather
  than in the lifespan because the tool context is built before `FastAPI(...)` too, and
  because construction *does* fail on a bad key: that is a boot failure, by the same rule
  `JWTService` follows below.

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

from core.approvals.execution_host import AsyncioApprovedChangeExecutor
from core.approvals.expiry import PendingExpirySweeper
from core.approvals.reaper import StrandedRunReaper
from core.auth.jwt_service import JWTService
from core.auth.ldap_service import LDAPService
from core.config import Settings, get_settings
from core.db.session import create_engine, create_session_factory
from core.secrets.crypto import SecretCipher
from noa_api import __version__
from noa_api.api.deps import (
    STATE_APPROVED_CHANGE_EXECUTOR,
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
)
from noa_api.api.errors import install_error_handling
from noa_api.api.routes.action_requests import router as action_requests_router
from noa_api.api.routes.auth import router as auth_router
from noa_api.mcp_request_auth import build_mcp_auth_context
from noa_api.mcp_server import MCP_MOUNT_PATH, build_mcp_http_app
from noa_api.mcp_tools.change_runners import build_change_runners
from noa_api.mcp_tools.context import McpToolContext, build_mcp_tool_context

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
    secret_cipher: SecretCipher
    # The tool context, held rather than rebuilt: the MCP mount needs it and so does the
    # executor's runner map (T38), and two contexts would be two worlds configured alike.
    tool_context: McpToolContext
    # T37's approvals hand a started run to this; T38 filled it with the real asyncio host
    # (V29, V30). One per app, not one per request — a per-request executor would leave nobody
    # holding its outstanding tasks at shutdown.
    #
    # Typed to the host, not to `ApprovedChangeExecutor`, and deliberately: the lifespan below
    # has to `stop()` it, while the Protocol declares `start` alone so that
    # `ActionDecisionService` — which is handed one through `app.state` — cannot reach a method
    # whose job is shutting the app's background work down.
    approved_change_executor: AsyncioApprovedChangeExecutor
    # T39's background half of V32. One per app for the same reason, and it draws sessions
    # from the factory above so a sweep sees the same rows every other path does.
    expiry_sweeper: PendingExpirySweeper
    # T38's background half of V30: the runs a died-mid-call process left STARTED. Same shape,
    # same loop, same lifespan ownership as the sweeper above.
    stranded_run_reaper: StrandedRunReaper


def build_runtime(settings: Settings) -> AppRuntime:
    """Construct one app's long-lived objects. Opens no connection (the engine is lazy)."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    # One cipher for the whole app (C7, V48). Every decrypt site takes it as an argument —
    # there is no module-level cipher to import (T15) — so this is the only place it is built,
    # and T54's admin routes will read the same one off `AppRuntime`.
    secret_cipher = SecretCipher.from_settings(settings)
    tool_context = build_mcp_tool_context(
        session_factory=session_factory,
        secret_cipher=secret_cipher,
        # V32's deadline, resolved once here rather than read again inside the gate:
        # `get_settings()` is called in exactly one place (T5) and the CHANGE gate (T33)
        # stamps `action_requests.expires_at` from this value.
        pending_ttl_seconds=settings.approval_pending_ttl_seconds,
    )
    return AppRuntime(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        ldap_service=LDAPService(settings),
        secret_cipher=secret_cipher,
        tool_context=tool_context,
        # T38's real executor (V29, V30). Constructing it starts nothing: it owns tasks only
        # once an approval hands it a run, and `stop()` in the lifespan is what ends them.
        # `build_change_runners` is empty until T22-T29 land, which the registry's coverage
        # check makes a startup guarantee rather than a hope.
        approved_change_executor=AsyncioApprovedChangeExecutor(
            session_factory=session_factory,
            runners=build_change_runners(context=tool_context),
        ),
        # Constructed here, started by the lifespan (T39, V32). Constructing it opens nothing
        # — like the engine above, it is inert until the lifespan says otherwise, so building
        # an app still costs no connection.
        expiry_sweeper=PendingExpirySweeper(
            session_factory=session_factory,
            interval_seconds=settings.approval_expiry_sweep_interval_seconds,
        ),
        # The same arrangement for T38's reaper (V30). Three settings, none derived from
        # another: how often it looks is a resolution, how long a run may sit STARTED is a
        # lifetime, and how much one pass may resolve is a bound (V92). Batch over interval is
        # the drain rate, which is the number `core.config` argues.
        stranded_run_reaper=StrandedRunReaper(
            session_factory=session_factory,
            interval_seconds=settings.approval_stranded_run_reap_interval_seconds,
            reap_after_seconds=settings.approval_stranded_run_reap_after_seconds,
            batch_size=settings.approval_stranded_run_reap_batch_size,
        ),
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
        setattr(app.state, STATE_APPROVED_CHANGE_EXECUTOR, runtime.approved_change_executor)

        # V32's terminality without traffic (T39) and V30's reaper (T38). Started here rather
        # than at construction because the tasks belong to the running loop, and stopped before
        # the engine is disposed below — a pass still in flight would otherwise run against a
        # dead pool.
        await runtime.expiry_sweeper.start()
        await runtime.stranded_run_reaper.start()

        try:
            yield
        finally:
            # **Order is load-bearing, and it is executor first.** An approved change in flight
            # holds a session; cancelling it leaves its run STARTED, which is the reaper's to
            # resolve — so the reaper must still be alive to see the shutdown's own leftovers on
            # its next boot, and nothing may still be *starting* work once the loops stop. Every
            # one of the three has to finish before `dispose()`, because the pool they draw from
            # is gone the moment it returns (T39's rule, three components now).
            await runtime.approved_change_executor.stop()
            await runtime.stranded_run_reaper.stop()
            await runtime.expiry_sweeper.stop()
            await runtime.engine.dispose()

    return lifespan


def create_app() -> FastAPI:
    """Build the FastAPI app with the MCP server mounted at `/mcp` (T13, I.mcp).

    Router set still to land: `/admin` (T51-T55).

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
        # The runtime's own context, not a second one built here (T38): the executor's runner
        # map is derived from it too, and two contexts would be two worlds configured alike —
        # the failure mode `AppRuntime` exists to prevent one field over.
        tool_context=runtime.tool_context,
    )

    app = FastAPI(
        title=TITLE,
        version=__version__,
        lifespan=combine_lifespans(build_lifespan(runtime), mcp_app.lifespan),
    )

    install_error_handling(app)
    app.include_router(auth_router)
    # The only writer of a terminal `action_requests.status` (T37, V22, V28). On the FastAPI
    # side of the app deliberately: it is reached by a cookie POST from a NOA-origin
    # document, never through the MCP mount below.
    app.include_router(action_requests_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe (V51). No dependencies: it must answer with Postgres down."""
        return {"status": "ok"}

    # Mounted last: the sub-app has no NOA exception handlers and no OpenAPI presence, so
    # anything registered after it would be easy to mistake for part of the admin surface.
    app.mount(MCP_MOUNT_PATH, mcp_app)

    return app


app = create_app()

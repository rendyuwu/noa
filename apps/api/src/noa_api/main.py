"""FastAPI application factory.

Live surfaces: `/health`, `/auth`, `/action-requests` and the mounted MCP server at `/mcp`.

**What is built where, and why it moved.** The login flow put every long-lived object in the
lifespan. Mounting the MCP app splits that in two, because it forces the order: `http_app()` reads
`self.auth` when it builds the authentication middleware, and the FastAPI app needs
`mcp_app.lifespan` at construction. So the verifier — and therefore the session
factory and the directory it resolves identities against — must exist *before* `FastAPI(...)`
is called. `AppRuntime` holds those, built in `create_app`:

- engine + session factory — one pool per app, as before. `create_async_engine` is lazy, so
  an app pointed at an unreachable database still starts and answers `/health`. That is
  deliberate: a liveness probe that needs Postgres cannot report "the API is up but the
  database is not".
- `LDAPService` — holds settings and a connect factory; nothing to fail at construction.
- `SecretCipher` — the one instance, from `NOA_SECRET_ENCRYPTION_KEY`. Here rather
  than in the lifespan because the tool context is built before `FastAPI(...)` too, and
  because construction *does* fail on a bad key: that is a boot failure, by the same rule
  `JWTService` follows below.

`JWTService` stays in the lifespan, and that is not a leftover. The login flow requires it
specifically: its algorithm allowlist and RFC 7518 key-length check raise at construction, so
building it here would turn a configuration error into a failure at import time of this module
rather than a clean startup failure the server reports. The lifespan also still owns engine
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
from core.secrets.delivery import build_yopass_delivery
from noa_api import __version__
from noa_api.api.deps import (
    STATE_APPROVED_CHANGE_EXECUTOR,
    STATE_JWT_SERVICE,
    STATE_LDAP_SERVICE,
    STATE_SECRET_CIPHER,
    STATE_SESSION_FACTORY,
    STATE_SETTINGS,
    STATE_TOOL_LIST_NOTIFIER,
)
from noa_api.api.errors import install_error_handling
from noa_api.api.routes.action_requests import router as action_requests_router
from noa_api.api.routes.admin_action_requests import router as admin_action_requests_router
from noa_api.api.routes.admin_audit import router as admin_audit_router
from noa_api.api.routes.admin_roles import router as admin_roles_router
from noa_api.api.routes.admin_servers import pmg_router as admin_pmg_servers_router
from noa_api.api.routes.admin_servers import proxmox_router as admin_proxmox_servers_router
from noa_api.api.routes.admin_servers import whm_router as admin_whm_servers_router
from noa_api.api.routes.admin_users import router as admin_users_router
from noa_api.api.routes.auth import router as auth_router
from noa_api.api.routes.mcp_tokens import admin_router as admin_tokens_router
from noa_api.api.routes.mcp_tokens import me_router as me_tokens_router
from noa_api.api.routes.result_tables import router as result_tables_router
from noa_api.mcp_notifications import McpSessionRegistry, McpToolListChangedNotifier
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
    # executor's runner map, and two contexts would be two worlds configured alike.
    tool_context: McpToolContext
    # The decision endpoints hand a started run to this; the approved-change executor filled it with
    # the real asyncio host. One per app, not one per request — a per-request executor would leave
    # nobody holding its outstanding tasks at shutdown. Typed to the host, not to
    # `ApprovedChangeExecutor`, and deliberately: the lifespan below has to `stop()` it, while the
    # Protocol declares `start` alone so that `ActionDecisionService` — which is handed one through
    # `app.state` — cannot reach a method whose job is shutting the app's background work down.
    approved_change_executor: AsyncioApprovedChangeExecutor
    # The expiry loop's background half of the TTL-to-EXPIRED rule. One per app for the same
    # reason, and it draws sessions from the factory above so a sweep sees the same rows every
    # other path does.
    expiry_sweeper: PendingExpirySweeper
    # The executor's background half — the reaper for runs a died-mid-call process left STARTED.
    # Same shape, same loop, same lifespan ownership as the sweeper above.
    stranded_run_reaper: StrandedRunReaper
    # The permission-change notifier's two halves, backstopped by the execution-time RBAC
    # re-check, held together because they are one object seen from two sides:
    # the MCP mount's middleware writes the register, and the admin surface's notifier reads it.
    # Built here for the same reason the tool context is — two registers would be two worlds
    # configured alike, and the failure would be silent (an emit to nobody).
    mcp_session_registry: McpSessionRegistry
    tool_list_notifier: McpToolListChangedNotifier


def build_runtime(settings: Settings) -> AppRuntime:
    """Construct one app's long-lived objects. Opens no connection (the engine is lazy)."""
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    # One cipher for the whole app. Every decrypt site takes it as an argument —
    # there is no module-level cipher to import — so this is the only place it is built,
    # and the admin server routes will read the same one off `AppRuntime`.
    secret_cipher = SecretCipher.from_settings(settings)
    tool_context = build_mcp_tool_context(
        session_factory=session_factory,
        secret_cipher=secret_cipher,
        # The TTL deadline, resolved once here rather than read again inside the gate:
        # `get_settings()` is called in exactly one place and the CHANGE gate
        # stamps `action_requests.expires_at` from this value.
        pending_ttl_seconds=settings.approval_pending_ttl_seconds,
        # The embed approval address, resolved here for the same reason: the CHANGE gate builds
        # every approval URL off this base, and a second `get_settings()` caller inside the gate is
        # how one deployment ends up handing out two different origins.
        embed_base_url=settings.noa_embed_base_url,
        # The large-READ table's two numbers, resolved here for the same reason as the two above: a
        # large READ parks its rows with this lifetime and this cap, and a second `get_settings()`
        # caller inside a tool is how two tools end up capping at two different counts.
        result_table_ttl_seconds=settings.result_table_ttl_seconds,
        result_table_max_rows=settings.result_table_max_rows,
        # The yopass delivery hop, bound once here for the reason every line above is:
        # `_yopass_store` needs three settings and there is no settings singleton to reach for.
        # Absent `YOPASS_BASE_URL` is *not* a boot failure — the reset tool reports
        # `yopass_not_configured` when an approved change reaches delivery, which is the delivery
        # design's own call and keeps a deployment that uses no Proxmox tools from being blocked by
        # them.
        secret_delivery=build_yopass_delivery(settings=settings),
        # The generated-password length, resolved here rather than read inside the generator, so the
        # two places that could answer "how long is a NOA-generated password" stay one.
        secret_password_length=settings.secret_password_length,
    )
    # The permission-change notifier, backstopped by the execution-time RBAC re-check. One
    # register per app, and the notifier over it, so the MCP middleware and the
    # admin routes share it. Constructing both opens nothing and starts nothing: the register is
    # an empty dict of weak sets until a client connects.
    mcp_session_registry = McpSessionRegistry()
    return AppRuntime(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        ldap_service=LDAPService(settings),
        secret_cipher=secret_cipher,
        tool_context=tool_context,
        # The approved-change executor. Constructing it starts nothing: it owns tasks only once an
        # approval hands it a run, and `stop()` in the lifespan is what ends them.
        # `build_change_runners` covers the CHANGE tools that exist and grows with each new one;
        # that it covers *every* registered one is the registry's coverage check, which makes it a
        # startup guarantee rather than a hope.
        approved_change_executor=AsyncioApprovedChangeExecutor(
            session_factory=session_factory,
            runners=build_change_runners(context=tool_context),
        ),
        # Constructed here, started by the lifespan. Constructing it opens nothing
        # — like the engine above, it is inert until the lifespan says otherwise, so building
        # an app still costs no connection.
        expiry_sweeper=PendingExpirySweeper(
            session_factory=session_factory,
            interval_seconds=settings.approval_expiry_sweep_interval_seconds,
        ),
        # The same arrangement for the executor's reaper. Three settings, none derived from
        # another: how often it looks is a resolution, how long a run may sit STARTED is a
        # lifetime, and how much one pass may resolve is a bound. Batch over interval is
        # the drain rate, which is the number `core.config` argues.
        stranded_run_reaper=StrandedRunReaper(
            session_factory=session_factory,
            interval_seconds=settings.approval_stranded_run_reap_interval_seconds,
            reap_after_seconds=settings.approval_stranded_run_reap_after_seconds,
            batch_size=settings.approval_stranded_run_reap_batch_size,
        ),
        mcp_session_registry=mcp_session_registry,
        tool_list_notifier=McpToolListChangedNotifier(registry=mcp_session_registry),
    )


def build_lifespan(
    runtime: AppRuntime,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The app's own lifespan: publish `app.state`, then dispose the engine on shutdown.

    `JWTService` is constructed inside, once. A construction failure here is a boot
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
        # The permission-change notifier, backstopped by the execution-time RBAC re-check: what
        # `get_authorization_service` hands the RBAC engine, so a committed
        # permission change reaches the MCP sessions the mount registered.
        setattr(app.state, STATE_TOOL_LIST_NOTIFIER, runtime.tool_list_notifier)
        # The admin server routes: the *same* cipher the MCP tool path holds through
        # `McpToolContext`, not a second one built per request. A credential the admin routes
        # encrypt and a tool decrypts has to be under one key, and one construction site is how that
        # stays true.
        setattr(app.state, STATE_SECRET_CIPHER, runtime.secret_cipher)

        # The TTL's terminality without traffic and the stranded-run reaper. Started here rather
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
            # is gone the moment it returns (the expiry loop's rule, three components now).
            await runtime.approved_change_executor.stop()
            await runtime.stranded_run_reaper.stop()
            await runtime.expiry_sweeper.stop()
            await runtime.engine.dispose()

    return lifespan


def create_app() -> FastAPI:
    """Build the FastAPI app with the MCP server mounted at `/mcp`.

    Router set still to land: the audit half of `/admin`.

    Three things about the mount are load-bearing:

    - **The verifier is built here, not in the lifespan.** `http_app()` snapshots
      `self.auth` into the authentication middleware, so an app assembled without one would
      serve `/mcp` unauthenticated for its whole life.
    - **`combine_lifespans` runs both**. The MCP app's lifespan starts the
      streamable-HTTP session manager; skip it and every MCP request fails at the transport
      while `/health` looks fine.
    - **`/mcp` versus `/mcp/`.** Starlette's mount matches the sub-app at the stripped path,
      so the endpoint answers at `/mcp/` and a request to `/mcp` gets a 307 to it. 307 preserves
      method and body, so a redirect-following client works either way; the LibreChat config should
      still configure the trailing-slash URL so the hot path costs no extra round trip.
    """
    runtime = build_runtime(get_settings())

    # The MCP identity resolver's production wiring: the same session factory and directory the
    # rest of the app uses, so an operator disabled through `/admin` is refused on their next MCP
    # call by the row this reads, not by a second copy of the world. Every tool call reads
    # that same factory for the same reason — the RBAC gate in front of every tool has to
    # see the grant an admin wrote a moment ago, not a pool of its own.
    mcp_app = build_mcp_http_app(
        auth_context=build_mcp_auth_context(
            session_factory=runtime.session_factory,
            directory=runtime.ldap_service,
            settings=runtime.settings,
        ),
        # The runtime's own context, not a second one built here: the executor's runner
        # map is derived from it too, and two contexts would be two worlds configured alike —
        # the failure mode `AppRuntime` exists to prevent one field over.
        tool_context=runtime.tool_context,
        # The permission-change notifier: the same register the notifier on `app.state` reads.
        # Passing the runtime's object rather than letting the mount build its own is the whole
        # point — a second register would be written by the middleware and read by nobody, and the
        # emit would reach zero sessions with nothing failing.
        session_registry=runtime.mcp_session_registry,
    )

    app = FastAPI(
        title=TITLE,
        version=__version__,
        lifespan=combine_lifespans(build_lifespan(runtime), mcp_app.lifespan),
    )

    install_error_handling(app)
    app.include_router(auth_router)
    # The only writer of a terminal `action_requests.status`. On the FastAPI
    # side of the app deliberately: it is reached by a cookie POST from a NOA-origin
    # document, never through the MCP mount below.
    app.include_router(action_requests_router)
    # The large-READ table surface. Read-only and cookie-authenticated, beside the
    # decision routes rather than inside them: a parked listing has nothing to authorise, and
    # the service behind this one can write nothing at all.
    app.include_router(result_tables_router)
    # Admin user management. Every route behind `require_admin` and
    # therefore behind the `is_active` row re-read: the panel's own surface, never the LLM's —
    # the MCP mount below cannot reach it, and this router holds no tool.
    app.include_router(admin_users_router)
    # Admin role management. Same gate, same reasoning: it decides which tools a role grants, so it
    # is the write "permissions take effect immediately" is about — and the surface a
    # prompt-injected tool name must never reach, which the MCP mount below cannot do.
    app.include_router(admin_roles_router)
    # MCP token management. The routes that issue the credential the MCP server's
    # whole surface authenticates with — so they are the one admin surface whose output is a
    # secret, and the reason `MintedTokenResponse` is the only model in this app carrying a
    # plaintext.
    app.include_router(admin_tokens_router)
    # The self-service half of the same pair. Behind `require_session_user` rather than
    # `require_admin`: the id it acts on is the session's, never the request's, so an operator
    # can reach their own credentials and no one else's.
    app.include_router(me_tokens_router)
    # Server inventory: WHM, Proxmox, PMG. Three routers because they are
    # three tables, one module because they share the validate answer shape and the field
    # validators (`routes/admin_servers.py`). Same `require_admin` gate as the rest of `/admin`,
    # and the only admin surface that opens a connection to a third-party host — which is why
    # its validate services draw their own sessions rather than this request's (see
    # `noa_api.api.deps.get_whm_server_validation_service`).
    app.include_router(admin_whm_servers_router)
    app.include_router(admin_proxmox_servers_router)
    app.include_router(admin_pmg_servers_router)
    # The audit trail. Reads `tool_runs` and nothing else — the writers are
    # the MCP tool path and the approval executor, all on the far side of the cookie/CSRF
    # boundary, and the service this router holds has no `commit` to write with. It closes the
    # tool-run trail's last clause: the rows have existed since the tool-run writer landed and
    # until now nothing could ask about them.
    app.include_router(admin_audit_router)
    # The authorisation trail beside the execution one. Reads
    # `action_requests` and `action_receipts`; the audit router above reads `tool_runs` and could
    # never answer "who approved this, and why" because `reason` lives on the other table and had
    # no reader anywhere. Registered here rather than beside `action_requests_router` at the top
    # of this function, and the distance is the point: that one holds the single writer of a
    # terminal status and is reached by a cookie POST from the embed, this one is `require_admin`
    # and holds a repository with no `commit` and no statement that is not a `SELECT`. Same table,
    # opposite capabilities, and nothing in the MCP mount below can see either.
    app.include_router(admin_action_requests_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe. No dependencies: it must answer with Postgres down."""
        return {"status": "ok"}

    # Mounted last: the sub-app has no NOA exception handlers and no OpenAPI presence, so
    # anything registered after it would be easy to mistake for part of the admin surface.
    app.mount(MCP_MOUNT_PATH, mcp_app)

    return app


app = create_app()

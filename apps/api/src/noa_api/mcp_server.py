"""The FastMCP server and its ASGI app (T13 — I.mcp, V1, V3, R3, R6, R7, R8).

Protocol era = handshake, negotiated per `initialize` (C23). LibreChat is the sole MCP
client and locks `@modelcontextprotocol/sdk` at exactly 1.29.0, whose client sends its own
`LATEST_PROTOCOL_VERSION` — `2025-11-25` (R9) — so that is the era this deployment answers
with, and `Mcp-Session-Id` is in play. NOA does not choose a digit: `mcp==1.29.0` echoes
whatever the client asks for when it is in `SUPPORTED_PROTOCOL_VERSIONS` (R8), which is why
an older client asking `2025-06-18` keeps working. The sessionless `2026-07-28` era is in
neither SDK's list, so reaching it is an SDK bump rather than a setting. `fastmcp==3.4.5`
is pinned for this; the pin is a decision, not an accident.

Two functions, because two things need to be separable: what the server *is* (name, auth,
and from T19-T31/T63 its tools) and how it becomes an ASGI app (`http_app`). Verified
against the installed `fastmcp==3.4.5` rather than docs:

- **`auth=` takes the verifier instance directly** (R3). `TokenVerifier` already subclasses
  `AuthProvider`, so a provider wrapper would only exist to add RFC 9728 metadata routes
  NOA has no use for (C5: per-user minted tokens, not OAuth).
- **`auth` is read at `http_app()` time, not at request time** — `http_app` passes
  `auth=self.auth` into `create_streamable_http_app`, which builds the authentication
  middleware once. That is why `build_mcp_server` takes the verifier as an argument instead
  of a module-level singleton getting one attached later: an app built before the verifier
  exists is an app that never authenticates, and V1 would fail silently rather than loudly.
- **No `stateless_http`, `json_response`, `host`, `port`, `log_level` on the constructor**
  (R7) — v3 raises `TypeError` for those; they belong on `http_app()` or `FASTMCP_*` env.
  Nothing here passes `stateless_http`, so sessions stay on and `Mcp-Session-Id` is minted
  per `initialize` (R8), which every era C23 admits expects.
- **The middleware is not optional.** `TokenVerifier.verify_token` has no response hook
  (R2), so without `McpAuthErrorMiddleware` every refusal collapses to the SDK's bare
  `invalid_token` and V3's named bodies — `librechat_user_header_missing` versus
  `librechat_user_mismatch` — do not ship. `fastmcp` appends caller middleware *after* its
  auth middleware, which is exactly where a reader of the stashed refusal has to sit
  (see `noa_api.mcp_request_auth`).

`MCP_APP_PATH` is `/` rather than `/mcp` because `noa_api.main` mounts this app at `/mcp`:
Starlette strips the mount prefix, so the route inside the sub-app is the root, and the
middleware's `mcp_path` has to agree with the *stripped* path. Leaving fastmcp's default
(`/mcp`) would put the endpoint at `/mcp/mcp`.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.http import StarletteWithLifespan
from starlette.middleware import Middleware

from noa_api.mcp_audit import ToolRunAuditMiddleware
from noa_api.mcp_auth import NoaTokenVerifier
from noa_api.mcp_rbac import RbacToolMiddleware
from noa_api.mcp_request_auth import McpAuthContext, McpAuthErrorMiddleware
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.registry import register_mcp_tools

# Shown to the client in `initialize`; also what `librechat.yaml` labels the server (T57).
SERVER_NAME = "NOA"

# Where `noa_api.main` mounts the sub-app, and the path inside it once Starlette has
# stripped that prefix. Kept together because changing one without the other yields either
# `/mcp/mcp` or a 404, and neither failure names its cause.
MCP_MOUNT_PATH = "/mcp"
MCP_APP_PATH = "/"


def build_mcp_server(*, tool_context: McpToolContext, auth: AuthProvider | None = None) -> FastMCP:
    """The FastMCP server: its tools, its RBAC gate and its authentication provider.

    One server per app rather than a module-level singleton: `create_app()` is called more
    than once in the test suite, and a shared instance would carry one app's verifier into
    another's mount.

    `auth=None` builds an unauthenticated server. Nothing in production passes that — V1
    requires every MCP request to resolve a user — but the default keeps a tool-registry
    test from having to construct a token verifier it will not use.

    Four things happen here and all four belong together (T19, T73):

    - **Tools are registered** (`register_mcp_tools`, T19-T31 and T63). That call is also
      the guard that every exposed name is in `TOOL_CATALOG`, so a name no role can be
      granted fails at construction (V10, C22), and it is where each tool's `ToolRisk` is
      declared (V20).
    - **`RbacToolMiddleware` is attached** (V1), and it is handed the names that were just
      registered. Registering a tool and gating it are the same decision: a server that
      exposes a tool without the gate serves it to every authenticated operator regardless
      of role, and the failure is invisible — the tool works. The registered set travels
      with it so a catalogued-but-unbuilt name is refused in NOA's shape rather than
      fastmcp's (V10; see `noa_api.mcp_rbac`).
    - **`ToolRunAuditMiddleware` is attached** (V45, V83b), for the same reason and with the
      same argument: a tool cannot forget a middleware. **Order matters and is load-bearing.**
      `FastMCP._run_middleware` composes over `reversed(self.middleware)`, so the first added
      is the outermost — RBAC decides, then audit wraps the execution. Added the other way
      round, a *registered* tool whose grant was revoked would still be classified as a READ,
      so its denial would be written as a `tool_runs` row for a call that never ran. (An
      unregistered name would not: it is absent from the risk map, so the audit middleware
      passes it through either way. The order is pinned by
      `test_a_call_refused_by_rbac_writes_no_row`, which is the case that can tell.)
    - **`mask_error_details=True`** (V19, second line). fastmcp's default is `False`, and
      its unmasked branch raises `ToolError(f"Error calling tool {name!r}: {e}")` — `str(e)`
      verbatim in front of the model. The first line is `sanitize_tool_errors` on each tool
      (`noa_api.mcp_tools.results`); this catches whatever is raised outside one, such as
      argument validation or a bug in the registration wrapper.
    """
    server = FastMCP(SERVER_NAME, auth=auth, mask_error_details=True)
    registered = register_mcp_tools(server, context=tool_context)
    server.add_middleware(
        RbacToolMiddleware(context=tool_context, registered_tools=frozenset(registered))
    )
    server.add_middleware(ToolRunAuditMiddleware(context=tool_context, tool_risks=registered))
    return server


def build_mcp_http_app(
    *, auth_context: McpAuthContext, tool_context: McpToolContext
) -> StarletteWithLifespan:
    """The mountable Streamable HTTP app, authenticated per T11/T12 (R6, V1, V3).

    Returns a Starlette app whose `lifespan` starts the session manager. It has to run:
    without it `StreamableHTTPASGIApp` has no session manager and every request fails at
    the transport. `noa_api.main` combines it with the app's own lifespan (R6).
    """
    server = build_mcp_server(
        tool_context=tool_context,
        auth=NoaTokenVerifier(context=auth_context),
    )

    return server.http_app(
        path=MCP_APP_PATH,
        middleware=[Middleware(McpAuthErrorMiddleware, mcp_path=MCP_APP_PATH)],
    )


__all__ = [
    "MCP_APP_PATH",
    "MCP_MOUNT_PATH",
    "SERVER_NAME",
    "build_mcp_http_app",
    "build_mcp_server",
]

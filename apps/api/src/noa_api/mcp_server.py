"""The FastMCP server and its ASGI app (T13 — I.mcp, V1, V3, R3, R6, R7, R8).

Protocol era = handshake `2025-06-18` (C23). LibreChat is the sole MCP client and declares
`@modelcontextprotocol/sdk: ^1.29.0`, so `initialize` + `Mcp-Session-Id` are in play, and
`mcp==1.29.0` serves that era from its `SUPPORTED_PROTOCOL_VERSIONS` (R8). `fastmcp==3.4.5`
is pinned for it; the pin is a decision, not an accident.

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
  per `initialize` (R8), which is what the era C23 pins expects.
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

from noa_api.mcp_auth import NoaTokenVerifier
from noa_api.mcp_request_auth import McpAuthContext, McpAuthErrorMiddleware

# Shown to the client in `initialize`; also what `librechat.yaml` labels the server (T57).
SERVER_NAME = "NOA"

# Where `noa_api.main` mounts the sub-app, and the path inside it once Starlette has
# stripped that prefix. Kept together because changing one without the other yields either
# `/mcp/mcp` or a 404, and neither failure names its cause.
MCP_MOUNT_PATH = "/mcp"
MCP_APP_PATH = "/"


def build_mcp_server(*, auth: AuthProvider | None = None) -> FastMCP:
    """The FastMCP server, with its authentication provider (R3).

    One server per app rather than a module-level singleton: `create_app()` is called more
    than once in the test suite, and a shared instance would carry one app's verifier into
    another's mount.

    `auth=None` builds an unauthenticated server. Nothing in production passes that — V1
    requires every MCP request to resolve a user — but the default keeps a tool-registry
    test from having to construct a token verifier it will not use.

    T19-T31 and T63 register the 14 exposed tools here, so the registry and the auth
    provider are decided in the same place.
    """
    return FastMCP(SERVER_NAME, auth=auth)


def build_mcp_http_app(*, auth_context: McpAuthContext) -> StarletteWithLifespan:
    """The mountable Streamable HTTP app, authenticated per T11/T12 (R6, V1, V3).

    Returns a Starlette app whose `lifespan` starts the session manager. It has to run:
    without it `StreamableHTTPASGIApp` has no session manager and every request fails at
    the transport. `noa_api.main` combines it with the app's own lifespan (R6).
    """
    server = build_mcp_server(auth=NoaTokenVerifier(context=auth_context))

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

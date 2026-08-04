"""FastMCP server instance.

Protocol era = handshake `2025-06-18` (C23). LibreChat is the sole MCP client and
declares `@modelcontextprotocol/sdk: ^1.29.0`, whose `LATEST_PROTOCOL_VERSION` is
`2025-06-18`, so `initialize` + `Mcp-Session-Id` are in play. `fastmcp==3.4.5`
speaks that era; the pin is a decision, not an accident.

Skeleton only. Later tasks fill this in:

- T11/T12: custom `TokenVerifier` + `resolve_mcp_identity`
- T13: mount into FastAPI via `mcp.http_app(path="/")`
- T19-T31, T63: the 14 exposed tools
"""

from fastmcp import FastMCP

SERVER_NAME = "NOA"

# No auth provider yet — T11 supplies the `TokenVerifier`, T13 wires it in.
# Until then this instance is not mounted, so it is unreachable (V1 holds).
mcp: FastMCP = FastMCP(SERVER_NAME)


def get_mcp_server() -> FastMCP:
    """Return the process-wide FastMCP server."""
    return mcp

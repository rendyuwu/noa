"""WHM READ tools (T19 — `whm_list_servers`; T20/T21 add the account readers).

`whm_list_servers` is exposed on purpose and it is the only `*_list_servers` that is
(DECISIONS §6.6, owner-decided 2026-08-04): the model has to know which servers exist before
it can name one, while Proxmox and PMG nodes are few and named directly by the operator, so
`proxmox_list_servers` and `pmg_list_servers` stay internal (§I.mcp).

**What leaves the process is `to_safe_dict()`, never the row.** That method is
`WHMServer`'s, it drops `api_token` and every SSH secret in favour of presence booleans
(V2, V8), and it is on `WHMServerRowLike` so this module cannot reach past it. The result
lands in a LibreChat transcript that persists in their MongoDB (V26), which is the reason
the rule is "no credential material", not "no plaintext password".

Two split responsibilities, both deliberate:

- The tool *function* takes a context and is directly unit-testable. Registration —
  the public name, description and annotations — is `register_whm_read_tools`, so the
  schema fastmcp derives comes from a signature with no context parameter in it.
- `sanitize_tool_errors` wraps the function, not the registration. A caller that reaches
  the function some other way (a future internal call, C9/V17) gets the same V19 guarantee.
"""

from __future__ import annotations

from fastmcp import FastMCP

from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import ToolPayload, sanitize_tool_errors, tool_ok

TOOL_WHM_LIST_SERVERS = "whm_list_servers"

DESCRIPTION_WHM_LIST_SERVERS = (
    "List the WHM/cPanel servers NOA is configured to manage. Returns each server's id, "
    "name and base URL. Use it to find the `server_ref` other WHM tools need, and prefer "
    "the id when two servers look alike. Read-only: it changes nothing."
)


@sanitize_tool_errors(TOOL_WHM_LIST_SERVERS)
async def whm_list_servers(*, context: McpToolContext) -> ToolPayload:
    """Every configured WHM server, credential-free (T19, V8)."""
    async with context.session_factory() as session:
        repository = context.whm_server_repository_factory(session)
        servers = await repository.list_servers()
        # Serialized inside the session: `to_safe_dict` reads mapped attributes, and a
        # detached instance would raise on a lazy refresh once the session closed.
        return tool_ok(servers=[server.to_safe_dict() for server in servers])


def register_whm_read_tools(server: FastMCP, *, context: McpToolContext) -> frozenset[str]:
    """Register the WHM READ tools on `server`; return their names (I.mcp).

    The returned set is what `registry.register_mcp_tools` checks against `TOOL_CATALOG`,
    and what a test compares against the server's awaited `list_tools()` — see
    `noa_api.mcp_tools.registry` for why the names travel back rather than being read off
    the server here.
    """

    @server.tool(
        name=TOOL_WHM_LIST_SERVERS,
        description=DESCRIPTION_WHM_LIST_SERVERS,
        # `readOnlyHint` is the MCP-standard way to say "this cannot change anything". It is
        # a hint to the client and nothing NOA relies on: the READ/CHANGE split that matters
        # is enforced by the approval gate (V16), not by an annotation a client may ignore.
        annotations={"readOnlyHint": True},
    )
    async def whm_list_servers_tool() -> ToolPayload:
        return await whm_list_servers(context=context)

    return frozenset({TOOL_WHM_LIST_SERVERS})


__all__ = [
    "DESCRIPTION_WHM_LIST_SERVERS",
    "TOOL_WHM_LIST_SERVERS",
    "register_whm_read_tools",
    "whm_list_servers",
]

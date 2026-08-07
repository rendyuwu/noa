"""The one place a tool name becomes callable over MCP (T19, T73 — I.mcp, V10, V20).

`build_mcp_server` calls this and nothing else registers tools. That single entry point is
what makes three properties checkable instead of hoped for:

- **Every exposed name is in `TOOL_CATALOG`.** RBAC grants are written against that catalog
  (T9), so a tool registered under a name outside it is a capability no role can be granted
  — and one the `admin` bypass would still reach, because the bypass hands out "every known
  tool" and the execution check only rejects names the catalog does not know (V10). The
  check below turns that into a startup failure rather than a permission surprise.
- **C22's never-implement names cannot appear.** They are absent from `TOOL_CATALOG`
  (`core.auth.tool_catalog`), so the same check refuses them. That is a management-policy
  boundary, not a technical one: re-adding one is an owner decision, and it should not be
  possible to do it by accident while wiring a tool.
- **Every exposed name has a declared `ToolRisk`** (T73, V20). The return value is a mapping
  rather than a set for exactly this: `ToolRunAuditMiddleware` stamps `risk` on the audit row
  and skips CHANGE tools, whose row belongs to the post-approval executor (T38, V46). A
  default would make a CHANGE tool that nobody remembered to classify record itself as a
  READ *and* write a row for a change that has not happened — silently, and in the audit
  trail. Declaring the risk beside the tool means a registrar cannot omit it.

`RegistryError` rather than `assert`: this runs at app construction, and `python -O` strips
asserts. A guard that vanishes under an optimization flag is not a guard.

**Why the names are returned rather than read back off the server.** fastmcp 3.4.5 exposes
its registry only through `async list_tools()`, and `build_mcp_server` is synchronous — so
the check here is against what each registrar *says* it registered. That is one assumption,
and it is closed by a test rather than trusted: `test_mcp_tool_rbac.py` asserts the awaited
`server.list_tools()` names equal these keys, so a registrar that under- or over-reports
fails there.

Registration order does not matter — `tools/list` is filtered per user (V1) and sorted by
the client — so tools are grouped by system for reading.
"""

from __future__ import annotations

from fastmcp import FastMCP

from core.auth.tool_catalog import TOOL_CATALOG
from core.db.lifecycle import ToolRisk
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.whm_read import register_whm_read_tools


class RegistryError(RuntimeError):
    """A tool was registered under a name the catalog does not know (V10, C22)."""


def register_mcp_tools(server: FastMCP, *, context: McpToolContext) -> dict[str, ToolRisk]:
    """Register every exposed tool on `server`; return name → risk (I.mcp, V20)."""
    registered: dict[str, ToolRisk] = dict(register_whm_read_tools(server, context=context))
    assert_names_in_catalog(frozenset(registered))
    return registered


def assert_names_in_catalog(names: frozenset[str]) -> None:
    """Refuse an uncatalogued tool name (V10, C22)."""
    unknown = sorted(names - TOOL_CATALOG)
    if unknown:
        raise RegistryError(
            f"tools registered outside `TOOL_CATALOG` (no role can be granted them): {unknown}"
        )


__all__ = [
    "RegistryError",
    "assert_names_in_catalog",
    "register_mcp_tools",
]

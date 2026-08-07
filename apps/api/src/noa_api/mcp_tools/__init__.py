"""The exposed MCP tools (T19-T31, T63 — I.mcp).

`noa_api.mcp_server` builds the server and mounts it; this package decides what that server
can *do*. The split is deliberate: a tool is a NOA capability that happens to be reachable
over MCP, not a piece of transport code.

Three rules hold for everything in here.

1. **A tool returns a structured result; it does not raise at the model.** Success is
   `{"ok": True, ...}`, refusal is `{"ok": False, "error_code", "message", "choices"}`.
   Ported from `noa-old` (C13), and it is what V18 requires — an ambiguous identifier is a
   result the model can act on, not an exception. `sanitize_tool_errors` (V19) is what makes
   the promise total.

2. **The registry is the only place a name becomes callable** (`registry.py`), and every
   registered name must be in `TOOL_CATALOG`. RBAC grants are written against that catalog,
   so a tool registered under a name outside it would be a capability no role can be
   granted — and, worse, one the admin bypass would still reach (V10).

3. **Nothing here decides whether the caller may call it.** That is
   `noa_api.mcp_rbac.RbacToolMiddleware` (V1), one gate for every tool, so a tool added in
   T20 cannot forget it.

Landed: `whm_list_servers` (T19). Still to come: the other thirteen of §I.mcp, and — before
any CHANGE tool — the approval gate (T32-T39).

**Known gap, recorded rather than hidden:** V45 wants a `tool_runs` row for every MCP READ.
`tool_runs` is created by T35, which has not been built, so tool calls are not yet audited.
"""

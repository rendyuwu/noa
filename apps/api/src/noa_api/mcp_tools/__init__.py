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

4. **Nothing here records that it ran.** V45 wants a `tool_runs` row for every MCP READ, and
   `noa_api.mcp_audit.ToolRunAuditMiddleware` writes it (T73) — beside the RBAC gate, for
   the same reason and by the same rule (V83b). What a tool *does* declare is its
   `ToolRisk`, at registration, because that is the one fact about a tool the middleware
   cannot work out for itself.

5. **A CHANGE tool executes nothing when it is called.** It runs its preflight in-process
   (C9, V17) and hands the evidence to `change_gate.open_change_request`, which writes a
   PENDING `action_requests` row and returns (T33, V16). "May this run?" is answered from
   that row's `status`, never from an argument and never from an LLM claim (V23); the
   decision itself arrives as a cookie POST from a NOA-origin document (V22, T37).

Landed: `whm_list_servers` (T19), `whm_search_accounts` (T21),
`whm_preflight_firewall_entries` (T24), the CHANGE gate's write side (T33 — no CHANGE tool
calls it yet), and the decision endpoints that answer it (T37,
`noa_api.api.routes.action_requests`). Still to come: the other eleven of §I.mcp, and the
rest of the approval path — the gate's result shape (T32), the executor (T38), expiry (T39).
"""

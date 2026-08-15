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
   `noa_api.mcp_rbac.RbacToolMiddleware` (V1), one gate for every tool, so a tool added
   later cannot forget it.

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

Landed: `whm_list_servers` (T19), `whm_list_accounts` (T20), `whm_search_accounts` (T21),
`whm_preflight_firewall_entries` (T24), `pmg_whitelist_search` (T31), `pmg_whitelist_list` (T30),
`noa_get_action_result` (T63 — `noa_read.py`, the read side of the approval loop), the CHANGE
gate's write side (T33 — no CHANGE tool calls it yet), the decision endpoints that answer it
(T37, `noa_api.api.routes.action_requests`), pending expiry (T39, whose check-on-read T63
is the first live caller of) and the post-approval executor with its reaper (T38,
`core.approvals.execution`), the CHANGE gate's result shape (T32, `change_gate.py`) and the
large-READ table surface's (T56, `table_surface.py` — parked by `whm_list_accounts` at T20 and
by `pmg_whitelist_list` at T30, which is where "one surface, not a per-tool special case"
stopped being prose), the WHM CHANGE tools (`whm_suspend_account` T22, `whm_unsuspend_account`
T23, `whm_firewall_release_and_allow` T25, `whm_firewall_allowlist_remove` T26) and the first
Proxmox one (`proxmox_reset_vm_password` T27 — the first CHANGE whose subject is a value NOA
generates and the model never sees, C15/V49). Still to come: `pmg_whitelist` (T29) and
`proxmox_vm_nic` (T28).

T27 is also the first tool whose two halves are two modules: `proxmox_password.py` opens the
question and `proxmox_password_runner.py` performs the change. C14 forced the split and V22's
boundary is where it falls — the dependency runs one way, so `registry.py` reaches the tool and
`change_runners.py` reaches the runner with no cycle. T26 made the same move for the same reason
one file over.

The executor's dispatch table lives here — `change_runners.build_change_runners` — and holds the
CHANGE tools that exist, growing with T28-T29. `registry.py` refuses at startup to expose a
CHANGE tool with no runner behind it, so a CHANGE tool cannot ship half of itself.
"""

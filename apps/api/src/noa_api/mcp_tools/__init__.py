"""The exposed MCP tools.

`noa_api.mcp_server` builds the server and mounts it; this package decides what that server
can *do*. The split is deliberate: a tool is a NOA capability that happens to be reachable
over MCP, not a piece of transport code.

Three rules hold for everything in here.

1. **A tool returns a structured result; it does not raise at the model.** Success is
   `{"ok": True, ...}`, refusal is `{"ok": False, "error_code", "message", "choices"}`.
   Ported from `noa-old`, and it is what refusing to guess requires — an ambiguous identifier
   is a result the model can act on, not an exception. `sanitize_tool_errors` is what makes
   the promise total.

2. **The registry is the only place a name becomes callable** (`registry.py`), and every
   registered name must be in `TOOL_CATALOG`. RBAC grants are written against that catalog,
   so a tool registered under a name outside it would be a capability no role can be
   granted — and, worse, one the admin bypass would still reach.

3. **Nothing here decides whether the caller may call it.** That is
   `noa_api.mcp_rbac.RbacToolMiddleware`, one gate for every tool, so a tool added
   later cannot forget it.

4. **Nothing here records that it ran.** Every MCP READ wants a `tool_runs` row, and
   `noa_api.mcp_audit.ToolRunAuditMiddleware` writes it — beside the RBAC gate, for
   the same reason and by the same rule: one seam, never per-tool code. What a tool *does*
   declare is its `ToolRisk`, at registration, because that is the one fact about a tool
   the middleware cannot work out for itself.

5. **A CHANGE tool executes nothing when it is called.** It runs its preflight in-process
   and hands the evidence to `change_gate.open_change_request`, which writes a
   PENDING `action_requests` row and returns. "May this run?" is answered from
   that row's `status`, never from an argument and never from an LLM claim; the
   decision itself arrives as a cookie POST from a NOA-origin document.

Landed: `whm_list_servers`, `whm_list_accounts`, `whm_search_accounts`,
`whm_preflight_firewall_entries`, `pmg_whitelist_search`, `pmg_whitelist_list`,
`noa_get_action_result` (`noa_read.py`, the read side of the approval loop), the CHANGE gate's write
side, the decision endpoints that answer it (`noa_api.api.routes.action_requests`), pending expiry
(whose check-on-read the action-result tool is the first live caller of) and the post-approval
executor with its reaper (`core.approvals.execution`), the CHANGE gate's result shape
(`change_gate.py`) and the large-READ table surface's (`table_surface.py` — parked by
`whm_list_accounts` and by `pmg_whitelist_list`, which is where "one surface, not a per-tool special
case" stopped being prose), the WHM CHANGE tools (`whm_suspend_account`, `whm_unsuspend_account`,
`whm_firewall_release_and_allow`, `whm_firewall_allowlist_remove`) and the first Proxmox one
(`proxmox_reset_vm_password` — the first CHANGE whose subject is a value NOA generates and the model
never sees). Still to come: `pmg_whitelist` and `proxmox_vm_nic`.

The password reset is also the first tool whose two halves are two modules:
`proxmox_password.py` opens the question and `proxmox_password_runner.py` performs the
change. The file-size cap forced the split and the cookie/CSRF boundary is where it
falls — the dependency runs one way, so `registry.py` reaches the tool and
`change_runners.py` reaches the runner with no cycle. The allowlist-remove tool made the
same move for the same reason one file over.

The executor's dispatch table lives here — `change_runners.build_change_runners` — and holds
the CHANGE tools that exist. `registry.py` refuses at startup to expose a
CHANGE tool with no runner behind it, so a CHANGE tool cannot ship half of itself.
"""

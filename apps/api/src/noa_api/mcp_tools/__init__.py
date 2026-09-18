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

Which tools exist is read from `core.auth.tool_catalog.TOOL_CATALOG` and `registry.py`, never
from a list kept here: the inventory this docstring used to carry drifted, and still named
`pmg_whitelist` and `proxmox_vm_nic` as unshipped long after both landed. A hand-kept copy of a
set the code already holds can only go stale, and a stale one reads as current.

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

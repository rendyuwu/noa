"""The set of tool names NOA recognises — what the admin bypass and the RBAC engine both
check against, and what the MCP server contract exposes.

Admin bypass splits two things that look alike: the `admin` role bypasses *per-tool permission
checks*, and it still gets refused an unregistered tool. Both halves need one answer to
"is this a real tool?", and that answer is this module.

Static list today, deliberately. The tools themselves land across the WHM, Proxmox and PMG
tool modules plus the action-result tool, and the FastMCP server mount will hold the live
registry — at which point this becomes registry-derived and the catalog stops being
hand-maintained. Until then a literal set plus a conformance test is the only thing that can
catch a drift between the MCP server contract and the code, so the test in
`test_rbac_engine.py` asserts this equals the MCP server contract's exposed list.

Two things are NOT here on purpose:

- **Risk classification** (`ToolRisk` READ/CHANGE — risk and status live in separate
  columns). The enum lives in `core.db.lifecycle` since the `tool_runs` schema, and since
  the tool-run writer the name → risk mapping is built at
  *registration* (`noa_api.mcp_tools.registry`), where the tool is defined — not here.
  Permission resolution never branches on risk: a CHANGE tool the operator may call still
  goes through the approval gate, and a CHANGE tool they may not call is refused by
  RBAC first. Adding risk here would invite a check that conflates "may call" with
  "may run now", and would put the classification somewhere a new tool can be missing from.
- **Internal functions** (the MCP server contract's `*_preflight_*`, `whm_validate_server`,
  and the rest). They run in-process inside a workflow tool, are never exposed over MCP, and
  therefore can never be the subject of a grant. Listing them would let an admin grant a
  permission that means nothing.

The never-implement names are absent and must stay absent — that list is a management
policy boundary, not a technical one, so re-adding one is an owner decision. A test
asserts the catalog and the never-implement list are disjoint.
"""

from __future__ import annotations

from typing import Final

# The 14 exposed tools of the MCP server contract. Order is for reading; membership is what
# matters.
TOOL_CATALOG: Final[frozenset[str]] = frozenset(
    {
        # WHM — accounts
        "whm_list_accounts",
        "whm_search_accounts",
        "whm_suspend_account",
        "whm_unsuspend_account",
        # WHM — firewall (CSF + Imunify)
        "whm_preflight_firewall_entries",
        "whm_firewall_release_and_allow",
        "whm_firewall_allowlist_remove",
        # WHM — inventory
        "whm_list_servers",
        # Proxmox
        "proxmox_reset_vm_password",
        "proxmox_vm_nic",
        # PMG
        "pmg_whitelist",
        "pmg_whitelist_list",
        "pmg_whitelist_search",
        # NOA itself
        "noa_get_action_result",
    }
)

# The never-implement list — management policy boundary (DECISIONS.md section 6.2). Recorded
# here so the disjointness is testable rather than a comment someone has to remember. Never
# port, never expose, never re-add.
NEVER_IMPLEMENT_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "whm_change_contact_email",
        "whm_change_primary_domain",
        "proxmox_move_vms_between_pools",
        "proxmox_preflight_move_vms_between_pools",
        "proxmox_get_user_by_email",
        "whm_check_binary_exists",
        "whm_firewall_denylist_add_ttl",
    }
)


def is_known_tool(tool_name: str) -> bool:
    """True when `tool_name` is a registered, exposed tool.

    Exact match, no normalization: a grant is written by an admin picking from the
    catalog, and silently accepting `WHM_List_Accounts` would mean the stored grant no
    longer matches the name MCP dispatches on.
    """
    return tool_name in TOOL_CATALOG

"""The one place a CHANGE tool becomes runnable after approval.

A CHANGE tool has two halves and they run at different moments, on different sides of the
cookie/CSRF boundary:

- the **tool** (`tools/call`) runs its in-process preflight and opens an `action_requests` row —
  one workflow, one tool, evidence stays in-process. It executes nothing, and the LLM can reach it.
- the **runner** performs the change, once an operator has approved it. It is reached only from
  `core.approvals.execution`, which is reached only from the asyncio host an approval hands its
  run to — never from the MCP path.

This module registers the second half. `build_change_runners` is called twice from one app, and
that is safe rather than a second answer: it is a pure function of the tool context, so both
callers get the same mapping. `noa_api.mcp_tools.registry` calls it to assert **coverage** — a
registered CHANGE tool with no runner is a change an operator could approve and NOA could never
run — and `noa_api.main` calls it to build the executor.

**Seven runners, one per exposed CHANGE tool** (`whm_suspend_account`,
`whm_unsuspend_account`, `whm_firewall_release_and_allow`,
`whm_firewall_allowlist_remove`, `proxmox_reset_vm_password`, `proxmox_vm_nic`,
`pmg_whitelist`). The mapping covers the whole registered CHANGE surface, so
`registry.assert_change_runners_cover` has nothing left to catch — which is a property to keep
rather than a check to drop: the executor's `change_runner_unavailable` path stays, because a
named terminal failure with a receipt is what makes the *next* CHANGE tool safe to register
before its runner lands (the decision endpoints' argument for the seam), and the registry check is
what makes sure it never has to.

**What a runner is.** A `ChangeRunner` takes a `ChangeExecutionRequest` — the tool name, the
arguments the gate recorded, the preflight evidence the operator approved against, and the
reason they typed (the suspend tool passes it through; a runner whose target system has
nowhere to put it simply does not read it, which is the unsuspend tool) — and answers with the
ordinary tool envelope (`noa_api.mcp_tools.results.tool_ok` / `tool_failure`). Two rules it
carries: it should not
raise, because the executor records what it is handed and a raise arrives as a coarser code
than the integration layer already knew; and it must not echo the reason back in its
payload, because `tool_runs.result_summary` is derived from that payload and
`noa_get_action_result` hands the summary to a model — and a value kept from a model must stay
unreadable on every path back.

**Not through the receipt**, which is the door it is tempting to name here: the action-result
reader takes exactly two scalars off `action_receipts` — the delta's `verification` and
`verification_cause`, lifted out of the JSONB **in SQL**
(`core.approvals.reads.select_requester_matched_with_change_verification`) — so no receipt row
enters that process, and a `before` half carrying the reason cannot be filtered out late because
it never arrives. The receipt *row* is read by the approval card and the admin audit surface, both
of which are the operator's own. A runner's author sent to the wrong field guards the wrong thing.

**One map, built here**, keyed by the tool names the runners answer to: a runner still lives
beside the tool that opens the request for it, so the before-state and the change that answers it
cannot drift into two files, and the keys sit in the one place `registry` checks them against the
registered CHANGE surface.
"""

from __future__ import annotations

from core.approvals.execution import ChangeRunner
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.pmg_whitelist import TOOL_PMG_WHITELIST
from noa_api.mcp_tools.pmg_whitelist_runner import build_pmg_whitelist_runner
from noa_api.mcp_tools.proxmox_nic import TOOL_PROXMOX_VM_NIC
from noa_api.mcp_tools.proxmox_nic_runner import build_proxmox_vm_nic_runner
from noa_api.mcp_tools.proxmox_password import TOOL_PROXMOX_RESET_VM_PASSWORD
from noa_api.mcp_tools.proxmox_password_runner import build_proxmox_reset_vm_password_runner
from noa_api.mcp_tools.whm_account_change import (
    TOOL_WHM_SUSPEND_ACCOUNT,
    TOOL_WHM_UNSUSPEND_ACCOUNT,
)
from noa_api.mcp_tools.whm_account_change_runner import (
    build_whm_suspend_runner,
    build_whm_unsuspend_runner,
)
from noa_api.mcp_tools.whm_firewall_allowlist import (
    TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE,
    build_whm_firewall_allowlist_remove_runner,
)
from noa_api.mcp_tools.whm_firewall_change import build_whm_firewall_release_runner
from noa_api.mcp_tools.whm_firewall_release_outcome import TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW


def build_change_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → the thing that performs that change once approved.

    `context` carries what every runner needs — the session factory, the cipher, the server
    repositories and, since the password-reset runner, the secret-delivery seam — so a runner
    never reaches for its own copy of the world.
    """
    return {
        TOOL_WHM_SUSPEND_ACCOUNT: build_whm_suspend_runner(context=context),
        TOOL_WHM_UNSUSPEND_ACCOUNT: build_whm_unsuspend_runner(context=context),
        TOOL_WHM_FIREWALL_RELEASE_AND_ALLOW: build_whm_firewall_release_runner(context=context),
        TOOL_WHM_FIREWALL_ALLOWLIST_REMOVE: build_whm_firewall_allowlist_remove_runner(
            context=context
        ),
        TOOL_PROXMOX_RESET_VM_PASSWORD: build_proxmox_reset_vm_password_runner(context=context),
        TOOL_PROXMOX_VM_NIC: build_proxmox_vm_nic_runner(context=context),
        TOOL_PMG_WHITELIST: build_pmg_whitelist_runner(context=context),
    }

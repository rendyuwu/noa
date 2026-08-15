"""The one place a CHANGE tool becomes runnable after approval (T38 — V23, V46).

A CHANGE tool has two halves and they run at different moments, on different sides of the
boundary V22 draws:

- the **tool** (`tools/call`) runs its in-process preflight and opens an `action_requests` row
  (C9, V17, T33). It executes nothing, and the LLM can reach it.
- the **runner** performs the change, once an operator has approved it. It is reached only from
  `core.approvals.execution`, which is reached only from the asyncio host an approval hands its
  run to — never from the MCP path.

This module registers the second half. `build_change_runners` is called twice from one app, and
that is safe rather than a second answer: it is a pure function of the tool context, so both
callers get the same mapping. `noa_api.mcp_tools.registry` calls it to assert **coverage** — a
registered CHANGE tool with no runner is a change an operator could approve and NOA could never
run — and `noa_api.main` calls it to build the executor.

**Four runners today** (T22 `whm_suspend_account`, T23 `whm_unsuspend_account`, T25
`whm_firewall_release_and_allow`, T26 `whm_firewall_allowlist_remove`). T27-T29 are unbuilt, so
the executor's `change_runner_unavailable` path is still reachable for their names — a named
terminal failure with a receipt, not a run left `STARTED` forever, which is what made shipping
the executor before its first tool safe rather than a silent hole (T37(a)'s argument for the
seam).

**What a runner is.** A `ChangeRunner` takes a `ChangeExecutionRequest` — the tool name, the
arguments the gate recorded, the preflight evidence the operator approved against, and the
reason they typed (T22; a runner whose target system has nowhere to put it simply does not read
it, which is T23) — and answers with the ordinary tool envelope
(`noa_api.mcp_tools.results.tool_ok` / `tool_failure`). Two rules it carries: it should not
raise, because the executor records what it is handed and a raise arrives as a coarser code
than the integration layer already knew (V19); and it must not echo the reason back in its
payload, which becomes the receipt a model can read through T63 (V76).

**Each system contributes its own map**, the way `noa_api.mcp_tools.registry` collects
registrars: a runner belongs beside the tool that opens the request for it, so the before-state
and the change that answers it cannot drift into two files.
"""

from __future__ import annotations

from core.approvals.execution import ChangeRunner
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.whm_account_change import build_whm_account_change_runners
from noa_api.mcp_tools.whm_firewall_allowlist import build_whm_firewall_allowlist_runners
from noa_api.mcp_tools.whm_firewall_change import build_whm_firewall_change_runners


def build_change_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → the thing that performs that change once approved (T22-T29).

    `context` carries what every runner needs — the session factory, the cipher, the server
    repositories — so a runner never reaches for its own copy of the world (C7, T15).
    """
    return {
        **build_whm_account_change_runners(context=context),
        **build_whm_firewall_change_runners(context=context),
        **build_whm_firewall_allowlist_runners(context=context),
    }


__all__ = ["build_change_runners"]

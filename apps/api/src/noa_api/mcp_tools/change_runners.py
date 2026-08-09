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

**Empty today, and the emptiness is exercised.** T22-T29 are unbuilt, so nothing is registered
here and the executor's `change_runner_unavailable` path is the reachable one. That is a named
terminal failure with a receipt, not a run left `STARTED` forever, which is what makes shipping
the executor before its first tool safe rather than a silent hole (T37(a)'s argument for the
seam, one task on).

**What a runner looks like when T22 lands.** A `ChangeRunner` takes a `ChangeExecutionRequest`
— the tool name, the arguments the gate recorded, and the preflight evidence the operator
approved against — and answers with the ordinary tool envelope
(`noa_api.mcp_tools.results.tool_ok` / `tool_failure`). It should carry `sanitize_tool_errors`
for the same reason the tool half does (V19): the executor records what it is handed, and a
raise reaches the audit row as a coarser code than the integration layer already knew.
"""

from __future__ import annotations

from core.approvals.execution import ChangeRunner
from noa_api.mcp_tools.context import McpToolContext


def build_change_runners(*, context: McpToolContext) -> dict[str, ChangeRunner]:
    """Tool name → the thing that performs that change once approved (T22-T29).

    `context` is taken now rather than when the first runner arrives, because every runner will
    need it — the session factory, the cipher, the server repositories — and a signature that
    changed on the first registration would make T22 a wiring change as well as a tool.
    """
    return {}


__all__ = ["build_change_runners"]

"""PMG READ tools (T31 — `pmg_whitelist_search`).

`pmg_whitelist_search` answers one question about one PMG node: **is this address already in
`mynetworks`?** It is the discovery step in front of `pmg_whitelist(action)` (T29) — the
operator has an address and needs to know whether adding it is a change or a no-op, and whether
removing it has anything to remove.

Exact membership, never containment (V59, and see
`core.integrations.pmg.mynetworks.find_matching_entries`): `1.2.3.0/24` sitting in the
whitelist does not make `1.2.3.4` an entry. Answering otherwise would tell an operator their
address is whitelisted when the CIDR they would have to remove is not the one they named.

**One `pmgsh` read, and the session is closed before it.** The row is resolved and turned into
an `SSHConnectionConfig` inside one database session, which then closes; everything after that
is SSH. That is T21's rule — a pooled Postgres connection held across a hop to someone else's
host is how a slow PMG node becomes a database outage — and it is why
`core.integrations.pmg.pmgsh_cli` takes a config rather than a row as of this task, the same
re-signing T24 did for the WHM firewall path.

`resolve_pmg_ssh_config` is deliberately allowed to raise. Its four refusals
(`ssh_invalid_host`, `ssh_invalid_port`, `ssh_not_configured`, `ssh_host_key_not_validated`)
are `NoaError`s, so `sanitize_tool_errors` hands the model the code that names the fix (V19)
and names the *server* rather than reporting a failed `pmgsh` command. `PMGSHCLIError` travels
the same way, which is why nothing here catches it: `ssh_sudo_required` and
`pmgsh_command_failed` have different remedies (`noa-old` GH #82), and a local `except` that
collapsed them would undo the split T18 built.

Two split responsibilities, both matching `whm_read`:

- The tool *function* takes a context and is directly unit-testable. Registration — the public
  name, description and annotations — is `register_pmg_read_tools`, so the schema fastmcp
  derives comes from a signature with no context parameter in it.
- `sanitize_tool_errors` wraps the function, not the registration, so a future in-process call
  (C9, V17 — T29's preflight is exactly that) gets the same V19 guarantee.

Registration declares `ToolRisk.READ` (T73, V20). Nothing here records anything: the
`tool_runs` row is written by `ToolRunAuditMiddleware` beside the RBAC gate (V83b), and the
risk it stamps comes from here, where the tool is defined.

`pmg_whitelist_list` (T30) is not here yet — it is the large-result case (V64) and its render
mechanism is gated on T59. A bounded membership question needs no table surface, so this tool
is not waiting on that gate.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from core.db.lifecycle import ToolRisk
from core.integrations.pmg.mynetworks import (
    find_matching_entries,
    normalize_cidr,
    parse_mynetworks_entries,
)
from core.integrations.pmg.pmgsh_cli import run_pmg_mynetworks_list
from core.integrations.pmg.ssh import resolve_pmg_ssh_config
from core.servers.pmg_ref import resolve_pmg_server_ref
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)

TOOL_PMG_WHITELIST_SEARCH = "pmg_whitelist_search"

ERROR_TARGET_REQUIRED = "target_required"
# Verbatim from `noa-old`: the target was neither an IP address nor a CIDR network.
ERROR_INVALID_TARGET = "invalid_whitelist_target"

MESSAGE_TARGET_REQUIRED = "A whitelist target is required."
MESSAGE_INVALID_TARGET = "Whitelist target must be a valid IP address or CIDR network."

DESCRIPTION_PMG_WHITELIST_SEARCH = (
    "Check whether an IP address or network is on one PMG server's mynetworks whitelist. "
    "Use it before asking to add or remove a whitelist entry, so the operator knows whether "
    "the change would do anything. Matching is exact: a whitelisted network does not count as "
    "a match for a single address inside it. Returns whether the address is there, the entries "
    "that matched, and how many entries the whitelist holds. Read-only: it changes nothing."
)


@sanitize_tool_errors(TOOL_PMG_WHITELIST_SEARCH)
async def pmg_whitelist_search(
    *,
    server_ref: str,
    target: str,
    context: McpToolContext,
) -> ToolPayload:
    """Is `target` in this PMG server's `mynetworks`, and on the strength of which entry? (T31)

    Two guards run before any I/O, so a malformed call costs no round trip (V21):

    - a blank or whitespace-only `target` is refused. The schema cannot express it —
      `min_length` counts whitespace — and searching for "" is not a question;
    - a `target` that is neither an address nor a network is refused, because the only
      alternative is comparing raw strings against normalised entries, which answers "not
      whitelisted" for everything.

    Then one database session: resolve the operator's word to a server (V18 — a tie is
    `choices`, never a pick) and turn that row into a connection. Both happen inside the
    session because both read mapped attributes; the `pmgsh` read is outside it.

    **The normalised target ships with the verdict.** `ipaddress` masks host bits, so
    `1.2.3.4/24` is really a question about `1.2.3.0/24`, and stating what membership was tested
    against is the difference between an answer and an echo.

    **So does the entry count.** `exists: false` against a whitelist that parsed to zero
    entries is a different fact from `exists: false` against three hundred, and `pmgsh ls`
    output alone cannot tell an empty `mynetworks` from a format NOA no longer recognises. The
    count is what lets an operator see which one they got instead of reading a clean bill into
    both.
    """
    normalized_input = target.strip()
    if not normalized_input:
        return tool_failure(ERROR_TARGET_REQUIRED, MESSAGE_TARGET_REQUIRED)

    normalized_target = normalize_cidr(normalized_input)
    if normalized_target is None:
        return tool_failure(ERROR_INVALID_TARGET, MESSAGE_INVALID_TARGET)

    async with context.session_factory() as session:
        repository = context.pmg_server_repository_factory(session)
        resolution = await resolve_pmg_server_ref(server_ref, repository=repository)
        if not resolution.ok or resolution.server is None:
            return tool_failure(
                resolution.error_code or ERROR_UNKNOWN,
                resolution.message,
                choices=resolution.choices,
            )
        server_id = str(resolution.server.id)
        config = resolve_pmg_ssh_config(
            resolution.server,
            cipher=context.secret_cipher,
            require_host_key_fingerprint=True,
        )

    entries = parse_mynetworks_entries(await run_pmg_mynetworks_list(config))
    matches = find_matching_entries(entries, normalized_target=normalized_target)

    return tool_ok(
        server_id=server_id,
        # The operator's text, as typed — the normalised form is beside it, not instead of it.
        target=normalized_input,
        normalized_target=normalized_target,
        exists=bool(matches),
        # PMG's own spelling of each match, which is what an operator sees on the box and what
        # a removal (T29) would name. Several only when `mynetworks` really holds the same
        # address twice, which is a fact about the whitelist rather than noise.
        matches=[entry.as_payload() for entry in matches],
        total_entries=len(entries),
    )


def register_pmg_read_tools(server: FastMCP, *, context: McpToolContext) -> dict[str, ToolRisk]:
    """Register the PMG READ tools on `server`; return each name with its risk (I.mcp, V20).

    One entry today. `pmg_whitelist_list` (T30) lands here beside it once T59 settles how a
    large result renders (V64), and `pmg_whitelist` (T29) is a CHANGE tool and goes in its own
    module with the approval gate.
    """

    @server.tool(
        name=TOOL_PMG_WHITELIST_SEARCH,
        description=DESCRIPTION_PMG_WHITELIST_SEARCH,
        # Standard MCP hint, and nothing NOA relies on — the READ/CHANGE split that matters is
        # enforced by the approval gate (V16), not by an annotation a client may ignore.
        annotations={"readOnlyHint": True},
    )
    async def pmg_whitelist_search_tool(
        server_ref: Annotated[
            str,
            Field(
                description=(
                    "Which PMG server: its id, its name in NOA, or its SSH host. Ask the "
                    "operator which node if they have not named one."
                )
            ),
        ],
        target: Annotated[
            str,
            Field(
                description=(
                    "The address to look for: an IPv4 or IPv6 address, or a network in CIDR "
                    "form. Pass it exactly as the operator gave it."
                )
            ),
        ],
    ) -> ToolPayload:
        return await pmg_whitelist_search(server_ref=server_ref, target=target, context=context)

    return {TOOL_PMG_WHITELIST_SEARCH: ToolRisk.READ}


__all__ = [
    "DESCRIPTION_PMG_WHITELIST_SEARCH",
    "ERROR_INVALID_TARGET",
    "ERROR_TARGET_REQUIRED",
    "MESSAGE_INVALID_TARGET",
    "MESSAGE_TARGET_REQUIRED",
    "TOOL_PMG_WHITELIST_SEARCH",
    "pmg_whitelist_search",
    "register_pmg_read_tools",
]

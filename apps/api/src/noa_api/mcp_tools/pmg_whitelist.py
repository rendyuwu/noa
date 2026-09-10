"""`pmg_whitelist` — one tool for two directions.

The second of the two **enum collapses** DECISIONS §9 adopted, after T28: `noa-old`'s
`pmg_whitelist_add` and `pmg_whitelist_remove` become one tool with an `action` parameter. The
recorded cost is the same one — RBAC gets coarser, because a role can no longer be granted "add"
without "remove" (DECISIONS §9) — and it is not re-litigated here.

**Two halves, and only the first is here.** This module runs the in-process preflight
and opens an `action_requests` row; it executes nothing, and the LLM can reach it. The half that
edits `mynetworks` is `pmg_whitelist_runner.py`, reachable only from `core.approvals.execution`
after an operator approved. Two files for C14, split on the boundary the design already
draws, and the dependency runs one way — T27's and T28's arrangement, one system over.

**No reason parameter, and nowhere to add one**. `open_change_request` refuses a
reason-shaped argument even for a caller reaching this function directly.

**The preflight is `read_pmg_mynetworks`, not a second copy of it**. Both PMG READ tools
already run resolve → connect → `pmgsh ls` → parse, and a CHANGE that gathered its before-state
through its own reader would be a second answer to "what is on this whitelist" that could drift
from the one the operator got from `pmg_whitelist_search` a minute earlier.

**§V.59 is worse here than it is one tool over, and the card carries the proof.**
`ipaddress.ip_network(…, strict=False)` masks host bits, so `1.2.3.4/24` is a question about
`1.2.3.0/24` — and where the search tool only *answers* about the masked form, this **writes** it:
an operator who typed one address would be approving a whole `/24`. So the operator's own text and
the normalised form travel together on the evidence and in every answer, never one instead of the
other.

**Two no-op branches, an answer rather than a question.** `add` against an address already on the
list, and `remove` against one that is not, have nothing for an operator to authorise — T22, T23,
T26 and T28's shape. That answer is transcript, so it is built from the server's name, the
two spellings of the target and one measured boolean, never from the `pmgsh` output it was decided
from.

**One source, so §V.86 has no partial case here.** PMG answers over exactly one transport, so
there is no second backend to be silent while the first speaks. A `pmgsh ls` that cannot answer
raises, and `sanitize_tool_errors` hands the model the integration's own code —
`ssh_sudo_required` and `pmgsh_command_failed` name different remedies (`noa-old` GH #82) — and
**no card is opened**. Fail-closed by construction rather than by choice, which is what makes this
different from T26's dual-backend gate rather than a weaker version of it.

**§V.96 has no instance.** `pmgsh create /config/mynetworks -cidr <cidr>` takes a CIDR and nothing
else — there is no comment, note or description field on a `mynetworks` entry — so nothing C8 keeps
from the LLM is ever written onto a PMG node, and nothing NOA wrote can come back through a later
READ. Stated rather than assumed, and asserted on the runner's commands and its payload.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from fastmcp import FastMCP
from pydantic import Field

from core.db.lifecycle import ToolRisk
from core.integrations.pmg.mynetworks import (
    MynetworksEntry,
    find_matching_entries,
    normalize_cidr,
)
from core.integrations.pmg.pmgsh_cli import MYNETWORKS_PATH
from noa_api.mcp_tools.change_gate import build_change_gate_response, open_change_request
from noa_api.mcp_tools.change_target import STATUS_NO_OP
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.pmg_read import (
    ERROR_INVALID_TARGET,
    ERROR_TARGET_REQUIRED,
    MESSAGE_INVALID_TARGET,
    MESSAGE_TARGET_REQUIRED,
    MynetworksRead,
    read_pmg_mynetworks,
)
from noa_api.mcp_tools.results import (
    ToolAnswer,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)

TOOL_PMG_WHITELIST: Final = "pmg_whitelist"

# --- The action enum (DECISIONS §9) ---
#
# Two words, and the pair is the whole of the collapse. Constants rather than literals at each site
# because they cross three boundaries: the published schema, the JSONB evidence, and the runner
# reading that evidence back minutes later.
ACTION_ADD: Final = "add"
ACTION_REMOVE: Final = "remove"
ACTIONS: Final[tuple[str, ...]] = (ACTION_ADD, ACTION_REMOVE)

# --- Evidence keys ---
#
# This tool's own. Written into `approval_context` here and read back by the runner after a
# decision, so a misspelling reads as an absent value rather than as an error — T27's warning, and
# the reason these are constants shared by both halves rather than string literals twice.
EVIDENCE_SERVER_ID: Final = "server_id"
EVIDENCE_SERVER_NAME: Final = "server"
EVIDENCE_ACTION: Final = "action"
EVIDENCE_TARGET: Final = "target"
EVIDENCE_NORMALIZED_TARGET: Final = "normalized_target"
EVIDENCE_MATCHES: Final = "matches"
EVIDENCE_TOTAL_ENTRIES: Final = "total_entries"
EVIDENCE_ENDPOINT: Final = "mynetworks_endpoint"

# --- Refusals ---

# The approved change names a PMG node that is no longer resolvable. Its own code rather than
# `change_target.ERROR_SERVER_UNAVAILABLE`, which names the WHM inventory: an administrator sent to
# the wrong table is an administrator sent nowhere (T27's precedent, `change_target`'s docstring).
ERROR_SERVER_UNAVAILABLE: Final = "pmg_server_unavailable"
MESSAGE_SERVER_UNAVAILABLE: Final = (
    "The PMG server this change was approved for is no longer available. Contact an administrator."
)

# The schema publishes an enum, so a well-behaved client cannot produce this. It is here for the
# caller that reaches the function directly, which is T25's three-place discipline for a bounded
# argument: what `tools/list` publishes, what the body re-checks, and what the runner re-checks
# after the JSONB round trip.
#
# Deliberately not imported from `noa_api.mcp_tools.proxmox_nic`, which spells the same word for the
# same fault. The value is one word because the fault is one fault; sharing the constant would make
# the PMG module import the Proxmox one, and one system's tools reaching into another's is the cost
# V66 does not ask anyone to pay (`change_target`'s argument for keeping the server codes apart).
ERROR_INVALID_ACTION: Final = "invalid_action"
MESSAGE_INVALID_ACTION: Final = (
    "The action must be `add` or `remove`, exactly — nothing else names a whitelist change."
)

DESCRIPTION_PMG_WHITELIST: Final = (
    "Add an address to one PMG server's mynetworks whitelist, or remove it. Whitelisting an "
    "address lets it relay mail through this gateway, so this changes a live system and does not "
    "run when you call it: NOA reads the whitelist, opens an approval request, and answers with "
    "the address of a card where an operator decides. If the address is already whitelisted (for "
    "`add`) or is not on the list at all (for `remove`), NOA says so and opens nothing. Matching "
    "is exact: a whitelisted network is not a match for a single address inside it. A target with "
    "host bits set, such as `1.2.3.4/24`, is a request about the whole network `1.2.3.0/24` — NOA "
    "reports both forms and the operator approves the network. Use `pmg_whitelist_search` first "
    "when you are not sure whether the change would do anything. Read the outcome with "
    "`noa_get_action_result`, and never report a whitelist as changed without it."
)

SERVER_REF_DESCRIPTION: Final = (
    "Which PMG server: its id, its name in NOA, or its SSH host. Ask the operator which node if "
    "they have not named one."
)


# --- The tool: it opens a question and changes nothing ---


@sanitize_tool_errors(TOOL_PMG_WHITELIST)
async def pmg_whitelist(
    *,
    server_ref: str,
    action: str,
    target: str,
    context: McpToolContext,
) -> ToolAnswer:
    """Ask for one `mynetworks` entry to be added or removed; change nothing.

    Three guards run before any I/O, so a malformed call costs no round trip: a blank or
    whitespace-only `target` is refused — the schema cannot express it, because `min_length` counts
    whitespace — a `target` that is neither an address nor a network is refused, because the only
    alternative is comparing raw strings against normalised entries, and an `action` outside the
    enum is refused here as well as by the schema, because a caller reaching this function directly
    bypasses pydantic.

    A hostname is refused rather than resolved, the same as in the search tool: `mynetworks` holds
    CIDRs, and turning a name into an address here would open a card about whatever DNS said at
    that moment.

    Then the read both PMG READ tools already share (`read_pmg_mynetworks`, V66): resolve the
    operator's word to a server (V18 — a tie is `choices`, never a pick), connect, read, parse, with
    the database session closed before the SSH hop (T21's rule). That read is this call's preflight
    (C9, V17): born here, milliseconds old, same user, reaching the operator through
    `approval_context` rather than through a transcript.

    One of its answers is an answer rather than a question: a whitelist already in the state being
    asked for is `no_op`, and no request is opened. There is nothing for an operator to authorise.
    """
    normalized_input = target.strip()
    if not normalized_input:
        return tool_failure(ERROR_TARGET_REQUIRED, MESSAGE_TARGET_REQUIRED)

    normalized_target = normalize_cidr(normalized_input)
    if normalized_target is None:
        return tool_failure(ERROR_INVALID_TARGET, MESSAGE_INVALID_TARGET)

    if action not in ACTIONS:
        return tool_failure(ERROR_INVALID_ACTION, MESSAGE_INVALID_ACTION)

    read = await read_pmg_mynetworks(server_ref=server_ref, context=context)
    if not isinstance(read, MynetworksRead):
        # Already a structured failure with its own code. Re-wrapping would rename the cause.
        return read

    matches = find_matching_entries(read.entries, normalized_target=normalized_target)
    exists = bool(matches)
    if (action == ACTION_ADD and exists) or (action == ACTION_REMOVE and not exists):
        return _no_op(
            read,
            action=action,
            target=normalized_input,
            normalized_target=normalized_target,
            exists=exists,
        )

    opened = await open_change_request(
        tool_name=TOOL_PMG_WHITELIST,
        arguments={"server_ref": server_ref, "action": action, "target": target},
        evidence=build_whitelist_evidence(
            read,
            action=action,
            target=normalized_input,
            normalized_target=normalized_target,
            matches=matches,
        ),
        context=context,
    )
    return build_change_gate_response(opened, tool_name=TOOL_PMG_WHITELIST, context=context)


def build_whitelist_evidence(
    read: MynetworksRead,
    *,
    action: str,
    target: str,
    normalized_target: str,
    matches: list[MynetworksEntry],
) -> dict[str, Any]:
    """The before-state an operator authorises a whitelist change against.

    JSON-native throughout, for `approval_context` JSONB (T33's rule), and a fixed set of fields
    built by naming what goes in rather than by sanitizing what came out of `pmgsh` — a structure
    with nowhere to put the raw command output cannot leak it by an omission nobody noticed (V26,
    V93's shape).

    **Both spellings of the target.** `target` is what the operator typed and `normalized_target` is
    what membership was decided on and what an `add` will write. A card showing only the first
    would ask an operator to approve `1.2.3.4/24`; showing only the second would ask them to approve
    an address they never named.

    **The matching entries, in PMG's own spelling.** Empty for an `add` — that absence is the
    before-state — and for a `remove` it is exactly the set of lines the runner will delete, each
    named the way `pmgsh ls` printed it. Two spellings of one address really are two lines
    (`core.integrations.pmg.mynetworks`), and a card that collapsed them would understate the
    change.

    **`total_entries` and the endpoint.** The count is the search tool's argument one surface over:
    "not on the list" against zero parsed entries is a different fact from "not on the list" against
    three hundred, and `pmgsh ls` output alone cannot tell an empty `mynetworks` from a format NOA
    no longer recognises. The endpoint names what was read, so a receipt records the config path
    rather than only that something answered.
    """
    return {
        EVIDENCE_SERVER_ID: read.server_id,
        EVIDENCE_SERVER_NAME: read.server_name,
        EVIDENCE_ACTION: action,
        EVIDENCE_TARGET: target,
        EVIDENCE_NORMALIZED_TARGET: normalized_target,
        EVIDENCE_MATCHES: [entry.as_payload() for entry in matches],
        EVIDENCE_TOTAL_ENTRIES: len(read.entries),
        EVIDENCE_ENDPOINT: MYNETWORKS_PATH,
    }


def _no_op(
    read: MynetworksRead,
    *,
    action: str,
    target: str,
    normalized_target: str,
    exists: bool,
) -> ToolPayload:
    """The answer for a whitelist already in the state being asked for. Nothing is opened.

    Built from the server's name, the two spellings of the target and one measured boolean — never
    from the `pmgsh` lines it was decided from. This is a plain tool result rather than a gate
    response, so it lands in the transcript LibreChat persists, and T26's rule one system over
    says a transcript surface is assembled from facts rather than from the evidence behind them.

    The normalised form is in the sentence, not only in the payload: an operator being told
    `1.2.3.4/24` is "already whitelisted" is being told something about `1.2.3.0/24`.
    """
    if action == ACTION_ADD:
        message = (
            f"`{normalized_target}` is already on the mynetworks whitelist on {read.server_name}; "
            "nothing to approve."
        )
    else:
        message = (
            f"`{normalized_target}` is not on the mynetworks whitelist on {read.server_name}, so "
            "there is nothing to remove and nothing to approve."
        )
    return tool_ok(
        status=STATUS_NO_OP,
        server=read.server_name,
        server_id=read.server_id,
        action=action,
        target=target,
        normalized_target=normalized_target,
        exists=exists,
        total_entries=len(read.entries),
        message=message,
    )


def register_pmg_whitelist_tools(
    server: FastMCP, *, context: McpToolContext
) -> dict[str, ToolRisk]:
    """Register the PMG whitelist CHANGE tool; return its name and risk (I.mcp, V20).

    **One tool, one `action`** — the whole of DECISIONS §9's second collapse, and the reason this
    registrar returns a single entry where `noa-old` had two tools. `Literal` rather than a free
    string is what puts the two words into the published input schema, so a model reads the pair
    from `tools/list` instead of from the description.

    `ToolRisk.CHANGE` is what tells `ToolRunAuditMiddleware` to write no `tool_runs` row for this
    call — it opens an approval request and executes nothing — and what makes
    `registry.assert_change_runners_cover` demand a runner for the name at startup, rather than
    letting an operator discover the gap after typing a reason and pressing Approve.
    """

    @server.tool(
        name=TOOL_PMG_WHITELIST,
        description=DESCRIPTION_PMG_WHITELIST,
        # Standard MCP hints, and nothing NOA relies on — a client may ignore them. The split that
        # matters is the approval gate; the classification that matters is the risk returned
        # below. `idempotentHint` is True because asking for a state the whitelist is already in is
        # a `no_op` rather than a second change.
        annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
    )
    async def pmg_whitelist_tool(
        server_ref: Annotated[str, Field(description=SERVER_REF_DESCRIPTION)],
        action: Annotated[
            Literal["add", "remove"],
            Field(
                description=(
                    "`add` puts the address on the mynetworks whitelist, letting it relay mail "
                    "through this gateway. `remove` takes it off. There is no other value."
                )
            ),
        ],
        target: Annotated[
            str,
            Field(
                description=(
                    "The address to whitelist or unwhitelist: an IPv4 or IPv6 address, or a "
                    "network in CIDR form. Pass it exactly as the operator gave it — NOA reports "
                    "the normalised form beside it, and a target with host bits set is a request "
                    "about the whole network."
                )
            ),
        ],
    ) -> ToolAnswer:
        # Annotated with the union on purpose: fastmcp derives no output schema from a return type
        # that can be a `ToolResult`, which is what lets the gate branch answer with content blocks
        # while a refusal and a no-op still answer with the envelope every tool shares.
        return await pmg_whitelist(
            server_ref=server_ref, action=action, target=target, context=context
        )

    return {TOOL_PMG_WHITELIST: ToolRisk.CHANGE}


__all__ = [
    "ACTIONS",
    "ACTION_ADD",
    "ACTION_REMOVE",
    "DESCRIPTION_PMG_WHITELIST",
    "ERROR_INVALID_ACTION",
    "ERROR_SERVER_UNAVAILABLE",
    "EVIDENCE_ACTION",
    "EVIDENCE_ENDPOINT",
    "EVIDENCE_MATCHES",
    "EVIDENCE_NORMALIZED_TARGET",
    "EVIDENCE_SERVER_ID",
    "EVIDENCE_SERVER_NAME",
    "EVIDENCE_TARGET",
    "EVIDENCE_TOTAL_ENTRIES",
    "MESSAGE_INVALID_ACTION",
    "MESSAGE_SERVER_UNAVAILABLE",
    "TOOL_PMG_WHITELIST",
    "build_whitelist_evidence",
    "pmg_whitelist",
    "register_pmg_whitelist_tools",
]

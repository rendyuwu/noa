"""PMG READ tools: `pmg_whitelist_search`, `pmg_whitelist_list`.

`pmg_whitelist_search` answers one question about one PMG node: **is this address already in
`mynetworks`?** It is the discovery step in front of `pmg_whitelist(action)` — the
operator has an address and needs to know whether adding it is a change or a no-op, and whether
removing it has anything to remove.

`pmg_whitelist_list` answers the other shape of the same read: **what is on this node's
whitelist?** The two split by size, not by subject, exactly as the WHM account pair does.
A membership question's answer fits the transcript; a whitelist can be hundreds of CIDRs, so
this one parks its rows in `tool_result_tables` and answers with a summary plus the address of
the page that renders them (`noa_api.mcp_tools.table_surface`). It is the **second** caller of
that surface after `whm_list_accounts`, and it adds nothing to it — which is what
"shared capability, never a per-tool special case" means once there is more than one producer.

**One read, shared.** `read_pmg_mynetworks` is the resolve-connect-read-parse step both tools
run; only what they do with the entries differs. Internal, never registered.

Exact membership, never containment (see
`core.integrations.pmg.mynetworks.find_matching_entries`): `1.2.3.0/24` sitting in the
whitelist does not make `1.2.3.4` an entry. Answering otherwise would tell an operator their
address is whitelisted when the CIDR they would have to remove is not the one they named.

**One `pmgsh` read, and the session is closed before it.** The row is resolved and turned into an
`SSHConnectionConfig` inside one database session, which then closes; everything after that is SSH.
That is the account search's rule — a pooled Postgres connection held across a hop to someone else's
host is how a slow PMG node becomes a database outage — and it is why
`core.integrations.pmg.pmgsh_cli` takes a config rather than a row as of this task, the same
re-signing the firewall preflight did for the WHM firewall path.

`resolve_pmg_ssh_config` is deliberately allowed to raise. Its four refusals
(`ssh_invalid_host`, `ssh_invalid_port`, `ssh_not_configured`, `ssh_host_key_not_validated`)
are `NoaError`s, so `sanitize_tool_errors` hands the model the code that names the fix
and names the *server* rather than reporting a failed `pmgsh` command. `PMGSHCLIError` travels
the same way, which is why nothing here catches it: `ssh_sudo_required` and
`pmgsh_command_failed` have different remedies (`noa-old` GH #82), and a local `except` that
collapsed them would undo the split the PMG port built.

Two split responsibilities, both matching `whm_read`:

- The tool *function* takes a context and is directly unit-testable. Registration — the public
  name, description and annotations — is `register_pmg_read_tools`, so the schema fastmcp
  derives comes from a signature with no context parameter in it.
- `sanitize_tool_errors` wraps the function, not the registration, so a future in-process call
  (one workflow, one tool — the whitelist tool's preflight is exactly that) gets the same
  sanitized-code guarantee.

Registration declares `ToolRisk.READ`. Nothing here records anything: the
`tool_runs` row is written by `ToolRunAuditMiddleware` beside the RBAC gate, at one seam, and the
risk it stamps comes from here, where the tool is defined. The listing's counts envelope is
what that middleware reads its status and summary off — see `table_surface`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from core.db.lifecycle import ToolRisk
from core.integrations.pmg.mynetworks import (
    MynetworksEntry,
    find_matching_entries,
    normalize_cidr,
    parse_mynetworks_entries,
    sort_entries,
)
from core.integrations.pmg.pmgsh_cli import run_pmg_mynetworks_list
from core.integrations.pmg.ssh import resolve_pmg_ssh_config
from core.results.tables import TableColumn
from core.servers.pmg_ref import resolve_pmg_server_ref
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolAnswer,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)
from noa_api.mcp_tools.table_surface import build_table_result, park_table_result

TOOL_PMG_WHITELIST_SEARCH = "pmg_whitelist_search"
TOOL_PMG_WHITELIST_LIST = "pmg_whitelist_list"

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

DESCRIPTION_PMG_WHITELIST_LIST = (
    "List every entry on one PMG server's mynetworks whitelist. The entries do not come back "
    "in the conversation: they are put on a page on NOA and the result carries its address, "
    "the number of entries, and whether the page is capped. Use `pmg_whitelist_search` "
    "instead when the operator is asking about one address. Read-only: it changes nothing."
)

# The columns of a parked whitelist, in the order they are rendered.
#
# Both fields of an entry, because they are two different facts. `cidr` is the token PMG
# printed and it is what an operator finds on the box and what a removal names;
# `normalized` is the form membership is decided on, and the two differ whenever
# `mynetworks` stores a bare host. A page showing only one of them would either hide the
# spelling in the file or hide what NOA compared against.
PMG_WHITELIST_TABLE_COLUMNS: list[TableColumn] = [
    TableColumn(key="cidr", label="Whitelist entry"),
    TableColumn(key="normalized", label="Normalised CIDR"),
]


@dataclass(frozen=True)
class MynetworksRead:
    """One node's whitelist, and which node it was.

    A value object rather than the `{"ok": True, ...}` envelope, unlike `fetch_whm_accounts`
    next door: `entries` are `MynetworksEntry` objects, and a `ToolPayload` holding values that
    do not serialise is a payload a later edit could return over MCP by accident. A failure out
    of `read_pmg_mynetworks` is still the ordinary envelope, so the two are told apart by type.
    """

    server_id: str
    server_name: str
    entries: list[MynetworksEntry]


async def read_pmg_mynetworks(
    *, server_ref: str, context: McpToolContext
) -> MynetworksRead | ToolPayload:
    """Every `mynetworks` entry on the named PMG node. Internal — never an MCP tool.

    Not exposed and not decorated with `sanitize_tool_errors`: both callers are exposed tools
    that already are, and a second boundary would turn a `NoaError` into a payload the caller
    then has to unwrap twice — `fetch_whm_accounts`' argument, one system over.

    **The database session closes before the SSH hop.** The row is resolved and turned into an
    `SSHConnectionConfig` inside the session, because both read mapped attributes and a detached
    instance would raise on a lazy refresh; the `pmgsh` read is outside it. That is the account
    search's rule: a pooled Postgres connection held across a hop to someone else's host is how a
    slow PMG node becomes a database outage.

    **Entries come back in PMG's order.** Ordering is the caller's, because the two callers owe
    different things — see `core.integrations.pmg.mynetworks`.

    A resolution failure travels back as a payload rather than an exception, `choices` and all,
    because it is information the model can act on. `resolve_pmg_ssh_config` and
    `PMGSHCLIError` are deliberately left to raise: their codes (`ssh_not_configured`,
    `ssh_host_key_not_validated`, `ssh_sudo_required`, `pmgsh_command_failed`) name different
    remedies, and `sanitize_tool_errors` passes a `NoaError`'s own code through.
    """
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
        server_name = resolution.server.name
        config = resolve_pmg_ssh_config(
            resolution.server,
            cipher=context.secret_cipher,
            require_host_key_fingerprint=True,
        )

    return MynetworksRead(
        server_id=server_id,
        server_name=server_name,
        entries=parse_mynetworks_entries(await run_pmg_mynetworks_list(config)),
    )


@sanitize_tool_errors(TOOL_PMG_WHITELIST_SEARCH)
async def pmg_whitelist_search(
    *,
    server_ref: str,
    target: str,
    context: McpToolContext,
) -> ToolPayload:
    """Is `target` in this PMG server's `mynetworks`, and on the strength of which entry?

    Two guards run before any I/O, so a malformed call costs no round trip:

    - a blank or whitespace-only `target` is refused. The schema cannot express it —
      `min_length` counts whitespace — and searching for "" is not a question;
    - a `target` that is neither an address nor a network is refused, because the only
      alternative is comparing raw strings against normalised entries, which answers "not
      whitelisted" for everything.

    Then the read both PMG tools share (`read_pmg_mynetworks`): resolve the operator's word to
    a server (a tie is `choices`, never a pick), connect, read, parse.

    **The entries are searched in PMG's own order**, unlike the listing's. Nothing here
    is capped, so there is no cut for an order to make arbitrary, and the position of a
    matching line is the one thing about it this tool cannot improve on.

    **The normalised target ships with the verdict.** `ipaddress` masks host bits, so
    `1.2.3.4/24` is really a question about `1.2.3.0/24`, and stating what membership was tested
    against is the difference between an answer and an echo.

    **So does the entry count.** `exists: false` against a whitelist that parsed to zero entries is
    a different fact from `exists: false` against three hundred, and `pmgsh ls` output alone cannot
    tell an empty `mynetworks` from a format NOA no longer recognises. The count is what lets an
    operator see which one they got instead of reading a clean bill into both.
    """
    normalized_input = target.strip()
    if not normalized_input:
        return tool_failure(ERROR_TARGET_REQUIRED, MESSAGE_TARGET_REQUIRED)

    normalized_target = normalize_cidr(normalized_input)
    if normalized_target is None:
        return tool_failure(ERROR_INVALID_TARGET, MESSAGE_INVALID_TARGET)

    read = await read_pmg_mynetworks(server_ref=server_ref, context=context)
    if not isinstance(read, MynetworksRead):
        # Already a structured failure with its own code. Re-wrapping would rename the cause.
        return read

    matches = find_matching_entries(read.entries, normalized_target=normalized_target)

    return tool_ok(
        server_id=read.server_id,
        # The operator's text, as typed — the normalised form is beside it, not instead of it.
        target=normalized_input,
        normalized_target=normalized_target,
        exists=bool(matches),
        # PMG's own spelling of each match, which is what an operator sees on the box and what
        # a removal would name. Several only when `mynetworks` really holds the same
        # address twice, which is a fact about the whitelist rather than noise.
        matches=[entry.as_payload() for entry in matches],
        total_entries=len(read.entries),
    )


@sanitize_tool_errors(TOOL_PMG_WHITELIST_LIST)
async def pmg_whitelist_list(*, server_ref: str, context: McpToolContext) -> ToolAnswer:
    """Every `mynetworks` entry on one PMG server, parked on a page.

    **The entries never enter the transcript.** A whitelist grows with every release an operator has
    ever whitelisted, and a listing in front of the model costs tokens for a body no human reads
    there anyway — so the rows go to `tool_result_tables` and the answer is a summary plus the
    address of the page that renders them. That is the whole of summary-plus-URL, and it is why this
    tool answers with content blocks while its sibling `pmg_whitelist_search` answers with the
    payload envelope: a membership question's answer *is* the answer, and a listing's rows are a
    surface.

    The second tool to park a table, after `whm_list_accounts`, and it adds nothing to
    the surface to do it — "shared capability, never a per-tool special case" is that sentence
    made checkable.

    **No `limit` argument, deliberately.** The cap is the bound an operator asked for; here
    nothing is dropped on their behalf. The only bound is the table's own
    (`RESULT_TABLE_MAX_ROWS`), it is applied at the write, and it reports itself in the text
    and in the envelope.

    **Sorted before it is handed over, and duplicates kept.** `cap_rows` is a prefix and never a
    re-sort, and `pmgsh ls` prints in whatever order PMG stores, so a capped page would otherwise be
    an arbitrary subset that changes between two identical calls. Two spellings of one address stay
    two rows: `mynetworks` really does hold both lines, a removal has to take each, and a listing
    that collapsed them would describe a file PMG does not have.

    A failure comes back as the ordinary envelope, `choices` and all. A table that
    cannot be *parked* raises instead, and `sanitize_tool_errors` turns it into
    `result_table_unavailable` — fail-closed, because a result carrying the address of a table
    that was never stored is a dead link in a transcript that persists.
    """
    read = await read_pmg_mynetworks(server_ref=server_ref, context=context)
    if not isinstance(read, MynetworksRead):
        # Already a structured failure with its own code. Re-wrapping would rename the cause.
        return read

    parked = await park_table_result(
        tool_name=TOOL_PMG_WHITELIST_LIST,
        columns=PMG_WHITELIST_TABLE_COLUMNS,
        rows=[entry.as_payload() for entry in sort_entries(read.entries)],
        context=context,
    )
    return build_table_result(
        parked,
        tool_name=TOOL_PMG_WHITELIST_LIST,
        summary=_whitelist_summary(read.server_name),
        context=context,
    )


def _whitelist_summary(server_name: str) -> str:
    """The tool's own one-line answer; the surface adds the counts and the address.

    The node is named because an operator with several PMG servers needs to know which one was
    read, and `server_ref` is whatever they typed — an id, an SSH host, a name in another case.
    This is a sentence about which box answered rather than a payload field the model can
    relay as data, which is why the search tool's result still identifies its node by id alone.
    A row that somehow carries no name degrades to the generic sentence rather than printing
    `None` at the top of an operator's page.
    """
    if server_name.strip():
        return f"Every mynetworks whitelist entry on the PMG server {server_name}."
    return "Every mynetworks whitelist entry on the PMG server that was read."


def register_pmg_read_tools(server: FastMCP, *, context: McpToolContext) -> dict[str, ToolRisk]:
    """Register the PMG READ tools on `server`; return each name with its risk.

    Both PMG reads today. `pmg_whitelist` is a CHANGE tool and goes in its own module
    with the approval gate.
    """

    @server.tool(
        name=TOOL_PMG_WHITELIST_SEARCH,
        description=DESCRIPTION_PMG_WHITELIST_SEARCH,
        # Standard MCP hint, and nothing NOA relies on — the READ/CHANGE split that matters is
        # enforced by the approval gate, not by an annotation a client may ignore.
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

    @server.tool(
        name=TOOL_PMG_WHITELIST_LIST,
        description=DESCRIPTION_PMG_WHITELIST_LIST,
        annotations={"readOnlyHint": True},
    )
    async def pmg_whitelist_list_tool(
        server_ref: Annotated[
            str,
            Field(
                description=(
                    "Which PMG server: its id, its name in NOA, or its SSH host. Ask the "
                    "operator which node if they have not named one."
                )
            ),
        ],
    ) -> ToolAnswer:
        # Annotated with the union on purpose: fastmcp derives no output schema from a return
        # type that can be a `ToolResult`, which is what lets the success path answer with
        # content blocks while a failure still answers with the envelope every tool shares.
        return await pmg_whitelist_list(server_ref=server_ref, context=context)

    return {
        TOOL_PMG_WHITELIST_SEARCH: ToolRisk.READ,
        TOOL_PMG_WHITELIST_LIST: ToolRisk.READ,
    }

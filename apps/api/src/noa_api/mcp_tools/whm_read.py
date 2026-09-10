"""WHM READ tools: `whm_list_servers`, `whm_list_accounts`, `whm_search_accounts`.

`whm_list_servers` is exposed on purpose and it is the only `*_list_servers` that is
(DECISIONS §6.6, owner-decided 2026-08-04): the model has to know which servers exist before
it can name one, while Proxmox and PMG nodes are few and named directly by the operator, so
`proxmox_list_servers` and `pmg_list_servers` stay internal (§I.mcp).

`whm_search_accounts` is the discovery step in front of every account CHANGE: T22 and T23 take
an exact `user`, and the operator has a domain or half a username. It answers over `listaccts`
because WHM has no server-side account search — see `fetch_whm_accounts`, which is internal
and which `whm_list_accounts` shares.

**The two account tools split by size, not by subject**. A bounded search answers in the
transcript; a whole listing does not, so `whm_list_accounts` parks its rows and answers with a
summary and the address of the page that holds them (`noa_api.mcp_tools.table_surface`). That
is the only tool here whose result is content blocks rather than the `{"ok": ...}` envelope —
the order of those blocks is part of what the operator is told — and it is why
`sanitize_tool_errors` widens a return type rather than fixing one.

**What leaves the process for `whm_list_servers` is `describe()`, never the row.** That
function is `core.servers.whm_ref`'s and answers id, name and `base_url` only — the three
fields that let a model construct a `server_ref` — and none of `to_safe_dict()`'s admin
extras (`api_username`, presence booleans, the SSH fields, two timestamps), which is a
different render for a different reader. The result lands in a LibreChat transcript
that persists in their MongoDB, which is the reason the rule is "no credential
material and no admin metadata either", not "no plaintext password".

Two split responsibilities, both deliberate:

- The tool *function* takes a context and is directly unit-testable. Registration —
  the public name, description and annotations — is `register_whm_read_tools`, so the
  schema fastmcp derives comes from a signature with no context parameter in it.
- `sanitize_tool_errors` wraps the function, not the registration. A caller that reaches
  the function some other way (a future internal call, C9/V17) gets the same V19 guarantee.

Registration also declares the tool's `ToolRisk`. Nothing in this module records
anything: the `tool_runs` row is written by `ToolRunAuditMiddleware` beside the RBAC gate
(V83b), and the risk it stamps on that row comes from here, where the tool is defined,
rather than from a list somewhere else that a new tool can be absent from.
"""

from __future__ import annotations

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from core.db.lifecycle import ToolRisk
from core.integrations.whm.accounts import WHMAccount, account_matches, normalize_whm_account_list
from core.results.tables import TableColumn
from core.servers.reference import hostname_of
from core.servers.whm_ref import describe, resolve_whm_server_ref
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

TOOL_WHM_LIST_SERVERS = "whm_list_servers"
TOOL_WHM_LIST_ACCOUNTS = "whm_list_accounts"
TOOL_WHM_SEARCH_ACCOUNTS = "whm_search_accounts"

# §T.21's bound. Also the schema's `ge`/`le`, so the two cannot drift apart.
MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 100
DEFAULT_SEARCH_LIMIT = 20

ERROR_QUERY_REQUIRED = "query_required"
ERROR_LIMIT_INVALID = "limit_invalid"

MESSAGE_QUERY_REQUIRED = "A search query is required."
MESSAGE_LIMIT_INVALID = f"Limit must be between {MIN_SEARCH_LIMIT} and {MAX_SEARCH_LIMIT}."
MESSAGE_LIST_ACCOUNTS_FAILED = "WHM did not return the account list."

DESCRIPTION_WHM_LIST_SERVERS = (
    "List the WHM/cPanel servers NOA is configured to manage. Returns each server's id, "
    "name and base URL. Use it to find the `server_ref` other WHM tools need, and prefer "
    "the id when two servers look alike. Read-only: it changes nothing."
)

DESCRIPTION_WHM_LIST_ACCOUNTS = (
    "List every cPanel account on one WHM server. The rows do not come back in the "
    "conversation: they are put on a page on NOA and the result carries its address, the "
    "number of accounts, and whether the page is capped. Use `whm_search_accounts` instead "
    "when the operator is looking for one account. Read-only: it changes nothing."
)

# The columns of a parked account listing, in the order they are rendered.
#
# Beside the whitelist they are read from rather than derived from a row: `listaccts` rows are
# sparse — `normalize_whm_account_summary` omits a field WHM did not send — so a column list
# built from the first row would drop a column every later row has. A test pins these keys
# against what the normaliser can emit, so the two lists cannot drift apart.
WHM_ACCOUNT_TABLE_COLUMNS: list[TableColumn] = [
    TableColumn(key="user", label="Account"),
    TableColumn(key="domain", label="Primary domain"),
    TableColumn(key="owner", label="Owner"),
    TableColumn(key="email", label="Email"),
    TableColumn(key="contactemail", label="Contact email"),
    TableColumn(key="suspended", label="Suspended"),
    TableColumn(key="is_locked", label="Suspension locked"),
    TableColumn(key="suspendtime", label="Suspended at (epoch seconds)"),
    TableColumn(key="suspendreason", label="Suspend reason"),
]

# The account fields a *model* may not read, dropped from `whm_search_accounts`' rows.
#
# `suspendreason` is WHM's suspension note, and as of T22 NOA writes the operator's approval
# reason into it — C8's single field, typed on the card. WHM echoes it back on every later
# `listaccts`, so a search result carrying the field would put that reason in the transcript and
# hand the LLM the one string C8 says it must never see. The parked table above keeps the column
# on purpose: `/tables/{token}` is behind the operator's own cookie, not in front of a
# model.
ACCOUNT_FIELDS_WITHHELD_FROM_MODEL = frozenset({"suspendreason"})

DESCRIPTION_WHM_SEARCH_ACCOUNTS = (
    "Search the cPanel accounts on one WHM server by username or domain, case-insensitively. "
    "Use it to find the exact `user` an account tool needs before calling one; never guess a "
    "username. Returns the matching accounts, how many matched in total, and whether the list "
    "was cut short by `limit`. Read-only: it changes nothing."
)


@sanitize_tool_errors(TOOL_WHM_LIST_SERVERS)
async def whm_list_servers(*, context: McpToolContext) -> ToolPayload:
    """Every configured WHM server a model may pick from, credential-free.

    Answers `describe()` — id, `name`, `base_url` — not `to_safe_dict()`. The latter is the
    **admin** view (11 fields: `api_username`, `has_api_token`, `verify_ssl`, 3 SSH fields, 2
    presence bools, 2 timestamps) and not one of those extras is read by a model choosing a
    server; at scale the gap is thousands of tokens spent on fields nobody here uses.

    A row with `is_reseller_credential = true` is left out (V109(a)) — visibility only, not
    authorization: the same row stays a valid `resolve_whm_server_ref` candidate by id, name
    and hostname, and stays fully visible in the admin UI. Filtering it out of *resolution*
    too would make the account CHANGE path V106 depends on unreachable.
    """
    async with context.session_factory() as session:
        repository = context.whm_server_repository_factory(session)
        servers = await repository.list_servers()
        # Serialized inside the session: `describe()` reads mapped attributes, and a
        # detached instance would raise on a lazy refresh once the session closed.
        return tool_ok(
            servers=[describe(server) for server in servers if not server.is_reseller_credential]
        )


async def fetch_whm_accounts(*, server_ref: str, context: McpToolContext) -> ToolPayload:
    """Every account on the named WHM server, normalised. Internal — ⊥ an MCP tool.

    Not exposed and not decorated with `sanitize_tool_errors`: its callers are exposed tools
    that already are, and a second boundary would turn a `NoaError` into a payload the caller
    then has to unwrap twice. `whm_list_accounts` is the other caller.

    **The resolved server's name and id come back with the accounts**. `server_ref`
    is whatever the operator typed — an id, a hostname, a name in another case — and the tool
    that reports "these are the accounts on X" has to name the machine it actually read, not the
    string it was handed. Resolution already happened here, so returning the answer costs
    nothing; re-reading it in the caller would be a second resolution that could disagree with
    this one. The id is what T22's CHANGE gate persists as evidence, so the change an operator
    approves runs against the machine the card described rather than against a string resolved
    again minutes later.

    **The resolved row's `api_username` and host come back too, for the same reason** (§V106,
    §V108). V106's preflight compare needs `api_username` to check against the account's
    `owner`, and V108's card/receipt/`tool_runs` need the host — both are already on the row
    that just won resolution, so returning them here costs the 0 extra round trips V106 asks
    for; reading them again in the CHANGE module would be a second, disagreeable resolution.
    `host` is `hostname_of(base_url)`, falling back to the raw `base_url` when that does not
    parse — a card field that silently read blank on an unparseable URL would be worse than
    one carrying the raw string (V86: silence is not evidence).

    Only `fetch_whm_accounts` itself carries these two: `whm_list_accounts` and
    `whm_search_accounts` pick `accounts` and `server` out of this payload and
    build their own `tool_ok(...)`, so today neither exposed tool forwards `api_username` or
    `host` into a transcript. That is a property of those two callers, not of this function,
    and it is pinned by test rather than assumed.

    Three refusals travel back as a payload rather than an exception, all of them information
    the model can act on:

    - the `server_ref` named nothing, or named several things → the resolver's own code, with
      `choices` when it was a tie;
    - WHM refused or could not be reached → `WHMClient`'s stable code (`auth_failed`,
      `timeout`, `whm_api_error`, …). Passed through rather than collapsed: those strings say
      which system to fix, and their messages carry transport state and WHM's own `reason`,
      never a credential (`core.integrations.whm.client`). The host in them is not a leak —
      `whm_list_servers` already publishes every `base_url` to the same transcript.

    **The database session closes before the HTTP call.** The client is built inside it,
    because that reads mapped attributes off the resolved row and a detached instance would
    raise on a lazy refresh; the call itself is outside, because holding a pooled connection
    open across a round trip to someone else's server is how a slow WHM host becomes a
    database outage.
    """
    async with context.session_factory() as session:
        repository = context.whm_server_repository_factory(session)
        resolution = await resolve_whm_server_ref(server_ref, repository=repository)
        if not resolution.ok or resolution.server is None:
            return tool_failure(
                resolution.error_code or ERROR_UNKNOWN,
                resolution.message,
                choices=resolution.choices,
            )
        client = context.whm_client_factory(resolution.server, cipher=context.secret_cipher)
        server_name = resolution.server.name
        server_id = str(resolution.server.id)
        api_username = resolution.server.api_username
        host = hostname_of(resolution.server.base_url) or resolution.server.base_url

    result = await client.list_accounts()
    if result.get("ok") is not True:
        message = result.get("message")
        # A blank message would leave the operator with a code and no sentence.
        spoken = message if isinstance(message, str) and message.strip() else None
        return tool_failure(
            str(result.get("error_code") or ERROR_UNKNOWN),
            spoken or MESSAGE_LIST_ACCOUNTS_FAILED,
        )

    return tool_ok(
        server=server_name,
        server_id=server_id,
        api_username=api_username,
        host=host,
        accounts=normalize_whm_account_list(result.get("accounts")),
    )


@sanitize_tool_errors(TOOL_WHM_LIST_ACCOUNTS)
async def whm_list_accounts(*, server_ref: str, context: McpToolContext) -> ToolAnswer:
    """Every cPanel account on one WHM server, parked on a page.

    **The rows never enter the transcript.** A dense server carries thousands of accounts, and
    a listing in front of the model costs tokens for a body no human reads there anyway — so
    the rows go to `tool_result_tables` and the answer is a summary plus the address of the
    page that renders them. That is the whole of V64, and it is why this tool answers with
    content blocks while its sibling `whm_search_accounts` answers with the payload envelope:
    a bounded search's rows *are* the answer, and a listing's rows are a surface.

    **No `limit` argument, deliberately.** V85's cap exists because an operator asked for one;
    here nothing is dropped on the operator's behalf. The only bound is the table's own
    (`RESULT_TABLE_MAX_ROWS`), it is applied at the write, and it reports itself in the text
    and in the envelope — which is the same invariant answered by the surface instead of by
    the tool.

    Sorted by username before it is handed over, all the same. `cap_rows` is a prefix and
    never a re-sort — only the producer knows which order is reproducible for its source — and
    `listaccts` order is WHM's own and undocumented, so a capped page would otherwise be an
    arbitrary subset that changes between two identical calls (V85, T21's rule).

    A failure comes back as the ordinary envelope, `choices` and all: a `server_ref`
    that named nothing or several things, or a WHM that refused, is information the model can
    act on. A table that cannot be *parked* raises instead, and `sanitize_tool_errors` turns it
    into `result_table_unavailable` — fail-closed, because a result carrying the address of a
    table that was never stored is a dead link in a transcript that persists.
    """
    listed = await fetch_whm_accounts(server_ref=server_ref, context=context)
    if listed.get("ok") is not True:
        # Already a structured failure with its own code. Re-wrapping would rename the cause.
        return listed

    accounts = listed.get("accounts")
    rows: list[WHMAccount] = sorted(
        (account for account in (accounts if isinstance(accounts, list) else [])),
        key=lambda account: str(account.get("user", "")),
    )

    parked = await park_table_result(
        tool_name=TOOL_WHM_LIST_ACCOUNTS,
        columns=WHM_ACCOUNT_TABLE_COLUMNS,
        rows=rows,
        context=context,
    )
    return build_table_result(
        parked,
        tool_name=TOOL_WHM_LIST_ACCOUNTS,
        summary=_list_accounts_summary(listed.get("server")),
        context=context,
    )


def _list_accounts_summary(server: object) -> str:
    """The tool's own one-line answer; the surface adds the counts and the address.

    The server is named because an operator with several WHM boxes needs to know which one was
    read, and `whm_list_servers` already publishes every name to the same transcript. A
    resolution that somehow returned no name degrades to the generic sentence rather than
    printing `None` at the top of an operator's page.
    """
    if isinstance(server, str) and server.strip():
        return f"Every cPanel account on the WHM server {server}."
    return "Every cPanel account on the WHM server that was read."


@sanitize_tool_errors(TOOL_WHM_SEARCH_ACCOUNTS)
async def whm_search_accounts(
    *,
    server_ref: str,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    context: McpToolContext,
) -> ToolPayload:
    """Accounts on one WHM server whose username or domain contains `query`.

    **Filtered here, not by WHM.** `listaccts` takes no search parameter, so the whole list
    comes back and the match runs in this process. That is the ported behaviour and it is also
    why `limit` matters: the tool result is what enters the transcript, and an unbounded answer
    on a shared server is thousands of rows (V64 is the answer for a genuinely large *listing*,
    which is `whm_list_accounts`' job above and not this tool's — a bounded search's answer
    fits the transcript, so it needs no table surface).

    Two guards before any I/O, so a malformed call costs no round trip:

    - a blank or whitespace-only `query` is refused. The schema cannot express it —
      `min_length` counts whitespace — so this is the gate, and returning every account for
      `"   "` would be the opposite of a search.
    - `limit` outside 1-100 is refused. The schema bounds it too, and over MCP that is what
      answers first; this branch is the one that holds for a direct in-process call
      and it is what makes the bound a property of the tool rather than of its registration.

    **Truncation is stated, not implied** (V85, and this tool is where that invariant was
    written). `total_matches` and `truncated` ship with the rows because `noa-old` returned the
    first N silently, and a model reading twenty rows with no other signal tells the operator
    there are twenty accounts — a fabrication the tool handed it rather than one the model
    invented.

    Matches are sorted by username before the cut, also V85: "the first twenty" has to be
    reproducible, and `listaccts` order is WHM's own and not documented as stable, which would
    make a truncated answer an arbitrary subset that changes between calls.

    **One field is withheld from the rows**: `suspendreason`. NOA writes the
    operator's approval reason into WHM's suspension note when it suspends an account, and WHM
    returns it on every later `listaccts` — so a search that reported the field would hand the
    model, by round trip, the one string C8 says it must never see. See
    `ACCOUNT_FIELDS_WITHHELD_FROM_MODEL`.
    """
    normalized_query = query.strip().lower()
    if not normalized_query:
        return tool_failure(ERROR_QUERY_REQUIRED, MESSAGE_QUERY_REQUIRED)
    if not MIN_SEARCH_LIMIT <= limit <= MAX_SEARCH_LIMIT:
        return tool_failure(ERROR_LIMIT_INVALID, MESSAGE_LIMIT_INVALID)

    listed = await fetch_whm_accounts(server_ref=server_ref, context=context)
    if listed.get("ok") is not True:
        # Already a structured failure, `choices` included. Re-wrapping would rename the cause.
        return listed

    accounts = listed.get("accounts")
    matches: list[WHMAccount] = sorted(
        (
            account
            for account in (accounts if isinstance(accounts, list) else [])
            if account_matches(account, query=normalized_query)
        ),
        key=lambda account: str(account.get("user", "")),
    )

    return tool_ok(
        # The operator's text, as typed — the normalised form is an implementation detail.
        query=query,
        accounts=[_without_withheld_fields(account) for account in matches[:limit]],
        total_matches=len(matches),
        truncated=len(matches) > limit,
    )


def _without_withheld_fields(account: WHMAccount) -> WHMAccount:
    """One account row as a model may read it.

    See `ACCOUNT_FIELDS_WITHHELD_FROM_MODEL`. Dropped here rather than in
    `normalize_whm_account_summary`, because the normaliser feeds three callers and only this
    one answers into a transcript: the parked table renders the column behind a cookie, and
    T22's suspend preflight puts the whole summary on the approval card, which is the operator's
    own surface. A field withheld from *everyone* would take it off the two surfaces that exist
    to show it.
    """
    return {
        key: value
        for key, value in account.items()
        if key not in ACCOUNT_FIELDS_WITHHELD_FROM_MODEL
    }


def register_whm_read_tools(server: FastMCP, *, context: McpToolContext) -> dict[str, ToolRisk]:
    """Register the WHM READ tools on `server`; return each name with its risk (I.mcp, V20).

    The returned keys are what `registry.register_mcp_tools` checks against `TOOL_CATALOG`,
    and what a test compares against the server's awaited `list_tools()` — see
    `noa_api.mcp_tools.registry` for why the names travel back rather than being read off
    the server here, and for why the risk travels with them.
    """

    @server.tool(
        name=TOOL_WHM_LIST_SERVERS,
        description=DESCRIPTION_WHM_LIST_SERVERS,
        # `readOnlyHint` is the MCP-standard way to say "this cannot change anything". It is
        # a hint to the client and nothing NOA relies on: the READ/CHANGE split that matters
        # is enforced by the approval gate, not by an annotation a client may ignore.
        annotations={"readOnlyHint": True},
    )
    async def whm_list_servers_tool() -> ToolPayload:
        return await whm_list_servers(context=context)

    @server.tool(
        name=TOOL_WHM_LIST_ACCOUNTS,
        description=DESCRIPTION_WHM_LIST_ACCOUNTS,
        annotations={"readOnlyHint": True},
    )
    async def whm_list_accounts_tool(
        server_ref: Annotated[
            str,
            Field(
                description=(
                    "Which WHM server: its id, its name in NOA, or its hostname. Call "
                    "`whm_list_servers` first if the operator has not named one."
                )
            ),
        ],
    ) -> ToolAnswer:
        # Annotated with the union on purpose: fastmcp derives no output schema from a return
        # type that can be a `ToolResult`, which is what lets the success path answer with
        # content blocks while a failure still answers with the envelope every tool shares.
        return await whm_list_accounts(server_ref=server_ref, context=context)

    @server.tool(
        name=TOOL_WHM_SEARCH_ACCOUNTS,
        description=DESCRIPTION_WHM_SEARCH_ACCOUNTS,
        annotations={"readOnlyHint": True},
    )
    async def whm_search_accounts_tool(
        server_ref: Annotated[
            str,
            Field(
                description=(
                    "Which WHM server: its id, its name in NOA, or its hostname. Call "
                    "`whm_list_servers` first if the operator has not named one."
                )
            ),
        ],
        query: Annotated[
            str,
            Field(
                description=(
                    "Text to look for in the account username or its primary domain, "
                    "case-insensitive substring. Must not be blank."
                )
            ),
        ],
        limit: Annotated[
            int,
            # Bounds live on the schema as well as in the tool: this is what refuses a bad
            # call at the MCP boundary, and it is what tells the model the range exists.
            Field(
                description="Maximum number of matching accounts to return.",
                ge=MIN_SEARCH_LIMIT,
                le=MAX_SEARCH_LIMIT,
            ),
        ] = DEFAULT_SEARCH_LIMIT,
    ) -> ToolPayload:
        return await whm_search_accounts(
            server_ref=server_ref, query=query, limit=limit, context=context
        )

    return {
        TOOL_WHM_LIST_SERVERS: ToolRisk.READ,
        TOOL_WHM_LIST_ACCOUNTS: ToolRisk.READ,
        TOOL_WHM_SEARCH_ACCOUNTS: ToolRisk.READ,
    }


__all__ = [
    "ACCOUNT_FIELDS_WITHHELD_FROM_MODEL",
    "DEFAULT_SEARCH_LIMIT",
    "DESCRIPTION_WHM_LIST_ACCOUNTS",
    "DESCRIPTION_WHM_LIST_SERVERS",
    "DESCRIPTION_WHM_SEARCH_ACCOUNTS",
    "ERROR_LIMIT_INVALID",
    "ERROR_QUERY_REQUIRED",
    "MAX_SEARCH_LIMIT",
    "MESSAGE_LIMIT_INVALID",
    "MESSAGE_QUERY_REQUIRED",
    "MIN_SEARCH_LIMIT",
    "TOOL_WHM_LIST_ACCOUNTS",
    "TOOL_WHM_LIST_SERVERS",
    "TOOL_WHM_SEARCH_ACCOUNTS",
    "WHM_ACCOUNT_TABLE_COLUMNS",
    "fetch_whm_accounts",
    "register_whm_read_tools",
    "whm_list_accounts",
    "whm_list_servers",
    "whm_search_accounts",
]

"""WHM READ tools (T19 — `whm_list_servers`; T21 — `whm_search_accounts`).

`whm_list_servers` is exposed on purpose and it is the only `*_list_servers` that is
(DECISIONS §6.6, owner-decided 2026-08-04): the model has to know which servers exist before
it can name one, while Proxmox and PMG nodes are few and named directly by the operator, so
`proxmox_list_servers` and `pmg_list_servers` stay internal (§I.mcp).

`whm_search_accounts` is the discovery step in front of every account CHANGE: T22 and T23 take
an exact `user`, and the operator has a domain or half a username. It answers over `listaccts`
because WHM has no server-side account search — see `fetch_whm_accounts`, which is internal
(C9, V17) and is also what `whm_list_accounts` (T20) will call.

**What leaves the process is `to_safe_dict()`, never the row.** That method is
`WHMServer`'s, it drops `api_token` and every SSH secret in favour of presence booleans
(V2, V8), and it is on `WHMServerRowLike` so this module cannot reach past it. The result
lands in a LibreChat transcript that persists in their MongoDB (V26), which is the reason
the rule is "no credential material", not "no plaintext password".

Two split responsibilities, both deliberate:

- The tool *function* takes a context and is directly unit-testable. Registration —
  the public name, description and annotations — is `register_whm_read_tools`, so the
  schema fastmcp derives comes from a signature with no context parameter in it.
- `sanitize_tool_errors` wraps the function, not the registration. A caller that reaches
  the function some other way (a future internal call, C9/V17) gets the same V19 guarantee.

Registration also declares the tool's `ToolRisk` (T73, V20). Nothing in this module records
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
from core.servers.whm_ref import resolve_whm_server_ref
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ERROR_UNKNOWN,
    ToolPayload,
    sanitize_tool_errors,
    tool_failure,
    tool_ok,
)

TOOL_WHM_LIST_SERVERS = "whm_list_servers"
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

DESCRIPTION_WHM_SEARCH_ACCOUNTS = (
    "Search the cPanel accounts on one WHM server by username or domain, case-insensitively. "
    "Use it to find the exact `user` an account tool needs before calling one; never guess a "
    "username. Returns the matching accounts, how many matched in total, and whether the list "
    "was cut short by `limit`. Read-only: it changes nothing."
)


@sanitize_tool_errors(TOOL_WHM_LIST_SERVERS)
async def whm_list_servers(*, context: McpToolContext) -> ToolPayload:
    """Every configured WHM server, credential-free (T19, V8)."""
    async with context.session_factory() as session:
        repository = context.whm_server_repository_factory(session)
        servers = await repository.list_servers()
        # Serialized inside the session: `to_safe_dict` reads mapped attributes, and a
        # detached instance would raise on a lazy refresh once the session closed.
        return tool_ok(servers=[server.to_safe_dict() for server in servers])


async def fetch_whm_accounts(*, server_ref: str, context: McpToolContext) -> ToolPayload:
    """Every account on the named WHM server, normalised. Internal — ⊥ an MCP tool (C9, V17).

    Not exposed and not decorated with `sanitize_tool_errors`: its callers are exposed tools
    that already are, and a second boundary would turn a `NoaError` into a payload the caller
    then has to unwrap twice. `whm_list_accounts` (T20) is the other caller.

    Three refusals travel back as a payload rather than an exception, all of them information
    the model can act on (V18, V19):

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

    result = await client.list_accounts()
    if result.get("ok") is not True:
        message = result.get("message")
        # A blank message would leave the operator with a code and no sentence.
        spoken = message if isinstance(message, str) and message.strip() else None
        return tool_failure(
            str(result.get("error_code") or ERROR_UNKNOWN),
            spoken or MESSAGE_LIST_ACCOUNTS_FAILED,
        )

    return tool_ok(accounts=normalize_whm_account_list(result.get("accounts")))


@sanitize_tool_errors(TOOL_WHM_SEARCH_ACCOUNTS)
async def whm_search_accounts(
    *,
    server_ref: str,
    query: str,
    limit: int = DEFAULT_SEARCH_LIMIT,
    context: McpToolContext,
) -> ToolPayload:
    """Accounts on one WHM server whose username or domain contains `query` (T21).

    **Filtered here, not by WHM.** `listaccts` takes no search parameter, so the whole list
    comes back and the match runs in this process. That is the ported behaviour and it is also
    why `limit` matters: the tool result is what enters the transcript, and an unbounded answer
    on a shared server is thousands of rows (V64 is the answer for a genuinely large *listing*,
    which is T20's problem, not this tool's — a bounded search needs no table surface, and the
    surface itself is built: `noa_api.mcp_tools.table_surface`, T56).

    Two guards before any I/O, so a malformed call costs no round trip:

    - a blank or whitespace-only `query` is refused (V21). The schema cannot express it —
      `min_length` counts whitespace — so this is the gate, and returning every account for
      `"   "` would be the opposite of a search.
    - `limit` outside 1-100 is refused. The schema bounds it too, and over MCP that is what
      answers first; this branch is the one that holds for a direct in-process call (C9, V17)
      and it is what makes the bound a property of the tool rather than of its registration.

    **Truncation is stated, not implied** (V85, and this tool is where that invariant was
    written). `total_matches` and `truncated` ship with the rows because `noa-old` returned the
    first N silently, and a model reading twenty rows with no other signal tells the operator
    there are twenty accounts — a fabrication the tool handed it rather than one the model
    invented.

    Matches are sorted by username before the cut, also V85: "the first twenty" has to be
    reproducible, and `listaccts` order is WHM's own and not documented as stable, which would
    make a truncated answer an arbitrary subset that changes between calls.
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
        accounts=matches[:limit],
        total_matches=len(matches),
        truncated=len(matches) > limit,
    )


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
        # is enforced by the approval gate (V16), not by an annotation a client may ignore.
        annotations={"readOnlyHint": True},
    )
    async def whm_list_servers_tool() -> ToolPayload:
        return await whm_list_servers(context=context)

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
        TOOL_WHM_SEARCH_ACCOUNTS: ToolRisk.READ,
    }


__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "DESCRIPTION_WHM_LIST_SERVERS",
    "DESCRIPTION_WHM_SEARCH_ACCOUNTS",
    "ERROR_LIMIT_INVALID",
    "ERROR_QUERY_REQUIRED",
    "MAX_SEARCH_LIMIT",
    "MESSAGE_LIMIT_INVALID",
    "MESSAGE_QUERY_REQUIRED",
    "MIN_SEARCH_LIMIT",
    "TOOL_WHM_LIST_SERVERS",
    "TOOL_WHM_SEARCH_ACCOUNTS",
    "fetch_whm_accounts",
    "register_whm_read_tools",
    "whm_list_servers",
    "whm_search_accounts",
]

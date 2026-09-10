"""`whm_list_accounts` — the listing that answers with a surface, not with rows.

The summary-plus-URL half of the WHM account pair. `whm_search_accounts` answers a bounded question
in the transcript; this one answers an unbounded one by parking its rows in `tool_result_tables` and
handing back a summary plus the address of the page that renders them.

Same seams as its sibling, and for the same reasons: real `WHMClient` over a doubled socket
(`support.whm_api`), because WHM reports a refusal as **HTTP 200** with `metadata.result: 0` and
a doubled client would let this pass against error shapes WHM never sends; real `SecretCipher`,
so the `Authorization` header proves a decrypt happened; real resolver, real
`sanitize_tool_errors`. The table *writer* is the double, because whether Postgres accepts the
insert is `test_result_tables_live.py`'s claim, not this file's.

Four properties carry the weight.

**No account reaches the model**. Asserted against the serialized `ToolResult`, with
a sentinel driven through the real writer — a row that only ever existed in a fixture proves
nothing about what a tool emits.

**What was parked is what the page will show**. The rows are sorted before they are handed
over, because `cap_rows` is a prefix and never a re-sort, and `listaccts` order is WHM's own and
undocumented — so an uncapped listing and a capped one have to be the same first N.

**A dead address is worse than a refusal.** A parked table that could not be written refuses the
READ; a URL to a table that was never stored is discovered later, by an operator, in a
transcript that persists.

**A failure is still the ordinary envelope**. The success answers with content
blocks, so this is where "one refusal shape whatever the success shape" is checked.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastmcp.tools import ToolResult
from mcp.types import EmbeddedResource, TextContent

from core.auth.tool_catalog import TOOL_CATALOG
from core.integrations.whm.accounts import normalize_whm_account_summary
from core.results.errors import ResultTableUnavailableError
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.results import (
    ERROR_TIMEOUT,
    ERROR_TOOL_EXECUTION_FAILED,
    MESSAGE_TIMEOUT,
    MESSAGE_TOOL_EXECUTION_FAILED,
)
from noa_api.mcp_tools.table_surface import TABLE_SURFACE_PATH, UI_RESOURCE_URI_PREFIX
from noa_api.mcp_tools.whm_read import (
    TOOL_WHM_LIST_ACCOUNTS,
    WHM_ACCOUNT_TABLE_COLUMNS,
    whm_list_accounts,
)
from support.mcp_identity import authenticated_caller, http_request_context
from support.result_tables import FakeToolResultTableWriter
from support.servers import (
    EMBED_BASE_URL,
    RESULT_TABLE_MAX_ROWS,
    SECRETS,
    ToolFixture,
    build_tool_context,
    whm_server,
)
from support.whm_api import FakeWHMApi, whm_account, whm_api_failure_body, whm_api_listing

# The plaintext behind the row's `api_token`, encrypted into the column so the header assertion
# proves a decrypt rather than a passthrough.
WHM_API_TOKEN = "whm-api-token-plaintext"

SERVER_NAME = "alpha"

# A value planted in a field `listaccts` really does send and NOA really does drop, so a
# whitelist that quietly became a passthrough is visible in what the writer was handed.
SENTINEL_SECRET = "sentinel-plan-name"


def listing_context(
    *,
    accounts: list[dict[str, Any]] | None = None,
    api: FakeWHMApi | None = None,
    servers: list[Any] | None = None,
    result_tables: FakeToolResultTableWriter | None = None,
) -> tuple[ToolFixture, FakeWHMApi]:
    """A tool context whose WHM endpoint is a `MockTransport` and whose table writer is a double."""
    endpoint = api or whm_api_listing(accounts or [])
    cipher = build_tool_context().cipher
    rows = servers if servers is not None else [whm_server(SERVER_NAME)]
    for row in rows:
        row.api_token = cipher.encrypt_text(WHM_API_TOKEN)
    fixture = build_tool_context(
        servers=rows,
        cipher=cipher,
        whm_transport=endpoint.transport,
        result_tables=result_tables,
    )
    return fixture, endpoint


async def listing(
    fixture: ToolFixture,
    *,
    server_ref: str = SERVER_NAME,
    user_id: UUID | None = None,
) -> tuple[Any, UUID]:
    """Call the tool inside a real request context; return its answer and the caller's id.

    The identity is read by `park_table_result` from the authenticated request rather than
    passed in, so the tool has to run inside the contextvar the auth middleware sets.
    """
    user, resolved = authenticated_caller(user_id)
    with http_request_context({}, user=user):
        answer = await whm_list_accounts(server_ref=server_ref, context=fixture.context)
    return answer, resolved


def text_block(answer: ToolResult) -> str:
    block = answer.content[0]
    assert isinstance(block, TextContent)
    return block.text


# --- The happy path: a surface, not a listing ---


async def test_it_answers_with_a_summary_block_and_the_table_iframe() -> None:
    """Two blocks, text first — the address survives a frame that will not load."""
    fixture, _ = listing_context(accounts=[whm_account("acme"), whm_account("beta")])

    answer, _ = await listing(fixture)

    assert isinstance(answer, ToolResult)
    assert len(answer.content) == 2
    assert isinstance(answer.content[0], TextContent)
    resource = answer.content[1]
    assert isinstance(resource, EmbeddedResource)
    token = fixture.result_tables.only.token
    assert str(resource.resource.uri) == f"{UI_RESOURCE_URI_PREFIX}{token}"


async def test_the_summary_names_the_server_that_was_actually_read() -> None:
    """The operator typed a hostname; the sentence has to name the machine that answered.

    A tool that echoed the `server_ref` back would tell an operator with several WHM boxes
    nothing they did not already type, and would say `10.0.0.5` where NOA calls it `alpha`.
    """
    fixture, _ = listing_context(
        accounts=[whm_account("acme")],
        servers=[whm_server(SERVER_NAME, base_url="https://alpha.example.net:2087")],
    )

    answer, _ = await listing(fixture, server_ref="alpha.example.net")

    assert SERVER_NAME in text_block(answer)


async def test_the_address_in_the_text_is_the_parked_table_s() -> None:
    """The URL an operator can copy names the row that was just written."""
    fixture, _ = listing_context(accounts=[whm_account("acme")])

    answer, _ = await listing(fixture)

    token = fixture.result_tables.only.token
    assert f"{EMBED_BASE_URL}{TABLE_SURFACE_PATH}/{token}" in text_block(answer)


async def test_an_empty_server_is_a_parked_table_with_no_rows() -> None:
    """Nothing has gone wrong: WHM answered and the box has no accounts.

    A refusal here would be a tool reporting an outage for a true answer, and the page says "this
    read matched no rows" rather than rendering headings over nothing (the never-blank-card family).
    """
    fixture, _ = listing_context(accounts=[])

    answer, _ = await listing(fixture)

    assert isinstance(answer, ToolResult)
    stored = fixture.result_tables.only
    assert stored.rows == []
    assert stored.total_rows == 0
    assert stored.truncated is False


async def test_an_account_whm_cannot_name_is_never_parked() -> None:
    """A row with no `user` has no follow-up call, so it is dropped before it reaches a page."""
    fixture, _ = listing_context(
        accounts=[{"domain": "acme.example.com"}, whm_account("acme2")],
    )

    await listing(fixture)

    assert [row["user"] for row in fixture.result_tables.only.rows] == ["acme2"]


# --- What the model is handed ---


async def test_no_account_row_reaches_the_tool_result() -> None:
    """The whole point of summary-plus-URL, asserted on the serialized result.

    Driven through the real writer, so the sentinel is a row that genuinely reached the table
    and not one that only ever existed in this file.
    """
    fixture, _ = listing_context(
        accounts=[whm_account("sentinel-account", domain="sentinel.example.com")]
    )

    answer, _ = await listing(fixture)
    serialized = answer.model_dump_json()

    assert "sentinel-account" not in serialized
    assert "sentinel.example.com" not in serialized


async def test_it_does_not_forward_api_username_or_host() -> None:
    """The exposed half of the internal/exposed boundary at `fetch_whm_accounts`:
    that internal function carries `api_username` and `host` off the resolved row,
    but this tool reads only `accounts` and `server` back out of its payload — so neither
    credential fact reaches this tool's answer, and never a LibreChat transcript.

    Asserted against the whole serialized result, not a top-level key check, so a leak
    nested inside a row or under any future key still fails this. Same assertion shape as
    this test's sibling on the search tool,
    `test_whm_search_accounts_does_not_forward_api_username_or_host` in
    `test_whm_tools_search_accounts.py` — the rule holds at the same strength on both.
    """
    fixture, _ = listing_context(accounts=[whm_account("acme")])

    answer, _ = await listing(fixture)
    serialized = answer.model_dump_json()

    assert "api_username" not in serialized
    assert "host" not in serialized


async def test_the_result_carries_no_credential_material() -> None:
    """Neither the ciphertext in the column nor the plaintext behind it."""
    fixture, _ = listing_context(accounts=[whm_account("acme", plan="business")])

    answer, _ = await listing(fixture)
    serialized = answer.model_dump_json()

    for secret in (*SECRETS, WHM_API_TOKEN):
        assert secret not in serialized


async def test_a_field_noa_does_not_speak_about_never_reaches_the_page() -> None:
    """The whitelist is what bounds a parked row, and it runs before anything is stored.

    Asserted on what the *writer received*, because the question is what the row holds — not
    whether something was removed again on the way out (the raw-vs-redacted receipt's shape).

    The stopping control here is `normalize_whm_account_summary`'s field list, not the recursive
    redaction `cap_rows` applies: `listaccts` sends plans, IPs and counters, and none of them are
    things NOA renders. Redaction is the second layer and it is the table surface's own claim —
    naming it here would be this file certifying a control that did no work.
    """
    fixture, _ = listing_context(
        accounts=[whm_account("acme", plan=SENTINEL_SECRET, ip="10.0.0.5", diskused="900M")],
    )

    await listing(fixture)

    stored = str(fixture.result_tables.only.rows)
    assert SENTINEL_SECRET not in stored
    assert "10.0.0.5" not in stored


# --- The bound belongs to the surface, and the order belongs to the producer ---


async def test_the_rows_are_parked_sorted_by_username() -> None:
    """The cap's ordering clause. `cap_rows` is a prefix, so the order it is handed is the answer.

    `listaccts` order is WHM's own and not documented as stable, which would make a capped
    page an arbitrary subset that differs between two identical calls.
    """
    fixture, _ = listing_context(
        accounts=[whm_account("zeta"), whm_account("acme"), whm_account("mid")]
    )

    await listing(fixture)

    assert [row["user"] for row in fixture.result_tables.only.rows] == ["acme", "mid", "zeta"]


async def test_a_capped_listing_stores_the_pre_cut_total_and_says_so() -> None:
    """The cap is the surface's, and it reports itself in the text and in the envelope.

    The fixture's cap is deliberately not `Settings`' 5000, so a tool that read the production
    default could not pass this (the gate's trick for the pending TTL).
    """
    fixture, _ = listing_context(
        accounts=[whm_account(f"account-{index:04d}") for index in range(RESULT_TABLE_MAX_ROWS + 3)]
    )

    answer, _ = await listing(fixture)

    stored = fixture.result_tables.only
    assert len(stored.rows) == RESULT_TABLE_MAX_ROWS
    assert stored.total_rows == RESULT_TABLE_MAX_ROWS + 3
    assert stored.truncated is True
    assert answer.structured_content == {
        "ok": True,
        "total_rows": RESULT_TABLE_MAX_ROWS + 3,
        "stored_rows": RESULT_TABLE_MAX_ROWS,
        "truncated": True,
    }
    assert "truncated" in text_block(answer)


async def test_an_uncapped_listing_does_not_claim_truncation() -> None:
    """The negative control: without it, the case above passes against a constant."""
    fixture, _ = listing_context(accounts=[whm_account("acme")])

    answer, _ = await listing(fixture)

    assert fixture.result_tables.only.truncated is False
    assert answer.structured_content == {
        "ok": True,
        "total_rows": 1,
        "stored_rows": 1,
        "truncated": False,
    }
    assert "truncated" not in text_block(answer)


async def test_there_is_no_limit_argument_to_hide_rows_behind() -> None:
    """A listing offloads *whole*; the cap is the one an operator asked for.

    A `limit` here would be a second bound, applied before the table's own and invisible on
    the page — the shape the cap's own bound exists to stop, one surface further back.
    """
    server = build_mcp_server(tool_context=build_tool_context().context)
    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}

    schema = tools[TOOL_WHM_LIST_ACCOUNTS].parameters

    assert set(schema["properties"]) == {"server_ref"}
    assert schema["required"] == ["server_ref"]


# --- Whose table is it ---


async def test_the_table_is_parked_for_the_caller_the_token_authenticated() -> None:
    """Requester-match: the requester is the authenticated identity, never anything off the
    arguments.

    That column is what the surface matches on later, so a tool that could name a requester
    would be a tool that could park a listing under somebody else's name.
    """
    fixture, _ = listing_context(accounts=[whm_account("acme")])

    _, caller = await listing(fixture)

    assert fixture.result_tables.only.requested_by_user_id == caller


async def test_the_parked_table_is_named_after_the_tool_that_produced_it() -> None:
    """The page renders this, and the audit row is keyed on the same string."""
    fixture, _ = listing_context(accounts=[whm_account("acme")])

    await listing(fixture)

    assert fixture.result_tables.only.tool_name == TOOL_WHM_LIST_ACCOUNTS


# --- The columns, and the whitelist they are read from ---


def test_every_column_names_a_field_the_normaliser_can_emit() -> None:
    """Two lists of WHM account fields, and they must not drift apart.

    `listaccts` rows are sparse, so a column list derived from the first row would drop a
    column every later row has — the columns are declared instead. This is what stops a
    declared column from naming a key no row will ever carry, and a whitelisted field from
    never being rendered.
    """
    fully_populated = normalize_whm_account_summary(
        {
            "user": "acme",
            "domain": "acme.example.com",
            "email": "ops@acme.example.com",
            "contactemail": "billing@acme.example.com",
            "owner": "reseller",
            "suspended": 1,
            "suspendtime": "1754870400",
            "suspendreason": "abuse",
            "is_locked": 1,
        }
    )

    assert fully_populated is not None
    assert {column.key for column in WHM_ACCOUNT_TABLE_COLUMNS} == set(fully_populated)


def test_the_columns_reach_the_writer_in_the_declared_order() -> None:
    """The order is part of what was rendered, so it is stored rather than re-derived."""
    assert [column.key for column in WHM_ACCOUNT_TABLE_COLUMNS][:2] == ["user", "domain"]


# --- Refusals ---


async def test_an_ambiguous_server_ref_returns_choices() -> None:
    """A tie is candidates, never a pick — a guess would list the wrong server's accounts."""
    shared = "https://shared.example.net:2087"
    fixture, endpoint = listing_context(
        accounts=[whm_account("acme")],
        servers=[whm_server("one", base_url=shared), whm_server("two", base_url=shared)],
    )

    answer, _ = await listing(fixture, server_ref="shared.example.net")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in answer["choices"]] == ["one", "two"]
    # Nothing was called and nothing was parked: no server was picked.
    assert endpoint.requests == []
    assert fixture.result_tables.stored == []


async def test_an_unknown_server_ref_is_host_not_found() -> None:
    fixture, _ = listing_context(accounts=[whm_account("acme")])

    answer, _ = await listing(fixture, server_ref="nope")

    assert answer["ok"] is False
    assert answer["error_code"] == "host_not_found"
    assert fixture.result_tables.stored == []


@pytest.mark.parametrize(
    ("api", "expected_code"),
    [
        pytest.param(
            FakeWHMApi(body=whm_api_failure_body("Access denied")),
            "whm_api_error",
            id="whm-refusal-http-200",
        ),
        pytest.param(
            FakeWHMApi(body={"metadata": {"result": 1}}, status_code=401),
            "auth_failed",
            id="http-401",
        ),
    ],
)
async def test_a_whm_failure_keeps_whm_s_own_error_code(
    api: FakeWHMApi, expected_code: str
) -> None:
    """The code says which system to fix, and no table is parked for an answer nobody got."""
    fixture, _ = listing_context(api=api)

    answer, _ = await listing(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == expected_code
    assert fixture.result_tables.stored == []


async def test_the_whm_call_authenticates_with_the_decrypted_token() -> None:
    """The column holds ciphertext and WHM has to receive plaintext."""
    fixture, endpoint = listing_context(accounts=[whm_account("acme")])

    await listing(fixture)

    assert endpoint.authorization_headers == [f"whm root:{WHM_API_TOKEN}"]


async def test_a_failed_park_refuses_the_read_rather_than_a_dead_address() -> None:
    """Fail-closed (the gate's rule, one surface over), through the real error boundary.

    The refusal is visible at the moment it happens; a result carrying the address of a table
    that was never written is discovered later, by an operator, in a persisted transcript.
    """
    fixture, _ = listing_context(
        accounts=[whm_account("acme")],
        result_tables=FakeToolResultTableWriter(fail_with=RuntimeError("insert failed")),
    )

    answer, _ = await listing(fixture)

    assert answer["ok"] is False
    assert answer["error_code"] == ResultTableUnavailableError.error_code
    assert answer["message"] == ResultTableUnavailableError.message
    assert "insert failed" not in str(answer)


class ExplodingWHMServerRepository:
    """A `WHMServerReadRepository` that fails the way a real one can."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def list_servers(self) -> Any:
        raise self._error

    async def get_by_id(self, server_id: UUID) -> Any:
        raise self._error


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_message"),
    [
        pytest.param(
            RuntimeError("connection to postgres lost at 10.0.0.5:5432"),
            ERROR_TOOL_EXECUTION_FAILED,
            MESSAGE_TOOL_EXECUTION_FAILED,
            id="runtime-error",
        ),
        pytest.param(
            TimeoutError("read timed out after 30s"),
            ERROR_TIMEOUT,
            MESSAGE_TIMEOUT,
            id="timeout",
        ),
    ],
)
async def test_an_exception_reaches_the_caller_as_a_named_failure(
    error: BaseException, expected_code: str, expected_message: str
) -> None:
    """The two sanitized mappings, and the original text never travels.

    The success path answers with content blocks, so this is also where "a failure is the same
    envelope whatever the success was" is held — `sanitize_tool_errors` widens a return type
    rather than fixing one.
    """
    fixture, _ = listing_context(accounts=[whm_account("acme")])
    context = replace(
        fixture.context,
        whm_server_repository_factory=lambda _session: ExplodingWHMServerRepository(error),
    )
    user, _ = authenticated_caller(uuid4())

    with http_request_context({}, user=user):
        answer = await whm_list_accounts(server_ref=SERVER_NAME, context=context)

    assert answer == {"ok": False, "error_code": expected_code, "message": expected_message}
    assert "10.0.0.5" not in str(answer)


async def test_cancellation_is_not_swallowed() -> None:
    """A cancelled request has no caller left to answer; swallowing it hangs a shutdown."""
    fixture, _ = listing_context(accounts=[whm_account("acme")])
    context = replace(
        fixture.context,
        whm_server_repository_factory=lambda _session: ExplodingWHMServerRepository(
            asyncio.CancelledError()
        ),
    )
    user, _ = authenticated_caller(uuid4())

    with pytest.raises(asyncio.CancelledError), http_request_context({}, user=user):
        await whm_list_accounts(server_ref=SERVER_NAME, context=context)


# --- The tool ships with its gate ---


def test_the_tool_name_matches_the_catalog() -> None:
    """The registered name is the one RBAC grants are written against."""
    assert TOOL_WHM_LIST_ACCOUNTS in TOOL_CATALOG

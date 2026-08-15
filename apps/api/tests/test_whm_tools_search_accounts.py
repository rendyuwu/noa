"""`whm_search_accounts` — the discovery step in front of every account CHANGE (T21).

Real `WHMClient`, real `SecretCipher`, real resolver, real `sanitize_tool_errors`. Only the
socket is doubled (`support.whm_api`), because the client's whole job is normalising answers a
fake client would never produce: WHM reports a refusal as **HTTP 200** with
`metadata.result: 0`.

Four properties carry the weight here.

**The token that reaches WHM is the decrypted one** (C7, §V.48). The row holds
`enc:v1:fernet:…`, and the assertion is on the `Authorization` header the transport captured —
so the decrypt site is exercised rather than assumed.

**Nothing a result carries is credential material** (§V.2, §V.8). Asserted against *both* the
ciphertext in the column and the plaintext behind it: a leak of either into a LibreChat
transcript (§V.26) is the same leak.

**A refusal names its cause** (§V.18, §V.19, §V.21). An ambiguous `server_ref` comes back with
`choices`; a blank query and an out-of-range limit are refused before any I/O; a WHM failure
keeps WHM's own code; an exception becomes one of §V.19's two mappings.

**A truncated answer says so.** `noa-old` returned the first N rows silently, which lets a
model tell the operator "there are twenty accounts" when there are two hundred.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest

from core.auth.tool_catalog import TOOL_CATALOG
from core.integrations.whm.accounts import normalize_whm_account_summary
from noa_api.mcp_server import build_mcp_server
from noa_api.mcp_tools.results import (
    ERROR_TIMEOUT,
    ERROR_TOOL_EXECUTION_FAILED,
    MESSAGE_TIMEOUT,
    MESSAGE_TOOL_EXECUTION_FAILED,
)
from noa_api.mcp_tools.whm_read import (
    DEFAULT_SEARCH_LIMIT,
    ERROR_LIMIT_INVALID,
    ERROR_QUERY_REQUIRED,
    MAX_SEARCH_LIMIT,
    MIN_SEARCH_LIMIT,
    TOOL_WHM_SEARCH_ACCOUNTS,
    whm_search_accounts,
)
from support.servers import SECRETS, ToolFixture, build_tool_context, whm_server
from support.whm_api import FakeWHMApi, whm_account, whm_api_failure_body, whm_api_listing

# The plaintext behind the row's `api_token`. Encrypted into the column, so the header
# assertion proves a decrypt happened rather than a passthrough.
WHM_API_TOKEN = "whm-api-token-plaintext"

SERVER_NAME = "alpha"


def search_context(
    *,
    accounts: list[dict[str, Any]] | None = None,
    api: FakeWHMApi | None = None,
    servers: list[Any] | None = None,
) -> tuple[ToolFixture, FakeWHMApi]:
    """A tool context whose WHM endpoint is a `MockTransport` over `api`.

    The row's API token is encrypted with the fixture's own cipher, so `build_whm_client`
    really decrypts it. Everything else — client, resolver, tool — is production code.
    """
    endpoint = api or whm_api_listing(accounts or [])
    cipher = build_tool_context().cipher
    rows = servers if servers is not None else [whm_server(SERVER_NAME)]
    encrypted = [replace_token(row, cipher.encrypt_text(WHM_API_TOKEN)) for row in rows]
    fixture = build_tool_context(servers=encrypted, cipher=cipher, whm_transport=endpoint.transport)
    return fixture, endpoint


def replace_token(server: Any, api_token: str) -> Any:
    """The same row with a decryptable token. Mutated in place — it is a fresh instance."""
    server.api_token = api_token
    return server


async def search(
    fixture: ToolFixture,
    *,
    query: str = "acme",
    server_ref: str = SERVER_NAME,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> dict[str, Any]:
    return await whm_search_accounts(
        server_ref=server_ref, query=query, limit=limit, context=fixture.context
    )


# --- The happy path ---


async def test_it_returns_the_matching_accounts_with_their_operational_fields() -> None:
    """The shape a model reads: matches, the query it asked for, and the totals."""
    fixture, _ = search_context(
        accounts=[
            whm_account("acme", domain="acme.example.com", suspended=1),
            whm_account("zeta", domain="zeta.example.com"),
        ]
    )

    result = await search(fixture, query="acme")

    assert result == {
        "ok": True,
        "query": "acme",
        "accounts": [
            {"user": "acme", "domain": "acme.example.com", "suspended": True},
        ],
        "total_matches": 1,
        "truncated": False,
    }


async def test_it_matches_username_and_domain_case_insensitively() -> None:
    """Operators type what they remember, in whatever case they remember it."""
    fixture, _ = search_context(
        accounts=[
            whm_account("AcmeCorp", domain="acme.example.com"),
            whm_account("beta", domain="ACME-SHOP.example.com"),
            whm_account("zeta", domain="zeta.example.com"),
        ]
    )

    result = await search(fixture, query="ACME")

    assert [account["user"] for account in result["accounts"]] == ["AcmeCorp", "beta"]


async def test_a_query_spanning_the_username_and_the_domain_does_not_match() -> None:
    """The departure from `noa-old`, asserted end to end.

    There the two fields were joined into one haystack, so a query with a space matched across
    the junction and returned a row containing nothing the operator typed.
    """
    fixture, _ = search_context(accounts=[whm_account("acme", domain="shop.example.com")])

    result = await search(fixture, query="acme sho")

    assert result["accounts"] == []
    assert result["total_matches"] == 0


async def test_no_match_is_a_success_with_no_accounts() -> None:
    """Nothing has gone wrong: the server answered and no account matched."""
    fixture, _ = search_context(accounts=[whm_account("zeta")])

    result = await search(fixture, query="acme")

    assert result["ok"] is True
    assert result["accounts"] == []
    assert result["truncated"] is False


async def test_an_account_whm_cannot_name_is_never_offered() -> None:
    """A row with no `user` has no follow-up call, so it is dropped before matching."""
    fixture, _ = search_context(
        accounts=[{"domain": "acme.example.com"}, whm_account("acme2", domain="acme.example.net")]
    )

    result = await search(fixture, query="acme")

    assert [account["user"] for account in result["accounts"]] == ["acme2"]


# --- V85: a capped READ carries its own bound ---


async def test_it_truncates_at_the_limit_and_says_so() -> None:
    """§V.85, first clause. Without the two fields, five rows read as "there are five".

    This tool is where §V.85 was written, so these three cases are the invariant's only
    coverage until a second capped READ lands.
    """
    fixture, _ = search_context(
        accounts=[whm_account(f"acme{index}") for index in range(5)],
    )

    result = await search(fixture, query="acme", limit=2)

    assert len(result["accounts"]) == 2
    assert result["total_matches"] == 5
    assert result["truncated"] is True


async def test_a_result_exactly_at_the_limit_is_not_truncated() -> None:
    """Off-by-one guard: five of five is a complete answer (§V.85)."""
    fixture, _ = search_context(accounts=[whm_account(f"acme{index}") for index in range(5)])

    result = await search(fixture, query="acme", limit=5)

    assert result["total_matches"] == 5
    assert result["truncated"] is False


async def test_matches_are_sorted_by_username_before_the_cut() -> None:
    """§V.85, second clause. `listaccts` order is WHM's own and undocumented.

    Sorting first makes "the first two" reproducible across calls; an unsorted cut is an
    arbitrary subset that can differ between two identical searches.
    """
    fixture, _ = search_context(
        accounts=[whm_account("acme-zeta"), whm_account("acme-beta"), whm_account("acme-alpha")]
    )

    result = await search(fixture, query="acme", limit=2)

    assert [account["user"] for account in result["accounts"]] == ["acme-alpha", "acme-beta"]


# --- V21: the argument guards, before any I/O ---


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
async def test_a_blank_query_is_refused(query: str) -> None:
    """§V.21. The schema cannot express it — `min_length` counts whitespace — so this is it.

    Returning every account for `"   "` would be the opposite of a search.
    """
    fixture, endpoint = search_context(accounts=[whm_account("acme")])

    result = await search(fixture, query=query)

    assert result["ok"] is False
    assert result["error_code"] == ERROR_QUERY_REQUIRED
    # Refused before any I/O: no server was resolved and WHM was never called.
    assert fixture.servers.reads == 0
    assert endpoint.requests == []


@pytest.mark.parametrize(
    "limit",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(MAX_SEARCH_LIMIT + 1, id="over-the-cap"),
    ],
)
async def test_a_limit_outside_one_to_a_hundred_is_refused(limit: int) -> None:
    """§T.21's bound, held by the tool and not only by its schema.

    Over MCP the schema refuses first; this branch is what holds for an in-process call
    (C9, §V.17) and it is what makes the bound a property of the tool.
    """
    fixture, endpoint = search_context(accounts=[whm_account("acme")])

    result = await search(fixture, query="acme", limit=limit)

    assert result["ok"] is False
    assert result["error_code"] == ERROR_LIMIT_INVALID
    assert endpoint.requests == []


@pytest.mark.parametrize("limit", [MIN_SEARCH_LIMIT, MAX_SEARCH_LIMIT])
async def test_the_bounds_themselves_are_accepted(limit: int) -> None:
    """Inclusive, both ends — a `<` in place of `<=` would refuse a legal call."""
    fixture, _ = search_context(accounts=[whm_account("acme")])

    result = await search(fixture, query="acme", limit=limit)

    assert result["ok"] is True


async def test_the_schema_bounds_limit_to_a_hundred() -> None:
    """The other half of the bound: what refuses a bad call at the MCP boundary.

    Read off the registered tool rather than restated, so a signature that drops the `Field`
    constraints fails here instead of silently accepting `limit=5000` from a client.
    """
    server = build_mcp_server(tool_context=build_tool_context().context)
    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}

    schema = tools[TOOL_WHM_SEARCH_ACCOUNTS].parameters
    limit = schema["properties"]["limit"]

    assert limit["minimum"] == MIN_SEARCH_LIMIT
    assert limit["maximum"] == MAX_SEARCH_LIMIT
    assert limit["default"] == DEFAULT_SEARCH_LIMIT
    assert sorted(schema["required"]) == ["query", "server_ref"]


async def test_the_schema_carries_no_reason_parameter() -> None:
    """C8: a READ tool has no reason either, and a schema is where one would appear."""
    server = build_mcp_server(tool_context=build_tool_context().context)
    tools = {tool.name: tool for tool in await server.list_tools(run_middleware=False)}

    properties = tools[TOOL_WHM_SEARCH_ACCOUNTS].parameters["properties"]

    assert set(properties) == {"server_ref", "query", "limit"}


async def test_a_suspension_note_never_reaches_the_model() -> None:
    """C8 by round trip, and the reason this file changed at T22.

    A schema with no reason parameter is only half the boundary. As of T22 NOA writes the
    operator's approval reason into WHM's suspension note, WHM returns it as `suspendreason` on
    every later `listaccts`, and this tool's rows go into the transcript — so a search would hand
    the model the one string C8 says it must never see, by way of the system NOA just wrote it
    to.

    Asserted on the serialized result rather than on the row dict: what C8 bounds is what reaches
    the model, and a field dropped from one place and kept in another is exactly the kind of
    thing a key-set assertion alone would miss. The normaliser still carries the field, and
    `whm_list_accounts`' parked table still renders it — that page is behind the operator's own
    cookie (V27), which is where the reason may be read.
    """
    operator_words = "Customer confirmed the abuse ticket by phone."
    fixture, _ = search_context(
        accounts=[
            whm_account(
                "acme",
                domain="acme.example.com",
                suspended=1,
                suspendreason=operator_words,
            )
        ]
    )

    result = await search(fixture, query="acme")

    assert result["accounts"] == [{"user": "acme", "domain": "acme.example.com", "suspended": True}]
    assert operator_words not in json.dumps(result)
    # The whitelist itself is unchanged: the field is dropped for this surface, not for NOA.
    assert "suspendreason" in normalize_whm_account_summary(
        whm_account("acme", suspended=1, suspendreason=operator_words)
    )


# --- V18: which server did they mean? ---


async def test_an_ambiguous_server_ref_returns_choices() -> None:
    """§V.18: a tie is candidates, never a pick — a guess would search the wrong server."""
    shared = "https://shared.example.net:2087"
    fixture, endpoint = search_context(
        accounts=[whm_account("acme")],
        servers=[whm_server("one", base_url=shared), whm_server("two", base_url=shared)],
    )

    result = await search(fixture, server_ref="shared.example.net")

    assert result["ok"] is False
    assert result["error_code"] == "host_ambiguous"
    assert [choice["name"] for choice in result["choices"]] == ["one", "two"]
    # No server was picked, so nothing was called.
    assert endpoint.requests == []


async def test_an_unknown_server_ref_is_host_not_found() -> None:
    fixture, _ = search_context(accounts=[whm_account("acme")])

    result = await search(fixture, server_ref="nope")

    assert result["ok"] is False
    assert result["error_code"] == "host_not_found"
    assert "choices" not in result


async def test_a_blank_server_ref_is_refused() -> None:
    """§V.21 again, on the other required string."""
    fixture, _ = search_context(accounts=[whm_account("acme")])

    result = await search(fixture, server_ref="   ")

    assert result["ok"] is False
    assert result["error_code"] == "host_required"


async def test_the_resolved_server_is_the_one_called() -> None:
    """The row that won the resolution is the row whose credentials are used.

    Asserted on the URL the transport saw: a re-read by id, or a fallback to "the first
    server", would send the operator's search to a machine they did not name.
    """
    fixture, endpoint = search_context(
        accounts=[whm_account("acme")],
        servers=[whm_server("alpha"), whm_server("beta")],
    )

    await search(fixture, server_ref="beta")

    [request] = endpoint.requests
    assert request.url.host == "beta.example.net"


async def test_a_server_ref_by_id_resolves() -> None:
    """The unambiguous form, and what a `choices` list tells the caller to come back with."""
    server_id = uuid4()
    fixture, endpoint = search_context(
        accounts=[whm_account("acme")],
        servers=[whm_server("alpha", server_id=server_id)],
    )

    result = await search(fixture, server_ref=str(server_id))

    assert result["ok"] is True
    assert endpoint.requests


# --- C7, V48: the credential path ---


async def test_the_whm_call_authenticates_with_the_decrypted_token() -> None:
    """The column holds ciphertext; WHM has to receive plaintext.

    This is the assertion that keeps `build_whm_client` in the path. A tool that passed the
    column value straight through would still "work" against a WHM that accepts anything, and
    fail only in production.
    """
    fixture, endpoint = search_context(accounts=[whm_account("acme")])

    await search(fixture, query="acme")

    assert endpoint.authorization_headers == [f"whm root:{WHM_API_TOKEN}"]


async def test_the_result_carries_no_credential_material() -> None:
    """§V.2, §V.8: neither the ciphertext nor the plaintext behind it (§V.26).

    Against the whole serialized payload rather than key by key: what must hold is that the
    *values* appear nowhere, however they are nested.
    """
    fixture, _ = search_context(
        accounts=[whm_account("acme", domain="acme.example.com", plan="business")]
    )

    result = await search(fixture, query="acme")
    serialized = json.dumps(result, default=str)

    for secret in (*SECRETS, WHM_API_TOKEN):
        assert secret not in serialized


async def test_the_result_carries_no_server_row_fields() -> None:
    """A search answers about accounts. Server rows are `whm_list_servers`' business."""
    fixture, _ = search_context(accounts=[whm_account("acme")])

    result = await search(fixture, query="acme")

    assert set(result) == {"ok", "query", "accounts", "total_matches", "truncated"}


# --- V19: failures ---


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
        pytest.param(
            FakeWHMApi(body={"metadata": {"result": 1}}, status_code=500),
            "http_error",
            id="http-500",
        ),
        pytest.param(
            FakeWHMApi(body={"data": {"acct": []}}),
            "invalid_response",
            id="no-metadata",
        ),
    ],
)
async def test_a_whm_failure_keeps_whm_s_own_error_code(
    api: FakeWHMApi, expected_code: str
) -> None:
    """The code says which system to fix: `auth_failed` is a NOA row, `http_error` is a host.

    Collapsing them into `tool_execution_failed` would tell the operator to retry, which is
    the wrong action for three of these four.
    """
    fixture, _ = search_context(api=api)

    result = await search(fixture, query="acme")

    assert result["ok"] is False
    assert result["error_code"] == expected_code


async def test_whm_s_own_reason_reaches_the_operator() -> None:
    """WHM's `reason` is the diagnosis; it carries no credential (see the client)."""
    fixture, _ = search_context(api=FakeWHMApi(body=whm_api_failure_body("Access denied")))

    result = await search(fixture, query="acme")

    assert result["message"] == "Access denied"


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
    """§V.19's two mappings, and the original text never travels (§V.8, §V.26)."""
    fixture, _ = search_context(accounts=[whm_account("acme")])
    context = replace(
        fixture.context,
        whm_server_repository_factory=lambda _session: ExplodingWHMServerRepository(error),
    )

    result = await whm_search_accounts(server_ref=SERVER_NAME, query="acme", context=context)

    assert result == {"ok": False, "error_code": expected_code, "message": expected_message}
    assert "10.0.0.5" not in json.dumps(result)


async def test_cancellation_is_not_swallowed() -> None:
    """A cancelled request has no caller left to answer; swallowing it hangs a shutdown."""
    fixture, _ = search_context(accounts=[whm_account("acme")])
    context = replace(
        fixture.context,
        whm_server_repository_factory=lambda _session: ExplodingWHMServerRepository(
            asyncio.CancelledError()
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await whm_search_accounts(server_ref=SERVER_NAME, query="acme", context=context)


# --- V83a: the tool ships with its gate ---


def test_the_tool_name_matches_the_catalog() -> None:
    """The registered name is the one RBAC grants are written against (§V.10)."""
    assert TOOL_WHM_SEARCH_ACCOUNTS in TOOL_CATALOG

"""WHM READ tools and the error boundary in front of the model.

Two properties, and the second is the one with teeth.

**Nothing a tool emits carries credential material**. The rows in `support.servers`
always have an API token, an SSH password and a private key, so "no secrets leaked" is a
claim about a row that had some. The result also lands in LibreChat's MongoDB, which
is why the assertion is against the ciphertext literals rather than against a plaintext
password nobody stores anyway.

**No raw exception reaches the caller**. `sanitize_tool_errors` is exercised by making
the repository raise, because that is where a real failure comes from: a dropped connection,
a timeout, a bug. The two mappings the sanitizer names are asserted by code, and a `NoaError` is
asserted to keep its own code — collapsing `ssh_host_key_mismatch` into
`tool_execution_failed` would strip the one string that says what to fix.

**The listing answers `describe()`, never `to_safe_dict()`**, and a
`is_reseller_credential = true` row is left out of it — visibility only, not authorization:
the same row still resolves through `resolve_whm_server_ref`, which is what keeps
the account CHANGE path's owner compare depends on reachable.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest

from core.auth.tool_catalog import TOOL_CATALOG
from core.remote_exec.errors import SSHExecutionError
from core.servers.reference import resolve_whm_server_ref
from noa_api.mcp_tools.context import McpToolContext
from noa_api.mcp_tools.results import (
    ERROR_TIMEOUT,
    ERROR_TOOL_EXECUTION_FAILED,
    MESSAGE_TIMEOUT,
    MESSAGE_TOOL_EXECUTION_FAILED,
)
from noa_api.mcp_tools.whm_read import TOOL_WHM_LIST_SERVERS, whm_list_servers
from support.servers import SECRETS, build_tool_context, whm_server


class ExplodingWHMServerRepository:
    """A `ServerRefRepository` that fails the way a real one can."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def list_servers(self) -> Any:
        raise self._error

    async def get_by_id(self, server_id: UUID) -> Any:
        raise self._error


def context_that_raises(error: BaseException) -> McpToolContext:
    """A production tool context whose WHM repository raises `error` on any read."""
    return replace(
        build_tool_context().context,
        whm_server_repository_factory=lambda _session: ExplodingWHMServerRepository(error),
    )


# --- The happy path ---


async def test_it_lists_every_configured_server_ordered_by_name() -> None:
    """DECISIONS section 6.6: the model needs to know which servers exist."""
    fixture = build_tool_context(servers=[whm_server("beta"), whm_server("alpha")])

    result = await whm_list_servers(context=fixture.context)

    assert result["ok"] is True
    assert [server["name"] for server in result["servers"]] == ["alpha", "beta"]


async def test_an_empty_inventory_is_a_success_with_no_servers() -> None:
    """Zero servers is an answer, not a failure — nothing has gone wrong yet."""
    result = await whm_list_servers(context=build_tool_context().context)

    assert result["ok"] is True
    assert result["servers"] == []


# --- What leaves the process ---


async def test_the_server_list_carries_no_api_token_and_no_ssh_credential() -> None:
    """Every stored secret is absent from the serialized result.

    Asserted against the whole JSON rather than key by key: a future column added to
    the row would slip past a key allowlist, and the thing that must hold is that the
    *values* never appear, wherever they are nested.
    """
    fixture = build_tool_context(servers=[whm_server("alpha")])

    result = await whm_list_servers(context=fixture.context)
    serialized = json.dumps(result, default=str)

    for secret in SECRETS:
        assert secret not in serialized


async def test_the_server_list_answers_describe_not_the_admin_view() -> None:
    """The payload is `describe()`'s 3 fields, none of `to_safe_dict`'s admin extras.

    Named individually rather than only diffed against an allowlist: a later change that
    routes `to_safe_dict()` back into this tool should fail on the specific field it
    reintroduces, not just on "the key set changed".
    """
    fixture = build_tool_context(servers=[whm_server("alpha")])

    result = await whm_list_servers(context=fixture.context)

    [server] = result["servers"]
    assert set(server.keys()) == {"id", "name", "base_url"}
    for admin_only_field in (
        "api_username",
        "has_api_token",
        "verify_ssl",
        "is_reseller_credential",
        "ssh_username",
        "ssh_port",
        "ssh_host_key_fingerprint",
        "has_ssh_password",
        "has_ssh_private_key",
        "created_at",
        "updated_at",
    ):
        assert admin_only_field not in server


async def test_inventory_is_read_on_every_call() -> None:
    """No memoization: a server added through `/admin` shows up on the next call, not the
    next process (the same no-cache rule the RBAC engine follows, in spirit)."""
    fixture = build_tool_context(servers=[whm_server("alpha")])

    await whm_list_servers(context=fixture.context)
    await whm_list_servers(context=fixture.context)

    assert fixture.servers.reads == 2


# --- A reseller-credential row is hidden here, not everywhere ---


async def test_a_reseller_credential_row_is_absent_from_the_listing() -> None:
    """`is_reseller_credential = true` hides a row from `whm_list_servers`' output.

    The listing problem the visibility filter exists for: 16 clusters x ~7 rows is 112 candidates in
    a transcript, and only the root row per cluster is a useful name for the model to read.

    Named `name == api_username` — the admin write refuses a `true` row any other
    way, so a fixture that skipped the pairing would test a shape production cannot hold.
    """
    reseller_row = whm_server(
        "web08cpnpool01", api_username="web08cpnpool01", is_reseller_credential=True
    )
    fixture = build_tool_context(servers=[reseller_row, whm_server("root1")])

    result = await whm_list_servers(context=fixture.context)

    assert [server["name"] for server in result["servers"]] == ["root1"]


async def test_a_non_reseller_row_is_listed() -> None:
    """Negative control for the filter above: without it, filtering everything out would
    also pass a naive `is not True` check (the negative-control rule — a listing with nothing
    hidden proves nothing about the filter itself)."""
    fixture = build_tool_context(servers=[whm_server("root1")])

    result = await whm_list_servers(context=fixture.context)

    assert [server["name"] for server in result["servers"]] == ["root1"]


async def test_a_reseller_credential_row_still_resolves_by_id_name_and_hostname() -> None:
    """The owner compare needs this row reachable — hiding it from the *listing* must not hide
    it from *resolution*, or the account CHANGE path becomes unreachable for every account
    such a row owns.

    Named `name == api_username`, same as the sibling test above: the row this
    proves reachable is a row the admin write can actually create, not a shape that is only
    ever hidden and never held. `server_ref = owner` resolves by exactly this pairing.
    """
    reseller_row = whm_server(
        "web08cpnpool01",
        api_username="web08cpnpool01",
        is_reseller_credential=True,
        base_url="https://cluster1.example.net:2087",
    )
    fixture = build_tool_context(servers=[reseller_row])

    by_id = await resolve_whm_server_ref(str(reseller_row.id), repository=fixture.servers)
    by_name = await resolve_whm_server_ref("web08cpnpool01", repository=fixture.servers)
    by_host = await resolve_whm_server_ref("cluster1.example.net", repository=fixture.servers)

    for resolution in (by_id, by_name, by_host):
        assert resolution.ok is True
        assert resolution.server is not None
        assert resolution.server.id == reseller_row.id


# --- The error boundary ---


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
    """The sanitizer's two named mappings, and the text of the original never travels.

    The exception messages carry an internal host and a port on purpose: those are exactly
    the strings that must not end up in a transcript.
    """
    result = await whm_list_servers(context=context_that_raises(error))

    assert result == {"ok": False, "error_code": expected_code, "message": expected_message}
    assert "10.0.0.5" not in json.dumps(result)


async def test_a_noa_error_keeps_its_own_code() -> None:
    """An integration-layer refusal names the thing to fix, rather than collapsing.

    `ssh_host_key_mismatch` tells an operator to investigate a host key; folding it into
    `tool_execution_failed` would tell them to retry — the opposite of the right action.
    """
    context = context_that_raises(
        SSHExecutionError(code="ssh_host_key_mismatch", message="Presented key is not the pin")
    )

    result = await whm_list_servers(context=context)

    assert result["ok"] is False
    assert result["error_code"] == "ssh_host_key_mismatch"


async def test_cancellation_is_not_swallowed() -> None:
    """`BaseException` passes through: a cancelled request has no caller left to answer.

    Catching `CancelledError` here would turn a shutdown into a hang, which is why the
    decorator catches `Exception` and stops there.
    """
    with pytest.raises(asyncio.CancelledError):
        await whm_list_servers(context=context_that_raises(asyncio.CancelledError()))


def test_the_tool_name_matches_the_catalog() -> None:
    """The registered name is the one RBAC grants are written against."""
    assert TOOL_WHM_LIST_SERVERS in TOOL_CATALOG

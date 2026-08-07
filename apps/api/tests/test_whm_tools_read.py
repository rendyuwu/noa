"""WHM READ tools and the error boundary in front of the model (T19 — V8, V18, V19).

Two properties, and the second is the one with teeth.

**Nothing a tool emits carries credential material** (V2, V8). The rows in `support.servers`
always have an API token, an SSH password and a private key, so "no secrets leaked" is a
claim about a row that had some. The result also lands in LibreChat's MongoDB (V26), which
is why the assertion is against the ciphertext literals rather than against a plaintext
password nobody stores anyway.

**No raw exception reaches the caller** (V19). `sanitize_tool_errors` is exercised by making
the repository raise, because that is where a real failure comes from: a dropped connection,
a timeout, a bug. The two mappings V19 names are asserted by code, and a `NoaError` is
asserted to keep its own code — collapsing `ssh_host_key_mismatch` into
`tool_execution_failed` would strip the one string that says what to fix.
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
    """A `WHMServerReadRepository` that fails the way a real one can."""

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
    """DECISIONS §6.6: the model needs to know which servers exist."""
    fixture = build_tool_context(servers=[whm_server("beta"), whm_server("alpha")])

    result = await whm_list_servers(context=fixture.context)

    assert result["ok"] is True
    assert [server["name"] for server in result["servers"]] == ["alpha", "beta"]


async def test_an_empty_inventory_is_a_success_with_no_servers() -> None:
    """Zero servers is an answer, not a failure — nothing has gone wrong yet."""
    result = await whm_list_servers(context=build_tool_context().context)

    assert result["ok"] is True
    assert result["servers"] == []


# --- V2, V8: what leaves the process ---


async def test_the_server_list_carries_no_api_token_and_no_ssh_credential() -> None:
    """Every stored secret is absent from the serialized result.

    Asserted against the whole JSON rather than key by key: a future column added to
    `to_safe_dict` would slip past a key allowlist, and the thing that must hold is that
    the *values* never appear, wherever they are nested.
    """
    fixture = build_tool_context(servers=[whm_server("alpha")])

    result = await whm_list_servers(context=fixture.context)
    serialized = json.dumps(result, default=str)

    for secret in SECRETS:
        assert secret not in serialized
    # The presence booleans survive, so an admin can still see the server is configured.
    [server] = result["servers"]
    assert server["has_api_token"] is True
    assert server["has_ssh_password"] is True
    assert "api_token" not in server
    assert "ssh_password" not in server


async def test_inventory_is_read_on_every_call() -> None:
    """No memoization: a server added through `/admin` shows up on the next call, not the
    next process (V14 in spirit — the same no-cache rule the RBAC engine follows)."""
    fixture = build_tool_context(servers=[whm_server("alpha")])

    await whm_list_servers(context=fixture.context)
    await whm_list_servers(context=fixture.context)

    assert fixture.servers.reads == 2


# --- V19: the error boundary ---


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
    """V19's two named mappings, and the text of the original never travels.

    The exception messages carry an internal host and a port on purpose: those are exactly
    the strings that must not end up in a transcript (V8, V26).
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
    """The registered name is the one RBAC grants are written against (V10)."""
    assert TOOL_WHM_LIST_SERVERS in TOOL_CATALOG

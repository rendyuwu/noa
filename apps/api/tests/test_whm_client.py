"""WHM JSON API client.

Ported from `noa-old` branch `MCP` (`test_whm_client_normalization.py`) and extended.

The error-mapping cases are the point of the file. WHM answers a *failed* call with HTTP 200
and `metadata.result: 0`, so the one thing a client here must never do is read success off the
status line. Each condition gets its own `error_code`, because the tool layer branches on those
strings to decide whether an operator can fix it (`auth_failed`) or should retry (`timeout`).

`httpx.MockTransport` throughout — no network, and the request object is inspectable, which is
how the credential assertions work.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest

from core.integrations.whm.client import (
    DEFAULT_WHM_READ_TIMEOUT_SECONDS,
    WHM_CONNECT_TIMEOUT_SECONDS,
    WHMClient,
    build_whm_client_from_creds,
)
from support.secrets import build_cipher

BASE_URL = "https://whm.example.com:2087"


def _client(handler, **overrides) -> WHMClient:  # type: ignore[no-untyped-def]
    kwargs = {
        "base_url": BASE_URL,
        "api_username": "root",
        "api_token": "SECRET",
        "verify_ssl": True,
        "transport": httpx.MockTransport(handler),
    }
    kwargs.update(overrides)
    return WHMClient(**kwargs)  # type: ignore[arg-type]


def _ok(payload: dict[str, object]):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=payload, request=request)

    return handler


# --- Sanitized codes: every failure gets a stable, distinct code ---


async def test_client_maps_401_to_auth_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=401, text="unauthorized", request=request)

    result = await _client(handler).applist()

    assert result["ok"] is False
    assert result["error_code"] == "auth_failed"


async def test_client_maps_timeout_to_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    result = await _client(handler, read_timeout_seconds=0.01).applist()

    assert result["ok"] is False
    assert result["error_code"] == "timeout"


async def test_client_maps_transport_failure_to_request_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = await _client(handler).applist()

    assert result["ok"] is False
    assert result["error_code"] == "request_failed"


async def test_client_maps_server_error_to_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, text="boom", request=request)

    result = await _client(handler).applist()

    assert result["ok"] is False
    assert result["error_code"] == "http_error"


async def test_client_maps_non_json_to_invalid_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, text="not json", request=request)

    result = await _client(handler).applist()

    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"


@pytest.mark.parametrize("payload", [["a", "list"], {"no": "metadata"}, {"metadata": "text"}])
async def test_client_maps_unexpected_shapes_to_invalid_response(payload: object) -> None:
    result = await _client(_ok(payload)).applist()  # type: ignore[arg-type]

    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"


async def test_client_maps_metadata_result_zero_to_whm_api_error() -> None:
    """HTTP 200 with `result: 0` is a refusal. Reading success off the status line is the
    single mistake this client exists to prevent."""
    result = await _client(_ok({"metadata": {"result": 0, "reason": "Nope"}})).applist()

    assert result["ok"] is False
    assert result["error_code"] == "whm_api_error"
    assert "Nope" in str(result["message"])


async def test_whm_api_error_without_a_reason_still_carries_a_message() -> None:
    result = await _client(_ok({"metadata": {"result": 0, "reason": "   "}})).applist()

    assert result["error_code"] == "whm_api_error"
    assert result["message"] == "WHM API error"


# --- request shape ---


async def test_requests_carry_the_whm_auth_header_and_api_version() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(status_code=200, json={"metadata": {"result": 1}}, request=request)

    await _client(handler).applist()

    assert seen["auth"] == "whm root:SECRET"
    assert "/json-api/applist" in str(seen["url"])
    assert "api.version=1" in str(seen["url"])


async def test_list_accounts_unwraps_data_acct_and_drops_non_dict_rows() -> None:
    payload = {
        "metadata": {"result": 1},
        "data": {"acct": [{"user": "alpha"}, "junk", {"user": "beta"}]},
    }

    result = await _client(_ok(payload)).list_accounts()

    assert result["ok"] is True
    assert result["accounts"] == [{"user": "alpha"}, {"user": "beta"}]


async def test_list_accounts_returns_the_error_untouched_on_failure() -> None:
    result = await _client(_ok({"metadata": {"result": 0, "reason": "denied"}})).list_accounts()

    assert result["ok"] is False
    assert "accounts" not in result


async def test_suspend_sends_the_operator_typed_reason_as_whms_suspension_note() -> None:
    """The `reason` argument is the approval-time operator text — it is never a
    tool-schema parameter and the LLM never authors it. Here it only has to reach WHM."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(status_code=200, json={"metadata": {"result": 1}}, request=request)

    result = await _client(handler).suspend_account(username="alpha", reason="abuse report #12")

    assert result == {"ok": True, "message": "ok"}
    assert "suspendacct" in str(seen["url"])
    assert "user=alpha" in str(seen["url"])
    assert "abuse" in str(seen["url"])


async def test_unsuspend_calls_unsuspendacct() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(status_code=200, json={"metadata": {"result": 1}}, request=request)

    result = await _client(handler).unsuspend_account(username="alpha")

    assert result == {"ok": True, "message": "ok"}
    assert "unsuspendacct" in str(seen["url"])


# --- The never-implement boundary holds at the client, not only at the tool ---


@pytest.mark.parametrize("method", ["change_contact_email", "change_primary_domain"])
def test_never_implement_client_methods_are_absent(method: str) -> None:
    """Never port, never expose, never re-add. Keeping the client method would leave the
    capability one line from exposure; re-adding it is an owner decision, never an agent call."""
    assert not hasattr(WHMClient, method)


# --- One decrypt site, and it is the factory ---


async def test_build_whm_client_from_creds_decrypts_token_into_auth_header() -> None:
    cipher = build_cipher()
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(status_code=200, json={"metadata": {"result": 1}}, request=request)

    client = build_whm_client_from_creds(
        base_url=BASE_URL,
        api_username="reseller1",
        encrypted_token=cipher.encrypt_text("REAL_TOKEN"),
        verify_ssl=True,
        cipher=cipher,
        transport=httpx.MockTransport(handler),
    )
    result = await client.applist()

    assert result["ok"] is True
    assert seen["auth"] == "whm reseller1:REAL_TOKEN"


async def test_build_whm_client_from_creds_passes_through_an_unencrypted_column() -> None:
    """`maybe_decrypt_text`, never `decrypt_text`: a row written before encryption still works,
    which is what lets the host-key-validation work migrate the column in place."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(status_code=200, json={"metadata": {"result": 1}}, request=request)

    client = build_whm_client_from_creds(
        base_url=BASE_URL,
        api_username="root",
        encrypted_token="PLAINTEXT_TOKEN",
        verify_ssl=True,
        cipher=build_cipher(),
        transport=httpx.MockTransport(handler),
    )
    await client.applist()

    assert seen["auth"] == "whm root:PLAINTEXT_TOKEN"


# --- The deadline: split, configurable, and it reaches the socket ---
#
# The bug behind this group was measured, not imagined: `unsuspendacct` on the production server
# takes 52.91 s and the client waited 20, so NOA reported `timeout` for a suspension WHM had
# completed. Raising the number alone would have been the wrong fix — one scalar sets connect,
# read, write and pool alike, so a two-minute budget for a slow call is also two minutes of
# silence before an unreachable host is called unreachable.


def _recording_handler(seen: dict[str, object]) -> Callable[[httpx.Request], httpx.Response]:
    """A stub that answers `ok` and keeps the deadlines httpx resolved for the request.

    `request.extensions["timeout"]` is the value the transport is handed — the four numbers that
    would arm the real socket — so this reads what the connection pool would act on rather than
    re-reading the attribute the client was built from.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(status_code=200, json={"metadata": {"result": 1}}, request=request)

    return handler


async def test_the_deadline_reaching_the_socket_is_split_rather_than_one_number_for_all_four() -> (
    None
):
    """Connect, read, write and pool are four different questions.

    Only the read deadline covers WHM doing real work behind an open socket; the other three
    cover a network that is answering. Collapsing them into a scalar is the shape that makes an
    unreachable host hang for the whole of the slowest call's budget, and it is what this
    assertion separates: a scalar would put the read number in all four slots.
    """
    seen: dict[str, object] = {}

    await _client(_recording_handler(seen)).applist()

    assert seen["timeout"] == {
        "connect": 10.0,
        "read": DEFAULT_WHM_READ_TIMEOUT_SECONDS,
        "write": 30.0,
        "pool": 10.0,
    }
    # Stated separately from the equality above, because the equality would still hold if every
    # number were edited to agree: the property that matters is that a host which never answers
    # the connect is given up on long before a host which is merely thinking.
    assert WHM_CONNECT_TIMEOUT_SECONDS < DEFAULT_WHM_READ_TIMEOUT_SECONDS


async def test_a_configured_read_deadline_reaches_the_socket_and_moves_nothing_else() -> None:
    """The setting is threaded, not merely readable.

    A factory that accepted the value and dropped it would leave the deployment's configured
    deadline inert while `core.config` documents it, which is worse than having no setting: the
    number would be quotable and wrong.
    """
    seen: dict[str, object] = {}
    cipher = build_cipher()

    client = build_whm_client_from_creds(
        base_url=BASE_URL,
        api_username="root",
        encrypted_token=cipher.encrypt_text("API_TOKEN"),
        verify_ssl=True,
        cipher=cipher,
        read_timeout_seconds=7.5,
        transport=httpx.MockTransport(_recording_handler(seen)),
    )
    await client.applist()

    assert seen["timeout"] == {"connect": 10.0, "read": 7.5, "write": 30.0, "pool": 10.0}


async def test_a_per_call_read_deadline_replaces_only_the_read_deadline() -> None:
    """The shape a confirming read needs, ported from the Proxmox client rather than reinvented.

    A read taken to find out what a change did must not wait as long as the change itself — that
    doubles the worst case an operator sits through — but it is still asking about WHM's thinking
    time, so it has no business shortening the deadline for reaching the host at all.
    """
    seen: dict[str, object] = {}

    await _client(_recording_handler(seen), read_timeout_seconds=90.0).list_accounts(
        read_timeout_seconds=5.0
    )

    assert seen["timeout"] == {"connect": 10.0, "read": 5.0, "write": 30.0, "pool": 10.0}


@asynccontextmanager
async def _slow_whm(delay_seconds: float) -> AsyncIterator[str]:
    """A real socket on loopback that accepts, waits, then answers a valid WHM body.

    A `MockTransport` cannot stage this: it never touches a socket, so no deadline it is handed
    is ever enforced and a test built on one would pass with the timeout deleted. The stub goes
    silent *after* accepting the request, which is the shape the incident had — WHM took the call
    and worked on it — and which only the read deadline governs.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            await asyncio.sleep(delay_seconds)
            body = b'{"metadata": {"result": 1}, "data": {}}'
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"Connection: close\r\n\r\n" + body
            )
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError):
            # The caller that gave up hung up. That is the case under test, not a failure here.
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        await server.wait_closed()


def _live_client(base_url: str, read_timeout_seconds: float) -> WHMClient:
    return WHMClient(
        base_url=base_url,
        api_username="root",
        api_token="SECRET",
        verify_ssl=False,
        read_timeout_seconds=read_timeout_seconds,
    )


async def test_a_server_that_goes_silent_is_abandoned_at_the_read_deadline_and_not_before() -> None:
    """The pair, against one stub: the deadline is what decides, and it is the one configured.

    One half alone proves nothing. A timeout with no successful twin passes against a client that
    can never reach anything, and a success with no timing-out twin passes against a deadline that
    was never armed. The same server answers both — only the number the client was built with
    differs — so what separates them is the setting and nothing else.
    """
    async with _slow_whm(0.6) as base_url:
        abandoned = await _live_client(base_url, read_timeout_seconds=0.1).applist()

        assert abandoned["ok"] is False
        assert abandoned["error_code"] == "timeout"

        answered = await _live_client(base_url, read_timeout_seconds=5.0).applist()

        assert answered["ok"] is True

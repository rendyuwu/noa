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

import httpx
import pytest

from core.integrations.whm.client import WHMClient, build_whm_client_from_creds
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

    result = await _client(handler, timeout_seconds=0.01).applist()

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

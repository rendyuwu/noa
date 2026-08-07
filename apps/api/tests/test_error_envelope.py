"""The error envelope: `request_id` in every error body, `x-request-id` on the response
(T64 — V73).

V73's wording is "∀ error response", so the tests here are organised by *which code writes
the response*, not by which route was called — there are five such writers and each one is a
separate way for the envelope to go missing:

1. `noa_api.api.errors.handle_noa_error` — everything routes raise.
2. `handle_http_exception` — Starlette's routing failures (404, 405).
3. `handle_validation_error` — pydantic's 422.
4. `handle_unhandled_exception` — a bug, answered from `ServerErrorMiddleware`, which sits
   *outside* the request-id middleware and so cannot inherit its header.
5. `noa_api.mcp_request_auth.McpAuthErrorMiddleware._send_error` — raw ASGI on the mounted
   MCP app, which has no exception-handler graph at all.

Plus the middleware itself: what it does with an inbound `x-request-id`, and that it puts
one on a response that is not an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.auth.errors import AuthInvalidCredentialsError, AuthRateLimitedError
from noa_api.api.errors import (
    FALLBACK_HTTP_SHAPE,
    INTERNAL_SHAPE,
    VALIDATION_SHAPE,
    envelope,
    error_body,
    install_error_handling,
    redacted_validation_errors,
)
from noa_api.api.request_context import (
    MAX_REQUEST_ID_LENGTH,
    REQUEST_ID_HEADER,
    current_request_id,
    request_id_for,
    sanitize_request_id,
)
from support.auth import OPERATOR_EMAIL, FakeAuthRepository, auth_harness, build_settings
from support.mcp_identity import LIBRECHAT_USER, FakeMcpIdentityRepository
from support.mcp_mount import MCP_URL, headers_for, mounted_app, post_initialize

WRONG_PASSWORD = "not-the-password"

ENVELOPE_KEYS = {"error_code", "message", "request_id"}


def assert_enveloped(response, expected_status: int, expected_code: str) -> str:
    """Assert the V73 envelope and return the id both halves agreed on."""
    assert response.status_code == expected_status, response.text

    body = response.json()
    assert set(body) == ENVELOPE_KEYS, body
    assert body["error_code"] == expected_code

    request_id = body["request_id"]
    assert request_id
    assert response.headers[REQUEST_ID_HEADER] == request_id
    return str(request_id)


@contextmanager
def raising_app() -> Iterator[TestClient]:
    """An app whose one route raises, wired through the production seam.

    `raise_server_exceptions=False` because Starlette's `ServerErrorMiddleware` re-raises
    after calling the handler; with the default the test would see the `ValueError` instead
    of the response the handler wrote.
    """
    app = FastAPI()
    install_error_handling(app)

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise ValueError("secret internal cause")

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


# --- 1. NoaError, the path every route uses ---


def test_noa_error_carries_request_id_matching_header() -> None:
    """V73: a raised `NoaError` answers with the id in the body and on the response."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        response = harness.login(password=WRONG_PASSWORD)

    assert_enveloped(response, 401, AuthInvalidCredentialsError.error_code)


def test_retry_after_survives_the_envelope() -> None:
    """V9 not regressed by T64: a 429 still says when to come back, *and* carries the id."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)
    settings = build_settings(
        auth_login_rate_limit_max_attempts=2,
        auth_login_rate_limit_block_seconds=600,
    )

    with auth_harness(settings=settings, repository=repository) as harness:
        for _ in range(2):
            harness.login(password=WRONG_PASSWORD)
        blocked = harness.login(password=WRONG_PASSWORD)

    assert_enveloped(blocked, 429, AuthRateLimitedError.error_code)
    assert 0 < int(blocked.headers["retry-after"]) <= 600


# --- 2. Starlette's own routing failures ---


def test_unknown_path_carries_envelope() -> None:
    """V73: the 404 for an unrouted path is an error response like any other."""
    with auth_harness() as harness:
        response = harness.client.get("/no-such-endpoint")

    assert_enveloped(response, 404, "not_found")


def test_method_not_allowed_carries_envelope() -> None:
    """V73 again, on a status FastAPI produces without any NOA code running."""
    with auth_harness() as harness:
        response = harness.client.get("/auth/login")

    assert_enveloped(response, 405, "method_not_allowed")


# --- 3. Request validation ---


def test_validation_error_carries_envelope() -> None:
    """V73: a malformed body answers in the envelope, not pydantic's `detail` list."""
    with auth_harness() as harness:
        response = harness.client.post("/auth/login", json={"email": OPERATOR_EMAIL})

    assert_enveloped(response, 422, VALIDATION_SHAPE[0])


def test_validation_error_does_not_echo_the_submitted_password() -> None:
    """V8: pydantic puts the failing value in `input`; on a login that is the password.

    The whole reason this handler does not forward `exc.errors()` the way `noa-old` did.
    """
    with auth_harness() as harness:
        response = harness.client.post(
            "/auth/login",
            json={"email": OPERATOR_EMAIL, "password": [WRONG_PASSWORD]},
        )

    assert response.status_code == 422
    assert WRONG_PASSWORD not in response.text


def test_redacted_validation_errors_keep_only_location_and_type() -> None:
    """V8 at the log boundary: `input` and `ctx` never reach the log either."""
    redacted = redacted_validation_errors(
        [
            {
                "loc": ("body", "password"),
                "type": "string_type",
                "input": WRONG_PASSWORD,
                "ctx": {"error": ValueError("boom")},
            }
        ]
    )

    assert redacted == [{"loc": "body.password", "type": "string_type"}]


# --- 4. The unhandled exception, answered outside the middleware ---


def test_unhandled_exception_carries_envelope() -> None:
    """V73 on the one path `RequestContextMiddleware`'s `send` wrapper cannot reach."""
    with raising_app() as client:
        response = client.get("/boom")

    assert_enveloped(response, 500, INTERNAL_SHAPE[0])


def test_unhandled_exception_body_omits_the_cause() -> None:
    """V8: an unhandled exception is by definition text nobody vetted for a body."""
    with raising_app() as client:
        response = client.get("/boom")

    assert "secret internal cause" not in response.text
    assert "ValueError" not in response.text


# --- 5. The mounted MCP app's raw-ASGI refusal ---


def test_mcp_refusal_carries_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """V73 + V3: the named 401 the MCP middleware writes itself is enveloped too."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with mounted_app(monkeypatch, repository=repository) as fixture:
        response = post_initialize(fixture.client, headers_for(plaintext, None))

    assert_enveloped(response, 401, "librechat_user_header_missing")


def test_mcp_refusal_sends_one_request_id_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the middleware and `_send_error` set the header; the client must see one value.

    `MutableHeaders.__setitem__` replaces rather than appends, which is what makes the
    belt-and-braces safe — `httpx` would join duplicates with `, ` and the assertion below
    would catch it.
    """
    repository = FakeMcpIdentityRepository()

    with mounted_app(monkeypatch, repository=repository) as fixture:
        response = fixture.client.post(MCP_URL, json={})

    request_id = assert_enveloped(response, 401, "mcp_token_missing")
    assert "," not in request_id


# --- The middleware itself ---


def test_success_response_carries_request_id_header() -> None:
    """The header rides every response, so a 200 can be correlated with its log line too."""
    with auth_harness() as harness:
        response = harness.client.post("/auth/logout")

    assert response.status_code == 204
    assert response.headers[REQUEST_ID_HEADER]


def test_request_ids_differ_between_requests() -> None:
    """One id per request, not one per process."""
    with auth_harness() as harness:
        first = harness.client.get("/no-such-endpoint")
        second = harness.client.get("/no-such-endpoint")

    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


def test_inbound_request_id_is_echoed() -> None:
    """A proxy that already minted an id keeps it, so one trace spans both hops."""
    inbound = "0af7651916cd43dd8448eb211c80319c"

    with auth_harness() as harness:
        response = harness.client.get("/no-such-endpoint", headers={REQUEST_ID_HEADER: inbound})

    assert response.headers[REQUEST_ID_HEADER] == inbound
    assert response.json()["request_id"] == inbound


@pytest.mark.parametrize(
    ("inbound", "why"),
    [
        pytest.param("has space", "whitespace", id="whitespace"),
        pytest.param("a" * (MAX_REQUEST_ID_LENGTH + 1), "over length", id="over-length"),
        pytest.param("<script>", "illegal characters", id="illegal-characters"),
        pytest.param("   ", "blank", id="blank"),
    ],
)
def test_inbound_request_id_rejected_when_malformed(inbound: str, why: str) -> None:
    """An inbound id is echoed into a header and a log line, so it is not taken on trust.

    The deviation from `noa-old`, which echoes verbatim. A rejected value is replaced, not
    refused: the request still gets an id.
    """
    with auth_harness() as harness:
        response = harness.client.get("/no-such-endpoint", headers={REQUEST_ID_HEADER: inbound})

    served = response.headers[REQUEST_ID_HEADER]
    assert served
    assert served != inbound, why


@pytest.mark.parametrize(
    "value",
    ["0af7651916cd43dd8448eb211c80319c", "a-b_c.d:e", "  padded  "],
)
def test_sanitize_request_id_accepts_id_shaped_values(value: str) -> None:
    """UUIDs, digests, `traceparent` and `service:id` forms all survive."""
    assert sanitize_request_id(value) == value.strip()


@pytest.mark.parametrize("value", [None, "", "\n", "with\nnewline", "a" * 201])
def test_sanitize_request_id_rejects_the_rest(value: str | None) -> None:
    assert sanitize_request_id(value) is None


def test_request_id_for_is_idempotent() -> None:
    """Every reader goes through it, so they cannot disagree about this request's id."""
    scope: dict[str, object] = {"type": "http"}

    first = request_id_for(scope)

    assert request_id_for(scope) == first


def test_current_request_id_is_none_off_request() -> None:
    """Nothing to correlate outside a request, and a minted id would only look like one."""
    assert current_request_id() is None


# --- The shape itself ---


def test_error_body_without_request_id_is_unchanged() -> None:
    """The two-key V8 shape other test modules assert against still exists."""
    assert error_body(AuthInvalidCredentialsError()) == {
        "error_code": AuthInvalidCredentialsError.error_code,
        "message": AuthInvalidCredentialsError.message,
    }


def test_envelope_is_the_one_shape() -> None:
    """`error_body` and `error_response` both build through it, so there is one body."""
    assert envelope("some_code", "Some message.", "rid-1") == {
        "error_code": "some_code",
        "message": "Some message.",
        "request_id": "rid-1",
    }
    assert error_body(AuthInvalidCredentialsError(), request_id="rid-1") == envelope(
        AuthInvalidCredentialsError.error_code,
        AuthInvalidCredentialsError.message,
        "rid-1",
    )


def test_fallback_http_shape_used_for_unmapped_status() -> None:
    """A status with no entry still answers in the envelope, not Starlette's `detail`."""
    app = FastAPI()
    install_error_handling(app)

    @app.get("/teapot")
    async def teapot() -> dict[str, str]:
        raise StarletteHTTPException(status_code=418)

    with TestClient(app) as client:
        response = client.get("/teapot")

    assert_enveloped(response, 418, FALLBACK_HTTP_SHAPE[0])

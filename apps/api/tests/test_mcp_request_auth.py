"""The MCP request-path auth entry point.

Division of labour across the three MCP auth test files:

- `test_mcp_identity_resolver.py` — the gates and their order, over doubles.
- `test_mcp_token_verifier.py` — the fastmcp adapter: `AccessToken` shape, `None` on refusal.
- this file — how a *request* reaches the gates and how a refusal reaches the client: the
  header reads R5 warns about, the rate limiter, the named response body, and the
  identity a tool sees.

Two levels, and both are needed. Direct calls to `resolve_mcp_identity` under the request
contextvar assert which writes happened and which did not — the V4/V9 rules are about
*whether* a counter moved, which no status code can show. A mounted probe app asserts the
bytes on the wire, because the claim T12 actually makes is that a client can tell "you
forgot the header" from "this token is someone else's", and only a real response proves that.

Everything on the path is production code except the session, the two repositories and the
directory.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.dependencies import get_http_headers
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.middleware import Middleware
from starlette.types import Message
from structlog.testing import capture_logs

from core.auth.errors import LdapUnavailableError
from core.auth.mcp_auth_errors import (
    LibreChatUserHeaderMissingError,
    LibreChatUserMismatchError,
    McpAuthRateLimitedError,
    McpTokenExpiredError,
    McpTokenInvalidError,
    McpTokenMissingError,
    McpUserInactiveError,
    McpUserNotInDirectoryError,
)
from core.auth.mcp_auth_rate_limiter import SCOPE_MCP_CLIENT, SCOPE_MCP_TOKEN
from core.auth.mcp_identity import McpIdentity
from core.auth.mcp_token_service import generate_mcp_token, hash_mcp_token
from noa_api.mcp_auth import NoaTokenVerifier
from noa_api.mcp_request_auth import (
    LOG_DENIED,
    SCOPE_AUTH_ERROR,
    McpAuthErrorMiddleware,
    McpToolIdentity,
    current_mcp_identity,
    identity_claims,
    parse_bearer,
    read_librechat_user,
    read_presented_bearer,
    resolve_mcp_identity,
)
from support.auth import FakeRateLimitRepository
from support.mcp_identity import (
    EMAIL,
    LIBRECHAT_USER,
    OTHER_LIBRECHAT_USER,
    FakeDirectory,
    FakeMcpIdentityRepository,
    build_auth_context,
    http_request_context,
    stale_check,
)

SERVER_NAME = "NOA-request-auth-probe"
MCP_PATH = "/mcp"

MCP_ACCEPT = "application/json, text/event-stream"

# `initialize` at an era C23 admits (an older v1.x client's) — the smallest body that gets
# past auth. Which era negotiates is `test_mcp_mount.py`'s subject, not this file's.
INITIALIZE_BODY: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0"},
    },
}


def headers_for(token: str | None, librechat_user: str | None = LIBRECHAT_USER) -> dict[str, str]:
    """Request headers, with either one omittable."""
    built: dict[str, str] = {}
    if token is not None:
        built["Authorization"] = f"Bearer {token}"
    if librechat_user is not None:
        built["X-Noa-LibreChat-User"] = librechat_user
    return built


# --- R5: reading the two headers ---


@pytest.mark.parametrize(
    ("header_value", "expected"),
    [
        pytest.param(None, None, id="absent"),
        pytest.param("", None, id="empty"),
        pytest.param("Bearer tok", "tok", id="canonical"),
        pytest.param("bearer tok", "tok", id="lowercase-scheme"),
        pytest.param("BEARER tok", "tok", id="uppercase-scheme"),
        pytest.param("Bearer   tok  ", "tok", id="padded-credential"),
        pytest.param("Bearer ", None, id="blank-credential-is-absent"),
        pytest.param("Basic dXNlcjpwYXNz", None, id="wrong-scheme"),
        pytest.param("tok", None, id="no-scheme"),
    ],
)
def test_parse_bearer(header_value: str | None, expected: str | None) -> None:
    """A blank credential is a client that failed to interpolate `{{NOA_MCP_TOKEN}}`.

    That is `mcp_token_missing`, not `mcp_token_invalid` — the remedy differs, and hashing
    the empty string would produce a digest that matches no row and reads as a bad token.
    """
    assert parse_bearer(header_value) == expected


def test_the_bearer_needs_the_authorization_include() -> None:
    """R5, asserted rather than trusted: the default exclusion list drops `authorization`.

    Both halves matter. Without the first, nothing shows that the obvious spelling is wrong;
    without the second, nothing shows the workaround works. Together they mean a fastmcp bump
    that changes the exclusion list fails here instead of turning every request into
    `mcp_token_missing` in production.
    """
    token = generate_mcp_token()

    with http_request_context(headers_for(token)):
        assert get_http_headers().get("authorization") is None
        assert read_presented_bearer() == token


def test_the_librechat_header_is_read_case_insensitively() -> None:
    """R5: custom headers come back lowercased, which is why the constant is lowercase."""
    with http_request_context({"X-NOA-LIBRECHAT-USER": LIBRECHAT_USER}):
        assert read_librechat_user() == LIBRECHAT_USER


def test_the_header_reads_answer_absent_off_request() -> None:
    """`get_http_headers()` returns `{}` off-request; that must read as absence, not crash."""
    assert read_presented_bearer() is None
    assert read_librechat_user() is None


# --- resolve_mcp_identity: the accepted path ---


async def test_a_bound_token_with_its_header_resolves() -> None:
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    context = build_auth_context(repository=repository)

    with http_request_context(headers_for(plaintext)):
        identity = await resolve_mcp_identity(context)

    assert identity.user_id == stored.user_id
    assert identity.email == EMAIL
    assert identity.librechat_user_id == LIBRECHAT_USER


async def test_the_bearer_is_read_from_the_header_when_no_token_is_passed() -> None:
    """The path the middleware needs: nobody handed us a token, so read the header."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=None)
    context = build_auth_context(repository=repository)

    with http_request_context(headers_for(plaintext)):
        assert await resolve_mcp_identity(context) is not None


async def test_a_passed_token_wins_over_the_header() -> None:
    """`verify_token` is handed the token by the SDK, so it must not be re-parsed here.

    A second parse is a second answer to "which credential is this request presenting", and
    the two could disagree — this proves the argument is authoritative.
    """
    repository = FakeMcpIdentityRepository()
    passed, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    in_header, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    context = build_auth_context(repository=repository)

    with http_request_context(headers_for(in_header)):
        identity = await resolve_mcp_identity(context, presented_token=passed)

    assert identity.token_id == stored.token_id


# --- resolve_mcp_identity: what each refusal writes ---


async def test_a_request_with_no_bearer_touches_nothing() -> None:
    """No credential cannot be a guess, so it costs no query and no counter."""
    repository = FakeMcpIdentityRepository()
    repository.add_token(librechat_user_id=LIBRECHAT_USER)
    rate_limits = FakeRateLimitRepository()
    directory = FakeDirectory()
    context = build_auth_context(
        repository=repository, directory=directory, rate_limits=rate_limits
    )

    with http_request_context(headers_for(None)), pytest.raises(McpTokenMissingError):
        await resolve_mcp_identity(context)

    assert rate_limits.buckets == {}
    assert repository.commits == 0
    assert directory.call_count == 0


async def test_an_unknown_token_is_counted_and_committed() -> None:
    """V9: a counter that rolls back with the refusal is a counter that never counted."""
    repository = FakeMcpIdentityRepository()
    rate_limits = FakeRateLimitRepository()
    context = build_auth_context(repository=repository, rate_limits=rate_limits)
    unknown = generate_mcp_token()

    with http_request_context(headers_for(unknown)), pytest.raises(McpTokenInvalidError):
        await resolve_mcp_identity(context)

    assert set(rate_limits.buckets) == {
        (SCOPE_MCP_CLIENT, LIBRECHAT_USER),
        (SCOPE_MCP_TOKEN, hash_mcp_token(unknown)),
    }
    assert repository.commits == 1


async def test_a_binding_mismatch_is_counted() -> None:
    """The stolen-credential shape, so it is one of the two that count."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    rate_limits = FakeRateLimitRepository()
    context = build_auth_context(repository=repository, rate_limits=rate_limits)

    with (
        http_request_context(headers_for(plaintext, OTHER_LIBRECHAT_USER)),
        pytest.raises(LibreChatUserMismatchError),
    ):
        await resolve_mcp_identity(context)

    assert set(rate_limits.buckets) == {
        (SCOPE_MCP_CLIENT, OTHER_LIBRECHAT_USER),
        (SCOPE_MCP_TOKEN, hash_mcp_token(plaintext)),
    }


async def test_an_expired_token_is_not_counted() -> None:
    """A real credential that aged out. Counting it blocks an operator for being early."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(
        librechat_user_id=LIBRECHAT_USER,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    rate_limits = FakeRateLimitRepository()
    context = build_auth_context(repository=repository, rate_limits=rate_limits)

    with http_request_context(headers_for(plaintext)), pytest.raises(McpTokenExpiredError):
        await resolve_mcp_identity(context)

    assert rate_limits.buckets == {}


async def test_a_missing_header_is_not_counted() -> None:
    """A misconfigured client, not a guess."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    rate_limits = FakeRateLimitRepository()
    context = build_auth_context(repository=repository, rate_limits=rate_limits)

    with (
        http_request_context(headers_for(plaintext, None)),
        pytest.raises(LibreChatUserHeaderMissingError),
    ):
        await resolve_mcp_identity(context)

    assert rate_limits.buckets == {}


async def test_an_inactive_operator_is_refused_and_not_counted() -> None:
    """V1: `users.is_active` is re-read per request. Authenticated, then refused."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(is_active=False, librechat_user_id=LIBRECHAT_USER)
    rate_limits = FakeRateLimitRepository()
    context = build_auth_context(repository=repository, rate_limits=rate_limits)

    with http_request_context(headers_for(plaintext)), pytest.raises(McpUserInactiveError):
        await resolve_mcp_identity(context)

    assert rate_limits.buckets == {}


async def test_a_departed_operator_is_refused_and_not_counted() -> None:
    """V4: the cascade revoke already happened; a counter would punish them for it too."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(
        librechat_user_id=LIBRECHAT_USER, last_ldap_check_at=stale_check()
    )
    rate_limits = FakeRateLimitRepository()
    context = build_auth_context(
        repository=repository,
        directory=FakeDirectory(present=False),
        rate_limits=rate_limits,
    )

    with http_request_context(headers_for(plaintext)), pytest.raises(McpUserNotInDirectoryError):
        await resolve_mcp_identity(context)

    assert repository.tokens == {}
    assert rate_limits.buckets == {}


async def test_a_directory_outage_denies_without_counting_or_revoking() -> None:
    """V4 fails closed. T8's lesson: counting an outage locks every operator out."""
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(
        librechat_user_id=LIBRECHAT_USER, last_ldap_check_at=None
    )
    rate_limits = FakeRateLimitRepository()
    context = build_auth_context(
        repository=repository,
        directory=FakeDirectory(unavailable=True),
        rate_limits=rate_limits,
    )

    with http_request_context(headers_for(plaintext)), pytest.raises(LdapUnavailableError):
        await resolve_mcp_identity(context)

    assert list(repository.tokens) == [stored.token_id]
    assert rate_limits.buckets == {}


async def test_the_limiter_gate_runs_before_the_token_lookup() -> None:
    """V9: a blocked client is refused whatever it presents, including a valid token.

    `max_attempts=1`, so the first unknown token blocks. The second call presents a
    perfectly good credential and must still be refused, which is only true if the gate
    precedes the resolver.
    """
    repository = FakeMcpIdentityRepository()
    good, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    rate_limits = FakeRateLimitRepository()
    context = build_auth_context(repository=repository, rate_limits=rate_limits, max_attempts=1)

    with http_request_context(headers_for(generate_mcp_token())):
        with pytest.raises(McpTokenInvalidError):
            await resolve_mcp_identity(context)

    with http_request_context(headers_for(good)), pytest.raises(McpAuthRateLimitedError) as info:
        await resolve_mcp_identity(context)

    assert info.value.retry_after_seconds > 0


async def test_every_refusal_is_logged_once_with_its_code_and_no_credential() -> None:
    """V8: one denial event per refusal, carrying `error_code` and the internal `detail`.

    Logged here rather than in `verify_token`: this is where the cause is known, and a
    second line there would double every entry in the denial stream.
    """
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    context = build_auth_context(repository=repository)

    with (
        capture_logs() as captured,
        http_request_context(headers_for(plaintext, OTHER_LIBRECHAT_USER)),
        pytest.raises(LibreChatUserMismatchError),
    ):
        await resolve_mcp_identity(context)

    assert [entry["event"] for entry in captured] == [LOG_DENIED]
    assert captured[0]["error_code"] == "librechat_user_mismatch"

    rendered = str(captured)
    assert plaintext not in rendered
    assert stored.token_hash not in rendered
    # Pairing the two identifiers in a log line would be a map between NOA and LibreChat.
    assert OTHER_LIBRECHAT_USER not in rendered


# --- V5: one resolution path ---


async def test_verify_token_resolves_through_the_one_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V5: the verifier does not resolve, it delegates — and stashes what came back.

    Patched rather than inferred from behaviour because the invariant is about *which*
    function runs: a verifier that grew its own copy of the gates would pass every
    behavioural test while making "auth mechanism swap = one file" false.
    """
    seen: list[str | None] = []

    async def spy(_context: Any, *, presented_token: str | None = None) -> None:
        seen.append(presented_token)
        raise McpTokenInvalidError("spy refused")

    monkeypatch.setattr("noa_api.mcp_auth.resolve_mcp_identity", spy)
    verifier = NoaTokenVerifier(context=build_auth_context(repository=FakeMcpIdentityRepository()))

    with http_request_context(headers_for("presented")) as request:
        assert await verifier.verify_token("presented") is None
        stashed = request.scope[SCOPE_AUTH_ERROR]

    assert seen == ["presented"]
    assert isinstance(stashed, McpTokenInvalidError)


# --- V3/V73: the named response body ---


@contextmanager
def probe_app(
    repository: FakeMcpIdentityRepository,
    *,
    directory: FakeDirectory | None = None,
    rate_limits: FakeRateLimitRepository | None = None,
    max_attempts: int = 3,
) -> Iterator[TestClient]:
    """A throwaway FastMCP app behind the real verifier and the real middleware.

    T12 mounts nothing (T13 does), but "a refusal names its cause" is a property of the
    middleware stack, and this is the smallest thing that exercises it end to end.
    """
    verifier = NoaTokenVerifier(
        context=build_auth_context(
            repository=repository,
            directory=directory,
            rate_limits=rate_limits,
            max_attempts=max_attempts,
        )
    )
    server: FastMCP = FastMCP(SERVER_NAME, auth=verifier)
    app = server.http_app(
        path=MCP_PATH,
        middleware=[Middleware(McpAuthErrorMiddleware, mcp_path=MCP_PATH)],
    )
    with TestClient(app) as client:
        yield client


def post_initialize(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post(
        MCP_PATH,
        content=json.dumps(INITIALIZE_BODY),
        headers={"Content-Type": "application/json", "Accept": MCP_ACCEPT, **headers},
    )


def test_an_authenticated_request_passes_through_untouched() -> None:
    """The middleware must be invisible when auth succeeded (and `initialize` completes)."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with probe_app(repository) as client:
        response = post_initialize(client, headers_for(plaintext))

    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get("mcp-session-id")
    assert '"protocolVersion":"2025-06-18"' in response.text


def test_the_bare_sdk_401_is_replaced() -> None:
    """The body T12 exists to remove: `{"error": …, "error_description": …}`."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with probe_app(repository) as client:
        response = post_initialize(client, headers_for(plaintext, None))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    body = response.json()
    # `request_id` joined the envelope with T64; the SDK's two keys are still gone.
    assert set(body) == {"error_code", "message", "request_id"}
    assert "error_description" not in body


@pytest.mark.parametrize(
    ("case", "expected_code", "expected_status"),
    [
        pytest.param("no-bearer", "mcp_token_missing", 401, id="no-bearer"),
        pytest.param("not-bearer-scheme", "mcp_token_missing", 401, id="not-bearer-scheme"),
        pytest.param("unknown", "mcp_token_invalid", 401, id="unknown-token"),
        pytest.param("expired", "mcp_token_expired", 401, id="expired-token"),
        pytest.param("no-header", "librechat_user_header_missing", 401, id="header-absent"),
        pytest.param("mismatch", "librechat_user_mismatch", 401, id="binding-mismatch"),
        pytest.param("inactive", "mcp_user_inactive", 403, id="inactive-user"),
        pytest.param("departed", "mcp_user_not_in_directory", 403, id="departed-user"),
        pytest.param("ldap-down", "ldap_unavailable", 503, id="directory-outage"),
    ],
)
def test_each_denial_renders_its_own_error_code(
    case: str, expected_code: str, expected_status: int
) -> None:
    """V3: a client can tell these apart. The two spellings V3 fixes are in here by name."""
    repository = FakeMcpIdentityRepository()
    directory = FakeDirectory()
    headers: dict[str, str] = {}

    if case == "no-bearer":
        headers = headers_for(None)
    elif case == "not-bearer-scheme":
        headers = {"Authorization": "Basic dXNlcjpwYXNz", "X-Noa-LibreChat-User": LIBRECHAT_USER}
    elif case == "unknown":
        headers = headers_for(generate_mcp_token())
    elif case == "expired":
        plaintext, _ = repository.add_token(
            librechat_user_id=LIBRECHAT_USER,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        headers = headers_for(plaintext)
    elif case == "no-header":
        plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
        headers = headers_for(plaintext, None)
    elif case == "mismatch":
        plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
        headers = headers_for(plaintext, OTHER_LIBRECHAT_USER)
    elif case == "inactive":
        plaintext, _ = repository.add_token(is_active=False, librechat_user_id=LIBRECHAT_USER)
        headers = headers_for(plaintext)
    elif case == "departed":
        plaintext, _ = repository.add_token(
            librechat_user_id=LIBRECHAT_USER, last_ldap_check_at=None
        )
        directory = FakeDirectory(present=False)
        headers = headers_for(plaintext)
    else:
        plaintext, _ = repository.add_token(
            librechat_user_id=LIBRECHAT_USER, last_ldap_check_at=None
        )
        directory = FakeDirectory(unavailable=True)
        headers = headers_for(plaintext)

    with probe_app(repository, directory=directory) as client:
        response = post_initialize(client, headers)

    assert response.status_code == expected_status
    assert response.json()["error_code"] == expected_code


def test_a_401_carries_a_challenge() -> None:
    """RFC 6750 §3: the SDK's 401 sends one, so dropping it would be a regression."""
    repository = FakeMcpIdentityRepository()

    with probe_app(repository) as client:
        response = post_initialize(client, headers_for(generate_mcp_token()))

    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert 'error="invalid_token"' in challenge
    assert "mcp_token_invalid" in challenge


def test_a_blocked_client_gets_429_with_retry_after() -> None:
    """V9: a 429 without `Retry-After` leaves the client guessing when to retry."""
    repository = FakeMcpIdentityRepository()
    good, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with probe_app(repository, max_attempts=1) as client:
        first = post_initialize(client, headers_for(generate_mcp_token()))
        second = post_initialize(client, headers_for(good))

    assert first.status_code == status.HTTP_401_UNAUTHORIZED
    assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert second.json()["error_code"] == "mcp_auth_rate_limited"
    assert int(second.headers["retry-after"]) >= 1


def test_a_denial_body_carries_no_detail_and_no_token_material() -> None:
    """V8/V2: `detail` names token ids and directory state. Logs only, never a body."""
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with probe_app(repository) as client:
        response = post_initialize(client, headers_for(plaintext, OTHER_LIBRECHAT_USER))

    raw = response.text
    assert "detail" not in response.json()
    assert plaintext not in raw
    assert stored.token_hash not in raw
    assert str(stored.token_id) not in raw
    assert str(stored.user_id) not in raw


async def test_the_middleware_leaves_other_paths_alone() -> None:
    """A public route on the same sub-app keeps answering for itself.

    Driven as raw ASGI rather than through fastmcp, because the property is about the path
    guard, not about any route fastmcp happens to register.
    """
    seen: list[str] = []
    sent: list[Message] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:  # pragma: no cover - never awaited
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = McpAuthErrorMiddleware(inner, mcp_path=MCP_PATH)
    scope: dict[str, Any] = {"type": "http", "path": "/healthz", "headers": []}

    await middleware(scope, receive, send)

    assert seen == ["/healthz"]
    assert sent[0]["status"] == 204


@pytest.mark.parametrize(
    ("root_path", "path", "intercepted"),
    [
        pytest.param("", MCP_PATH, True, id="unmounted"),
        pytest.param(MCP_PATH, f"{MCP_PATH}{MCP_PATH}", True, id="mounted"),
        pytest.param(MCP_PATH, f"{MCP_PATH}/healthz", False, id="mounted-sibling"),
    ],
)
async def test_the_path_guard_is_mount_relative(
    root_path: str, path: str, intercepted: bool
) -> None:
    """T13 mounts this app; Starlette's Mount rewrites `root_path`, ⊥ `scope["path"]`.

    Guarding on the raw path passes every test that runs the sub-app standalone and then
    lets every refusal through once it is mounted — the SDK's bare `invalid_token` comes
    back with the same 401 status, so only the body says anything is wrong.
    """
    reached: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> Message:  # pragma: no cover - never awaited
        return {"type": "http.request"}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = McpAuthErrorMiddleware(inner, mcp_path=MCP_PATH)
    scope: dict[str, Any] = {
        "type": "http",
        "path": path,
        "root_path": root_path,
        "headers": [],
    }

    await middleware(scope, receive, send)

    assert (reached == []) is intercepted
    if intercepted:
        # No bearer on the scope, so the middleware infers and names the cause itself.
        assert sent[0]["status"] == status.HTTP_401_UNAUTHORIZED
        assert json.loads(sent[1]["body"])["error_code"] == "mcp_token_missing"


# --- R5: the identity a tool sees ---


async def test_a_tool_reads_its_caller_from_the_access_token() -> None:
    """R5: `get_access_token()`, never a second parse of the bearer.

    Round trip through the real verifier, so the claim keys the verifier writes and the ones
    `current_mcp_identity` reads are proved to be the same constants.
    """
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    verifier = NoaTokenVerifier(context=build_auth_context(repository=repository))

    with http_request_context(headers_for(plaintext)):
        access_token = await verifier.verify_token(plaintext)

    assert access_token is not None
    with http_request_context({}, user=AuthenticatedUser(access_token)):
        identity = current_mcp_identity()

    assert identity == McpToolIdentity(
        user_id=stored.user_id,
        email=EMAIL,
        token_id=stored.token_id,
        librechat_user_id=LIBRECHAT_USER,
    )


def test_no_access_token_in_context_is_refused_not_guessed() -> None:
    """A tool reached without an access token means the mount lost its verifier."""
    with http_request_context({}), pytest.raises(McpTokenMissingError):
        current_mcp_identity()


def test_claims_that_are_not_ours_are_refused() -> None:
    """An access token minted by something else must not serve a tool a half-known caller."""
    foreign = AccessToken(token="opaque", client_id="someone", scopes=[], claims={"sub": "x"})

    with (
        http_request_context({}, user=AuthenticatedUser(foreign)),
        pytest.raises(McpTokenInvalidError),
    ):
        current_mcp_identity()


def test_identity_claims_carry_no_token_material() -> None:
    """V2/V8: `claims` renders in tracebacks and structlog values."""
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    claims = identity_claims(
        McpIdentity(
            user_id=stored.user_id,
            email=EMAIL,
            display_name=None,
            token_id=stored.token_id,
            librechat_user_id=LIBRECHAT_USER,
            expires_at=None,
        )
    )

    rendered = json.dumps(claims)
    assert plaintext not in rendered
    assert hash_mcp_token(plaintext) not in rendered

"""`NoaTokenVerifier`, the fastmcp adapter (T11 — C5, C20, V2, V3, V4, V5, V8, R1-R5).

`test_mcp_identity_resolver.py` owns the gate rules. This file owns everything that is
about fastmcp rather than about policy: whether the header is readable where R4 says it
is, what an accepted identity looks like as an `AccessToken`, what a refusal returns, and
what a refusal writes to the log.

Two levels, and both are needed:

- **Direct calls** under `fastmcp.server.http.set_http_request` — the same contextvar
  `RequestContextMiddleware` sets in production, so `get_http_headers()` behaves exactly as
  it would on a live request.
- **A mounted app** (`mcp_probe_app`), which proves R4 end to end: that the middleware
  ordering really does make headers visible inside `verify_token`, in *our* wiring rather
  than in a source read of somebody else's. §R records that as live-verified once, on one
  machine, on one day; this keeps it verified on every run and on every fastmcp bump.

The verifier under test is the production class. Only the session, the repository and the
directory are doubles — the adapter's own code path is real.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.server.http import set_http_request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from structlog.testing import capture_logs

from core.auth.mcp_auth_errors import (
    LibreChatUserHeaderMissingError,
    LibreChatUserMismatchError,
    McpAuthError,
    McpTokenExpiredError,
    McpTokenInvalidError,
    McpTokenMissingError,
    McpUserInactiveError,
    McpUserNotInDirectoryError,
)
from core.auth.mcp_identity import LIBRECHAT_USER_HEADER
from core.auth.mcp_token_service import generate_mcp_token, hash_mcp_token
from noa_api.api.errors import FALLBACK_STATUS, STATUS_BY_ERROR, error_body, status_for
from noa_api.mcp_auth import LOG_DENIED, NoaTokenVerifier
from support.mcp_identity import (
    EMAIL,
    LIBRECHAT_USER,
    OTHER_LIBRECHAT_USER,
    FakeDirectory,
    FakeMcpIdentityRepository,
    stale_check,
)

SERVER_NAME = "NOA-probe"

MCP_PATH = "/mcp"

# `initialize` at the era C23 pins. The smallest body that gets past auth and produces a
# protocol answer rather than a parse error.
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

MCP_ACCEPT = "application/json, text/event-stream"


class FakeSession:
    """Stands in for `AsyncSession`. The repository double never touches it."""


@contextmanager
def http_context(headers: dict[str, str] | None = None) -> Iterator[None]:
    """Run a block inside the request contextvar `RequestContextMiddleware` sets.

    Built from a raw ASGI scope rather than a `TestClient` round trip so a unit test can
    name the exact headers on the wire, including their absence.
    """
    raw = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": MCP_PATH,
        "raw_path": MCP_PATH.encode(),
        "query_string": b"",
        "headers": raw,
        "client": ("127.0.0.1", 12345),
        "scheme": "http",
        "server": ("noa.internal", 80),
    }
    with set_http_request(Request(scope)):
        yield


def build_verifier(
    *,
    repository: FakeMcpIdentityRepository,
    directory: FakeDirectory | None = None,
    ldap_revalidate_seconds: int = 900,
) -> NoaTokenVerifier:
    """The production verifier over a session that does nothing and a fake repository."""

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        yield cast("AsyncSession", FakeSession())

    return NoaTokenVerifier(
        session_factory=session_factory,
        directory=directory or FakeDirectory(),
        ldap_revalidate_seconds=ldap_revalidate_seconds,
        repository_factory=lambda _session: repository,
    )


# --- R4/R5: the header is readable inside `verify_token` ---


async def test_header_is_read_from_the_request_context() -> None:
    """R4: `get_http_headers()` works inside `verify_token`, so TOFU can live there."""
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=None)
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        access_token = await verifier.verify_token(plaintext)

    assert access_token is not None
    assert stored.librechat_user_id == LIBRECHAT_USER


async def test_the_header_name_is_matched_case_insensitively() -> None:
    """R5: `get_http_headers()` lowercases keys, which is why the constant is lowercase."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    verifier = build_verifier(repository=repository)

    with http_context({"X-NOA-LIBRECHAT-USER": LIBRECHAT_USER}):
        assert await verifier.verify_token(plaintext) is not None

    assert LIBRECHAT_USER_HEADER == LIBRECHAT_USER_HEADER.lower()


async def test_a_request_without_the_header_is_rejected() -> None:
    """V3/C24: no header, no access — for a bound token and an unbound one alike."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=None)
    verifier = build_verifier(repository=repository)

    with http_context({}):
        assert await verifier.verify_token(plaintext) is None


async def test_no_http_context_at_all_is_rejected_not_crashed() -> None:
    """`get_http_headers()` answers `{}` off-request; that must read as "absent header"."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    verifier = build_verifier(repository=repository)

    assert await verifier.verify_token(plaintext) is None


# --- R2: the `AccessToken` an accepted identity becomes ---


async def test_access_token_carries_the_digest_not_the_plaintext() -> None:
    """V2 "⊥ logged" / V8: `AccessToken` renders in tracebacks and structlog values."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        access_token = await verifier.verify_token(plaintext)

    assert access_token is not None
    assert access_token.token == hash_mcp_token(plaintext)
    assert plaintext not in repr(access_token)
    assert plaintext not in access_token.model_dump_json()


async def test_access_token_identifies_the_user_not_the_token() -> None:
    """`streamable_http_manager` pins a session to `client_id`/`subject` (R8).

    Keying either on the token id would 404 a live session the moment an operator rotated
    their credential, so both carry the user id and two tokens for one operator agree.
    """
    repository = FakeMcpIdentityRepository()
    first, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    second, _ = repository.add_token(user_id=stored.user_id, librechat_user_id=LIBRECHAT_USER)
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        one = await verifier.verify_token(first)
        two = await verifier.verify_token(second)

    assert one is not None and two is not None
    assert one.client_id == two.client_id == str(stored.user_id)
    assert one.subject == two.subject == str(stored.user_id)


async def test_access_token_carries_no_scopes() -> None:
    """RBAC is not OAuth scope: a cached scope list is the stale catalog V74 distrusts."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        access_token = await verifier.verify_token(plaintext)

    assert access_token is not None
    assert access_token.scopes == []


async def test_access_token_claims_carry_the_identity_for_tools() -> None:
    """R5: tools read identity through `get_access_token()` / `TokenClaim`, not the header."""
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        access_token = await verifier.verify_token(plaintext)

    assert access_token is not None
    assert access_token.claims == {
        "user_id": str(stored.user_id),
        "email": EMAIL,
        "token_id": str(stored.token_id),
        "librechat_user_id": LIBRECHAT_USER,
    }


async def test_access_token_reports_expires_at_to_the_sdk() -> None:
    """R2: the SDK re-checks expiry itself, so passing it through is a free second gate."""
    repository = FakeMcpIdentityRepository()
    # Relative to the wall clock, not the fixture's `NOW`: `verify_token` takes no `now`
    # parameter (a live request has no business injecting one), so the token has to be
    # unexpired at the moment the test runs.
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER, expires_at=expires_at)
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        access_token = await verifier.verify_token(plaintext)

    assert access_token is not None
    assert access_token.expires_at == int(expires_at.timestamp())


async def test_a_non_expiring_token_reports_no_expiry() -> None:
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER, expires_at=None)
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        access_token = await verifier.verify_token(plaintext)

    assert access_token is not None
    assert access_token.expires_at is None


# --- R1: every denial is `None`, and only denials are ---


@pytest.mark.parametrize(
    ("presented", "header"),
    [
        pytest.param("", LIBRECHAT_USER, id="no-bearer"),
        pytest.param(None, LIBRECHAT_USER, id="unknown-token"),
        pytest.param("bound", None, id="header-absent"),
        pytest.param("bound", OTHER_LIBRECHAT_USER, id="binding-mismatch"),
    ],
)
async def test_every_refusal_returns_none(presented: str | None, header: str | None) -> None:
    repository = FakeMcpIdentityRepository()
    bound, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    token = bound if presented == "bound" else (presented or generate_mcp_token())
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": header} if header else {}):
        assert await verifier.verify_token(token) is None


async def test_an_inactive_user_is_refused() -> None:
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(is_active=False, librechat_user_id=LIBRECHAT_USER)
    verifier = build_verifier(repository=repository)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        assert await verifier.verify_token(plaintext) is None


async def test_a_directory_outage_is_refused_not_raised() -> None:
    """V4 fails closed, and `LdapUnavailableError` must not escape as a 500 either."""
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(
        librechat_user_id=LIBRECHAT_USER, last_ldap_check_at=None
    )
    verifier = build_verifier(repository=repository, directory=FakeDirectory(unavailable=True))

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        assert await verifier.verify_token(plaintext) is None

    # Denied, but nothing revoked — the distinction V4 rests on.
    assert list(repository.tokens) == [stored.token_id]


async def test_an_unexpected_failure_is_not_swallowed_as_a_denial() -> None:
    """A broken database must be a 500, not "your token is bad".

    Catching everything here would send an operator re-minting a credential that was fine
    while the real fault went unreported.
    """

    class BrokenRepository(FakeMcpIdentityRepository):
        async def get_by_token_hash(self, token_hash: str) -> None:
            raise RuntimeError("connection reset")

    verifier = build_verifier(repository=BrokenRepository())

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}), pytest.raises(RuntimeError):
        await verifier.verify_token(generate_mcp_token())


# --- V4: the verifier drives the real revalidation path ---


async def test_a_stale_token_revalidates_through_the_verifier() -> None:
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(
        librechat_user_id=LIBRECHAT_USER, last_ldap_check_at=stale_check()
    )
    directory = FakeDirectory()
    verifier = build_verifier(repository=repository, directory=directory)

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        assert await verifier.verify_token(plaintext) is not None

    assert directory.checked_emails == [EMAIL]


async def test_a_departed_operator_loses_every_token_through_the_verifier() -> None:
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(
        librechat_user_id=LIBRECHAT_USER, last_ldap_check_at=None
    )
    repository.add_token(user_id=stored.user_id, last_ldap_check_at=None)
    verifier = build_verifier(repository=repository, directory=FakeDirectory(present=False))

    with http_context({"X-Noa-LibreChat-User": LIBRECHAT_USER}):
        assert await verifier.verify_token(plaintext) is None

    assert repository.tokens == {}


# --- V5: one resolution path ---


async def test_the_verifier_holds_no_repository_of_its_own() -> None:
    """V5: the adapter carries a factory, not a query. Policy lives in the resolver.

    Asserted structurally rather than by behaviour because the invariant is about where
    code may live: an adapter that grew its own lookup would still pass every behavioural
    test above while making "auth mechanism swap = one file" false.
    """
    verifier = build_verifier(repository=FakeMcpIdentityRepository())

    assert not hasattr(verifier, "_repository")
    assert callable(verifier._repository_factory)


# --- V8: what a refusal writes down ---


async def test_denial_log_carries_no_token_material() -> None:
    """V2 "⊥ logged" / V8: the code and the internal detail, nothing else.

    `structlog.testing.capture_logs`, not `caplog`: structlog's default configuration
    writes through its own `PrintLogger`, so the stdlib capture sees an empty record list
    and an assertion built on it would pass while the credential sat in stdout.
    """
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=LIBRECHAT_USER)
    verifier = build_verifier(repository=repository)

    with capture_logs() as captured, http_context({"X-Noa-LibreChat-User": OTHER_LIBRECHAT_USER}):
        assert await verifier.verify_token(plaintext) is None

    assert [entry["event"] for entry in captured] == [LOG_DENIED]
    assert captured[0]["error_code"] == "librechat_user_mismatch"

    rendered = str(captured)
    assert plaintext not in rendered
    assert hash_mcp_token(plaintext) not in rendered
    assert stored.token_hash not in rendered
    # Pairing the two identifiers in a log line would be a map between NOA and LibreChat.
    assert OTHER_LIBRECHAT_USER not in rendered


# --- V73: every denial code has a status waiting for T12 ---


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (McpTokenMissingError(), status.HTTP_401_UNAUTHORIZED),
        (McpTokenInvalidError(), status.HTTP_401_UNAUTHORIZED),
        (McpTokenExpiredError(), status.HTTP_401_UNAUTHORIZED),
        (LibreChatUserHeaderMissingError(), status.HTTP_401_UNAUTHORIZED),
        (LibreChatUserMismatchError(), status.HTTP_401_UNAUTHORIZED),
        (McpUserInactiveError(), status.HTTP_403_FORBIDDEN),
        (McpUserNotInDirectoryError(), status.HTTP_403_FORBIDDEN),
        (McpAuthError(), status.HTTP_400_BAD_REQUEST),
    ],
)
def test_status_for_every_mcp_auth_error(error: McpAuthError, expected_status: int) -> None:
    assert status_for(error) == expected_status


def test_every_mcp_auth_error_is_mapped_explicitly() -> None:
    """No denial may reach the 503 fallback — that answer means "NOA is down"."""

    def subclasses(klass: type[McpAuthError]) -> set[type[McpAuthError]]:
        found = {klass}
        for child in klass.__subclasses__():
            found |= subclasses(child)
        return found

    for klass in subclasses(McpAuthError):
        assert status_for(klass.__new__(klass)) != FALLBACK_STATUS, f"{klass.__name__} → 503"


def test_the_v3_error_codes_are_spelled_as_the_spec_spells_them() -> None:
    """V3 fixes these two strings; a rename here is a silent contract break."""
    assert LibreChatUserHeaderMissingError.error_code == "librechat_user_header_missing"
    assert LibreChatUserMismatchError.error_code == "librechat_user_mismatch"


def test_mcp_auth_error_codes_are_unique() -> None:
    """Clients branch on `error_code`, so two classes sharing one string is a bug."""
    codes = [klass.error_code for klass in STATUS_BY_ERROR if issubclass(klass, McpAuthError)]
    assert len(codes) == len(set(codes))


def test_error_body_omits_internal_detail_for_denials() -> None:
    """V8: `detail` names token ids and directory state. Logs only."""
    error = LibreChatUserMismatchError("token `deadbeef` is bound to another LibreChat user")

    body = error_body(error)

    assert body == {"error_code": "librechat_user_mismatch", "message": error.message}
    assert "deadbeef" not in str(body)


# --- R4 end to end: a mounted server, over HTTP ---


@contextmanager
def mcp_probe_app(repository: FakeMcpIdentityRepository) -> Iterator[TestClient]:
    """A throwaway FastMCP app guarded by the real verifier.

    The `admin_probe_app` pattern from `support.rbac`: T11 ships no mount (that is T13),
    but "the header is visible inside `verify_token`" is a property of the middleware stack
    and this is the smallest thing that exercises it for real. Everything on the path is
    production code except the repository and the directory.
    """
    server: FastMCP = FastMCP(SERVER_NAME, auth=build_verifier(repository=repository))
    with TestClient(server.http_app(path="/")) as client:
        yield client


def _post_initialize(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post(
        "/",
        content=json.dumps(INITIALIZE_BODY),
        headers={"Content-Type": "application/json", "Accept": MCP_ACCEPT, **headers},
    )


def test_mounted_server_accepts_a_bound_token_with_its_header() -> None:
    """R4, live: header + bearer reach `verify_token` through the real middleware stack.

    A whole `initialize` completes, so this also re-checks two facts §R records from source
    reads: the negotiated era is C23's `2025-06-18`, and `Mcp-Session-Id` is minted by
    default (R8, `stateless_http=False`).
    """
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with mcp_probe_app(repository) as client:
        response = _post_initialize(
            client,
            {
                "Authorization": f"Bearer {plaintext}",
                "X-Noa-LibreChat-User": LIBRECHAT_USER,
            },
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get("mcp-session-id")
    assert '"protocolVersion":"2025-06-18"' in response.text


def test_mounted_server_binds_on_first_use() -> None:
    """C20 over the wire: the TOFU write really happens inside the request."""
    repository = FakeMcpIdentityRepository()
    plaintext, stored = repository.add_token(librechat_user_id=None)

    with mcp_probe_app(repository) as client:
        _post_initialize(
            client,
            {
                "Authorization": f"Bearer {plaintext}",
                "X-Noa-LibreChat-User": LIBRECHAT_USER,
            },
        )

    assert stored.librechat_user_id == LIBRECHAT_USER


def test_mounted_server_rejects_a_token_without_the_header() -> None:
    """V3/C24 over the wire. The 401 is the SDK's — T12 owns naming the cause in a body."""
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with mcp_probe_app(repository) as client:
        response = _post_initialize(client, {"Authorization": f"Bearer {plaintext}"})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_mounted_server_rejects_an_unknown_token() -> None:
    repository = FakeMcpIdentityRepository()
    repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with mcp_probe_app(repository) as client:
        response = _post_initialize(
            client,
            {
                "Authorization": f"Bearer {generate_mcp_token()}",
                "X-Noa-LibreChat-User": LIBRECHAT_USER,
            },
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_mounted_server_rejects_a_request_with_no_bearer() -> None:
    """V1: an unauthenticated `/mcp` is the thing T13's mount must never open."""
    repository = FakeMcpIdentityRepository()

    with mcp_probe_app(repository) as client:
        response = _post_initialize(client, {"X-Noa-LibreChat-User": LIBRECHAT_USER})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED

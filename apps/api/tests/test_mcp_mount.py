"""The MCP server mounted into the API (T13 — V1, V3, I.mcp, R3, R6, R7, R8).

`test_mcp_request_auth.py` proves the refusal machinery over a throwaway probe app. This
file is about the *real* app: that `create_app()` mounts an MCP endpoint, that the endpoint
is authenticated, that both lifespans run, and that the pins the mount depends on still
hold in the installed `fastmcp==3.4.5`.

Everything is production code except the auth context: `main.build_mcp_auth_context` is
patched to return T12's doubles, so the whole mount — verifier, middleware, session manager,
combined lifespan — runs without Postgres or a directory. The seam is one function, and it
is the same one `create_app` calls in production, so the wiring under test is not a copy.

The engine is never used here for the same reason, but it is still created: the app's own
lifespan builds and disposes it, which is half of what "both lifespans ran" means.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from fastmcp import FastMCP

from core.auth.ldap_service import LDAPService
from noa_api import main
from noa_api.api.deps import STATE_JWT_SERVICE, STATE_SESSION_FACTORY, STATE_SETTINGS
from noa_api.mcp_auth import NoaTokenVerifier
from noa_api.mcp_request_auth import McpAuthContext
from noa_api.mcp_server import MCP_MOUNT_PATH, SERVER_NAME
from support.auth import build_settings
from support.mcp_identity import (
    LIBRECHAT_USER,
    OTHER_LIBRECHAT_USER,
    FakeMcpIdentityRepository,
    build_auth_context,
)

# The endpoint as Starlette resolves it: the mount strips `/mcp`, and the sub-app's route is
# the root, so the canonical URL carries the trailing slash. `/mcp` itself is covered below.
MCP_URL = f"{MCP_MOUNT_PATH}/"

MCP_ACCEPT = "application/json, text/event-stream"

# `initialize` at the era C23 pins (R8). The smallest body that gets past auth.
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
    built: dict[str, str] = {"Content-Type": "application/json", "Accept": MCP_ACCEPT}
    if token is not None:
        built["Authorization"] = f"Bearer {token}"
    if librechat_user is not None:
        built["X-Noa-LibreChat-User"] = librechat_user
    return built


@dataclass
class MountFixture:
    """The mounted app plus what was handed to the verifier behind it."""

    client: TestClient
    app: Any
    repository: FakeMcpIdentityRepository
    context_kwargs: dict[str, Any]


@contextmanager
def mounted_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repository: FakeMcpIdentityRepository | None = None,
) -> Iterator[MountFixture]:
    """`create_app()` with T12's doubles behind the verifier.

    Two patches, both narrow: settings so the lifespan reads explicit values rather than a
    developer's `.env`, and `build_mcp_auth_context` so identity resolution runs over the
    in-memory repository. The kwargs that production would have passed are captured, so a
    test can assert what the real wiring hands the verifier (rather than that a doubled
    context reached it, which proves nothing).
    """
    resolved_repository = repository or FakeMcpIdentityRepository()
    captured: dict[str, Any] = {}

    def fake_context(**kwargs: Any) -> McpAuthContext:
        captured.update(kwargs)
        return build_auth_context(repository=resolved_repository)

    monkeypatch.setattr(main, "get_settings", build_settings)
    monkeypatch.setattr(main, "build_mcp_auth_context", fake_context)

    app = main.create_app()
    with TestClient(app) as client:
        yield MountFixture(
            client=client,
            app=app,
            repository=resolved_repository,
            context_kwargs=captured,
        )


def post_initialize(client: TestClient, headers: dict[str, str], url: str = MCP_URL) -> Any:
    return client.post(url, content=json.dumps(INITIALIZE_BODY), headers=headers)


# --- V1: the mount is authenticated ---


def test_the_mounted_endpoint_refuses_an_unauthenticated_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1: mounting an MCP server means mounting one that resolves a user first.

    This is the failure the mount could introduce and nothing else would catch: an app
    assembled without the verifier serves a working `/mcp` to anyone, and every other test
    in the suite still passes. The request never reaches a database — a missing bearer is
    refused before `resolve_mcp_identity` opens a session.
    """
    with mounted_app(monkeypatch) as fixture:
        response = post_initialize(fixture.client, headers_for(None))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error_code"] == "mcp_token_missing"
    # Not merely refused: nothing about the server leaked on the way.
    assert "protocolVersion" not in response.text


def test_initialize_completes_through_the_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    """I.mcp end to end: a real token, through the mount, to a completed handshake.

    Asserts the era C23 pins and the session header R8 says is minted by default — if a
    later fastmcp carried `stateless_http=True` by default, `Mcp-Session-Id` would vanish
    and this fails rather than LibreChat.
    """
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with mounted_app(monkeypatch, repository=repository) as fixture:
        response = post_initialize(fixture.client, headers_for(plaintext))

    assert response.status_code == status.HTTP_200_OK
    assert response.headers.get("mcp-session-id")
    assert '"protocolVersion":"2025-06-18"' in response.text
    assert f'"name":"{SERVER_NAME}"' in response.text


# --- V3: the named refusal survives the mount ---


@pytest.mark.parametrize(
    ("librechat_user", "expected_code"),
    [
        pytest.param(None, "librechat_user_header_missing", id="header-absent"),
        pytest.param(OTHER_LIBRECHAT_USER, "librechat_user_mismatch", id="binding-mismatch"),
    ],
)
def test_the_mount_carries_the_named_401_bodies(
    monkeypatch: pytest.MonkeyPatch, librechat_user: str | None, expected_code: str
) -> None:
    """V3: the two spellings only exist if `McpAuthErrorMiddleware` is on the mounted app.

    Drop the `middleware=` argument in `build_mcp_http_app` and both of these collapse to
    the SDK's bare `invalid_token` — a 401 either way, which is why the assertion is on the
    body rather than the status.
    """
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with mounted_app(monkeypatch, repository=repository) as fixture:
        response = post_initialize(fixture.client, headers_for(plaintext, librechat_user))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    body = response.json()
    assert body["error_code"] == expected_code
    assert "error_description" not in body


# --- T12 wiring ---


def test_the_verifier_is_wired_to_the_app_session_factory_and_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP path and the admin path read one world, not two configured alike.

    If the verifier resolved identities through a session factory of its own, an operator
    disabled through `/admin` could keep calling tools against a stale pool, and V1's
    per-request `is_active` re-read would be true of the wrong connection. The directory is
    the same rule for V4's revalidation.
    """
    with mounted_app(monkeypatch) as fixture:
        captured = fixture.context_kwargs
        state_session_factory = getattr(fixture.app.state, STATE_SESSION_FACTORY)
        state_settings = getattr(fixture.app.state, STATE_SETTINGS)

    assert captured["session_factory"] is state_session_factory
    assert isinstance(captured["directory"], LDAPService)
    assert captured["settings"] is state_settings


# --- R6: both lifespans ---


def test_the_app_lifespan_still_runs_beside_the_mcp_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R6: `combine_lifespans` means neither half is silently skipped.

    Both directions are asserted because both are real failure modes. Pass only the app's
    lifespan and the streamable-HTTP session manager is never started, so every MCP request
    dies at the transport while `/health` looks healthy. Pass only the MCP one and
    `app.state.jwt_service` is missing, so `/auth` breaks instead.
    """
    repository = FakeMcpIdentityRepository()
    plaintext, _ = repository.add_token(librechat_user_id=LIBRECHAT_USER)

    with mounted_app(monkeypatch, repository=repository) as fixture:
        health = fixture.client.get("/health")
        initialize = post_initialize(fixture.client, headers_for(plaintext))
        jwt_service = getattr(fixture.app.state, STATE_JWT_SERVICE, None)

    assert health.status_code == status.HTTP_200_OK
    assert initialize.status_code == status.HTTP_200_OK
    assert jwt_service is not None


def test_the_admin_surface_is_unchanged_by_the_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mounted sub-app must not shadow a route or appear in the schema.

    `/mcp` is a Starlette app with no NOA exception handlers and no OpenAPI presence, so its
    absence from `paths` is the correct answer, not a gap.
    """
    monkeypatch.setattr(main, "get_settings", build_settings)
    paths = set(main.create_app().openapi()["paths"])

    assert {"/auth/login", "/auth/logout", "/auth/me", "/health"} <= paths
    assert not any(path.startswith(MCP_MOUNT_PATH) for path in paths)


# --- I.mcp: where the endpoint answers ---


def test_the_endpoint_is_mounted_at_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """I.mcp puts the server at `/mcp`; the mount answers at `/mcp/` and redirects to it.

    Pinned rather than assumed. A client that does not follow redirects sees the 307, and
    T57's `librechat.yaml` should carry the trailing slash to skip the extra round trip —
    but 307 preserves method and body, so the bare path stays usable. This test is what
    would catch that turning into a 404 or a method-dropping 302.
    """
    with mounted_app(monkeypatch) as fixture:
        redirect = fixture.client.post(
            MCP_MOUNT_PATH,
            content=json.dumps(INITIALIZE_BODY),
            headers=headers_for(None),
            follow_redirects=False,
        )
        followed = post_initialize(fixture.client, headers_for(None), url=MCP_MOUNT_PATH)

    assert redirect.status_code == status.HTTP_307_TEMPORARY_REDIRECT
    assert redirect.headers["location"].endswith(MCP_URL)
    # Followed, it reaches the authenticated endpoint rather than a 404.
    assert followed.status_code == status.HTTP_401_UNAUTHORIZED
    assert followed.json()["error_code"] == "mcp_token_missing"


# --- R7: the v3 constructor pins the mount depends on ---


def test_v3_removed_ctor_kwargs_still_raise() -> None:
    """R7: `stateless_http` and friends moved to `http_app()` in fastmcp v3.

    Asserted because the failure is quiet in the direction that matters: someone "fixing" a
    session problem by passing `stateless_http=True` to `FastMCP()` gets a `TypeError` here
    instead of discovering at runtime that sessions were never configurable there. It also
    pins that we are on v3 semantics at all — on 2.x these are accepted and `Mcp-Session-Id`
    would depend on a constructor argument nobody reads.
    """
    for kwarg in ("stateless_http", "json_response", "log_level"):
        with pytest.raises(TypeError):
            FastMCP(SERVER_NAME, **{kwarg: True})


def test_auth_attached_after_http_app_authenticates_nothing() -> None:
    """R3: `http_app()` snapshots `auth`, which is why `build_mcp_server` takes it.

    The trap, demonstrated against the installed fastmcp rather than trusted from the source
    read: build the ASGI app first, set `server.auth` afterwards, and the handshake still
    completes for a caller presenting no credential at all. A mount assembled in that order
    would breach V1 while every request looked normal.
    """
    server: FastMCP = FastMCP(SERVER_NAME)
    late_app = server.http_app(path="/")
    context = build_auth_context(repository=FakeMcpIdentityRepository())
    server.auth = NoaTokenVerifier(context=context)

    with TestClient(late_app) as client:
        response = post_initialize(client, headers_for(None), url="/")

    assert response.status_code == status.HTTP_200_OK

"""Driving the real mounted MCP endpoint from a test (T13, T19).

`create_app()` with T12's doubles behind the verifier and T19's doubles behind the tools.
Two patches, both narrow: settings, so the lifespan reads explicit values rather than a
developer's `.env`, and the two context builders, so identity resolution and the tool path
run over in-memory repositories. Everything else — the verifier, the auth middleware, the
RBAC middleware, the session manager, the combined lifespan, the tool registry — is
production code. The seams are the same two functions `create_app` calls in production, so
the wiring under test is not a copy of it.

Shared between `test_mcp_mount.py` (the mount itself) and `test_mcp_tool_rbac.py` (V1 over
that mount) rather than duplicated, because the harness *is* the thing both are asserting
against and two copies would drift (V66).

`McpSession` exists because a Streamable HTTP tool call is not one request: `initialize`,
then `notifications/initialized`, then the call, with `Mcp-Session-Id` carried between them
(R8). Replies come back as SSE by default, so `json_rpc_payload` unwraps the `data:` frame —
asserting on raw text is fine for a handshake but not for a tool result.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from noa_api import main
from noa_api.mcp_request_auth import McpAuthContext
from noa_api.mcp_server import MCP_MOUNT_PATH
from noa_api.mcp_tools.context import McpToolContext
from support.auth import build_settings
from support.mcp_identity import LIBRECHAT_USER, FakeMcpIdentityRepository, build_auth_context
from support.servers import build_tool_context

# The endpoint as Starlette resolves it: the mount strips `/mcp`, and the sub-app's route is
# the root, so the canonical URL carries the trailing slash.
MCP_URL = f"{MCP_MOUNT_PATH}/"

MCP_ACCEPT = "application/json, text/event-stream"

SESSION_HEADER = "mcp-session-id"

# The era C23 pins (R8).
PROTOCOL_VERSION = "2025-06-18"

# `initialize` at that era. The smallest body that gets past auth.
INITIALIZE_BODY: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": PROTOCOL_VERSION,
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


def json_rpc_payload(response: httpx.Response) -> dict[str, Any]:
    """The JSON-RPC object out of a reply, SSE-framed or not.

    Streamable HTTP answers a request with `text/event-stream` unless the server was built
    with `json_response=True`, so the body is `event: message\\n\\ndata: {...}`. Parsed here
    rather than in each test: a test that string-matched the frame would pass on a reply
    whose `result` said something else entirely.
    """
    body = response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        frames = [
            line[len("data:") :].strip() for line in body.splitlines() if line.startswith("data:")
        ]
        assert frames, f"no SSE data frame in reply: {body!r}"
        body = frames[-1]
    parsed: dict[str, Any] = json.loads(body)
    return parsed


@dataclass
class MountFixture:
    """The mounted app plus what was handed to the verifier behind it."""

    client: TestClient
    app: Any
    repository: FakeMcpIdentityRepository
    auth_context_kwargs: dict[str, Any]
    tool_context_kwargs: dict[str, Any]


@contextmanager
def mounted_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    repository: FakeMcpIdentityRepository | None = None,
    tool_context: McpToolContext | None = None,
) -> Iterator[MountFixture]:
    """`create_app()` with T12's and T19's doubles behind it.

    The kwargs production would have passed to each context builder are captured, so a test
    can assert what the real wiring hands the verifier and the tool path — rather than that
    a doubled context reached them, which proves nothing.
    """
    resolved_repository = repository or FakeMcpIdentityRepository()
    resolved_tools = tool_context or build_tool_context().context
    auth_kwargs: dict[str, Any] = {}
    tool_kwargs: dict[str, Any] = {}

    def fake_auth_context(**kwargs: Any) -> McpAuthContext:
        auth_kwargs.update(kwargs)
        return build_auth_context(repository=resolved_repository)

    def fake_tool_context(**kwargs: Any) -> McpToolContext:
        tool_kwargs.update(kwargs)
        return resolved_tools

    monkeypatch.setattr(main, "get_settings", build_settings)
    monkeypatch.setattr(main, "build_mcp_auth_context", fake_auth_context)
    monkeypatch.setattr(main, "build_mcp_tool_context", fake_tool_context)

    app = main.create_app()
    with TestClient(app) as client:
        yield MountFixture(
            client=client,
            app=app,
            repository=resolved_repository,
            auth_context_kwargs=auth_kwargs,
            tool_context_kwargs=tool_kwargs,
        )


def post_initialize(client: TestClient, headers: dict[str, str], url: str = MCP_URL) -> Any:
    """One `initialize` POST, unwrapped — the handshake half of the harness."""
    return client.post(url, content=json.dumps(INITIALIZE_BODY), headers=headers)


@dataclass
class McpSession:
    """An initialized MCP session over the mounted endpoint.

    Holds the negotiated `Mcp-Session-Id` and stamps it on every later request, which is
    what the era C23 pins requires (R8) — without it the transport answers 400 and a test
    would be asserting against a protocol error rather than a permission decision.
    """

    client: TestClient
    headers: dict[str, str]
    next_id: int = field(default=2)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One JSON-RPC request; returns the parsed reply object."""
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            body["params"] = params
        self.next_id += 1

        response = self.client.post(MCP_URL, content=json.dumps(body), headers=self.headers)
        assert response.status_code == 200, response.text
        return json_rpc_payload(response)

    def result(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """The `result` of a JSON-RPC request, asserting it was not an error."""
        reply = self.request(method, params)
        assert "error" not in reply, reply["error"]
        payload: dict[str, Any] = reply["result"]
        return payload

    def tool_names(self) -> list[str]:
        """The names `tools/list` returns for this caller (V1)."""
        return [tool["name"] for tool in self.result("tools/list")["tools"]]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """`tools/call`, returning the raw `CallToolResult` (so `isError` is visible)."""
        return self.result("tools/call", {"name": name, "arguments": arguments or {}})


def open_session(
    client: TestClient, token: str, *, librechat_user: str | None = LIBRECHAT_USER
) -> McpSession:
    """Complete the handshake and return a session ready to make calls."""
    headers = headers_for(token, librechat_user)
    initialize = post_initialize(client, headers)
    assert initialize.status_code == 200, initialize.text

    session_id = initialize.headers.get(SESSION_HEADER)
    assert session_id, "server minted no `Mcp-Session-Id` (R8)"
    headers[SESSION_HEADER] = session_id

    # The spec's post-handshake notification. Skipping it leaves the session uninitialized
    # and every later request answers 400.
    notified = client.post(
        MCP_URL,
        content=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        headers=headers,
    )
    assert notified.status_code in (200, 202), notified.text

    return McpSession(client=client, headers=headers)


__all__ = [
    "INITIALIZE_BODY",
    "MCP_ACCEPT",
    "MCP_URL",
    "PROTOCOL_VERSION",
    "SESSION_HEADER",
    "McpSession",
    "MountFixture",
    "headers_for",
    "json_rpc_payload",
    "mounted_app",
    "open_session",
    "post_initialize",
]

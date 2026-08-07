"""Doubles for the Proxmox integration layer (T17).

Both Proxmox client test files build the same thing — a `ProxmoxClient` whose transport is an
`httpx.MockTransport` — so the builder lives here (V66). No network and no live Proxmox: the
layer under test is request composition and failure classification, neither of which needs a
socket, and `MockTransport` hands the request object back for the credential assertions.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from core.integrations.proxmox.client import ProxmoxClient

BASE_URL = "https://proxmox.example.com:8006"
API_TOKEN_ID = "root@pam!token"
API_TOKEN_SECRET = "SECRET"

Handler = Callable[[httpx.Request], httpx.Response]


def build_client(handler: Handler, **overrides: object) -> ProxmoxClient:
    """A client answered entirely by `handler`. Overrides go straight to the constructor."""
    kwargs: dict[str, object] = {
        "base_url": BASE_URL,
        "api_token_id": API_TOKEN_ID,
        "api_token_secret": API_TOKEN_SECRET,
        "verify_ssl": True,
        "transport": httpx.MockTransport(handler),
    }
    kwargs.update(overrides)
    return ProxmoxClient(**kwargs)  # type: ignore[arg-type]


def json_handler(payload: object, *, status_code: int = 200) -> Handler:
    """Answer every request with one JSON body — for the tests that only read the result."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=status_code, json=payload, request=request)

    return handler


def data_handler(data: object) -> Handler:
    """Answer every request with a Proxmox success envelope wrapping `data`."""
    return json_handler({"data": data})


__all__ = [
    "API_TOKEN_ID",
    "API_TOKEN_SECRET",
    "BASE_URL",
    "Handler",
    "build_client",
    "data_handler",
    "json_handler",
]

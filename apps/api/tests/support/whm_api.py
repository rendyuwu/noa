"""A WHM `/json-api/` endpoint, doubled at the socket (T21).

`httpx.MockTransport` rather than a fake `WHMClient`, for the reason `build_whm_client_from_creds`
takes a `transport` at all: the client's job is normalising WHM's answers — HTTP 200 with
`metadata.result: 0` is a *failure* — and a doubled client would let a tool test pass against
error shapes the real one never produces.

It also keeps the credential path live. The request is captured, so a test can assert the
`Authorization` header carries the *decrypted* API token: the cipher, `build_whm_client` and
the one decrypt site all run, and the only thing replaced is the network.

Separate from `support/servers.py` (rows and repositories) and separate from `support/whm.py`,
whose surface `test_support_layout.py` pins to `FakeWHMServer` alone (T72) — this is the WHM
*API*, not a WHM row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

# What `listaccts` wraps its rows in: `{"data": {"acct": [...]}}` under a success `metadata`.
LISTACCTS_PATH = "/json-api/listaccts"


def whm_account(
    user: str,
    *,
    domain: str | None = None,
    suspended: object = 0,
    **extra: Any,
) -> dict[str, Any]:
    """One `listaccts` row, as WHM sends it.

    `suspended` defaults to the integer `0` because that is what WHM sends — not `False`. The
    normaliser's job is turning that into a bool, so a fixture that pre-normalised it would
    hide the case.

    `extra` carries the fields NOA drops (`ip`, `plan`, `diskused`, …) so a test can assert
    they do not reach the result.
    """
    row: dict[str, Any] = {"user": user, "suspended": suspended}
    row["domain"] = domain if domain is not None else f"{user}.example.com"
    row.update(extra)
    return row


def listaccts_body(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    """A successful `listaccts` payload."""
    return {"metadata": {"result": 1, "reason": "OK"}, "data": {"acct": accounts}}


def whm_api_failure_body(reason: str) -> dict[str, Any]:
    """WHM's own refusal: HTTP 200, `result: 0`, the cause in `reason`."""
    return {"metadata": {"result": 0, "reason": reason}}


@dataclass
class FakeWHMApi:
    """A `MockTransport` answering `/json-api/*`, with every request recorded."""

    body: dict[str, Any]
    status_code: int = 200
    requests: list[httpx.Request] = field(default_factory=list)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    @property
    def authorization_headers(self) -> list[str]:
        """The `Authorization` header of each captured request — `whm <user>:<token>`."""
        return [request.headers.get("Authorization", "") for request in self.requests]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.body)


def whm_api_listing(accounts: list[dict[str, Any]]) -> FakeWHMApi:
    """A WHM endpoint whose `listaccts` returns `accounts`."""
    return FakeWHMApi(body=listaccts_body(accounts))


__all__ = [
    "LISTACCTS_PATH",
    "FakeWHMApi",
    "listaccts_body",
    "whm_account",
    "whm_api_failure_body",
    "whm_api_listing",
]

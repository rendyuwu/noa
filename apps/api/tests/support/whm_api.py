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

# The account CHANGE endpoints (T22, T23). Named here beside the read so a test asserting "the
# gate ran nothing" can count requests to *this* path rather than to WHM in general.
SUSPENDACCT_PATH = "/json-api/suspendacct"
UNSUSPENDACCT_PATH = "/json-api/unsuspendacct"

# The validate probe (§V111). `myprivs` reports what the token may do, which is why it replaced
# `applist` — a test asserting "validate asked about capability" counts requests to this path.
MYPRIVS_PATH = "/json-api/myprivs"


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


def myprivs_body(privileges: dict[str, Any]) -> dict[str, Any]:
    """A successful `myprivs` payload — `data.privileges` is a list holding ONE object.

    The list wrapper is WHM's, measured on a live host (§R.33), and it is the whole reason the
    client has an unwrap: a fixture that handed the object over bare would let a client that
    forgot the `[0]` pass here and fail against WHM.
    """
    return {"metadata": {"result": 1, "reason": "OK"}, "data": {"privileges": [privileges]}}


def reseller_privileges(**overrides: Any) -> dict[str, Any]:
    """The measured reseller ACL set (§R.33): the suspend flag granted, and nothing else that
    writes. Values are WHM's own spellings — granted is the *string* `"1"` from a reseller
    token where root sends the integer `1`, and not-granted is `0` or `""` depending on the
    key. Both spellings are in here on purpose (§V113)."""
    privileges: dict[str, Any] = {
        "basic-whm-functions": "1",
        "list-accts": "1",
        "suspend-acct": "1",
        "all": 0,
        "create-acct": "",
        "kill-acct": "",
        "passwd": "",
        "create-user-session": "",
        "manage-api-tokens": "",
    }
    privileges.update(overrides)
    return privileges


def whm_api_failure_body(reason: str) -> dict[str, Any]:
    """WHM's own refusal: HTTP 200, `result: 0`, the cause in `reason`."""
    return {"metadata": {"result": 0, "reason": reason}}


def whm_api_success_body() -> dict[str, Any]:
    """A bare success — what `suspendacct` answers, with no `data` of its own."""
    return {"metadata": {"result": 1, "reason": "OK"}}


@dataclass
class FakeWHMApi:
    """A `MockTransport` answering `/json-api/*`, with every request recorded.

    `body` is the default answer for any path. `scripted` overrides it per path with a queue,
    which is what a CHANGE workflow needs (T22): one endpoint answers *differently* on the
    preflight read and the postflight read, and a single body cannot express "the account was
    live, then it was suspended". The last entry of a queue repeats once the queue runs dry, so
    a test only scripts the answers it is asserting on.
    """

    body: dict[str, Any]
    status_code: int = 200
    requests: list[httpx.Request] = field(default_factory=list)
    scripted: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    status_codes: dict[str, int] = field(default_factory=dict)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    @property
    def authorization_headers(self) -> list[str]:
        """The `Authorization` header of each captured request — `whm <user>:<token>`."""
        return [request.headers.get("Authorization", "") for request in self.requests]

    def requests_to(self, path: str) -> list[httpx.Request]:
        """Every captured request whose path is `path` — the counter a "nothing ran" assertion
        needs, because a CHANGE call still reads `listaccts` for its preflight."""
        return [request for request in self.requests if request.url.path == path]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        queue = self.scripted.get(path)
        if queue:
            # Keep the last scripted answer in place: a postflight that reads twice for its own
            # reasons should not fall back to an unrelated default.
            body = queue.pop(0) if len(queue) > 1 else queue[0]
        else:
            body = self.body
        return httpx.Response(self.status_codes.get(path, self.status_code), json=body)


def whm_api_listing(accounts: list[dict[str, Any]]) -> FakeWHMApi:
    """A WHM endpoint whose `listaccts` returns `accounts`."""
    return FakeWHMApi(body=listaccts_body(accounts))


__all__ = [
    "LISTACCTS_PATH",
    "MYPRIVS_PATH",
    "SUSPENDACCT_PATH",
    "UNSUSPENDACCT_PATH",
    "FakeWHMApi",
    "listaccts_body",
    "myprivs_body",
    "reseller_privileges",
    "whm_account",
    "whm_api_failure_body",
    "whm_api_listing",
    "whm_api_success_body",
]

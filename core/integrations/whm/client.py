"""WHM JSON API client (T16, C7, C22, V69).

Copied from `noa-old` branch `MCP` (`whm/integrations/client.py`), with the method surface
scoped to what this repo's 14 tools and admin routes actually reach for (see "What is not
here").

The behaviour worth copying is the **error normalisation**, not the HTTP. WHM answers a failed
call with HTTP 200 and `metadata.result: 0`, so a naive `raise_for_status()` reads a refusal as
a success. Every call funnels through `_get_json_api`, which turns each distinct failure into
one `{"ok": False, "error_code": …, "message": …}` shape:

| condition | `error_code` |
|---|---|
| connect/read deadline | `timeout` |
| DNS, refused, reset, TLS | `request_failed` |
| HTTP 401/403 | `auth_failed` |
| any other HTTP ≥ 400 | `http_error` |
| body is not JSON, not an object, or has no `metadata` | `invalid_response` |
| `metadata.result != 1` | `whm_api_error` (carries WHM's own `reason`) |

Dict-returning, not exception-raising: these codes are the tool layer's material for a
structured result, and V19's sanitisation boundary is one layer up. The strings are stable —
tools and tests branch on them.

**Credentials.** `WHMClient` takes a plaintext token; `build_whm_client_from_creds` is the one
place ciphertext becomes plaintext. `noa-old` reached for a module-level `maybe_decrypt_text`
there; T15 deleted that wrapper along with the settings singleton it hid, so the `SecretCipher`
arrives as an argument (T16 deviation (b)) and `build_runtime` builds the single instance on
`AppRuntime` (T21).

**A client per call, deliberately.** `httpx.AsyncClient` is opened and closed inside each
request rather than held on the instance. Verbatim from `noa-old`: `verify_ssl` is per-server
and the call rate is human-paced (one operator, one approval), so a pooled connection buys
nothing and a long-lived client pinned to a server row that an admin has since edited costs
something.

**What is not here**, and why — each was in the source:

- `change_contact_email`, `change_primary_domain` — C22 never-implement boundary. Their tools
  (`whm_change_contact_email`, `whm_change_primary_domain`) are a management policy decision,
  ⊥ port, ⊥ re-add. Porting the client methods would leave the capability one line from
  exposure.
- `get_domain_owner` — only caller on `MCP` was the primary-domain preflight, which C22 drops
  with its tool.
- `list_zones` — zero callers on `MCP`.
- `csf_grep`, `csf_request_action` — the `cgi/addon_csf.cgi` HTTP path, also uncalled on `MCP`.
  I.ext puts WHM's firewall access on SSH (`core.integrations.whm.csf_cli`), which is what the
  firewall tools use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from core.secrets.crypto import SecretCipher

PrimitiveQueryValue = str | int | float | bool
QueryValue = PrimitiveQueryValue | Sequence[PrimitiveQueryValue]

# The two ACL names a WHM row's API token is read for. `suspend-acct` is ONE ACL covering BOTH
# directions — cPanel's chart spells it "The user can suspend and unsuspend cPanel accounts" —
# so there is no unsuspend name to check and no second gate that could fall out of step with
# this one (§V112). `list-accts` is what a row an account listing is read from needs.
WHM_ACL_SUSPEND_ACCOUNT = "suspend-acct"
WHM_ACL_LIST_ACCOUNTS = "list-accts"

# The granted spellings, and the whole of them (§V113). WHM's answer for the *same* function on
# the *same* host differs by credential — root sends the integer `1` where a reseller sends the
# string `"1"` (measured, §R.33) — so the value's type carries no information and only the value
# itself does.
_GRANTED_TOKENS = frozenset({"1", "true", "yes", "y"})


def whm_privilege_granted(value: object) -> bool:
    """Is one `myprivs` value a granted privilege? (§V113)

    The truth table lives here and nowhere else: two readers of one table drift, and the
    direction they drift in decides whether an operator is sent to type an approval reason for
    a change WHM was always going to refuse.

    **Fails closed.** Not-granted has three measured spellings — the integer `0`, the empty
    string, and the key simply absent — and an unrecognised value joins them rather than being
    guessed toward "has it". `core.integrations.whm.accounts._optional_bool` is deliberately
    *not* reused for this: `""` is not among its false tokens, so it answers `None`, and on
    this path "not granted" must never read as "not known".

    **A string is matched after `strip().lower()`, deliberately** (§V113 carries the clause).
    The accepted set is exactly `1`, `true`, `yes`, `y` and any non-zero integer; normalising a
    *spelling* is not the guess this rule forbids, because `" TRUE "` means granted — the
    forbidden move is answering granted for a value that carries no grant. Do not narrow it
    back to a literal comparison: that answers "not granted" for a token that holds the ACL, so
    validate refuses a row which can in fact suspend. A false refusal is not the safe
    direction — §V111 exists to catch the real ACL gap at validate, not to invent one.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in _GRANTED_TOKENS
    return False


def _unwrap_privileges(data: object) -> dict[str, object] | None:
    """`data.privileges` → the single object it holds, or `None` when that is not the shape.

    The measured shape is a list holding exactly one object (§R.33, §V113), so `[0]` is the
    unwrap. A bare object is accepted as well, because unwrapping one cannot invent a grant —
    every value still goes through `whm_privilege_granted`. Anything else is a parse failure
    and gets reported as one rather than as an empty ACL set.
    """
    if not isinstance(data, dict):
        return None
    privileges = data.get("privileges")
    if isinstance(privileges, dict):
        return privileges
    if isinstance(privileges, list) and privileges:
        first = privileges[0]
        if isinstance(first, dict):
            return first
    return None


def _coerce_query_params(params: Mapping[str, object]) -> dict[str, QueryValue]:
    """Reduce arbitrary values to what `httpx` accepts as a query parameter.

    Sequences stay sequences (WHM takes repeated keys); everything else that is not a
    primitive is stringified rather than rejected, so a caller passing a UUID gets a request
    instead of a `TypeError`.
    """
    normalized: dict[str, QueryValue] = {}
    for key, value in params.items():
        if isinstance(value, (str, int, float, bool)):
            normalized[key] = value
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            normalized[key] = [
                item if isinstance(item, (str, int, float, bool)) else str(item) for item in value
            ]
            continue
        normalized[key] = str(value)
    return normalized


class WHMClient:
    """One authenticated WHM endpoint. Construct via `build_whm_client_from_creds`.

    `api_token` is plaintext here — decryption happens in the factory, so this class stays
    testable without a cipher and there is exactly one decrypt site (C7).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_username: str,
        api_token: str,
        verify_ssl: bool,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_username = api_username
        self._api_token = api_token
        self._verify_ssl = verify_ssl
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"whm {self._api_username}:{self._api_token}",
            "Accept": "application/json",
        }

    async def _get_json_api(
        self, command: str, *, params: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Call `/json-api/<command>` and normalise every outcome (see module docstring)."""
        merged_params: dict[str, object] = {"api.version": 1}
        if params is not None:
            merged_params.update(dict(params))

        url = f"{self._base_url}/json-api/{command}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify_ssl,
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    url,
                    params=_coerce_query_params(merged_params),
                    headers=self._headers(),
                )
        except httpx.TimeoutException:
            return {"ok": False, "error_code": "timeout", "message": "Request timed out"}
        except httpx.RequestError as exc:
            # V8: `exc` reports transport state (host, errno), never the token in `_headers`.
            return {
                "ok": False,
                "error_code": "request_failed",
                "message": f"Request failed: {exc}",
            }

        if response.status_code in {401, 403}:
            return {
                "ok": False,
                "error_code": "auth_failed",
                "message": "WHM authentication failed",
            }

        if response.status_code >= 400:
            return {
                "ok": False,
                "error_code": "http_error",
                "message": f"WHM returned HTTP {response.status_code}",
            }

        try:
            payload = response.json()
        except ValueError:
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "WHM returned a non-JSON response",
            }

        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "WHM returned an unexpected response shape",
            }

        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "WHM response missing metadata",
            }

        # The reason this client exists: HTTP 200 with `result: 0` is a *failure*.
        result = metadata.get("result")
        if result != 1:
            reason = metadata.get("reason")
            message = str(reason) if isinstance(reason, str) and reason.strip() else "WHM API error"
            return {"ok": False, "error_code": "whm_api_error", "message": message}

        return {
            "ok": True,
            "message": "ok",
            "data": payload.get("data"),
            "metadata": metadata,
        }

    async def applist(self) -> dict[str, object]:
        """Cheapest authenticated call WHM offers. No longer the validate probe — `privileges`
        replaced it (§V111), and the only callers left are the client's own normalisation
        tests, which need a method with no unwrap of its own between them and `_get_json_api`.
        """
        return await self._get_json_api("applist")

    async def privileges(self) -> dict[str, object]:
        """`myprivs` → `{"ok": True, "acls": [...]}`, the granted ACL names, sorted.

        The validate probe (§V111). It replaced `applist` because `applist` does not appear in
        cPanel's ACL chart at all, so whatever gates it is undocumented, and a restricted token
        *is* refused on unmapped functions — a healthy reseller row could read RED for a reason
        with nothing to do with what it may actually do. `myprivs` answers the question that
        decides whether the row may write: is `suspend-acct` there.

        The `data.privileges` unwrap lives here so no caller re-derives it — same family as
        `data.acct` in `list_accounts`. A shape that will not unwrap answers `invalid_response`
        rather than an empty ACL set: "this token holds nothing" and "the answer could not be
        read" have different remedies, and reporting the first for the second sends an operator
        to edit a credential that was fine (§V86).
        """
        result = await self._get_json_api("myprivs")
        if result.get("ok") is not True:
            return result
        privileges = _unwrap_privileges(result.get("data"))
        if privileges is None:
            return {
                "ok": False,
                "error_code": "invalid_response",
                "message": "WHM response missing privileges",
            }
        acls = sorted(name for name, value in privileges.items() if whm_privilege_granted(value))
        return {"ok": True, "message": "ok", "acls": acls}

    async def list_accounts(self) -> dict[str, object]:
        """`listaccts` → `{"ok": True, "accounts": [...]}`.

        The `data.acct` unwrap lives here so no caller re-derives it, and non-dict rows are
        dropped rather than handed on: `whm_list_accounts` (T20) renders this straight into a
        table surface.
        """
        result = await self._get_json_api("listaccts")
        if result.get("ok") is not True:
            return result
        data = result.get("data")
        accounts: list[dict[str, Any]] = []
        if isinstance(data, dict):
            acct = data.get("acct")
            if isinstance(acct, list):
                accounts = [a for a in acct if isinstance(a, dict)]
        return {"ok": True, "message": "ok", "accounts": accounts}

    async def suspend_account(self, *, username: str, reason: str) -> dict[str, object]:
        """`suspendacct`. Backs `whm_suspend_account` (T22).

        `reason` is WHM's own suspension-note field and is written from the operator-typed
        approval reason at execute time — it is ⊥ a tool argument, and the LLM never authors
        or sees it (C8, V15, V43).
        """
        result = await self._get_json_api(
            "suspendacct", params={"user": username, "reason": reason}
        )
        if result.get("ok") is not True:
            return result
        return {"ok": True, "message": "ok"}

    async def unsuspend_account(self, *, username: str) -> dict[str, object]:
        """`unsuspendacct`. Backs `whm_unsuspend_account` (T23)."""
        result = await self._get_json_api("unsuspendacct", params={"user": username})
        if result.get("ok") is not True:
            return result
        return {"ok": True, "message": "ok"}


def build_whm_client_from_creds(
    *,
    base_url: str,
    api_username: str,
    encrypted_token: str,
    verify_ssl: bool,
    cipher: SecretCipher,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WHMClient:
    """Single construction point for an authenticated WHM client.

    `encrypted_token` is the at-rest column value; `maybe_decrypt_text` unwraps it, tolerating
    a row that predates encryption (C7). One decrypt site means one place to audit and one
    place to change when a `v2` scheme lands.

    `transport` is a test seam — `httpx.MockTransport` in `test_whm_client.py`. `noa-old`'s
    test reached into `client._transport` after construction because the factory had no such
    parameter; a private attribute is not an interface.
    """
    return WHMClient(
        base_url=base_url,
        api_username=api_username,
        api_token=cipher.maybe_decrypt_text(encrypted_token),
        verify_ssl=verify_ssl,
        transport=transport,
    )


__all__ = [
    "WHM_ACL_LIST_ACCOUNTS",
    "WHM_ACL_SUSPEND_ACCOUNT",
    "WHMClient",
    "build_whm_client_from_creds",
    "whm_privilege_granted",
]

"""`myprivs`: the ACL unwrap and the granted-value truth table — the validate route's ACL
report, and the granted value's type differing per credential.

The truth table is the substance here, so it is tested as a table. The reason it needs one at all is
measured on a live host: asked the *same* function on the *same* host, a root token answers the
integer `1` where a reseller token answers the string `"1"`, and not-granted arrives as the integer
`0`, as the empty string, or as no key at all. A parser that reads only one of those spellings
reports a capability the credential does not have.

Every case runs through the real `WHMClient` over `httpx.MockTransport`, because the shape being
parsed is a WHM response body and a doubled client would let the `[0]` unwrap go missing.

**Fail closed is the direction that matters.** A wrong "granted" costs an operator a typed
approval reason on a change WHM refuses; a wrong "missing" costs a red validate that names
itself. So the unrecognised values are asserted as *not* granted, and the negative controls
(the values that really are granted) are here to prove the check still separates rather than
answering "no" to everything.
"""

from __future__ import annotations

import httpx
import pytest

from core.integrations.whm.accounts import _optional_bool
from core.integrations.whm.client import (
    WHM_ACL_LIST_ACCOUNTS,
    WHM_ACL_SUSPEND_ACCOUNT,
    WHMClient,
    whm_privilege_granted,
)
from support.whm_api import myprivs_body

BASE_URL = "https://whm.example.com:2087"
TOKEN = "MYPRIVS-TOKEN-SHOULD-NEVER-BE-REPORTED"


def _client(body: object, *, status_code: int = 200) -> WHMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=status_code, json=body, request=request)

    return WHMClient(
        base_url=BASE_URL,
        api_username="reseller1",
        api_token=TOKEN,
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )


# --- The granted set, and nothing else ---

GRANTED_VALUES: list[object] = [1, "1", "true", "yes", "y", 2, -1, True, "TRUE", " 1 "]
NOT_GRANTED_VALUES: list[object] = [
    0,
    "",
    "0",
    "false",
    "no",
    "n",
    "   ",
    False,
    None,
    [],
    {},
    "maybe",
    1.0,
]


@pytest.mark.parametrize("value", GRANTED_VALUES)
def test_the_granted_spellings_read_as_granted(value: object) -> None:
    """`1` and `"1"` are the two WHM actually sends, and which one depends on the credential.

    The rest of the list is the accepted set spelled out: `true`, `yes`, `y`, any non-zero
    integer, either case, surrounding whitespace stripped.
    """
    assert whm_privilege_granted(value) is True


@pytest.mark.parametrize("value", NOT_GRANTED_VALUES)
def test_everything_else_reads_as_not_granted(value: object) -> None:
    """The three measured not-granted spellings, plus the values nobody has seen.

    `0`, `""` and the absent key are WHM's; `None`, `[]`, `{}`, `"maybe"` and the float are the
    fail-closed half — an unrecognised value is not a grant. `1.0` is in the list on purpose:
    it is *numerically* one and still not granted, because guessing at a type WHM has never
    sent is how a capability check starts inventing permissions.
    """
    assert whm_privilege_granted(value) is False


def test_the_empty_string_is_false_here_and_none_in_the_account_normaliser() -> None:
    """Why `_optional_bool` (`core.integrations.whm.accounts`) is not reused.

    `""` is not among its false tokens, so it answers `None` — a third state. On an account
    listing that is right: an absent `suspended` field is not a claim either way. On a
    capability check there is no third state to have, because every consumer of `None` would
    have to pick a meaning for it, and one of the two choices hands out a permission.
    """
    assert _optional_bool("") is None
    assert whm_privilege_granted("") is False


# --- the parse, end to end through the real client ---


async def test_privileges_unwraps_the_one_element_list_and_keeps_only_grants() -> None:
    """The reseller answer measured on a live host, granted names sorted, everything else
    dropped."""
    result = await _client(
        myprivs_body(
            {
                "basic-whm-functions": "1",
                "list-accts": "1",
                "suspend-acct": "1",
                "all": 0,
                "create-acct": "",
                "kill-acct": "",
            }
        )
    ).privileges()

    assert result["ok"] is True
    assert result["acls"] == ["basic-whm-functions", "list-accts", "suspend-acct"]


async def test_a_root_credentials_integer_grants_parse_the_same() -> None:
    """Same host, same function, different credential, different type — measured on a live host."""
    result = await _client(
        myprivs_body({"suspend-acct": 1, "list-accts": 1, "all": 0})
    ).privileges()

    assert result["acls"] == [WHM_ACL_LIST_ACCOUNTS, WHM_ACL_SUSPEND_ACCOUNT]


async def test_an_absent_key_never_reaches_the_acl_set() -> None:
    """`suspend-acct` is simply not in the object — the third not-granted spelling."""
    result = await _client(myprivs_body({"list-accts": 1})).privileges()

    assert result["ok"] is True
    assert result["acls"] == [WHM_ACL_LIST_ACCOUNTS]
    assert WHM_ACL_SUSPEND_ACCOUNT not in list(result["acls"])  # type: ignore[arg-type]


async def test_a_privileges_object_sent_bare_is_accepted() -> None:
    """A shape nobody measured, tolerated because unwrapping it cannot invent a grant.

    Every value still goes through the same truth table, so the worst case is that a host
    answering an object instead of a list gets read correctly instead of red.
    """
    result = await _client(
        {"metadata": {"result": 1}, "data": {"privileges": {"suspend-acct": "1"}}}
    ).privileges()

    assert result["acls"] == [WHM_ACL_SUSPEND_ACCOUNT]


@pytest.mark.parametrize(
    "data",
    [
        {"privileges": []},
        {"privileges": [[]]},
        {"privileges": ["suspend-acct"]},
        {"privileges": None},
        {"nothing": "useful"},
        None,
        "text",
    ],
)
async def test_a_shape_that_will_not_unwrap_fails_closed_with_a_named_code(data: object) -> None:
    """`invalid_response`, not an empty ACL set, and never an exception.

    An empty set would be indistinguishable from a token that holds nothing, and the two have
    different remedies: one is a credential to re-issue, the other is a WHM answering something
    this client does not understand — folding it into an empty ACL would be folding a
    non-answer into the benign value. Raising instead would reach the admin route as a
    500, for a remote's response shape.
    """
    result = await _client({"metadata": {"result": 1}, "data": data}).privileges()

    assert result["ok"] is False
    assert result["error_code"] == "invalid_response"
    assert "acls" not in result


async def test_a_whm_refusal_of_myprivs_keeps_its_own_error_code() -> None:
    """The normalisation the client already does is not re-wrapped: a 200 with `result: 0` is
    `whm_api_error`, and an auth failure would still be `auth_failed`. Validate branches on
    those strings to tell "wrong token" from "token without rights"."""
    result = await _client({"metadata": {"result": 0, "reason": "Access denied"}}).privileges()

    assert result["ok"] is False
    assert result["error_code"] == "whm_api_error"
    assert result["message"] == "Access denied"
    assert "acls" not in result


# --- The credential is presented, never reported ---


async def test_the_call_is_authenticated_and_the_token_stays_out_of_the_answer() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=myprivs_body({"suspend-acct": "1"}), request=request)

    client = WHMClient(
        base_url=BASE_URL,
        api_username="reseller1",
        api_token=TOKEN,
        verify_ssl=True,
        transport=httpx.MockTransport(handler),
    )
    result = await client.privileges()

    assert seen["auth"] == f"whm reseller1:{TOKEN}"
    assert "/json-api/myprivs" in seen["url"]
    assert "api.version=1" in seen["url"]
    assert TOKEN not in str(result)

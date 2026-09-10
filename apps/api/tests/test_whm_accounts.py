"""Shaping a `listaccts` row into what NOA speaks about.

Pure functions, no transport — same split as `test_whm_csf_parsing.py`. Three properties, and
the first two are the ones a live WHM would otherwise decide for us.

**The field set is a whitelist.** Everything WHM sends that no NOA surface reads is dropped,
because the result lands in a LibreChat transcript that persists in their MongoDB.

**WHM's booleans are not Python's.** `suspended` arrives as `0`/`1` or `"0"`/`"1"` depending on
the cPanel version, and `"0"` is truthy — a tool branching on the raw value would report a live
account as suspended. Every spelling is pinned here rather than discovered in production.

**A row NOA cannot name is not a row.** `user` is the argument every account CHANGE tool takes,
so an account without one is dropped rather than carried into a result the operator cannot act
on.
"""

from __future__ import annotations

import pytest

from core.integrations.whm.accounts import (
    account_matches,
    normalize_whm_account_list,
    normalize_whm_account_summary,
)
from support.whm_api import whm_account

# --- The whitelist ---


def test_it_keeps_the_fields_noa_speaks_about() -> None:
    """The operational fields: identity, contact, owner and suspension state."""
    summary = normalize_whm_account_summary(
        {
            "user": "acme",
            "domain": "acme.example.com",
            "email": "ops@acme.example.com",
            "contactemail": "billing@acme.example.com",
            "owner": "reseller1",
            "suspended": 1,
            "suspendreason": "abuse report 41",
            "suspendtime": "1754400000",
            "is_locked": 1,
        }
    )

    assert summary == {
        "user": "acme",
        "domain": "acme.example.com",
        "email": "ops@acme.example.com",
        "contactemail": "billing@acme.example.com",
        "owner": "reseller1",
        "suspended": True,
        "suspendreason": "abuse report 41",
        "suspendtime": 1754400000,
        "is_locked": True,
    }


def test_it_drops_the_fields_no_noa_surface_reads() -> None:
    """`listaccts` sends far more than this; the extra never reaches a transcript."""
    summary = normalize_whm_account_summary(
        whm_account("acme", ip="10.0.0.9", plan="business", diskused="4096M", theme="jupiter")
    )

    assert summary is not None
    assert set(summary) == {"user", "domain", "suspended"}


def test_a_blank_field_is_absent_rather_than_empty() -> None:
    """An empty `domain` is no domain: a key with `""` reads as "the domain is blank"."""
    summary = normalize_whm_account_summary({"user": "acme", "domain": "   ", "email": ""})

    assert summary == {"user": "acme"}


def test_a_username_is_stripped() -> None:
    """The value becomes a CHANGE tool's argument, so trailing space would be a wrong user."""
    summary = normalize_whm_account_summary({"user": "  acme  "})

    assert summary == {"user": "acme"}


# --- Rows that are not rows ---


@pytest.mark.parametrize(
    "row",
    [
        pytest.param({"domain": "acme.example.com"}, id="no-user-key"),
        pytest.param({"user": "   "}, id="blank-user"),
        pytest.param({"user": None}, id="null-user"),
        pytest.param({"user": 42}, id="non-string-user"),
        pytest.param("acme", id="not-a-mapping"),
        pytest.param(None, id="null-row"),
    ],
)
def test_a_row_noa_cannot_name_is_dropped(row: object) -> None:
    """No `user` means no follow-up call is possible, so the row is not offered."""
    assert normalize_whm_account_summary(row) is None


def test_the_list_drops_unusable_rows_and_keeps_whm_order() -> None:
    """Order is WHM's; the caller that truncates decides its own (T21 sorts first)."""
    accounts = normalize_whm_account_list(
        [whm_account("zeta"), {"domain": "orphan.example.com"}, whm_account("alpha"), "junk"]
    )

    assert [account["user"] for account in accounts] == ["zeta", "alpha"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="missing"),
        pytest.param({}, id="mapping"),
        pytest.param("acme", id="string"),
        pytest.param(7, id="number"),
    ],
)
def test_a_payload_that_is_not_a_list_of_rows_is_empty(payload: object) -> None:
    """`{}` and `"acme"` are both iterable-ish; neither is a list of accounts.

    A mapping would otherwise iterate its keys and a string its characters, and both would
    come back as zero rows only by accident.
    """
    assert normalize_whm_account_list(payload) == []


# --- WHM's booleans and epochs ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(1, True, id="int-1"),
        pytest.param(0, False, id="int-0"),
        pytest.param("1", True, id="str-1"),
        pytest.param("0", False, id="str-0"),
        pytest.param(" Yes ", True, id="yes-padded"),
        pytest.param("no", False, id="no"),
        pytest.param(True, True, id="bool"),
        pytest.param("maybe", None, id="unknown-token-absent"),
        pytest.param(None, None, id="null-absent"),
    ],
)
def test_whm_boolean_spellings_normalize(raw: object, expected: bool | None) -> None:
    """`"0"` is the case with teeth: truthy in Python, false in WHM."""
    summary = normalize_whm_account_summary({"user": "acme", "suspended": raw})

    assert summary is not None
    assert summary.get("suspended") is expected


def test_the_lock_field_falls_back_to_the_older_name() -> None:
    """`suspendlock` on older cPanel; a lock blocks `unsuspendacct`, so T23 must see it."""
    summary = normalize_whm_account_summary({"user": "acme", "suspendlock": "1"})

    assert summary is not None
    assert summary["is_locked"] is True


def test_the_lock_fallback_fires_on_a_json_null_not_only_a_missing_key() -> None:
    """`.get` defaults only when the key is *absent*, never when it is present and null."""
    summary = normalize_whm_account_summary(
        {"user": "acme", "is_locked": None, "suspendlock": "yes"}
    )

    assert summary is not None
    assert summary["is_locked"] is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(1754400000, 1754400000, id="int"),
        pytest.param("1754400000", 1754400000, id="digit-string"),
        pytest.param("not-a-time", None, id="prose"),
        pytest.param(True, None, id="bool-is-not-an-epoch"),
    ],
)
def test_suspend_time_normalizes_to_an_epoch(raw: object, expected: int | None) -> None:
    """`True` is an `int` in Python; a `suspendtime` of 1 is 1970, not a flag."""
    summary = normalize_whm_account_summary({"user": "acme", "suspendtime": raw})

    assert summary is not None
    assert summary.get("suspendtime") == expected


# --- The search predicate ---


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        pytest.param("acme", True, id="exact-user"),
        pytest.param("cm", True, id="user-substring"),
        pytest.param("example", True, id="domain-substring"),
        pytest.param("shop.example.com", True, id="exact-domain"),
        pytest.param("zeta", False, id="no-match"),
    ],
)
def test_it_matches_either_field(query: str, expected: bool) -> None:
    account = {"user": "acme", "domain": "shop.example.com"}

    assert account_matches(account, query=query) is expected


def test_a_query_spanning_the_two_fields_does_not_match() -> None:
    """`noa-old` joined username and domain into one haystack, so `"acme sho"` matched.

    The operator gets a row that contains nothing they typed. Two independent substring tests
    cost the same and cannot invent a match.
    """
    account = {"user": "acme", "domain": "shop.example.com"}

    assert account_matches(account, query="acme sho") is False


def test_a_row_with_no_domain_still_matches_on_its_username() -> None:
    """The domain key is absent for a blank one, and absence is not a failure."""
    assert account_matches({"user": "acme"}, query="acm") is True

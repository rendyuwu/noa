"""WHM account rows, reduced to the fields NOA speaks about.

Ported from `noa-old` branch `MCP` (`whm/tools/result_shapes.py`), where it sat under the
tool package. It lives in `core/` here because three callers need it, not one:
`whm_search_accounts`, `whm_list_accounts` and the suspend/unsuspend preflights
that run in-process inside the suspend and unsuspend tools. Two copies of a field list is how
one of them starts reporting a field the others do not.

**A whitelist, not a passthrough.** `listaccts` answers with far more per account than any
NOA surface needs — IP, plan, disk and bandwidth counters, theme, locale, partition. Every
field kept below is one a tool result, an approval card or a suspension preflight actually
reads, and everything else is dropped. The result lands in a LibreChat transcript that
persists in their MongoDB, so "what does the model need" is the right question rather
than "what did WHM send".

**A row with no `user` is dropped entirely.** `user` is the identifier every CHANGE tool
takes, so an account NOA cannot name is an account it cannot act on — carrying it into a
result would offer the operator a row whose follow-up call has no argument.

**Types are normalised, not trusted.** WHM answers `suspended` as `0`/`1` in some versions and
`"0"`/`"1"` in others, and `suspendtime` as a string epoch. A tool that branched on the raw value
would read `"0"` as truthy and report a live account as suspended. `is_locked` carries a second name
(`suspendlock`) for the same reason, and the fallback fires on a JSON `null` too — `.get` defaults
only when the key is *missing*, never when it is present and null.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Truthy and falsy spellings WHM is known to use for a boolean field. Compared after
# stripping and lowercasing, so `"Yes"` and `" 1 "` both land.
_TRUE_TOKENS = frozenset({"1", "true", "yes", "y"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "n"})

WHMAccount = dict[str, Any]


def normalize_whm_account_summary(account: object) -> WHMAccount | None:
    """One `listaccts` row → the fields NOA speaks about, or `None` if unusable.

    `None` rather than a partial row when `user` is absent: see the module docstring.
    """
    if not isinstance(account, dict):
        return None

    user = _optional_string(account.get("user"))
    if user is None:
        return None

    normalized: WHMAccount = {"user": user}

    for key, source_key in (
        ("domain", "domain"),
        ("email", "email"),
        ("contactemail", "contactemail"),
        ("owner", "owner"),
        ("suspendreason", "suspendreason"),
    ):
        value = _optional_string(account.get(source_key))
        if value is not None:
            normalized[key] = value

    suspended = _optional_bool(account.get("suspended"))
    if suspended is not None:
        normalized["suspended"] = suspended

    suspend_time = _optional_epoch(account.get("suspendtime"))
    if suspend_time is not None:
        normalized["suspendtime"] = suspend_time

    # Field name varies by cPanel version. `is_locked` first, `suspendlock` as the fallback;
    # a suspension lock blocks `unsuspendacct`, so the unsuspend tool's preflight has to see it.
    raw_lock = account.get("is_locked")
    if raw_lock is None:
        raw_lock = account.get("suspendlock")
    is_locked = _optional_bool(raw_lock)
    if is_locked is not None:
        normalized["is_locked"] = is_locked

    return normalized


def normalize_whm_account_list(accounts: object) -> list[WHMAccount]:
    """Every usable row of a `listaccts` payload, in the order WHM returned them.

    Order is preserved rather than sorted: a caller that truncates decides its own order
    — the account search sorts before it cuts — and a caller that does not should not have
    WHM's answer silently rearranged under it.
    """
    if not isinstance(accounts, Iterable) or isinstance(accounts, (str, bytes, bytearray, dict)):
        return []

    normalized: list[WHMAccount] = []
    for account in accounts:
        summary = normalize_whm_account_summary(account)
        if summary is not None:
            normalized.append(summary)
    return normalized


def account_suspension_state(account: WHMAccount) -> bool | None:
    """A normalised row's suspension state, or `None` when WHM's value was unreadable.

    **The tri-state exists because `normalize_whm_account_summary` drops what it cannot read.**
    A row whose `suspended` is absent, JSON `null`, or a spelling `_optional_bool` does not know
    arrives here with no `suspended` key at all, and `dict.get` answers that with the same `None`
    it answers a live account's `false` with only if a caller folds it. Every caller of this
    function is deciding something an operator reads — whether there is anything to approve,
    whether a change that WHM accepted actually took — and folding an unread field into `false`
    answers those from silence: it reports a suspension that landed as a failed one, and reports
    an unsuspension nobody confirmed as a confirmed success. There is no benign direction.

    `isinstance(..., bool)` rather than `_optional_bool` a second time, deliberately. This reads
    a row the normaliser already produced, where WHM's `0`/`"1"`/`"yes"` spellings were resolved
    once; accepting them again here would put a second normalisation boundary behind the first,
    and a summary that reached a runner through JSONB having been written by something other than
    `listaccts` could then make a string read as a state.
    """
    value = account.get("suspended")
    return value if isinstance(value, bool) else None


def account_matches(account: WHMAccount, *, query: str) -> bool:
    """True when `query` is a case-insensitive substring of the username or the domain.

    **Per field, deliberately.** `noa-old` joined the two into one haystack
    (`f"{user} {domain}"`), which makes a query containing a space match *across* the
    junction: `"alpha exa"` matched user `alpha` plus domain `example.net`, and the operator
    got a row that matches nothing they typed. Two independent substring tests cost the same
    and cannot invent a match.

    `query` is expected already normalised (stripped, lowercased) — the caller does that once
    per search rather than once per account.
    """
    for key in ("user", "domain"):
        value = account.get(key)
        if isinstance(value, str) and query in value.lower():
            return True
    return False


# --- Internals ---


def _optional_string(value: object) -> str | None:
    """A non-blank string, or `None`. Blank is absence: an empty `domain` is no domain."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_bool(value: object) -> bool | None:
    """WHM's several spellings of a boolean, or `None` when it is none of them."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_TOKENS:
            return True
        if normalized in _FALSE_TOKENS:
            return False
    return None


def _optional_epoch(value: object) -> int | None:
    """An epoch second as an `int`, or `None`.

    `bool` is rejected before `int` because `True` is an `int` in Python, and a
    `suspendtime` of `1` is a timestamp in 1970 rather than a flag.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            return int(normalized)
    return None


__all__ = [
    "WHMAccount",
    "account_matches",
    "account_suspension_state",
    "normalize_whm_account_list",
    "normalize_whm_account_summary",
]

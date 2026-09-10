"""The CSRF token that guards a decision POST.

V22 puts the decision on exactly one path: a cookie POST from a NOA-origin document. A
cookie rides automatically, which is the whole point of C17/V40 — and also the whole
problem, because *any* page that can reach the endpoint gets the cookie sent for it. This
module is what makes "the browser sent the cookie" insufficient on its own.

**Server-minted and signed, never double-submit.** V39 is explicit that a double-submit
cookie is not enough here: `noa_session` is scoped `Domain=.noa.internal`, so any
sibling host under that registrable domain can plant a cookie of its own and echo it back.
A token this process signed cannot be forged by something that can only *write* cookies.

**Bound to the session and to the request.** The signed message covers `user_id` *and*
`action_request_id`. V39 asks for session-bound; binding the request id too costs nothing
and removes a whole shape of mistake — a token minted for one approval card cannot
authorise a different one, so a card left open in another tab is not a spare key.

**The key is derived, not configured.** `hmac(AUTH_JWT_SECRET, CSRF_KEY_LABEL)` rather than
a new required production secret. Two consequences, both wanted: there is no second key to
rotate out of step with the first, and the domain-separation label means a session JWT can
never verify here and a token from here can never verify as a session (a test asserts the
second half rather than trusting the reasoning).

**Lifetime is the pending TTL**, read off `APPROVAL_PENDING_TTL_SECONDS` rather than given a
number of its own. A CSRF token that outlives its request buys an attacker nothing, because
the decision path checks `action_requests.expires_at` under the row lock anyway — so a
second, independent timeout would be a second thing to get wrong and nothing to gain.

Clock discipline matches V79: zero leeway, and a token issued in the future is invalid
rather than tolerated. Same single-process assumption, and the same revisit trigger — more
than one API replica makes clock drift a real term, and the fix then is an explicit leeway,
not a silent widening.

There is no minting *route*, and T41 settled that there never will be: the approval card's own
`GET /action-requests/{id}` mints one in the same answer that renders the card. A token
that arrived separately from the thing it authorises is a token a page could hold without ever
having passed V27's requester-match, and that read is where the match happens. Terminal
requests get `null` rather than a token — a live key for a card with no door.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from core.approvals.errors import DecisionCsrfInvalidError
from core.config import Settings

# Domain separation. The signing key is `HMAC(jwt_secret, this)`, so the CSRF key and the
# session-signing key are different keys derived from one secret — a token from either
# mechanism is meaningless to the other.
CSRF_KEY_LABEL: Final = b"noa/approvals/csrf/v1"

# Prefix on the wire. Present so a future format change is a *rejection* rather than a
# signature comparison against a message shaped differently than intended.
#
# Named `CSRF_VERSION` and `WIRE_*` rather than `TOKEN_*` on purpose: ruff's S105 flags any
# constant whose name contains "token" as a possible hardcoded credential, and blanket-
# ignoring that rule in the one module that handles a real secret is the wrong trade.
CSRF_VERSION: Final = "v1"

# `.` never appears in a UUID, in a decimal timestamp, or in urlsafe base64 without padding
# (padding is stripped), so splitting on it cannot be ambiguous.
WIRE_SEPARATOR: Final = "."
WIRE_FIELD_COUNT: Final = 3

# What gets signed, joined by a character that appears in none of the fields: the version,
# the operator, the request, and when the token was minted.
MESSAGE_SEPARATOR: Final = "|"

# Diagnostics for the `detail` slot — logs only, never a response body. None of them
# quotes the token or the expected signature.
DETAIL_BLANK = "empty CSRF token; ⊥ verification attempted"
DETAIL_MALFORMED = "CSRF token is not `<version>.<issued_at>.<signature>`"
DETAIL_WRONG_VERSION = f"CSRF token version is not `{CSRF_VERSION}`"
DETAIL_BAD_ISSUED_AT = "CSRF token `issued_at` segment is not an integer"
DETAIL_BAD_SIGNATURE = "CSRF token signature does not match this session and request"
DETAIL_EXPIRED = "CSRF token is older than the pending TTL"
DETAIL_FUTURE = "CSRF token `issued_at` is in the future (V79: zero leeway)"


def _signing_key(settings: Settings) -> bytes:
    """The CSRF key, derived from the session secret (see the module docstring)."""
    return hmac.new(settings.jwt_secret.encode(), CSRF_KEY_LABEL, hashlib.sha256).digest()


def _signature(
    settings: Settings,
    *,
    user_id: UUID,
    action_request_id: UUID,
    issued_at: int,
) -> str:
    """Sign one (operator, request, moment) triple.

    The message is assembled here and nowhere else, so mint and verify cannot drift into
    signing two different things — which would show up as "CSRF is always invalid" rather
    than as a security hole, but only if someone happened to test the pair together.
    """
    message = MESSAGE_SEPARATOR.join(
        (CSRF_VERSION, str(user_id), str(action_request_id), str(issued_at))
    )
    digest = hmac.new(_signing_key(settings), message.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def mint_decision_csrf_token(
    *,
    settings: Settings,
    user_id: UUID,
    action_request_id: UUID,
    issued_at: datetime | None = None,
) -> str:
    """Mint the token an approval card carries into its decision POST.

    `issued_at` is a parameter rather than always the clock so a test can pin the moment and
    assert the age rules directly, instead of sleeping or comparing two tokens minted a
    fraction of a second apart (V87: a clock-stamped byte does not belong in an equality
    compare).
    """
    stamp = int((issued_at or datetime.now(UTC)).timestamp())
    signature = _signature(
        settings,
        user_id=user_id,
        action_request_id=action_request_id,
        issued_at=stamp,
    )
    return WIRE_SEPARATOR.join((CSRF_VERSION, str(stamp), signature))


def verify_decision_csrf_token(
    *,
    settings: Settings,
    token: str,
    user_id: UUID,
    action_request_id: UUID,
    now: datetime | None = None,
) -> None:
    """Accept `token` for this operator and this request, or raise.

    Raises `DecisionCsrfInvalidError` — never returns a boolean. A predicate invites
    `if verify(...)` written the wrong way round, or called and not branched on at all; a
    raise cannot be ignored by a caller that forgot to look.

    `user_id` and `action_request_id` are the *verifier's* values, read from the session
    cookie and the URL path. They are recomputed into the message rather than parsed out of
    the token, so a token cannot assert who it belongs to.
    """
    if not token or not token.strip():
        raise DecisionCsrfInvalidError(DETAIL_BLANK)

    parts = token.strip().split(WIRE_SEPARATOR)
    if len(parts) != WIRE_FIELD_COUNT:
        raise DecisionCsrfInvalidError(DETAIL_MALFORMED)

    version, raw_issued_at, presented = parts
    if version != CSRF_VERSION:
        raise DecisionCsrfInvalidError(DETAIL_WRONG_VERSION)

    try:
        issued_at = int(raw_issued_at)
    except ValueError:
        raise DecisionCsrfInvalidError(DETAIL_BAD_ISSUED_AT) from None

    expected = _signature(
        settings,
        user_id=user_id,
        action_request_id=action_request_id,
        issued_at=issued_at,
    )
    # Constant-time: a `==` here leaks the signature one byte at a time to a caller who can
    # time the answer, and the endpoint is happy to be called repeatedly.
    if not hmac.compare_digest(expected, presented):
        raise DecisionCsrfInvalidError(DETAIL_BAD_SIGNATURE)

    # Age last: `issued_at` is only trustworthy once the signature over it has held, so a
    # forged stamp cannot buy an attacker a longer window. Both refusals answer with the same
    # `error_code` and the same body — only `detail`, which is logs-only, says which.
    stamp = int((now or datetime.now(UTC)).timestamp())
    if issued_at > stamp:
        raise DecisionCsrfInvalidError(DETAIL_FUTURE)
    if stamp - issued_at > settings.approval_pending_ttl_seconds:
        raise DecisionCsrfInvalidError(DETAIL_EXPIRED)


__all__ = [
    "CSRF_KEY_LABEL",
    "CSRF_VERSION",
    "mint_decision_csrf_token",
    "verify_decision_csrf_token",
]

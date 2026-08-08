"""The CSRF token that guards a decision POST (T37 — V22, V39, V79, V87).

V39 says the token is server-minted, signed and session-bound, and that a double-submit
cookie is not enough. Each of those is a separate claim and each gets its own case here:

- *server-minted and signed* — a token this process did not sign is refused, and the
  signature is over a key that is not the session-signing key;
- *session-bound* — a token minted for one operator is refused for another;
- *request-bound* — and for one approval card, refused for another. Stronger than V39 asks;
  the point is that a card left open in a second tab is not a spare key.

Plus the two the mechanism needs to be a mechanism at all: the comparison is constant-time,
and it still *separates* (V87 — a comparator that has degraded into a tautology passes every
run, including the ones it was written to catch).

No Postgres and no app: this is the primitive, driven directly. Its behaviour inside the two
routes is `test_action_request_decision_routes.py`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest

from core.approvals.csrf import (
    CSRF_KEY_LABEL,
    CSRF_VERSION,
    MESSAGE_SEPARATOR,
    WIRE_SEPARATOR,
    mint_decision_csrf_token,
    verify_decision_csrf_token,
)
from core.approvals.errors import DecisionCsrfInvalidError
from core.auth.jwt_service import JWTService
from support.auth import JWT_SECRET, build_settings

# A fixed moment, so an age assertion is arithmetic rather than a race. V87: nothing here
# compares two tokens minted a fraction of a second apart and calls that equality.
NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)

USER = UUID("11111111-1111-4111-8111-111111111111")
OTHER_USER = UUID("22222222-2222-4222-8222-222222222222")
REQUEST = UUID("33333333-3333-4333-8333-333333333333")
OTHER_REQUEST = UUID("44444444-4444-4444-8444-444444444444")


@pytest.fixture
def settings():
    return build_settings()


def mint(settings, *, user_id: UUID = USER, request_id: UUID = REQUEST, at: datetime = NOW) -> str:
    return mint_decision_csrf_token(
        settings=settings,
        user_id=user_id,
        action_request_id=request_id,
        issued_at=at,
    )


def verify(settings, token: str, *, user_id: UUID = USER, request_id: UUID = REQUEST, at=NOW):
    verify_decision_csrf_token(
        settings=settings,
        token=token,
        user_id=user_id,
        action_request_id=request_id,
        now=at,
    )


# --------------------------------------------------------------------------------------
# The happy path, and that it is not vacuous
# --------------------------------------------------------------------------------------


def test_a_minted_token_verifies_for_its_own_operator_and_request(settings) -> None:
    """The mechanism works at all — everything below is about what it refuses."""
    verify(settings, mint(settings))


def test_verification_separates_distinct_tokens(settings) -> None:
    """V87: the comparator still tells two genuinely different tokens apart.

    A refusal test proves nothing on its own if the verifier could be rejecting everything,
    and an acceptance test proves nothing if it could be accepting everything. This asserts
    both directions against the *same* pair, so neither degradation passes: one token is
    accepted for its own triple and the other is not, and vice versa.
    """
    mine = mint(settings, request_id=REQUEST)
    theirs = mint(settings, request_id=OTHER_REQUEST)

    assert mine != theirs

    verify(settings, mine, request_id=REQUEST)
    verify(settings, theirs, request_id=OTHER_REQUEST)

    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, mine, request_id=OTHER_REQUEST)
    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, theirs, request_id=REQUEST)


# --------------------------------------------------------------------------------------
# Signed: a token this process did not mint is refused
# --------------------------------------------------------------------------------------


def test_a_tampered_signature_is_refused(settings) -> None:
    """The signature is the whole guarantee; flipping one character must break it."""
    version, stamp, signature = mint(settings).split(WIRE_SEPARATOR)
    flipped = ("B" if signature[0] != "B" else "C") + signature[1:]

    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, WIRE_SEPARATOR.join((version, stamp, flipped)))


def test_a_forged_stamp_is_refused(settings) -> None:
    """`issued_at` travels in the clear, so it must be covered by the signature.

    Otherwise an expired token is renewable by editing three digits — which is exactly why
    the age check runs *after* the signature, never before.
    """
    version, _, signature = mint(settings, at=NOW - timedelta(days=30)).split(WIRE_SEPARATOR)
    fresh_stamp = str(int(NOW.timestamp()))

    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, WIRE_SEPARATOR.join((version, fresh_stamp, signature)))


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "not-a-token",
        f"{CSRF_VERSION}.{int(NOW.timestamp())}",
        f"{CSRF_VERSION}.{int(NOW.timestamp())}.sig.extra",
        f"v0.{int(NOW.timestamp())}.signature",
        f"{CSRF_VERSION}.not-a-number.signature",
    ],
    ids=["blank", "whitespace", "garbage", "two-parts", "four-parts", "old-version", "bad-stamp"],
)
def test_malformed_tokens_are_refused(settings, token: str) -> None:
    """Every shape that is not `<version>.<issued_at>.<signature>` is a refusal.

    Named one by one rather than left to the signature check, because several of these would
    otherwise reach `int()` or an index and raise something that is *not* a
    `DecisionCsrfInvalidError` — a 500 where V39 wants a 403.
    """
    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, token)


def test_a_token_signed_with_the_raw_session_secret_is_refused(settings) -> None:
    """Domain separation is real, not a comment (V39).

    The key is `HMAC(jwt_secret, CSRF_KEY_LABEL)`. This mints the same message under the raw
    `jwt_secret` — what an attacker who learned the session secret's *use* but not the label
    would produce, and what a future refactor that "simplified" the derivation away would
    produce too. It must not verify.
    """
    stamp = int(NOW.timestamp())
    message = MESSAGE_SEPARATOR.join((CSRF_VERSION, str(USER), str(REQUEST), str(stamp)))
    digest = hmac.new(JWT_SECRET.encode(), message.encode(), hashlib.sha256).digest()
    undomained = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, WIRE_SEPARATOR.join((CSRF_VERSION, str(stamp), undomained)))


def test_a_session_jwt_is_not_a_csrf_token(settings) -> None:
    """The other half of the same claim: the session credential does not work here.

    A real `JWTService` token for this operator, presented as CSRF. It is signed with the
    same secret and it names the same user, and it is still refused — because the CSRF key
    is a different key and the wire format is a different format.
    """
    session_token = JWTService(settings).create_access_token(email="op@example.com", user_id=USER)

    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, session_token.token)


def test_a_token_minted_under_a_different_secret_is_refused(settings) -> None:
    """Rotating `AUTH_JWT_SECRET` invalidates outstanding cards, and that is correct."""
    other_settings = build_settings(auth_jwt_secret="z" * 64)

    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, mint(other_settings))


# --------------------------------------------------------------------------------------
# Bound: to this operator, to this request
# --------------------------------------------------------------------------------------


def test_another_operators_token_is_refused(settings) -> None:
    """Session-bound, V39's own word. A planted cookie plus a borrowed token is not enough."""
    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, mint(settings, user_id=OTHER_USER))


def test_a_token_for_another_request_is_refused(settings) -> None:
    """Request-bound: one card, one token. A second open card is not a spare key."""
    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, mint(settings, request_id=OTHER_REQUEST))


# --------------------------------------------------------------------------------------
# Age: the pending TTL, with V79's clock discipline
# --------------------------------------------------------------------------------------


def test_a_token_at_the_ttl_boundary_still_verifies(settings) -> None:
    """Exactly `approval_pending_ttl_seconds` old is inside, not outside.

    The boundary is asserted rather than assumed because the difference between `>` and `>=`
    here is a card that stops working one second before its request does.
    """
    ttl = settings.approval_pending_ttl_seconds
    verify(settings, mint(settings, at=NOW - timedelta(seconds=ttl)))


def test_a_token_past_the_ttl_is_refused(settings) -> None:
    ttl = settings.approval_pending_ttl_seconds
    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, mint(settings, at=NOW - timedelta(seconds=ttl + 1)))


def test_the_ttl_follows_the_configured_pending_window(settings) -> None:
    """One number, not two (V32). A CSRF lifetime of its own would drift from the request's.

    Asserted by *changing* the setting: a token that is stale under a 60-second window is
    fresh under an hour-long one, with nothing else different.
    """
    narrow = build_settings(approval_pending_ttl_seconds=60)
    token = mint(narrow, at=NOW - timedelta(seconds=120))

    verify(settings, token)  # the default hour still accepts it
    with pytest.raises(DecisionCsrfInvalidError):
        verify(narrow, token)


def test_a_future_token_is_refused(settings) -> None:
    """V79's rule, one mechanism over: zero leeway, and `issued_at` ahead of now is invalid.

    Holds because mint and verify share one process clock. More than one API replica makes
    drift a real term, and the fix then is an explicit leeway rather than a silent widening.
    """
    with pytest.raises(DecisionCsrfInvalidError):
        verify(settings, mint(settings, at=NOW + timedelta(seconds=1)))


# --------------------------------------------------------------------------------------
# Mechanism
# --------------------------------------------------------------------------------------


def test_the_comparison_is_constant_time() -> None:
    """`hmac.compare_digest`, asserted on the source rather than by timing it.

    A timing assertion is the flakiest test that could be written here (V87's neighbourhood).
    What is checkable is that the byte comparison in this module is the constant-time one and
    that no bare `==` sneaked in beside it — a plain comparison leaks the expected signature
    one byte at a time to a caller who may retry as often as they like.
    """
    source = inspect.getsource(verify_decision_csrf_token)

    assert "hmac.compare_digest(expected, presented)" in source
    assert "expected == presented" not in source
    assert "presented == expected" not in source


def test_the_signature_covers_every_field_it_claims_to() -> None:
    """Each of the four message fields changes the signature.

    Written as a set-size assertion because the failure it catches is a field silently
    dropped from the joined message — which leaves every other case in this file passing
    while the token stops being bound to whatever was dropped.
    """
    settings = build_settings()
    tokens = {
        mint(settings),
        mint(settings, user_id=OTHER_USER),
        mint(settings, request_id=OTHER_REQUEST),
        mint(settings, at=NOW + timedelta(seconds=60)),
    }
    assert len(tokens) == 4


def test_the_derived_key_is_not_the_session_secret() -> None:
    """The label does something. Cheap, and it is the premise every claim above rests on."""
    settings = build_settings()
    derived = hmac.new(JWT_SECRET.encode(), CSRF_KEY_LABEL, hashlib.sha256).digest()

    assert derived != JWT_SECRET.encode()
    assert settings.jwt_secret == JWT_SECRET


def test_a_minted_token_carries_no_secret_material(settings) -> None:
    """V8, one mechanism over: the token is a MAC, not an envelope.

    Nothing readable travels in it — not the operator's id, not the request's, not the
    signing key. A verifier recomputes the message from values it already holds, which is
    what makes "a token cannot assert who it belongs to" true rather than merely intended.
    """
    token = mint(settings)

    assert str(USER) not in token
    assert str(REQUEST) not in token
    assert JWT_SECRET not in token
    # And it is not a JWT: no readable claims to decode.
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])

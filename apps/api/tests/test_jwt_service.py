"""Session JWT + cookie guards (T7, V6, V8).

No app and no DB: `JWTService` takes only `Settings`, so mint/verify run directly
and the cookie paths run against a bare Starlette `Response`. Cookie assertions
parse the real `Set-Cookie` header rather than trusting the call arguments — V6 is
a statement about what the browser receives.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.fernet import Fernet
from starlette.responses import Response

from core.auth.errors import (
    AuthConfigurationError,
    AuthSessionExpiredError,
    AuthSessionInvalidError,
)
from core.auth.jwt_service import (
    ALLOWED_ALGORITHMS,
    CLAIM_EMAIL,
    CLAIM_EXPIRES_AT,
    CLAIM_ISSUED_AT,
    CLAIM_USER_ID,
    MIN_KEY_BYTES_BY_ALGORITHM,
    IssuedToken,
    JWTService,
)
from core.config import Settings

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_ID = UUID("11111111-2222-3333-4444-555555555555")

JWT_SECRET = "s" * 64
OTHER_SECRET = "d" * 64

COOKIE_NAME = "noa_session"
COOKIE_DOMAIN = ".noa.internal"


def build_settings(**overrides: object) -> Settings:
    """Settings from explicit values only — the developer's `.env` cannot leak in."""
    defaults: dict[str, object] = {
        "environment": "test",
        "auth_jwt_secret": JWT_SECRET,
        "auth_session_cookie_name": COOKIE_NAME,
        "auth_session_cookie_domain": COOKIE_DOMAIN,
    }
    return Settings(_env_file=None, **{**defaults, **overrides})  # type: ignore[arg-type]


def build_service(**overrides: object) -> JWTService:
    return JWTService(build_settings(**overrides))


def build_production_service(**overrides: object) -> JWTService:
    """Production settings need the secrets V52/V53 demand outside dev."""
    return build_service(
        environment="production",
        noa_secret_encryption_key=Fernet.generate_key().decode(),
        ldap_server_uri="ldaps://ldap.example.com:636",
        **overrides,
    )


def set_cookie_header(response: Response) -> str:
    """The single `Set-Cookie` header the browser would receive."""
    headers = response.headers.getlist("set-cookie")
    assert len(headers) == 1, f"expected exactly one Set-Cookie, got {headers}"
    return headers[0]


def parse_cookie(response: Response) -> SimpleCookie:
    cookie: SimpleCookie = SimpleCookie()
    cookie.load(set_cookie_header(response))
    return cookie


# --- Mint / verify round trip ---


def test_mint_then_verify_round_trip() -> None:
    """Identity survives a mint→verify cycle intact."""
    service = build_service()

    issued = service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID)
    claims = service.decode_token(issued.token)

    assert claims.user_id == OPERATOR_ID
    assert claims.email == OPERATOR_EMAIL


def test_expires_in_matches_configured_ttl_and_encoded_exp() -> None:
    """Cookie Max-Age and signature expiry agree — ⊥ cookie outliving its token."""
    service = build_service(auth_jwt_access_token_ttl_seconds=900)

    issued = service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID)
    claims = service.decode_token(issued.token)

    assert issued.expires_in == 900
    assert claims.expires_at - claims.issued_at == timedelta(seconds=900)


def test_issued_at_is_utc_aware_and_recent() -> None:
    """Claims come back timezone-aware; a naive datetime would break TTL math."""
    before = datetime.now(UTC) - timedelta(seconds=5)
    service = build_service()

    claims = service.decode_token(
        service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID).token
    )

    assert claims.issued_at.tzinfo is not None
    assert before <= claims.issued_at <= datetime.now(UTC) + timedelta(seconds=5)


def test_email_normalized_at_mint() -> None:
    """Emails compare case-insensitively; normalize once, at the boundary."""
    service = build_service()

    issued = service.create_access_token(email="  Operator@Example.COM  ", user_id=OPERATOR_ID)

    assert service.decode_token(issued.token).email == OPERATOR_EMAIL


def test_blank_email_rejected_at_mint() -> None:
    """A session token with no subject is a bug, not a valid credential."""
    with pytest.raises(AuthConfigurationError):
        build_service().create_access_token(email="   ", user_id=OPERATOR_ID)


def test_claims_carry_identity_only() -> None:
    """Roles and permissions stay out: they go stale, and V1 re-reads them anyway."""
    service = build_service()

    payload = jwt.decode(
        service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID).token,
        JWT_SECRET,
        algorithms=["HS256"],
    )

    assert set(payload) == {CLAIM_EMAIL, CLAIM_USER_ID, CLAIM_ISSUED_AT, CLAIM_EXPIRES_AT}


# --- Verify rejections ---


@pytest.mark.parametrize("token", ["", "   ", "not-a-jwt", "a.b.c"])
def test_absent_or_malformed_token_rejected(token: str) -> None:
    with pytest.raises(AuthSessionInvalidError):
        build_service().decode_token(token)


def test_expired_token_reports_expiry_not_invalid() -> None:
    """Routine expiry is its own error so the UI ⊥ imply tampering (V6)."""
    service = build_service(auth_jwt_access_token_ttl_seconds=60)
    past = datetime.now(UTC) - timedelta(hours=2)
    token = jwt.encode(
        {
            CLAIM_EMAIL: OPERATOR_EMAIL,
            CLAIM_USER_ID: str(OPERATOR_ID),
            CLAIM_ISSUED_AT: past,
            CLAIM_EXPIRES_AT: past + timedelta(seconds=60),
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(AuthSessionExpiredError):
        service.decode_token(token)


def test_token_signed_with_another_secret_rejected() -> None:
    """Signature verification is real: a foreign signer ⊥ mint NOA sessions."""
    foreign = JWTService(build_settings(auth_jwt_secret=OTHER_SECRET))
    issued = foreign.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID)

    with pytest.raises(AuthSessionInvalidError):
        build_service().decode_token(issued.token)


def test_unsigned_token_rejected() -> None:
    """`alg: none` ⊥ verify: algorithms are pinned to the configured HMAC."""
    token = jwt.encode(
        {
            CLAIM_EMAIL: OPERATOR_EMAIL,
            CLAIM_USER_ID: str(OPERATOR_ID),
            CLAIM_ISSUED_AT: datetime.now(UTC),
            CLAIM_EXPIRES_AT: datetime.now(UTC) + timedelta(hours=1),
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(AuthSessionInvalidError):
        build_service().decode_token(token)


@pytest.mark.parametrize("claim", [CLAIM_EMAIL, CLAIM_USER_ID, CLAIM_ISSUED_AT, CLAIM_EXPIRES_AT])
def test_missing_required_claim_rejected(claim: str) -> None:
    """Every claim in `REQUIRED_CLAIMS` is required — a token without `exp` would be
    a session that never ends, and one without `iat` has no verifiable age."""
    payload = {
        CLAIM_EMAIL: OPERATOR_EMAIL,
        CLAIM_USER_ID: str(OPERATOR_ID),
        CLAIM_ISSUED_AT: datetime.now(UTC),
        CLAIM_EXPIRES_AT: datetime.now(UTC) + timedelta(hours=1),
    }
    del payload[claim]
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")

    with pytest.raises(AuthSessionInvalidError):
        build_service().decode_token(token)


def test_token_signed_with_another_hmac_algorithm_rejected() -> None:
    """Algorithm confusion: same secret, stronger hash, still rejected.

    `decode` pins `algorithms` to the single configured value rather than the whole
    allowlist, so a header advertising HS512 finds no accepted algorithm even though
    the HMAC would verify against the same secret. Pinned behaviour, hence a test:
    passing `ALLOWED_ALGORITHMS` instead would make this token verify.
    """
    token = jwt.encode(
        {
            CLAIM_EMAIL: OPERATOR_EMAIL,
            CLAIM_USER_ID: str(OPERATOR_ID),
            CLAIM_ISSUED_AT: datetime.now(UTC),
            CLAIM_EXPIRES_AT: datetime.now(UTC) + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS512",
    )

    with pytest.raises(AuthSessionInvalidError):
        build_service(auth_jwt_algorithm="HS256").decode_token(token)


def test_future_dated_iat_rejected() -> None:
    """An `iat` ahead of now is a forged or clock-broken token, not a fresh one.

    PyJWT enforces this (`iat > now + leeway`), and the pin is what makes it a
    property NOA can rely on: `PyJWT==2.13.0`. The check has moved across 2.x
    releases, so this test is the tripwire for a pin bump that drops it.
    """
    future = datetime.now(UTC) + timedelta(hours=5)
    token = jwt.encode(
        {
            CLAIM_EMAIL: OPERATOR_EMAIL,
            CLAIM_USER_ID: str(OPERATOR_ID),
            CLAIM_ISSUED_AT: future,
            CLAIM_EXPIRES_AT: future + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(AuthSessionInvalidError):
        build_service().decode_token(token)


@pytest.mark.parametrize("user_id", ["not-a-uuid", "", 12345])
def test_non_uuid_user_id_claim_rejected(user_id: object) -> None:
    """Correctly signed but wrong shape → reject, ⊥ hand back a bogus `user_id`."""
    token = jwt.encode(
        {
            CLAIM_EMAIL: OPERATOR_EMAIL,
            CLAIM_USER_ID: user_id,
            CLAIM_ISSUED_AT: datetime.now(UTC),
            CLAIM_EXPIRES_AT: datetime.now(UTC) + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(AuthSessionInvalidError):
        build_service().decode_token(token)


# --- V8: no token material in error text ---


def test_errors_never_quote_the_token() -> None:
    """V8: neither message nor internal detail carries token bytes."""
    service = build_service()
    token = service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID).token
    tampered = f"{token}tamper"

    with pytest.raises(AuthSessionInvalidError) as exc_info:
        service.decode_token(tampered)

    error = exc_info.value
    assert token not in error.detail
    assert token not in error.message
    assert token not in str(error)
    # Cause chain dropped with `from None`: PyJWT's own text can quote the token.
    assert error.__cause__ is None


# --- Algorithm allowlist ---


@pytest.mark.parametrize("algorithm", sorted(ALLOWED_ALGORITHMS))
def test_allowed_hmac_algorithms_round_trip(algorithm: str) -> None:
    service = build_service(auth_jwt_algorithm=algorithm)

    issued = service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID)

    assert service.decode_token(issued.token).user_id == OPERATOR_ID


def test_lowercase_algorithm_accepted() -> None:
    """Config strings are human-typed; `hs256` is the same algorithm."""
    assert build_service(auth_jwt_algorithm="hs256").create_access_token(
        email=OPERATOR_EMAIL, user_id=OPERATOR_ID
    )


@pytest.mark.parametrize("algorithm", ["none", "None", "RS256", "ES256", "HS128", ""])
def test_disallowed_algorithm_fails_at_construction(algorithm: str) -> None:
    """Fail at construction, ⊥ per mint. `none` would accept unsigned tokens."""
    with pytest.raises(AuthConfigurationError):
        build_service(auth_jwt_algorithm=algorithm)


# --- Key length (RFC 7518 §3.2) ---


@pytest.mark.parametrize(("algorithm", "minimum"), sorted(MIN_KEY_BYTES_BY_ALGORITHM.items()))
def test_secret_shorter_than_algorithm_minimum_fails_at_construction(
    algorithm: str, minimum: int
) -> None:
    """V53's 32-char floor suits HS256 only; HS384/HS512 need more.

    PyJWT warns per mint instead of refusing, so a short key would otherwise ship
    quietly and weaken every session signature.
    """
    with pytest.raises(AuthConfigurationError, match=f"at least {minimum} bytes"):
        build_service(auth_jwt_algorithm=algorithm, auth_jwt_secret="k" * (minimum - 1))


def test_mint_emits_no_insecure_key_warning(recwarn: pytest.WarningsRecorder) -> None:
    """The length guard's payoff: a clean mint path for every allowed algorithm."""
    for algorithm in sorted(ALLOWED_ALGORITHMS):
        service = build_service(auth_jwt_algorithm=algorithm)
        service.decode_token(
            service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID).token
        )

    assert [str(warning.message) for warning in recwarn] == []


# --- V6: cookie attributes ---


def test_set_session_cookie_attributes() -> None:
    """V6: httpOnly, SameSite=Lax, `Domain=.noa.internal`, `Path=/`."""
    service = build_service(auth_jwt_access_token_ttl_seconds=1800)
    response = Response()

    issued = service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID)
    service.set_session_cookie(response, issued)

    morsel = parse_cookie(response)[COOKIE_NAME]
    assert morsel.value == issued.token
    assert morsel["httponly"] is True
    assert morsel["samesite"].lower() == "lax"
    assert morsel["domain"] == COOKIE_DOMAIN
    assert morsel["path"] == "/"
    assert morsel["max-age"] == "1800"


def test_session_cookie_not_secure_in_dev_but_secure_in_production() -> None:
    """Dev runs plain HTTP, where a Secure cookie would never be sent."""
    dev_response, prod_response = Response(), Response()
    issued = IssuedToken(token="t", expires_in=60)

    build_service(environment="development").set_session_cookie(dev_response, issued)
    build_production_service().set_session_cookie(prod_response, issued)

    assert "secure" not in set_cookie_header(dev_response).lower()
    assert "secure" in set_cookie_header(prod_response).lower()


def test_clear_session_cookie_expires_it_with_matching_attributes() -> None:
    """V6: logout clears. Domain/Path must match the set path or the live cookie stays."""
    service = build_service()
    set_response, clear_response = Response(), Response()

    service.set_session_cookie(
        set_response, service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID)
    )
    service.clear_session_cookie(clear_response)

    cleared = parse_cookie(clear_response)[COOKIE_NAME]
    original = parse_cookie(set_response)[COOKIE_NAME]

    assert cleared["max-age"] == "0"
    assert not cleared.value
    for attribute in ("domain", "path", "samesite", "httponly", "secure"):
        assert cleared[attribute] == original[attribute], attribute


def test_clear_session_cookie_is_idempotent_and_needs_no_auth() -> None:
    """V6: logout works without a session and repeats safely."""
    service = build_service()
    first, second = Response(), Response()

    service.clear_session_cookie(first)
    service.clear_session_cookie(second)

    assert set_cookie_header(first) == set_cookie_header(second)


def test_cookie_name_follows_settings() -> None:
    """The name is configurable; nothing hardcodes `noa_session`."""
    service = build_service(auth_session_cookie_name="custom_session")
    response = Response()

    service.set_session_cookie(response, IssuedToken(token="t", expires_in=60))

    assert "custom_session" in parse_cookie(response)


# --- Cookie read ---


def test_read_session_cookie_returns_token_when_present() -> None:
    service = build_service()

    assert service.read_session_cookie({COOKIE_NAME: "token-value"}) == "token-value"


@pytest.mark.parametrize("cookies", [{}, {COOKIE_NAME: ""}, {COOKIE_NAME: "   "}, {"other": "x"}])
def test_read_session_cookie_returns_none_when_absent_or_blank(cookies: dict[str, str]) -> None:
    """A blank cookie is "no session", so callers ⊥ verify an empty string."""
    assert build_service().read_session_cookie(cookies) is None


# --- Isolation from the MCP token mechanism (C5) ---


def test_session_token_is_not_an_mcp_credential() -> None:
    """C5/V2: MCP tokens are opaque and hashed. Nothing here mints one."""
    service = build_service()

    issued = service.create_access_token(email=OPERATOR_EMAIL, user_id=uuid4())

    # A JWT is three base64 segments; an MCP token is not, and is never a JWT.
    assert issued.token.count(".") == 2
    assert not hasattr(service, "create_mcp_token")


def test_verification_is_time_based_not_call_count_based() -> None:
    """A token stays valid until `exp`; repeat verification ⊥ consume it."""
    service = build_service(auth_jwt_access_token_ttl_seconds=60)
    issued = service.create_access_token(email=OPERATOR_EMAIL, user_id=OPERATOR_ID)

    first = service.decode_token(issued.token)
    time.sleep(0.01)
    second = service.decode_token(issued.token)

    assert first == second

"""Admin-session JWT + session cookie.

Ported from `noa-old` branch `MCP` (`core/auth/jwt_service.py`, C13). Three
deliberate departures from the original:

- **Cookie set/clear live here, next to mint/verify.** `noa-old` spread them across
  its auth routes, so the attributes on the clear path could drift from the set
  path and leave a cookie behind. V6 demands logout actually clears, so both calls
  read one attribute source: `Settings.session_cookie_kwargs()`.
- **Stale sessions get their own errors.** `noa-old` raised
  `AuthInvalidCredentialsError` for a bad token, which tells an operator "Email or
  password is incorrect" when the truth is "your session ended". T8 maps
  `AuthSessionExpiredError` / `AuthSessionInvalidError` to 401 and the browser
  re-authenticates instead of the operator doubting their password.
- **Algorithm allowlisted at construction, key length checked with it.**
  `AUTH_JWT_ALGORITHM` is a config string; `none` would mint unsigned tokens that
  verify, and an asymmetric name would treat the HMAC secret as a public key. Only
  HS256/384/512 pass, each against its RFC 7518 minimum key length, and a bad
  combination fails at construction rather than warning on every mint. "At
  construction", not "at startup": the guards fire wherever the service is first
  built, so T8 must build it once during app startup. A request-scoped dependency
  would turn a config error into a 500 on the first login instead of a boot failure.

This is the *admin/embed session* credential — cookie-borne, LDAP-backed,
short-lived. MCP bearer tokens are a different mechanism entirely: opaque, hashed
at rest, no JWT. Nothing here touches them.

Scope of trust: a valid token proves the bearer authenticated as `sub`/`uid` before
`exp`. It proves nothing about *current* state — `is_active` can flip and roles can
change after minting, so callers re-read the row. Claims stay minimal for
that reason: no roles, no permissions, nothing that goes stale in an attacker's
favour.

V8: no token, and no fragment of one, reaches a log or an exception message. Errors
name the failure class only, and PyJWT's own text (which can quote the token) is
dropped with `from None` rather than chained into a traceback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from starlette.responses import Response

from core.auth.errors import (
    AuthConfigurationError,
    AuthSessionExpiredError,
    AuthSessionInvalidError,
)
from core.config import Settings

# HMAC only: the signing key is a shared secret. An `RS*`/`ES*` value would
# hand that secret to a public-key verifier, and `none` would accept unsigned
# tokens outright. Each maps to its RFC 7518 §3.2 minimum key length — a key
# shorter than the hash output weakens the MAC, and PyJWT warns on every single
# mint rather than failing, so the check belongs here where it fails once.
MIN_KEY_BYTES_BY_ALGORITHM: Final = {"HS256": 32, "HS384": 48, "HS512": 64}

ALLOWED_ALGORITHMS: Final = frozenset(MIN_KEY_BYTES_BY_ALGORITHM)

# Claims. `sub` = email (who the operator is to LDAP), `uid` = `users.id` (who they
# are to NOA). Both travel because the email is what the directory knows and the
# UUID is what every FK references.
CLAIM_EMAIL: Final = "sub"
CLAIM_USER_ID: Final = "uid"
CLAIM_ISSUED_AT: Final = "iat"
CLAIM_EXPIRES_AT: Final = "exp"

# Absent `exp` would mean a session that never ends, so it is required rather than
# defaulted. PyJWT verifies each of these once present.
REQUIRED_CLAIMS: Final = (CLAIM_EMAIL, CLAIM_USER_ID, CLAIM_ISSUED_AT, CLAIM_EXPIRES_AT)

# Internal diagnostics for the `detail` slot: logs only, never a response body, and
# never carrying token bytes.
DETAIL_BLANK_INPUT = "empty session token; ⊥ verification attempted"
DETAIL_EXPIRED = "session token past its `exp`"
DETAIL_MALFORMED = "session token failed signature or claim verification"
DETAIL_BAD_USER_ID = f"`{CLAIM_USER_ID}` claim is not a UUID"
DETAIL_BLANK_EMAIL = f"`{CLAIM_EMAIL}` claim is blank"


@dataclass(frozen=True)
class IssuedToken:
    """A freshly minted session token and its lifetime.

    `expires_in` is the cookie's `Max-Age`, so cookie and signature expire
    together: a cookie outliving its token yields a 401 with a cookie still
    present, which reads to the operator as a broken app.
    """

    token: str
    expires_in: int


@dataclass(frozen=True)
class SessionClaims:
    """Verified claims. Identity only — authorization is re-read per request."""

    user_id: UUID
    email: str
    issued_at: datetime
    expires_at: datetime


class JWTService:
    """Mint/verify session tokens and own the `noa_session` cookie."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._algorithm = self._resolve_algorithm(settings.auth_jwt_algorithm)
        self._verify_key_length()

    # --- Mint / verify ---

    def create_access_token(self, *, email: str, user_id: UUID) -> IssuedToken:
        """Sign a session token for `user_id`.

        `iat`/`exp` are whole seconds: PyJWT truncates on encode, so keeping the
        returned `expires_in` aligned with the encoded `exp` avoids a token that
        outlives its own advertised lifetime by a fraction of a second.
        """
        normalized_email = email.strip().lower()
        if not normalized_email:
            raise AuthConfigurationError(DETAIL_BLANK_EMAIL)

        ttl_seconds = self._settings.auth_jwt_access_token_ttl_seconds
        issued_at = datetime.now(UTC).replace(microsecond=0)

        payload: dict[str, Any] = {
            CLAIM_EMAIL: normalized_email,
            CLAIM_USER_ID: str(user_id),
            CLAIM_ISSUED_AT: issued_at,
            CLAIM_EXPIRES_AT: issued_at + timedelta(seconds=ttl_seconds),
        }
        token = jwt.encode(payload, self._secret, algorithm=self._algorithm)
        return IssuedToken(token=str(token), expires_in=ttl_seconds)

    def decode_token(self, token: str) -> SessionClaims:
        """Verify signature + claims; return the identity inside.

        `algorithms` is pinned to the single configured algorithm, so a token whose
        header advertises something else is rejected rather than trusted.
        """
        if not token or not token.strip():
            raise AuthSessionInvalidError(DETAIL_BLANK_INPUT)

        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": list(REQUIRED_CLAIMS)},
            )
        except ExpiredSignatureError:
            # `from None` on both paths: PyJWT messages can quote token bytes.
            raise AuthSessionExpiredError(DETAIL_EXPIRED) from None
        except InvalidTokenError:
            raise AuthSessionInvalidError(DETAIL_MALFORMED) from None

        return self._to_claims(payload)

    # --- Cookie ---

    def set_session_cookie(self, response: Response, issued: IssuedToken) -> None:
        """Attach the httpOnly session cookie.

        Attributes come from `Settings.session_cookie_kwargs()`: httpOnly (⊥ JS
        reach), SameSite=Lax, `Domain=.noa.internal` so the cookie rides to the
        embed origin where the approval POST happens, `Path=/`.
        """
        response.set_cookie(
            value=issued.token,
            max_age=issued.expires_in,
            **self._settings.session_cookie_kwargs(),
        )

    def clear_session_cookie(self, response: Response) -> None:
        """Expire the session cookie.

        Starlette's `delete_cookie` re-sends the same cookie with `Max-Age=0`, so
        the attributes must match the set path exactly — a differing `Domain` or
        `Path` writes a *second* cookie and leaves the live one in place. Sharing
        one kwargs source is what makes that impossible.

        Takes no token and reads no request: logout is idempotent and works without
        authentication.
        """
        response.delete_cookie(**self._settings.session_cookie_kwargs())

    def read_session_cookie(self, cookies: dict[str, str]) -> str | None:
        """Pull the raw token out of a request's cookies. None when absent."""
        value = cookies.get(self._settings.auth_session_cookie_name)
        return value if value and value.strip() else None

    # --- Internals ---

    @staticmethod
    def _resolve_algorithm(configured: str) -> str:
        """Allowlist the configured algorithm at construction (⊥ `none`)."""
        algorithm = configured.strip().upper()
        if algorithm not in ALLOWED_ALGORITHMS:
            allowed = ", ".join(sorted(ALLOWED_ALGORITHMS))
            raise AuthConfigurationError(
                f"auth_jwt_algorithm must be one of: {allowed} (got {configured!r})"
            )
        return algorithm

    def _verify_key_length(self) -> None:
        """Reject a secret too short for the chosen algorithm (RFC 7518 §3.2).

        V53's floor is 32 characters, which suits HS256 but not HS384/HS512. Caught
        at construction so the operator sees one config error instead of a warning
        buried in every request's logs — at boot, provided T8 builds the service
        once at startup.
        """
        minimum = MIN_KEY_BYTES_BY_ALGORITHM[self._algorithm]
        actual = len(self._secret.encode())
        if actual < minimum:
            raise AuthConfigurationError(
                f"auth_jwt_secret must be at least {minimum} bytes for {self._algorithm} "
                f"(got {actual})"
            )

    @property
    def _secret(self) -> str:
        """The HMAC signing secret (V53 guarantees it exists outside dev)."""
        try:
            secret = self._settings.jwt_secret
        except RuntimeError as exc:  # pragma: no cover - config validator guarantees
            raise AuthConfigurationError("auth_jwt_secret is not configured") from exc

        if not secret.strip():  # pragma: no cover - config validator guarantees
            raise AuthConfigurationError("auth_jwt_secret is empty")
        return secret

    @staticmethod
    def _to_claims(payload: dict[str, Any]) -> SessionClaims:
        """Shape a verified payload into `SessionClaims`.

        A token this service signed always satisfies these checks. They exist for
        the token it did *not* sign but which still verifies — same secret, older
        claim shape after a future change. Failing here beats handing a caller a
        `user_id` that is not a UUID.
        """
        try:
            user_id = UUID(str(payload[CLAIM_USER_ID]))
        except (KeyError, ValueError, TypeError):
            raise AuthSessionInvalidError(DETAIL_BAD_USER_ID) from None

        email = str(payload.get(CLAIM_EMAIL) or "").strip().lower()
        if not email:
            raise AuthSessionInvalidError(DETAIL_BLANK_EMAIL)

        return SessionClaims(
            user_id=user_id,
            email=email,
            issued_at=_to_datetime(payload[CLAIM_ISSUED_AT]),
            expires_at=_to_datetime(payload[CLAIM_EXPIRES_AT]),
        )


def _to_datetime(value: Any) -> datetime:
    """NumericDate → timezone-aware UTC datetime.

    `require` + PyJWT's own verification guarantee these claims are present and
    numeric by the time this runs.
    """
    return datetime.fromtimestamp(int(value), tz=UTC)


__all__ = [
    "ALLOWED_ALGORITHMS",
    "MIN_KEY_BYTES_BY_ALGORITHM",
    "IssuedToken",
    "JWTService",
    "SessionClaims",
]

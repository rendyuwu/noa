"""Login + session resolution.

Ported from `noa-old` branch `MCP` (`core/auth/auth_service.py`, C13). This is the
one place the three auth mechanisms meet: LDAP says whether the operator is employed
(C4, T6), the `users` row says whether NOA has activated them (V7), and `JWTService`
mints the cookie credential.

Two entry points, and the second one is not an optimization:

- `authenticate()` — the login path.
- `resolve_session_user()` — run on *every* session-authenticated request. V6 records
  that the session JWT has no revocation path before `exp`: there is no `jti`, no
  denylist, and V4's cascade revoke covers `mcp_tokens` only. So re-reading
  `users.is_active` here is the ONLY thing that bounds a disabled operator's live
  session. Caching it, trusting a claim, or skipping it on a "cheap" route reopens an
  unbounded window (default `AUTH_JWT_ACCESS_TOKEN_TTL_SECONDS` = 3600).

Departures from `noa-old`, each with a test:

1. **A directory outage does not consume rate-limit budget.** `noa-old` called
   `record_failure()` for every `AuthError` LDAP raised, so a `LdapUnavailableError`
   blip counted as five wrong passwords and locked every operator out for the whole
   block duration — an availability failure amplified into a lockout. Counters move
   for credential guesses only (`AuthInvalidCredentialsError`).
2. **The transaction boundary is explicit.** `noa-old`'s FastAPI dependency inspected
   `getattr(exc, "error_code", None) == "user_pending_approval"` to decide whether to
   commit, because a first login must persist its new row *and* raise 403. Here
   `authenticate()` commits after provisioning and before the activation gate, so the
   rule sits where the rule is, and no caller sniffs exception types.
3. **A recorded failure is committed before the error propagates.** Otherwise the
   request's rollback discards the counter and V9 never blocks anything — the limiter
   would look correct in isolation and do nothing in production.
4. **Session re-read keys on the `uid` claim, not `sub`.** See
   `SQLAuthRepository.get_user_by_id`.

V8 throughout: the password is a parameter and nothing else. It is never logged,
returned, stored, or placed in an error message, and the session token leaves here
inside `IssuedToken` for the cookie only — never in a response body.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from core.auth.auth_repository import AuthRepository, AuthUserRecord
from core.auth.errors import (
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
    AuthSessionInvalidError,
)
from core.auth.jwt_service import IssuedToken, JWTService
from core.auth.ldap_service import LdapUser
from core.auth.login_rate_limiter import UNKNOWN_IP, LoginRateLimiter
from core.db.models import ADMIN_ROLE_NAME

# Internal diagnostics for the `detail` slot: logs only, never a response body.
DETAIL_BLANK_INPUT = "blank email or password; ⊥ directory bind attempted"
DETAIL_PENDING_APPROVAL = "`users.is_active` is False; awaiting admin activation"
DETAIL_SESSION_USER_GONE = "`uid` claim resolves to no `users` row"


class DirectoryAuthenticator(Protocol):
    """The slice of `LDAPService` the login path uses."""

    async def authenticate(self, email: str, password: str) -> LdapUser: ...


@dataclass(frozen=True)
class SessionUser:
    """Who the caller is, as of this request.

    Built from a fresh row read every time, so `is_active` and `roles` reflect the
    database rather than the cookie.
    """

    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str]


@dataclass(frozen=True)
class AuthenticatedSession:
    """Result of a successful login: the cookie credential plus who it belongs to.

    `issued` goes to `JWTService.set_session_cookie` and nowhere near a response body
    — the browser gets the token as an httpOnly cookie, the JSON gets the user.
    """

    issued: IssuedToken
    user: SessionUser


class AuthService:
    """LDAP login, NOA activation, and per-request session resolution."""

    def __init__(
        self,
        *,
        repository: AuthRepository,
        directory: DirectoryAuthenticator,
        jwt_service: JWTService,
        rate_limiter: LoginRateLimiter,
        bootstrap_admin_emails: Iterable[str],
    ) -> None:
        self._repository = repository
        self._directory = directory
        self._jwt_service = jwt_service
        self._rate_limiter = rate_limiter
        self._bootstrap_admin_emails = {email.strip().lower() for email in bootstrap_admin_emails}

    # --- Login ---

    async def authenticate(
        self, *, email: str, password: str, source_ip: str | None = None
    ) -> AuthenticatedSession:
        """Authenticate against LDAP, provision/activate, mint the session token.

        Raises, in the order the gates run: `AuthRateLimitedError`,
        `AuthInvalidCredentialsError` / `AuthAccountDisabledError` /
        `LdapUnavailableError` from the directory, then
        `AuthPendingApprovalError` when NOA has not activated the row.
        """
        normalized_email = email.strip().lower()
        if not normalized_email or not password:
            # Before the limiter, as in `noa-old`: an empty form submit guesses
            # nothing, so spending block budget on it would only lock out operators
            # who mis-clicked.
            raise AuthInvalidCredentialsError(DETAIL_BLANK_INPUT)

        ip_address = (source_ip or "").strip() or UNKNOWN_IP
        await self._rate_limiter.assert_allowed(email=normalized_email, ip_address=ip_address)

        ldap_user = await self._authenticate_against_directory(
            normalized_email, password, ip_address
        )

        user = await self._provision(normalized_email, ldap_user)
        # Commit BEFORE the activation gate: a first login provisions an inactive row
        # and then raises, and that row is what an admin enables. Rolling it back
        # would make first login a silent no-op the operator can only retry.
        await self._repository.commit()

        if not user.is_active:
            raise AuthPendingApprovalError(DETAIL_PENDING_APPROVAL)

        user = await self._repository.update_user(user, last_login_at=datetime.now(UTC))
        await self._rate_limiter.record_success(email=normalized_email, ip_address=ip_address)
        roles = await self._repository.get_role_names(user.id)
        await self._repository.commit()

        return AuthenticatedSession(
            issued=self._jwt_service.create_access_token(email=user.email, user_id=user.id),
            user=self._to_session_user(user, roles),
        )

    # --- Per-request session resolution ---

    async def resolve_session_user(self, user_id: UUID) -> SessionUser:
        """Re-read the row a verified session cookie points at.

        `AuthSessionInvalidError` when the row is gone: a correctly signed cookie for a
        deleted operator is not a session, and 401 sends the browser to sign in rather
        than showing an empty panel.

        `AuthPendingApprovalError` when inactive — one error code for both the
        never-activated operator and the one an admin just disabled, because the
        schema carries nothing that separates them and inventing two messages would be
        a guess about which happened.
        """
        user = await self._repository.get_user_by_id(user_id)
        if user is None:
            raise AuthSessionInvalidError(DETAIL_SESSION_USER_GONE)

        if not user.is_active:
            raise AuthPendingApprovalError(DETAIL_PENDING_APPROVAL)

        roles = await self._repository.get_role_names(user.id)
        return self._to_session_user(user, roles)

    # --- Internals ---

    async def _authenticate_against_directory(
        self, email: str, password: str, ip_address: str
    ) -> LdapUser:
        """Bind against LDAP; count the attempt only if it was a credential guess.

        `AuthInvalidCredentialsError` covers both "no such entry" and "bind rejected"
        (T6 merges them so login is not an enumeration oracle), which is exactly the
        set worth rate limiting. `LdapUnavailableError`, `AuthConfigurationError` and
        `AuthAccountDisabledError` pass through untouched: the first two are NOA's or
        the network's fault, and the third already proved the password.
        """
        try:
            return await self._directory.authenticate(email, password)
        except AuthInvalidCredentialsError:
            await self._rate_limiter.record_failure(email=email, ip_address=ip_address)
            # Commit the counter: the caller's error path rolls the session back, and
            # a rolled-back counter is a limiter that never limits.
            await self._repository.commit()
            raise

    async def _provision(self, email: str, ldap_user: LdapUser) -> AuthUserRecord:
        """Create or refresh the `users` row for an operator LDAP just vouched for.

        V7: a new row lands `is_active=False` and waits for an admin. The single
        exception is a bootstrap admin from `AUTH_BOOTSTRAP_ADMIN_EMAILS`, who is
        activated and given the `admin` role — otherwise a fresh deployment has nobody
        able to activate anybody.
        """
        user = await self._repository.get_user_by_email(email)
        is_bootstrap_admin = email in self._bootstrap_admin_emails

        if user is None:
            user = await self._repository.create_user(
                email=email,
                ldap_dn=ldap_user.dn,
                display_name=ldap_user.display_name,
                is_active=is_bootstrap_admin,
            )
        else:
            user = await self._repository.update_user(
                user,
                ldap_dn=ldap_user.dn,
                display_name=ldap_user.display_name,
            )

        if is_bootstrap_admin:
            await self._repository.ensure_role(ADMIN_ROLE_NAME)
            await self._repository.assign_role(user.id, ADMIN_ROLE_NAME)
            # Also re-activates a bootstrap admin an admin disabled. Deliberate: the
            # env var is the deployment's break-glass, and it must not be defeatable
            # from inside the app.
            if not user.is_active:
                user = await self._repository.update_user(user, is_active=True)

        return user

    @staticmethod
    def _to_session_user(user: AuthUserRecord, roles: list[str]) -> SessionUser:
        return SessionUser(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            is_active=user.is_active,
            roles=roles,
        )


__all__ = [
    "AuthService",
    "AuthenticatedSession",
    "DirectoryAuthenticator",
    "SessionUser",
]

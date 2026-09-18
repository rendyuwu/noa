"""Auth error taxonomy.

Ported, not imported, from `noa-old` branch `MCP` (`core/auth/errors.py`), plus
`LdapUnavailableError` and `AuthAccountDisabledError` — new work for the token
revalidation fail-closed rule and the LDAP employment check.

Every class carries three things, and the split matters:

- `error_code` — stable machine string for the error envelope. Clients and
  tests branch on this, never on prose.
- `message` — what the operator reads. Says what happened and who can fix it, so
  nobody files a ticket for a problem they could clear themselves. Safe to render:
  no credential, no directory internals, no config state.
- `detail` — optional internal cause for logs only. Defaults to `message`.
  `str(exc)` yields it, so tracebacks stay useful while responses stay clean.

Route handlers send `error_code` + `message`. They must not send `detail`.

Login denials that operators actually hit, and why each is its own class:

- `AuthInvalidCredentialsError` — wrong password OR no such account. Deliberately
  ONE message for both. Separate texts here would let anyone test whether an
  address exists, so the vagueness is the feature (never an enumeration oracle).
- `AuthAccountDisabledError` — directory disabled the account: employment ended,
  and the directory is source of truth for it. Terminal, and NOA cannot override it.
  Raised post-bind only, so it never reveals account state to an unauthenticated caller.
- `AuthPendingApprovalError` — directory is happy, but this NOA row is
  `is_active=False`. Every new LDAP user lands here on first login and an
  admin activates them. Recoverable, NOA-side only: the login flow raises it, the LDAP
  service never does.
- `AuthConfigurationError` — NOA is misconfigured. The operator's credentials are
  fine and retrying will not help, so the message says so and points at an
  administrator rather than sending them hunting their own password.
- `LdapUnavailableError` — directory unreachable. Message invites a retry, because
  unlike the others this one usually clears on its own. Callers fail closed but
  must NOT read it as "user gone" and cascade-revoke tokens.
- `AuthRateLimitedError` — too many failed attempts. Carries
  `retry_after_seconds` because the handler owes the client a `Retry-After` header,
  and a "try again later" with no number is a client-side guessing game.

Session-cookie denials, distinct from login denials above:

- `AuthSessionExpiredError` — routine: the token aged out. Recoverable by
  signing in again.
- `AuthSessionInvalidError` — cookie absent, malformed, or badly signed.

Both exist because `noa-old` reused `AuthInvalidCredentialsError` for a stale
cookie, which told operators their password was wrong when it was not.

`AuthError` derives from `core.errors.NoaError`, which owns the three-field
shape. Authentication keeps its own base so the handler can treat an unclassified
*authentication* failure as an infrastructure answer (503) while an unclassified
authorization failure is a different question entirely.
"""

from __future__ import annotations

from core.errors import NoaError, RetryAfterMixin


class AuthError(NoaError):
    """Base for every auth failure.

    Subclasses override `error_code` and `message`. Callers pass `detail` when
    there is internal context worth logging.
    """

    error_code: str = "auth_failed"
    message: str = "Sign-in failed. Contact an administrator if this continues."


class AuthInvalidCredentialsError(AuthError):
    """Wrong password, or no such account. One message for both.

    Naming which half failed would turn login into an account-enumeration oracle,
    so this text stays deliberately ambiguous.
    """

    error_code: str = "invalid_credentials"
    message: str = "Email or password is incorrect."


class AuthAccountDisabledError(AuthError):
    """Directory disabled the account: employment ended. Terminal.

    Distinct from `AuthPendingApprovalError`: enabling the NOA row would not help, and must not.
    """

    error_code: str = "ldap_account_disabled"
    message: str = (
        "This account is disabled in the company directory. Contact IT if you believe "
        "this is a mistake."
    )


class AuthPendingApprovalError(AuthError):
    """NOA row is `is_active=False`. First login lands here until an admin enables.

    NOA-side state, so `LDAPService` never raises this — the login flow does, after the
    directory has already vouched for the operator.
    """

    error_code: str = "user_pending_approval"
    message: str = (
        "Your sign-in worked, but this account is waiting for administrator approval. "
        "Ask a NOA admin to enable it."
    )


class AuthSessionExpiredError(AuthError):
    """Session token past its `exp`. Expected, not a fault — sessions are short.

    Split from `AuthSessionInvalidError` so the UI can say "session ended" without
    implying tampering, and so logs distinguish routine expiry from a forged cookie.
    """

    error_code: str = "session_expired"
    message: str = "Your session has expired. Sign in again."


class AuthSessionInvalidError(AuthError):
    """Session cookie absent, malformed, or badly signed.

    Same 401 and same remedy as expiry, so the message stays vague: a caller
    presenting a forged cookie learns nothing about why it failed.
    """

    error_code: str = "session_invalid"
    message: str = "Your session is no longer valid. Sign in again."


class AuthConfigurationError(AuthError):
    """NOA-side misconfiguration — never the operator's fault, never their credentials.

    Message avoids directory internals on purpose: an operator learns retrying is
    pointless, an attacker learns nothing about the deployment. `detail` carries
    the specific fault for the logs.
    """

    error_code: str = "auth_misconfigured"
    message: str = (
        "Sign-in is misconfigured on the NOA side. Your credentials are fine — "
        "contact an administrator."
    )


class LdapUnavailableError(AuthError):
    """Directory unreachable. Deny the request; never conclude the user is gone."""

    error_code: str = "ldap_unavailable"
    message: str = "Cannot reach the company directory right now. Try again in a few moments."


class AuthRateLimitedError(RetryAfterMixin, AuthError):
    """Login blocked: too many failed attempts in the window.

    Says nothing about whether the address exists or the password was close — the
    limiter counts attempts, not outcomes, so this text stays as uninformative as
    `AuthInvalidCredentialsError`.

    `RetryAfterMixin` is what makes the shared handler emit `Retry-After`; the MCP path's
    `McpAuthRateLimitedError` carries the same marker.
    """

    error_code: str = "login_rate_limited"
    message: str = "Too many sign-in attempts. Wait a few minutes and try again."

    def __init__(self, retry_after_seconds: int, detail: str | None = None) -> None:
        # Floored at 1: a block with under a second left truncates to 0, and
        # `Retry-After: 0` tells the client to retry immediately — the opposite of
        # what a rate limit means.
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(detail)

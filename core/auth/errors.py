"""Auth error taxonomy (T6).

Ported from `noa-old` branch `MCP` (`core/auth/errors.py`, C13), plus
`LdapUnavailableError` and `AuthAccountDisabledError` — new work for V4's
fail-closed rule and C4's employment check.

Every class carries three things, and the split matters:

- `error_code` — stable machine string for the error envelope (V73). Clients and
  tests branch on this, never on prose.
- `message` — what the operator reads. Says what happened and who can fix it, so
  nobody files a ticket for a problem they could clear themselves. Safe to render:
  no credential, no directory internals, no config state (V8).
- `detail` — optional internal cause for logs only. Defaults to `message`.
  `str(exc)` yields it, so tracebacks stay useful while responses stay clean.

Route handlers send `error_code` + `message`. They must not send `detail`.

Login denials that operators actually hit, and why each is its own class:

- `AuthInvalidCredentialsError` — wrong password OR no such account. Deliberately
  ONE message for both. Separate texts here would let anyone test whether an
  address exists, so the vagueness is the feature (⊥ enumeration oracle).
- `AuthAccountDisabledError` — directory disabled the account: employment ended
  (C4). Terminal, and NOA cannot override it. Raised post-bind only, so it never
  reveals account state to an unauthenticated caller.
- `AuthPendingApprovalError` — directory is happy, but this NOA row is
  `is_active=False`. Every new LDAP user lands here on first login (V7) and an
  admin activates them. Recoverable, NOA-side only: T8 raises it, T6 never does.
- `AuthConfigurationError` — NOA is misconfigured. The operator's credentials are
  fine and retrying will not help, so the message says so and points at an
  administrator rather than sending them hunting their own password.
- `LdapUnavailableError` — directory unreachable. Message invites a retry, because
  unlike the others this one usually clears on its own. Callers fail closed but
  must NOT read it as "user gone" and cascade-revoke tokens (V4).
"""

from __future__ import annotations


class AuthError(Exception):
    """Base for every auth failure.

    Subclasses override `error_code` and `message`. Callers pass `detail` when
    there is internal context worth logging.
    """

    error_code: str = "auth_failed"
    message: str = "Sign-in failed. Contact an administrator if this continues."

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.message
        super().__init__(self.detail)


class AuthInvalidCredentialsError(AuthError):
    """Wrong password, or no such account. One message for both.

    Naming which half failed would turn login into an account-enumeration oracle,
    so this text stays deliberately ambiguous.
    """

    error_code: str = "invalid_credentials"
    message: str = "Email or password is incorrect."


class AuthAccountDisabledError(AuthError):
    """Directory disabled the account: employment ended (C4). Terminal.

    Distinct from `AuthPendingApprovalError`: enabling the NOA row would not help,
    and must not.
    """

    error_code: str = "ldap_account_disabled"
    message: str = (
        "This account is disabled in the company directory. Contact IT if you believe "
        "this is a mistake."
    )


class AuthPendingApprovalError(AuthError):
    """NOA row is `is_active=False`. First login lands here until an admin enables (V7).

    NOA-side state, so `LDAPService` never raises this — T8 does, after the
    directory has already vouched for the operator.
    """

    error_code: str = "user_pending_approval"
    message: str = (
        "Your sign-in worked, but this account is waiting for administrator approval. "
        "Ask a NOA admin to enable it."
    )


class AuthConfigurationError(AuthError):
    """NOA-side misconfiguration — ⊥ the operator's fault, ⊥ their credentials.

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
    """Directory unreachable. Deny the request; ⊥ conclude the user is gone (V4)."""

    error_code: str = "ldap_unavailable"
    message: str = "Cannot reach the company directory right now. Try again in a few moments."


__all__ = [
    "AuthAccountDisabledError",
    "AuthConfigurationError",
    "AuthError",
    "AuthInvalidCredentialsError",
    "AuthPendingApprovalError",
    "LdapUnavailableError",
]

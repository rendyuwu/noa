"""Shared auth building blocks (C12, V66).

`LDAPService` (T6), `JWTService` (T7) and `AuthService` (T8) land first; RBAC (T9) and
MCP identity resolution (T11-T12) join them here so all three deployables read one
implementation.

Two credentials live in this package and never mix:

- session JWT in the `noa_session` cookie — admin panel + embed, LDAP-backed,
  short-lived (V6). `JWTService` owns mint, verify, and the cookie itself.
- MCP bearer token — opaque, hashed at rest, no JWT (C5, V2). T10-T12.

`AuthService` is where the mechanisms meet: it authenticates against the directory,
applies NOA's activation gate (V7), rate limits attempts (V9), and re-reads
`users.is_active` on every session-authenticated request — the only bound V6 leaves
on a disabled operator's live session.
"""

from core.auth.auth_repository import (
    AuthRepository,
    AuthUserRecord,
    SQLAuthRepository,
    SQLLoginRateLimitRepository,
)
from core.auth.auth_service import (
    AuthenticatedSession,
    AuthService,
    DirectoryAuthenticator,
    SessionUser,
)
from core.auth.errors import (
    AuthAccountDisabledError,
    AuthConfigurationError,
    AuthError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
    AuthRateLimitedError,
    AuthSessionExpiredError,
    AuthSessionInvalidError,
    LdapUnavailableError,
)
from core.auth.jwt_service import IssuedToken, JWTService, SessionClaims
from core.auth.ldap_service import LDAP_AVAILABLE, LDAPService, LdapUser
from core.auth.login_rate_limiter import (
    LoginRateLimitBucket,
    LoginRateLimiter,
    LoginRateLimitRepository,
)

__all__ = [
    "LDAP_AVAILABLE",
    "AuthAccountDisabledError",
    "AuthConfigurationError",
    "AuthError",
    "AuthInvalidCredentialsError",
    "AuthPendingApprovalError",
    "AuthRateLimitedError",
    "AuthRepository",
    "AuthService",
    "AuthSessionExpiredError",
    "AuthSessionInvalidError",
    "AuthUserRecord",
    "AuthenticatedSession",
    "DirectoryAuthenticator",
    "IssuedToken",
    "JWTService",
    "LDAPService",
    "LdapUnavailableError",
    "LdapUser",
    "LoginRateLimitBucket",
    "LoginRateLimitRepository",
    "LoginRateLimiter",
    "SQLAuthRepository",
    "SQLLoginRateLimitRepository",
    "SessionClaims",
    "SessionUser",
]

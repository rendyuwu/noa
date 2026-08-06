"""Shared auth building blocks (C12, V66).

`LDAPService` (T6) and `JWTService` (T7) land first; RBAC (T9) and MCP identity
resolution (T11-T12) join them here so all three deployables read one
implementation.

Two credentials live in this package and never mix:

- session JWT in the `noa_session` cookie — admin panel + embed, LDAP-backed,
  short-lived (V6). `JWTService` owns mint, verify, and the cookie itself.
- MCP bearer token — opaque, hashed at rest, no JWT (C5, V2). T10-T12.

`AuthPendingApprovalError` is re-exported here though T6 never raises it: the NOA
activation gate (V7) is T8's, and both gates belong to one taxonomy so a caller
cannot handle employment without seeing activation next to it.
"""

from core.auth.errors import (
    AuthAccountDisabledError,
    AuthConfigurationError,
    AuthError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
    AuthSessionExpiredError,
    AuthSessionInvalidError,
    LdapUnavailableError,
)
from core.auth.jwt_service import IssuedToken, JWTService, SessionClaims
from core.auth.ldap_service import LDAP_AVAILABLE, LDAPService, LdapUser

__all__ = [
    "LDAP_AVAILABLE",
    "AuthAccountDisabledError",
    "AuthConfigurationError",
    "AuthError",
    "AuthInvalidCredentialsError",
    "AuthPendingApprovalError",
    "AuthSessionExpiredError",
    "AuthSessionInvalidError",
    "IssuedToken",
    "JWTService",
    "LDAPService",
    "LdapUnavailableError",
    "LdapUser",
    "SessionClaims",
]

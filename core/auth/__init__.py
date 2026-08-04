"""Shared auth building blocks (C12, V66).

`LDAPService` (T6) lands first; JWT (T7), RBAC (T9), and MCP identity resolution
(T11-T12) join it here so all three deployables read one implementation.

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
    LdapUnavailableError,
)
from core.auth.ldap_service import LDAP_AVAILABLE, LDAPService, LdapUser

__all__ = [
    "LDAP_AVAILABLE",
    "AuthAccountDisabledError",
    "AuthConfigurationError",
    "AuthError",
    "AuthInvalidCredentialsError",
    "AuthPendingApprovalError",
    "LDAPService",
    "LdapUnavailableError",
    "LdapUser",
]

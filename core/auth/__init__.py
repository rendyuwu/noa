"""Shared auth building blocks (C12, V66).

`LDAPService` (T6), `JWTService` (T7), `AuthService` (T8) and the RBAC engine (T9) live
here; MCP identity resolution (T11-T12) joins them so all three deployables read one
implementation.

Authentication and authorization are separate taxonomies on purpose. `AuthError` means "we
do not know who you are" and its unclassified case is an infrastructure answer (503);
`AuthorizationError` means "we know, and no". Both derive from `core.errors.NoaError`, so
one handler shapes both responses (V73).

Two credentials live in this package and never mix:

- session JWT in the `noa_session` cookie — admin panel + embed, LDAP-backed,
  short-lived (V6). `JWTService` owns mint, verify, and the cookie itself.
- MCP bearer token — opaque, hashed at rest, no JWT (C5, V2). `McpTokenService` owns mint,
  list and revoke (T10); T11-T12 add the verify path on top of `hash_mcp_token`.

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
from core.auth.authorization_errors import (
    AdminAccessRequiredError,
    AuthorizationError,
    InternalRoleError,
    InvalidRoleNameError,
    LastActiveAdminError,
    ReservedRoleError,
    RoleNotFoundError,
    SelfDeactivateAdminError,
    SelfDeleteAdminError,
    SelfDeleteError,
    SelfRemoveAdminRoleError,
    UnknownRoleError,
    UnknownToolError,
    UserNotFoundError,
)
from core.auth.authorization_repository import SQLAuthorizationRepository
from core.auth.authorization_service import AuthorizationService
from core.auth.authorization_types import (
    AuthorizationRepository,
    AuthorizationUserRecord,
    AuthorizedUser,
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
from core.auth.mcp_token_errors import (
    InvalidTokenLabelError,
    McpTokenError,
    McpTokenNotFoundError,
)
from core.auth.mcp_token_repository import SQLMcpTokenRepository
from core.auth.mcp_token_service import (
    McpTokenRepository,
    McpTokenService,
    McpTokenView,
    MintedMcpToken,
    generate_mcp_token,
    hash_mcp_token,
)
from core.auth.tool_catalog import NEVER_IMPLEMENT_TOOLS, TOOL_CATALOG, is_known_tool

__all__ = [
    "LDAP_AVAILABLE",
    "NEVER_IMPLEMENT_TOOLS",
    "TOOL_CATALOG",
    "AdminAccessRequiredError",
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
    "AuthorizationError",
    "AuthorizationRepository",
    "AuthorizationService",
    "AuthorizationUserRecord",
    "AuthorizedUser",
    "DirectoryAuthenticator",
    "InternalRoleError",
    "InvalidRoleNameError",
    "InvalidTokenLabelError",
    "IssuedToken",
    "JWTService",
    "LDAPService",
    "LastActiveAdminError",
    "LdapUnavailableError",
    "LdapUser",
    "LoginRateLimitBucket",
    "LoginRateLimitRepository",
    "LoginRateLimiter",
    "McpTokenError",
    "McpTokenNotFoundError",
    "McpTokenRepository",
    "McpTokenService",
    "McpTokenView",
    "MintedMcpToken",
    "ReservedRoleError",
    "RoleNotFoundError",
    "SQLAuthRepository",
    "SQLAuthorizationRepository",
    "SQLLoginRateLimitRepository",
    "SQLMcpTokenRepository",
    "SelfDeactivateAdminError",
    "SelfDeleteAdminError",
    "SelfDeleteError",
    "SelfRemoveAdminRoleError",
    "SessionClaims",
    "SessionUser",
    "UnknownRoleError",
    "UnknownToolError",
    "UserNotFoundError",
    "generate_mcp_token",
    "hash_mcp_token",
    "is_known_tool",
]

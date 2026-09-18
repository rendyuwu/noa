"""Shared auth building blocks.

`LDAPService`, `JWTService`, `AuthService` and the RBAC engine live
here; MCP identity resolution joins them so all three deployables read one
implementation.

Authentication and authorization are separate taxonomies on purpose. `AuthError` means "we
do not know who you are" and its unclassified case is an infrastructure answer (503);
`AuthorizationError` means "we know, and no". Both derive from `core.errors.NoaError`, so
one handler shapes both responses.

Two credentials live in this package and never mix:

- session JWT in the `noa_session` cookie — admin panel + embed, LDAP-backed,
  short-lived. `JWTService` owns mint, verify, and the cookie itself.
- MCP bearer token — opaque, hashed at rest, no JWT. `McpTokenService` owns mint,
  list and revoke; the token verifier and identity resolver add the verify path
  on top of `hash_mcp_token`.

`AuthService` is where the mechanisms meet: it authenticates against the directory,
applies NOA's activation gate, rate limits attempts, and re-reads
`users.is_active` on every session-authenticated request — the only bound a
non-revocable session JWT leaves on a disabled operator's live session.
"""

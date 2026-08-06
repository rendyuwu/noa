"""Rate limiting for failed MCP authentication (T12, V9).

New work — `noa-old` had no MCP credential to limit. The counting lives in
`core.auth.attempt_limiter.AttemptLimiter`, shared with the login path (V66); this module
decides the two things that are specific to the MCP surface: which keys an attempt lands
in, and which denials count as attempts at all.

**No source-IP bucket.** The login limiter keys one bucket on the client address; that key
does not transfer here. NOA runs inside a Kubernetes cluster behind many workers, so the
address a request appears to come from is neither stable across pod churn nor
discriminating between operators — and C24 makes LibreChat the *sole* MCP client, so every
legitimate request arrives from the same handful of addresses. A block on that key would
be a fleet-wide outage triggered by one bad token, which is a worse failure than the abuse
it prevents.

The two keys below are stable in that environment and answer different attacks, which is
why both exist (the same reasoning as login's IP+email pair — one key alone leaves a hole):

- `mcp_client` — the `X-Noa-LibreChat-User` value, required on every request (C24, V3).
  Catches an operator inside LibreChat working through guesses; the blast radius of a
  block is that one LibreChat account.
- `mcp_token` — the SHA-256 digest of the presented bearer, the same digest `mint()`
  stores (V2). Catches replay of one stolen token while the header is rotated to dodge the
  bucket above. The digest, never the plaintext: this value is the class of thing
  `mcp_tokens.token_hash` already holds at rest, and V2/V8 forbid the other one.

Note what each key does *not* do, so neither is mistaken for more than it is: a caller who
varies both keys gets a fresh bucket every request and is never blocked. Against a token
guesser that is fine — the digest of each guess differs anyway, so no bucket could
accumulate, and 256 bits of entropy is the actual defence. V9 asks for attempt limiting,
not bot detection.

**What counts.** `COUNTED_DENIALS` is the narrow set, and the exclusions are the
interesting part:

- `mcp_token_expired` — a genuine token that aged out. Counting it would block an operator
  for holding an old credential.
- `librechat_user_header_missing` — a misconfigured client, not a guess.
- `mcp_user_inactive`, `mcp_user_not_in_directory` — authenticated, then refused. The
  credential was real; rate limiting is the wrong answer.
- `LdapUnavailableError` — an outage. This is T8's lesson written down: `noa-old` counted
  every `AuthError` from LDAP, so a directory blip locked every operator out for the full
  block duration.

**No `record_success`.** The login limiter clears its buckets on a successful sign-in; this
one does not. The window ages a bucket out on its own, and every authenticated MCP request
already carries two writes (`last_used_at`, and often `last_ldap_check_at`) — two more
DELETEs per request to clear buckets that are usually absent is a cost with no matching
benefit, since a legitimate client's bucket only fills if its own token is bad.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from core.auth.attempt_limiter import AttemptKey, AttemptLimiter
from core.auth.errors import LdapUnavailableError
from core.auth.mcp_auth_errors import (
    LibreChatUserMismatchError,
    McpAuthError,
    McpAuthRateLimitedError,
    McpTokenInvalidError,
)

# Bucket scopes. Values reach `login_rate_limits.scope` (String(20)) and are stable —
# renaming one orphans every live bucket under the old name. The table is named for the
# login path it was built for (T8); it holds a generic (scope, key) counter and T12 reuses
# it rather than duplicating the schema (V66). See `core.db.models.LoginRateLimit`.
SCOPE_MCP_CLIENT: Final = "mcp_client"
# S105: a bucket scope name, not a credential — the `TOKEN` in the constant name trips it.
SCOPE_MCP_TOKEN: Final = "mcp_token"  # noqa: S105

SCOPES: Final = (SCOPE_MCP_CLIENT, SCOPE_MCP_TOKEN)

DETAIL_BLOCKED = "mcp auth blocked by rate limiter"

# Credential-class denials only — see the module docstring for every exclusion and why.
COUNTED_DENIALS: Final[tuple[type[McpAuthError], ...]] = (
    McpTokenInvalidError,
    LibreChatUserMismatchError,
)


def counts_against_limit(error: McpAuthError | LdapUnavailableError) -> bool:
    """Whether `error` is the kind of refusal that moves a counter (V9).

    A function rather than a set membership test so subclasses inherit their parent's
    answer, matching how `noa_api.api.errors.status_for` walks the MRO. `LdapUnavailableError`
    is not in `COUNTED_DENIALS` and is not an `McpAuthError`, so it answers `False` here by
    construction rather than by a caller remembering to skip it.
    """
    return isinstance(error, COUNTED_DENIALS)


class McpAuthRateLimiter(AttemptLimiter):
    """Count failed MCP authentications per LibreChat account and per presented token."""

    async def assert_allowed(
        self,
        *,
        librechat_user_id: str | None,
        token_digest: str,
        now: datetime | None = None,
    ) -> None:
        """Raise `McpAuthRateLimitedError` when either bucket is still blocked."""
        await self._assert_keys_allowed(self._keys(librechat_user_id, token_digest), now=now)

    async def record_failure(
        self,
        *,
        librechat_user_id: str | None,
        token_digest: str,
        now: datetime | None = None,
    ) -> None:
        """Count one failed attempt against every key this request supplied."""
        await self._record_keys_failure(self._keys(librechat_user_id, token_digest), now=now)

    # --- Internals ---

    def _blocked_error(self, retry_after_seconds: int, scope: str) -> McpAuthRateLimitedError:
        return McpAuthRateLimitedError(retry_after_seconds, f"{DETAIL_BLOCKED} on {scope}")

    @classmethod
    def _keys(cls, librechat_user_id: str | None, token_digest: str) -> tuple[AttemptKey, ...]:
        """(scope, key) pairs for one attempt.

        An absent or blank `librechat_user_id` yields no client key at all, rather than
        folding into a shared "unknown" bucket the way login's blank IP does. That request
        is refused by V3 for the missing header — an uncounted denial — so a bucket for it
        would only ever collect requests that never reached the token gates, and every such
        client would share one counter and block each other.
        """
        keys: list[AttemptKey] = []
        client_key = (librechat_user_id or "").strip()
        if client_key:
            keys.append((SCOPE_MCP_CLIENT, client_key))
        keys.append((SCOPE_MCP_TOKEN, cls._normalize_key(token_digest)))
        return tuple(keys)


__all__ = [
    "COUNTED_DENIALS",
    "SCOPES",
    "SCOPE_MCP_CLIENT",
    "SCOPE_MCP_TOKEN",
    "McpAuthRateLimiter",
    "counts_against_limit",
]

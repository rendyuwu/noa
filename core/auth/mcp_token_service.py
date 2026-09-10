"""MCP bearer token mint / list / revoke.

New work, not a port: `noa-old` had no per-user MCP credential — its `/mcp` surface was
reached from NOA's own chat, so there was nothing to mint. The shapes here follow the login
flow's `AuthService` and the RBAC engine's `AuthorizationService` (Protocol repository, frozen
result dataclasses, an audit event per mutation) so the three read the same.

This is the *MCP* credential and it shares nothing with the session JWT: opaque
rather than signed, hashed at rest rather than verified by key, and revocable by deleting
one row rather than not at all. Keeping them separate is what lets `mcp_tokens` carry a
real revocation story while the session cookie has none.

Three properties the implementation is shaped around:

1. **The plaintext exists for exactly one call.** `mint()` returns it, nothing stores it,
   and `McpTokenView` — the shape every read path returns — has no field that could hold
   it. A response serializer therefore cannot leak one by accident. `MintedMcpToken`
   hides it from `repr()` for the same reason: a dataclass printed into a log line or a
   traceback would otherwise publish the credential — credentials never get logged, never
   land in an error body.
2. **Hashing lives in one function.** The token verifier's `verify_token` hashes the
   *presented* bearer and looks the digest up; if it computed the digest differently from
   `mint()`, every token would silently fail to authenticate. `hash_mcp_token` is that single
   implementation.
3. **Revoke is scoped by user in the query, not checked afterwards.** `/me/mcp-tokens/{id}`
   and `/admin/users/{id}/tokens/{token_id}` both call `revoke(user_id, token_id)`,
   so neither can delete a row belonging to someone else by guessing an id, and a foreign
   id answers exactly as an unknown one does — the same requester-match principle that keeps
   existence from leaking elsewhere.
4. **Every mutation commits, and the commit is the last thing it does**. Added
   with the routes rather than when this service first landed, and the gap in between is the
   lesson: the repository flushed, `noa_api.api.deps.get_db_session` never commits, and no
   test could see the difference because a flushed row reads back identically inside its own
   session. The same flush-only rollback hole showed up in the RBAC engine. Both guards raise
   before the commit, so a refused mint persists nothing.

Deliberately NOT here, each owned by a later task: TOFU binding of `librechat_user_id` and
LDAP staleness revalidation (both live in the token verifier — mint leaves the column NULL
until first use binds it), `resolve_mcp_identity`, and cascade revoke on admin
disable. The HTTP routes landed with token management
(`noa_api.api.routes.mcp_tokens`) and hold no policy — every rule is here.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
from uuid import UUID

from core.audit.admin_events import (
    EVENT_MCP_TOKEN_MINTED,
    EVENT_MCP_TOKEN_REVOKED,
    AdminAuditEvent,
    AdminAuditSink,
)
from core.auth.authorization_errors import UserNotFoundError
from core.auth.mcp_token_errors import InvalidTokenLabelError, McpTokenNotFoundError

# Every NOA token starts with this. Not a secret and not parsed on the verify path — it
# exists so a leaked token is recognizable to a secret scanner and to whoever finds it in
# a config file.
# S105: a public prefix every token carries, not a secret — that is the whole point of it.
TOKEN_MARKER: Final = "noa_"  # noqa: S105

# 32 bytes = 256 bits of CSPRNG output, urlsafe-base64 to 43 characters. Well past
# guessing range, which is what lets the digest below go unsalted.
TOKEN_ENTROPY_BYTES: Final = 32

# Stored display fragment: the marker plus 8 random characters. Fits `String(16)` and
# leaves ~208 bits unrevealed, so publishing it in an admin list costs nothing.
TOKEN_PREFIX_LENGTH: Final = len(TOKEN_MARKER) + 8

# `mcp_tokens.label` is `String(255)`. Checked here so an over-long label is a 400
# rather than a database error surfacing as a 500 — same reasoning as the RBAC engine's
# role-name cap.
MAX_LABEL_LENGTH: Final = 255

DETAIL_LABEL_TOO_LONG = f"token label exceeds {MAX_LABEL_LENGTH} characters"


def generate_mcp_token() -> str:
    """A fresh token plaintext. Returned to the operator once, never stored."""
    return f"{TOKEN_MARKER}{secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)}"


def hash_mcp_token(plaintext: str) -> str:
    """SHA-256 hex digest of a token — the only form that reaches the database.

    Unsalted, and that is the right call rather than a shortcut: the input is 256 bits of
    CSPRNG output, so there is no dictionary to defend against, while a per-row salt would
    make the verify path unable to find a row by digest without reading every token
    in the table.

    Not a password hash for the same reason. Argon2/bcrypt buy resistance to offline
    guessing of *low-entropy* inputs; here the input has none of that weakness, and their
    cost would land on every MCP request.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class McpTokenView:
    """One `mcp_tokens` row as every read path returns it.

    Carries no `token_hash` and no plaintext — not redacted, absent. A field that does not
    exist cannot be serialized into a response by a future route that forgets to strip it.

    `librechat_user_id` is NULL until the token verifier binds it on first use. `last_used_at`
    and `last_ldap_check_at` are likewise written by the token verifier's verify path; this
    service only reads them, so a freshly minted token shows all three as `None`.
    """

    id: UUID
    user_id: UUID
    token_prefix: str
    label: str | None
    librechat_user_id: str | None
    last_used_at: datetime | None
    last_ldap_check_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class MintedMcpToken:
    """A newly minted token: the plaintext, once, plus the row it belongs to.

    `repr=False` on `plaintext` is load-bearing. Structlog renders unknown values with
    `repr()`, and so does every traceback frame that shows a local — either would put the
    credential in a log file, and credentials never get logged, never land in an error body.
    The caller has to reach for the field by name, which is the only place it should ever appear.
    """

    plaintext: str = field(repr=False)
    token: McpTokenView


class McpTokenRepository(Protocol):
    """Persistence behind `McpTokenService`.

    `delete_for_user` takes both ids rather than one: the scoping is part of the query,
    not a check the caller may forget (the same requester-match principle that keeps a
    foreign id from leaking, for the 404 shape).

    `commit` is on the Protocol rather than left to the caller. The alternative —
    a route that commits after calling the service — puts the boundary on the *subset of
    mutations today's caller happens to reach*, which is precisely the trap the
    whole-class commit-boundary rule names.
    """

    async def user_exists(self, user_id: UUID) -> bool: ...

    async def insert(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        token_prefix: str,
        label: str | None,
        expires_at: datetime | None,
    ) -> McpTokenView: ...

    async def list_for_user(self, user_id: UUID) -> list[McpTokenView]: ...

    async def delete_for_user(self, user_id: UUID, token_id: UUID) -> bool: ...

    async def commit(self) -> None: ...


class McpTokenService:
    """Mint, list and revoke per-user MCP bearer tokens."""

    def __init__(
        self,
        *,
        repository: McpTokenRepository,
        audit_sink: AdminAuditSink,
        ttl_seconds: int | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit_sink
        # `MCP_TOKEN_TTL_SECONDS`, `None` by default: a token lives until someone deletes
        # the row. An expiry is an extra bound, never the primary one — LDAP staleness
        # revalidation and admin revoke are what actually retire a credential.
        self._ttl_seconds = ttl_seconds

    # --- Mint ---

    async def mint(
        self,
        user_id: UUID,
        *,
        label: str | None = None,
        actor_email: str | None = None,
        now: datetime | None = None,
    ) -> MintedMcpToken:
        """Issue a token for `user_id`; return the plaintext exactly once.

        `UserNotFoundError` for an absent row, rather than letting the foreign key raise:
        an `IntegrityError` reaches the client as a 500 and reads like a broken server
        instead of a stale id.

        An *inactive* user is not refused. Minting before activation is a legitimate
        order for an admin setting someone up, and the credential grants nothing while
        the row says `is_active=False` — a disabled account has zero permissions,
        re-checked at every request. Refusing here would add a rule neither invariant asks for.
        """
        if not await self._repository.user_exists(user_id):
            raise UserNotFoundError(f"no `users` row for `{user_id}`")

        normalized_label = self._validate_label(label)
        plaintext = generate_mcp_token()

        view = await self._repository.insert(
            user_id=user_id,
            token_hash=hash_mcp_token(plaintext),
            token_prefix=plaintext[:TOKEN_PREFIX_LENGTH],
            label=normalized_label,
            expires_at=self._expires_at(now),
        )

        await self._record(EVENT_MCP_TOKEN_MINTED, actor_email, view)
        # Last statement, after both guards and after the audit event — the commit-is-last
        # rule: a refused mint
        # persists nothing, and the plaintext is not handed back until the row it hashes to is
        # durable. Returning one over an uncommitted row is worse than failing — the operator
        # pastes a credential into a config and it authenticates nothing, with no error anywhere.
        await self._repository.commit()
        return MintedMcpToken(plaintext=plaintext, token=view)

    # --- List ---

    async def list_for_user(self, user_id: UUID) -> list[McpTokenView]:
        """This user's tokens, newest first. Prefix, label and timestamps only.

        `UserNotFoundError` rather than an empty list for a deleted operator, matching
        `AuthorizationService.get_permitted_tools`: "no tokens" and "no such user" are
        different answers, and collapsing them makes a stale admin-panel link render as an
        empty page instead of a 404.
        """
        if not await self._repository.user_exists(user_id):
            raise UserNotFoundError(f"no `users` row for `{user_id}`")

        return await self._repository.list_for_user(user_id)

    # --- Revoke ---

    async def revoke(
        self, user_id: UUID, token_id: UUID, *, actor_email: str | None = None
    ) -> None:
        """Delete one token. Revocation is the row's absence, nothing else.

        No tombstone and no `revoked_at` column: the verify path resolves a caller by
        finding a row for the presented digest, so a deleted row is already a 401 and a
        status column would be a second source of truth that could disagree with it.

        The user id is not validated separately — the scoped delete already answers
        "not yours or not there" with one code.
        """
        if not await self._repository.delete_for_user(user_id, token_id):
            raise McpTokenNotFoundError(f"no `mcp_tokens` row `{token_id}` for user `{user_id}`")

        await self._record(
            EVENT_MCP_TOKEN_REVOKED,
            actor_email,
            None,
            token_id=token_id,
            user_id=user_id,
        )
        # The whole-class commit rule: the *class* of mutations commits, not the one a route
        # reaches first. An
        # uncommitted revoke is the dangerous half of the pair — the panel would report the
        # credential gone while the row, and every request it authenticates, survives.
        await self._repository.commit()

    # --- Internals ---

    @staticmethod
    def _validate_label(label: str | None) -> str | None:
        """Strip the label; blank becomes `None`, over-long is refused.

        Blank is not an error: `mcp_tokens.label` is nullable, and an unnamed token is a
        thing an admin may legitimately want.
        """
        if label is None:
            return None

        normalized = label.strip()
        if not normalized:
            return None
        if len(normalized) > MAX_LABEL_LENGTH:
            raise InvalidTokenLabelError(DETAIL_LABEL_TOO_LONG)
        return normalized

    def _expires_at(self, now: datetime | None) -> datetime | None:
        """Absolute expiry from the configured TTL, or `None` for non-expiring."""
        if self._ttl_seconds is None:
            return None
        return (now or datetime.now(UTC)) + timedelta(seconds=self._ttl_seconds)

    async def _record(
        self,
        event_type: str,
        actor_email: str | None,
        view: McpTokenView | None,
        *,
        token_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> None:
        """Record one token change.

        Metadata carries ids, the display prefix and the label — never the plaintext and
        never the digest. The prefix is there so an admin reading the trail can
        match an event to the row they see in the panel.
        """
        resolved_token_id = view.id if view is not None else token_id
        resolved_user_id = view.user_id if view is not None else user_id

        metadata: dict[str, object] = {
            "token_id": str(resolved_token_id),
            "target_user_id": str(resolved_user_id),
        }
        if view is not None:
            metadata["token_prefix"] = view.token_prefix
            metadata["label"] = view.label

        await self._audit.record(
            AdminAuditEvent(
                event_type=event_type,
                actor_email=actor_email,
                target=str(resolved_token_id),
                metadata=metadata,
            )
        )


__all__ = [
    "MAX_LABEL_LENGTH",
    "TOKEN_ENTROPY_BYTES",
    "TOKEN_MARKER",
    "TOKEN_PREFIX_LENGTH",
    "McpTokenRepository",
    "McpTokenService",
    "McpTokenView",
    "MintedMcpToken",
    "generate_mcp_token",
    "hash_mcp_token",
]

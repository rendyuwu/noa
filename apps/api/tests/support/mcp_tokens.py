"""Doubles for the MCP token service.

Same split as `support.rbac`: the in-memory repository covers policy, and
`SQLMcpTokenRepository` gets its own coverage against a live scratch database in
`test_mcp_token_repository.py`. The *real* `McpTokenService` runs against these doubles, so
the mint/list/revoke rules are exercised for real — only the SQL is faked.

`FakeMcpTokenRepository` stores whatever the service hands it, `token_hash` included. That
is the point: a double that dropped the digest could not prove the service never leaks it,
because there would be nothing to leak. The tests assert on `stored_hashes` directly.

It also records commits. An in-memory double has no rollback, so "the row is
there" is true whether or not a boundary exists; `commits` and `committed` are what let a test
assert that a mutation ended in a commit and that a refused one ended in none.

`RecordingAuditSink` is reused from `support.rbac` rather than reimplemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from core.auth.mcp_token_service import McpTokenService, McpTokenView
from support.rbac import RecordingAuditSink

LABEL = "librechat laptop"
OTHER_LABEL = "on-call phone"

# Fixed clock, so an expiry assertion is an equality rather than a tolerance.
NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


@dataclass
class StoredToken:
    """One row as the fake holds it: the view plus the digest the view omits."""

    view: McpTokenView
    token_hash: str


class FakeMcpTokenRepository:
    """In-memory `McpTokenRepository`.

    `created_at` advances by a second per insert so "newest first" has something to order
    by; the SQL path gets its ordering from the column and its own test.
    """

    def __init__(self) -> None:
        self.users: set[UUID] = set()
        self.tokens: dict[UUID, StoredToken] = {}
        self._clock = NOW
        # Commit counter and snapshot. The same trick `FakeAuthorizationRepository`
        # uses: an in-memory double cannot roll back, so without recording *what was committed*
        # a test cannot tell a written row from a durable one — which is exactly the difference
        # the flush-only-rollback bug hinged on.
        self.commits = 0
        self.committed: dict[UUID, StoredToken] = {}

    # --- Protocol ---

    async def user_exists(self, user_id: UUID) -> bool:
        return user_id in self.users

    async def insert(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        token_prefix: str,
        label: str | None,
        expires_at: datetime | None,
    ) -> McpTokenView:
        self._clock += timedelta(seconds=1)
        view = McpTokenView(
            id=uuid4(),
            user_id=user_id,
            token_prefix=token_prefix,
            label=label,
            librechat_user_id=None,
            last_used_at=None,
            last_ldap_check_at=None,
            expires_at=expires_at,
            created_at=self._clock,
        )
        self.tokens[view.id] = StoredToken(view=view, token_hash=token_hash)
        return view

    async def list_for_user(self, user_id: UUID) -> list[McpTokenView]:
        owned = [stored.view for stored in self.tokens.values() if stored.view.user_id == user_id]
        return sorted(owned, key=lambda view: view.created_at, reverse=True)

    async def delete_for_user(self, user_id: UUID, token_id: UUID) -> bool:
        stored = self.tokens.get(token_id)
        if stored is None or stored.view.user_id != user_id:
            return False
        del self.tokens[token_id]
        return True

    async def commit(self) -> None:
        """Snapshot every row, so a test can separate "written" from "committed"."""
        self.commits += 1
        self.committed = dict(self.tokens)

    # --- Test helpers ---

    def add_user(self, user_id: UUID | None = None) -> UUID:
        """Register a `users` row.

        `user_id` is passed when a caller needs it to match another double's id —
        `support.admin` mirrors every user it creates into this repository so the token routes
        and the RBAC routes act on one identity.
        """
        resolved = user_id or uuid4()
        self.users.add(resolved)
        return resolved

    @property
    def stored_hashes(self) -> list[str]:
        return [stored.token_hash for stored in self.tokens.values()]

    @property
    def stored_values(self) -> list[str]:
        """Every string this repository holds, for a "the plaintext is nowhere" assertion."""
        return [
            str(value)
            for stored in self.tokens.values()
            for value in (
                stored.token_hash,
                stored.view.token_prefix,
                stored.view.label,
            )
        ]


@dataclass
class TokenFixture:
    """The service under test plus the doubles behind it."""

    service: McpTokenService
    repository: FakeMcpTokenRepository
    audit: RecordingAuditSink


def build_token_service(*, ttl_seconds: int | None = None) -> TokenFixture:
    """A real `McpTokenService` over in-memory doubles."""
    repository = FakeMcpTokenRepository()
    audit = RecordingAuditSink()
    return TokenFixture(
        service=McpTokenService(
            repository=repository,
            audit_sink=audit,
            ttl_seconds=ttl_seconds,
        ),
        repository=repository,
        audit=audit,
    )


__all__ = [
    "LABEL",
    "NOW",
    "OTHER_LABEL",
    "FakeMcpTokenRepository",
    "StoredToken",
    "TokenFixture",
    "build_token_service",
]

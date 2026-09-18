"""Shapes the RBAC engine passes around.

Ported from `noa-old` branch `MCP` (`core/auth/authorization_types.py`), minus the
direct-grant fields. `AuthorizationUser` there carried `direct_tools` alongside `tools`;
direct per-user grants become a 410 (`direct_tool_grants_disabled`), so a
field for them would be a slot nothing can ever fill.

`AuthorizedUser` is the answer to "who is this and what may they call". It is built from a
freshly read row every time — never cached, never reconstructed from a cookie
claim. That is the whole reason it is a frozen dataclass: a caller cannot flip `is_active`
on it and have the engine believe them.

The repository is a Protocol so `AuthorizationService`'s policy — admin bypass, the
last-admin guards, internal-role preservation — is tested against in-memory dicts, while
`SQLAuthorizationRepository` is tested against a live database. Same split as the login flow's
`AuthRepository`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class AuthorizedUser:
    """A user plus the tools they may call, as of this read.

    `tools` is the *effective* set: role grants filtered to the catalog, or every known
    tool for an admin, or empty when disabled. It is a snapshot for a response
    body, not a permission cache — the execution gate re-resolves.
    """

    user_id: UUID
    email: str
    display_name: str | None
    is_active: bool
    roles: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class AuthorizationUserRecord(Protocol):
    """The slice of `users` the RBAC engine reads."""

    id: UUID
    email: str
    display_name: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class AuthorizationRepository(Protocol):
    """Persistence behind `AuthorizationService`.

    Split into three groups: role-grant reads (the hot path, called per MCP request),
    role/grant writes, and user administration.
    """

    # --- Reads on the permission path ---

    async def get_user_by_id(self, user_id: UUID) -> AuthorizationUserRecord | None: ...

    async def get_role_names(self, user_id: UUID) -> list[str]: ...

    async def get_role_tool_names(self, role_names: list[str]) -> list[str]: ...

    async def list_users(self) -> list[AuthorizationUserRecord]: ...

    # The list-changed emitter's audience. Not on the permission path — it answers "whose
    # catalog did this write move?", which is a question about who to *tell*, never about who
    # may call what.
    async def list_user_ids_with_role(self, role_name: str) -> list[UUID]: ...

    # --- Roles and grants ---

    async def list_assignable_role_names(self) -> list[str]: ...

    async def role_exists(self, role_name: str) -> bool: ...

    async def ensure_role(self, role_name: str) -> str: ...

    async def delete_role(self, role_name: str) -> bool: ...

    async def list_existing_role_names(self, role_names: list[str]) -> list[str]: ...

    async def get_role_tool_names_for_role(self, role_name: str) -> list[str]: ...

    async def replace_role_tool_permissions(
        self, role_name: str, tool_names: list[str]
    ) -> None: ...

    async def replace_user_assignable_roles(self, user_id: UUID, role_names: list[str]) -> None: ...

    # --- User administration ---

    async def update_user_active(
        self, user_id: UUID, *, is_active: bool
    ) -> AuthorizationUserRecord | None: ...

    async def count_active_admin_users(self) -> int: ...

    async def delete_user(self, user_id: UUID) -> bool: ...

    # The cascade revoke. On the RBAC repository rather than the token service's own repository
    # because `set_user_active` is what triggers it, and a disable that revoked through a
    # second repository would need a second transaction to go wrong in.
    async def delete_mcp_tokens_for_user(self, user_id: UUID) -> int: ...

    # --- Transaction boundary ---

    # Every method above flushes; this is what makes the flush durable. On the repository
    # rather than in a route because the service is what knows a mutation succeeded — see
    # `AuthorizationService`'s docstring, and `AuthRepository.commit` for the same shape one
    # taxonomy over.
    async def commit(self) -> None: ...

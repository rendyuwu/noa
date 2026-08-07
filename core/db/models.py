"""Schema v1 ORM models (T4, C3).

Eight tables, three groups:

- Identity + RBAC: `users`, `roles`, `user_roles`, `role_tool_permissions` (V1, V11)
- MCP auth: `mcp_tokens` (C5, V2, V3)
- Managed infrastructure: `whm_servers`, `proxmox_servers`, `pmg_servers` (C7, V48)

Plus `login_rate_limits` from T8 (V9) and `tool_runs` from T35 (V20, V45-V47).

Later tasks add their own tables and migrations: `action_requests` (T34),
`action_receipts` (T36), `audit_log` (T14). Ported from `noa-old` branch `MCP`
per C13, minus the chat-presentation tables (threads/messages/assistant_runs/
workflow_todos) that die with C16.

Credential columns hold Fernet ciphertext, never plaintext (C7, V48). Each server
model exposes `to_safe_dict()` returning presence booleans instead of secret
values so an admin response cannot leak one (V2, V8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base
from core.db.columns import (
    TimestampMixin,
    created_at,
    encrypted_secret,
    lifecycle_enum,
    optional_encrypted_secret,
    updated_at,
    uuid_pk,
)
from core.db.lifecycle import ToolRisk, ToolRunStatus

# `admin` is reserved: it bypasses per-tool permission checks for known tools and
# cannot be edited or deleted through the API (V10, V13).
ADMIN_ROLE_NAME = "admin"

# Roles prefixed `user:` are internal — assigned by NOA itself, never through the
# admin API, and preserved across role replacement (V13, V75).
INTERNAL_ROLE_PREFIX = "user:"


def is_internal_role(name: str) -> bool:
    """True when `name` is an internal role (V13, V75)."""
    return name.startswith(INTERNAL_ROLE_PREFIX)


class User(Base, TimestampMixin):
    """A NOA operator, mirrored from LDAP.

    LDAP stays the source of truth for employment (C4); this row carries NOA-local
    state. New LDAP users are auto-provisioned `is_active=False` and an admin
    activates them (V7). `is_active=False` means zero permissions regardless of
    roles (V11), re-checked on every MCP request (V1).
    """

    __tablename__ = "users"

    id: Mapped[UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    ldap_dn: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Role(Base):
    """A named permission bundle. Permissions flow role → user only (V75)."""

    __tablename__ = "roles"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = created_at()


class UserRole(Base):
    """user ↔ role assignment. Composite PK; both sides cascade on delete."""

    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_id_role_id"),)

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = created_at()


class RoleToolPermission(Base):
    """role → tool grant. Sole source of tool permission (V75).

    `tool_name` is a plain string, not an FK: the tool catalog lives in code, and a
    grant for a tool that is not registered must resolve to "no permission" rather
    than a dangling reference. Admin bypass is for *known* tools only (V10).
    """

    __tablename__ = "role_tool_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "tool_name", name="uq_role_tool_permissions_role_id_tool"),
    )

    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    tool_name: Mapped[str] = mapped_column(
        String(200), nullable=False, primary_key=True, index=True
    )
    created_at: Mapped[datetime] = created_at()


class McpToken(Base):
    """Per-user MCP bearer token (C5, V2, T10).

    Only the SHA-256 hash is stored; plaintext is shown once at mint and never
    logged (V2, V8). `token_prefix` is a short non-secret display fragment so the
    admin UI can identify a token in a list without holding the secret.

    `librechat_user_id` drives TOFU binding (C20, V3): NULL until the first MCP
    call carrying `X-Noa-LibreChat-User`, then pinned; later calls must present a
    matching header or get 401. `last_ldap_check_at` drives revalidation staleness,
    where LDAP being unreachable fails closed (V4).

    No `to_safe_dict()` here, unlike the server models below: T10's `McpTokenView`
    (`core.auth.mcp_token_service`) is the read shape for this table, and it omits
    `token_hash` by not having a field for it. Two safe views of one table is one too
    many — a route could pick the weaker (V66).
    """

    __tablename__ = "mcp_tokens"

    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex digest — lookup key for every MCP request (V2).
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    librechat_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ldap_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # NULL = no expiry; revocation is a row delete (V2).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = created_at()


class LoginRateLimit(Base):
    """One rate-limit bucket for any auth surface (V9, T8, T12).

    Named for the login path it was built for, and now shared: `scope` says which surface
    a row belongs to. Login writes `ip` and `email` (T8); failed MCP authentication writes
    `mcp_client` and `mcp_token` (T12, `core.auth.mcp_auth_rate_limiter`). One generic
    (scope, key) counter rather than a second identical table plus a second SQL repository
    (V66) — the name is the cost of that, and renaming it would be a migration for
    cosmetics.

    Two rows accumulate per failed attempt, on both surfaces, because either key alone
    leaves a hole: for login, IP-only lets a botnet spread guesses against one account and
    email-only lets one host spray a whole directory. `assert_allowed` denies when *either*
    bucket is blocked.

    `scope_key` holds whatever identifies the attempt on that surface — an IP, a
    normalized email, a LibreChat account id, or a token digest (never a plaintext
    credential, V2/V8) — so a row is created per distinct value a caller supplies. Bounded
    only by `String(255)`; pruning stale buckets is deliberately not here, because the
    sweep belongs with T39's background sweeper rather than in a table definition.

    No `created_at`: `window_started_at` already carries the only creation time that
    means anything for a bucket, and a second timestamp would invite reading the
    wrong one.
    """

    __tablename__ = "login_rate_limits"
    __table_args__ = (
        # Also the lookup index: every query filters on both columns.
        UniqueConstraint("scope", "scope_key", name="uq_login_rate_limits_scope_key"),
    )

    id: Mapped[UUID] = uuid_pk()
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    window_started_at: Mapped[datetime] = created_at()
    # NULL = counting but not blocked. Set once `attempt_count` reaches the max.
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = updated_at()


class ToolRun(Base):
    """One MCP tool execution, READ or CHANGE (T35, V45-V47).

    Written for *every* tool call, not only the interesting ones: V45 covers READs, V46
    covers approved CHANGEs, and V47 fixes the field list. This table is the answer to
    "what did NOA actually do", so it is queried by the admin audit surface (T55) and
    never by the tool path itself.

    `risk` and `status` are separate columns on purpose (V20). Folding them into one
    lifecycle set would make `FAILED` and `READ` compete for the same cell, and a failed
    READ is exactly the row an audit trail must be able to hold. `noa-old` kept `risk`
    only on `action_requests`, so its `tool_runs` could not say whether a run was a
    change at all without a join to a row that may not exist.

    `created_at` and `completed_at` are the timing pair. Duration is derived on read
    rather than stored, so the two can never disagree.

    Nothing on the tool path reads this table, and nothing in a tool writes it:
    `noa_api.mcp_audit.ToolRunAuditMiddleware` does, beside the RBAC gate, so no individual
    tool can forget it (T73, V83b). It also redacts `args` before they land (C7, V8) — the
    column below only guarantees somewhere to put the redacted form. READ rows are written
    by that middleware; an approved CHANGE's row belongs to the post-approval executor
    (T38, V46).
    """

    __tablename__ = "tool_runs"

    id: Mapped[UUID] = uuid_pk()
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # Nullable with `SET NULL`, unlike every other user FK in schema v1, which cascades.
    # An audit trail that a user deletion erases is not an audit trail, and `RESTRICT`
    # would instead make `DELETE /admin/users/{id}` fail once a user had run one tool.
    # T73 always writes an id; NULL describes life after the subject is deleted.
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Classification, fixed before the call runs (V20).
    risk: Mapped[ToolRisk] = lifecycle_enum(ToolRisk, name="tool_run_risk")
    # Execution state. Defaults to STARTED so a row inserted before the tool body runs is
    # already correct, and a process that dies mid-call leaves evidence (T38's reaper).
    status: Mapped[ToolRunStatus] = lifecycle_enum(
        ToolRunStatus,
        name="tool_run_status",
        default=ToolRunStatus.STARTED,
        index=True,
    )
    # Audit/grouping label only — never a security scope (DECISIONS §3.2, old V165).
    # Nullable because MCP has no thread concept to guarantee one (C16 dropped threads):
    # LibreChat sends no conversation id in the call, so it arrives as an optional header
    # (`noa_api.mcp_audit`, T73) that T57 fills from `{{LIBRECHAT_BODY_CONVERSATIONID}}`.
    conversation_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # Redacted by the writer (`noa_api.mcp_audit`, T73). `'{}'` rather than NULL so "no
    # arguments" and "arguments not recorded" cannot be confused in an audit view.
    args: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Truncated (V45, V47). Bounded so a large READ result cannot bloat the audit table —
    # the full body lives behind the table surface (V64), not here.
    result_summary: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Timing, half one: when the run started. Indexed — the audit list sorts and pages on it.
    created_at: Mapped[datetime] = created_at(index=True)
    # Timing, half two. NULL while STARTED.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SSHCredentialsMixin:
    """SSH connection fields shared by WHM and PMG servers (V66).

    `ssh_username` NULL means connect as `root`; any other user gets `sudo -n`
    prefixing at command-build time (V55). `ssh_host_key_fingerprint` is the pinned
    host key — absent means not yet validated, and the integration refuses to
    connect until an admin runs validate (TOFU capture, V69).
    """

    ssh_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ssh_password: Mapped[str | None] = optional_encrypted_secret()
    ssh_private_key: Mapped[str | None] = optional_encrypted_secret()
    ssh_private_key_passphrase: Mapped[str | None] = optional_encrypted_secret()
    ssh_host_key_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def _ssh_safe_fields(self) -> dict[str, Any]:
        """Secret-free SSH view: presence booleans, never the credentials."""
        return {
            "ssh_username": self.ssh_username,
            "ssh_port": self.ssh_port,
            "ssh_host_key_fingerprint": self.ssh_host_key_fingerprint,
            "has_ssh_password": bool(self.ssh_password),
            "has_ssh_private_key": bool(self.ssh_private_key),
        }


class WHMServer(Base, SSHCredentialsMixin, TimestampMixin):
    """A WHM/cPanel server. API token for WHM API, SSH for CSF/Imunify (I.ext)."""

    __tablename__ = "whm_servers"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_username: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token: Mapped[str] = encrypted_secret()
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    def to_safe_dict(self) -> dict[str, Any]:
        """Admin view. No `api_token`, no SSH credentials (V2, V8)."""
        return {
            "id": str(self.id),
            "name": self.name,
            "base_url": self.base_url,
            "api_username": self.api_username,
            "has_api_token": bool(self.api_token),
            "verify_ssl": self.verify_ssl,
            **self._ssh_safe_fields(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProxmoxServer(Base, TimestampMixin):
    """A Proxmox VE endpoint. API-only (HTTP), no SSH path (I.ext)."""

    __tablename__ = "proxmox_servers"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_token_id: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token_secret: Mapped[str] = encrypted_secret()
    # Proxmox ships a self-signed cert by default, so this defaults off, unlike WHM.
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    def to_safe_dict(self) -> dict[str, Any]:
        """Admin view. No `api_token_secret` (V2, V8)."""
        return {
            "id": str(self.id),
            "name": self.name,
            "base_url": self.base_url,
            "api_token_id": self.api_token_id,
            "has_api_token_secret": bool(self.api_token_secret),
            "verify_ssl": self.verify_ssl,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class PMGServer(Base, SSHCredentialsMixin, TimestampMixin):
    """A Proxmox Mail Gateway node.

    Reached over SSH + `pmgsh` only (V58, I.ext), so `ssh_host` is required and
    there is no `base_url`/`verify_ssl` — `noa-old` carried both and never used
    them for PMG.
    """

    __tablename__ = "pmg_servers"

    id: Mapped[UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    ssh_host: Mapped[str] = mapped_column(String(255), nullable=False)

    def to_safe_dict(self) -> dict[str, Any]:
        """Admin view. No SSH credentials (V2, V8)."""
        return {
            "id": str(self.id),
            "name": self.name,
            "ssh_host": self.ssh_host,
            **self._ssh_safe_fields(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


__all__ = [
    "ADMIN_ROLE_NAME",
    "INTERNAL_ROLE_PREFIX",
    "LoginRateLimit",
    "McpToken",
    "PMGServer",
    "ProxmoxServer",
    "Role",
    "RoleToolPermission",
    "SSHCredentialsMixin",
    "ToolRun",
    "User",
    "UserRole",
    "WHMServer",
    "is_internal_role",
]

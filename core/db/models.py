"""Schema v1 ORM models (T4, C3).

Eight tables, three groups:

- Identity + RBAC: `users`, `roles`, `user_roles`, `role_tool_permissions` (V1, V11)
- MCP auth: `mcp_tokens` (C5, V2, V3)
- Managed infrastructure: `whm_servers`, `proxmox_servers`, `pmg_servers` (C7, V48)

Plus `login_rate_limits` from T8 (V9), `tool_runs` from T35 (V20, V45-V47),
`action_requests` from T34 (V20, V32, V33, V43) and `action_receipts` from T36 (V46).

Later tasks add their own tables and migrations: `audit_log` (T14). Ported from `noa-old`
branch `MCP` per C13, minus the chat-presentation tables
(threads/messages/assistant_runs/workflow_todos) that die with C16.

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
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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
from core.db.lifecycle import ActionRequestStatus, ToolRisk, ToolRunStatus

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
    by that middleware. An approved CHANGE's row is opened by the decision that authorised
    it (`core.approvals.decisions`, T37, V46) — in the same transaction, so `APPROVED` with
    no run cannot exist — and moved to a terminal state by the executor
    (`core.approvals.execution`, T38). A row that stays `STARTED` past its deadline is moved
    there by `core.approvals.reaper` instead, with a summary saying the outcome was never
    observed rather than that the change failed.
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


class ActionRequest(Base):
    """One "may this CHANGE run?" question and its answer (T34, V20, V32, V33, V43).

    This row *is* the authorization. V23 says the question is answered from
    `action_requests.status` every time — never from an LLM claim and never from a tool
    argument — so the gate (T33) writes PENDING here and the tool executes only once this
    column says APPROVED. A held bearer token can create one of these; what it cannot do
    is decide one (V22: the decision arrives as a cookie POST from a NOA-origin document).

    Five columns `noa-old` had here are deliberately absent, and one it lacked is present:

    - `proposed_reason` — forbidden outright (C8, V43). `noa-old` had no such column
      either, but it did something worse: the reason travelled inside `args` JSONB, so the
      LLM authored it. Here `reason` is a column of its own, NULL until an operator types
      one into the approval card (V15), and there is exactly one of them.
    - `args` — folded into `approval_context`. V33 wants the gate-time payload persisted
      as one object rather than rebuilt at render time; splitting the arguments out would
      make two records of one moment that can disagree.
    - `risk` — every row here is a CHANGE by construction (V16: READs never reach the
      gate). `noa-old` kept `risk` on this table *instead* of on `tool_runs`, which is why
      its audit trail could not describe a failed READ; T35 put it where it belongs.
    - `decided_by_user_id` — V27 makes the decider the requester (a mismatch is a 404, not
      a 403). A second identity column would be a second truth about one person.
    - `updated_at` — `decided_at` already stamps the only mutation V28 permits. A second
      timestamp invites reading the wrong one (`LoginRateLimit` above records the same
      call for the same reason).
    - `expires_at` — present, and `noa-old` had nothing like it. Without a deadline a
      request nobody answers stays PENDING forever (V32, T39).
    """

    __tablename__ = "action_requests"
    __table_args__ = (
        # T39's sweep is `status = PENDING AND expires_at <= now`. Composite rather than
        # two indexes; `status` leads, so status-only lookups use it too. (`<=`, not `<`:
        # the sweep and the decision door judge a deadline the same way — `core.approvals.expiry`.)
        Index("ix_action_requests_status_expires_at", "status", "expires_at"),
        # T37 (C8, V15), closing what T34 left open by name. A row that says an operator
        # decided must carry what they typed, at the mechanism rather than in the endpoint
        # alone (V84c) — so a second writer cannot record a decision nobody justified.
        # `~ '[^[:space:]]'` — "holds a non-whitespace character" — rather than `btrim(...)
        # <> ''`, because bare `btrim` strips spaces only and would accept a reason of one
        # tab that the endpoint's `.strip()` rejects. The `IS NOT NULL` is not redundant:
        # `NULL ~ '…'` is NULL, and a CHECK evaluating to NULL is satisfied, so without it
        # the constraint would catch every blank string and wave through the NULL. EXPIRED
        # and PENDING sit outside it: an expiry is the *absence* of an answer (T39's sweep
        # writes one with `reason IS NULL`), and a pending request has not been answered.
        CheckConstraint(
            "status NOT IN ('APPROVED', 'DENIED') "
            "OR (reason IS NOT NULL AND reason ~ '[^[:space:]]')",
            name="ck_action_requests_decided_reason",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # SET NULL, like `tool_runs` and unlike every schema-v1 user FK, which cascades. An
    # approved CHANGE is an audit artifact (V46) and T36's receipts hang off this row, so
    # cascading would let one user deletion erase both. Fails closed against V27: NULL
    # matches no caller, so a requester-match lookup 404s.
    requested_by_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # The answer to "may this run?" (V23). Defaults to PENDING because the gate inserts
    # before anyone has decided anything; the three terminal states are reached exactly
    # once, under a row lock (V28).
    status: Mapped[ActionRequestStatus] = lifecycle_enum(
        ActionRequestStatus,
        name="action_request_status",
        default=ActionRequestStatus.PENDING,
    )
    # Audit/grouping label only, never a security scope — the same column and the same
    # caveat as `tool_runs.conversation_ref` above.
    conversation_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # V33: built at gate time and persisted here, never rebuilt from a transcript when the
    # card renders. Holds what V35 shows an operator — provenance, tool arguments, and the
    # in-process preflight evidence (V17). No server default, unlike `tool_runs.args`: an
    # empty context is never a legitimate state here, so an insert that omits it should
    # fail rather than quietly record a card with nothing on it.
    approval_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # The one reason that exists (C8, V15, V43). NULL until decided, and still NULL after
    # an expiry — nobody typed one. Unbounded `Text` because no machine writes it: a cap
    # would silently truncate the operator's own words, and truncating the field that
    # authorises a change is worse than storing a long one. T37 bounds the input at the
    # endpoint, and the CHECK in `__table_args__` binds it to a decided `status`.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The execution this decision produced. NULL until an approval starts one (T37 — in the
    # same transaction as the decision, so the two cannot disagree), and NULL forever on deny
    # or expiry. The link lives here and only here — a matching `action_request_id` on
    # `tool_runs` would be two truths about one edge.
    tool_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tool_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    # V32's deadline. Required: a row without one cannot expire, and "pending forever" is
    # the state V32 exists to remove. Kept after a decision as the historical fact it is.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()
    # NULL while PENDING. Set by whichever terminal transition wins the lock (V28),
    # including the expiry sweep — an expiry is a decision the clock made.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionReceipt(Base):
    """What an approved CHANGE actually did (T36, V46).

    V46 names three artifacts for an approved change: the `tool_runs` row (what ran),
    this receipt (what it did), and the audit log. The run says a change completed; the
    receipt is the two-part story DECISIONS §6.5 requires an operator to be able to read
    back — before-state and after-state, each verified separately, never collapsed into a
    single "done".

    Written by `core.audit.receipts`, whose two callers are T38's executor — for a change that
    ran — and T38's reaper, for one whose process died before it could report. T42's card and
    T63's `noa_get_action_result` read it beside the run. What T36 owed was a shape those
    cannot quietly reshape, and the `UNIQUE` below is the load-bearing part of it.

    Ported from `noa-old` `MCP:.../0006_action_receipts.py` (C13) with three departures,
    each named because a port carries the code and not the defect (T21(b)):

    - `receipt_data`, not `payload` — §T.36's name.
    - `terminal_phase` absent. It carried the terminal state of a multi-phase workflow,
      and C16 drops workflows. The terminal state now lives on `tool_runs.status` and
      `action_requests.status`; a third column saying it again is a third truth about one
      moment, which is T34's argument for the four columns it dropped.
    - `schema_version` absent. `approval_context` and `tool_runs.args` are both unversioned
      JSONB; versioning the third would make their bareness look deliberate when it is not.

    The one thing the port had for free and this shape does not: `noa-old` made
    `action_request_id` the primary key, so one receipt per request came with the table.
    §T.36 names a separate `id`, so the uniqueness is stated below or it is lost — and it
    is load-bearing, because T38's executor and its reaper can both reach a finished run.
    A second receipt turns "the receipt" into "some receipt", and a reader picking one
    arbitrarily is exactly the disagreement T34 refused when it folded `args` into
    `approval_context`.
    """

    __tablename__ = "action_receipts"
    __table_args__ = (
        # One decision, one outcome (V28, V34). Also the lookup index — every reader
        # arrives holding an `action_request_id` — so there is no second index for it.
        UniqueConstraint("action_request_id", name="uq_action_receipts_action_request_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    # CASCADE and NOT NULL, unlike every other FK added since T35. Those are `SET NULL`
    # because the row survives losing its subject and still describes something: a
    # `tool_runs` row minus its requester is still what ran. A receipt minus its request
    # is a JSONB blob nothing is about — the tool name, the requester and the arguments
    # all live on `action_requests`. The audit-survival property that matters is carried
    # there, where T34 made both FKs `SET NULL` so a user deletion erases neither the
    # authorization nor this.
    action_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("action_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SET NULL, mirroring `action_requests.tool_run_id` above: one edge, described the same
    # way at both ends. NULL describes life after the run row is deleted, not a receipt
    # written without one.
    tool_run_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tool_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    # No server default, deliberately unlike `tool_runs.args` (`'{}'` there so "took no
    # arguments" and "not recorded" stay distinguishable) and exactly like
    # `approval_context` above: an empty receipt is never a legitimate state, so an insert
    # that omits it should fail rather than record an outcome with nothing in it.
    receipt_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at()


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
    "ActionReceipt",
    "ActionRequest",
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

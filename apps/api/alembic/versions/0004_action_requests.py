"""action requests: one row per "may this CHANGE run?" question

Revision ID: 0004_action_requests
Revises: 0003_tool_runs
Create Date: 2026-08-08

T34 (V20, V32, V33, V43). This table is the authorization: V23 answers "may this
run?" from `status` every time, never from an LLM claim or a tool argument.

`status` is a VARCHAR plus a CHECK, not a Postgres enum type, matching `tool_runs`
(0003) — adding a member to a native enum is `ALTER TYPE`, and the type outlives the
table on downgrade.

`EXPIRED` is new against `noa-old`, which had only PENDING/APPROVED/DENIED: without it
a request nobody answered stayed PENDING forever (V32).

`reason` is a column of its own and there is no `proposed_reason` (C8, V43). `noa-old`
carried the reason inside the request's `args` JSONB, which made it LLM-authored; here
it arrives only at approve time, typed by an operator.

Two indexes, and deliberately fewer than 0003 laid on `tool_runs`: nothing filters this
table the way §I.admin-api filters the audit surface. The composite serves T39's sweep
(`status = PENDING AND expires_at < now()`) and, on its leading column, status-only
lookups; the requester index serves V31's per-user in-flight cap.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_action_requests"
down_revision: str | None = "0003_tool_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)

# Values mirror `core.db.lifecycle`. Spelled out rather than imported so the migration
# keeps describing the schema as of this revision even after the enum gains members.
_ACTION_REQUEST_STATUS = ("PENDING", "APPROVED", "DENIED", "EXPIRED")


def upgrade() -> None:
    op.create_table(
        "action_requests",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        # SET NULL, not CASCADE: an approved CHANGE is an audit artifact (V46) and T36's
        # receipts hang off this row, so cascading would let one user deletion erase both.
        sa.Column("requested_by_user_id", _UUID, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                *_ACTION_REQUEST_STATUS,
                name="action_request_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        # Audit/grouping label only, never a security scope (DECISIONS §3.2).
        sa.Column("conversation_ref", sa.String(length=255), nullable=True),
        # V33: persisted at gate time, never rebuilt from a transcript. No default — an
        # empty context is not a legitimate state, so an insert omitting it must fail.
        sa.Column("approval_context", postgresql.JSONB(), nullable=False),
        # The one reason that exists (C8, V15, V43). NULL until an operator types one.
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("tool_run_id", _UUID, nullable=True),
        # V32: required, because a row without a deadline cannot expire.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_action_requests_requested_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tool_run_id"],
            ["tool_runs.id"],
            name="fk_action_requests_tool_run_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_action_requests_status_expires_at", "action_requests", ["status", "expires_at"]
    )
    op.create_index(
        "ix_action_requests_requested_by_user_id", "action_requests", ["requested_by_user_id"]
    )


def downgrade() -> None:
    op.drop_table("action_requests")

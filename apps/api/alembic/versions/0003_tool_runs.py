"""tool runs: one row per MCP tool execution

Revision ID: 0003_tool_runs
Revises: 0002_login_rate_limits
Create Date: 2026-08-07

`risk` and `status` are separate checked columns so a failed
READ is representable — folding them into one lifecycle set would make `FAILED`
and `READ` compete for the same cell.

Both enums are `native_enum=False` with `create_constraint=True`: a VARCHAR plus a
CHECK, not a Postgres enum type. Adding a member to a native enum is `ALTER TYPE`,
and the type outlives the table on downgrade; a CHECK is dropped with it.

Indexes cover the admin API's audit filter set (`toolName`, `status`,
`conversationRef`, requester, date range) plus the `created_at` ordering the cursor
pagination in the admin audit reader pages on. No index on `risk` — nothing filters by it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_tool_runs"
down_revision: str | None = "0002_login_rate_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)

# Values mirror `core.db.lifecycle`. Spelled out rather than imported so the migration
# keeps describing the schema as of this revision even after the enums gain members.
_TOOL_RISK = ("READ", "CHANGE")
_TOOL_RUN_STATUS = ("STARTED", "COMPLETED", "FAILED")


def _checked_enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.create_table(
        "tool_runs",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        # SET NULL, not CASCADE: an audit trail a user deletion erases is not an audit
        # trail. Every other user FK in schema v1 cascades; this one must not.
        sa.Column("requested_by_user_id", _UUID, nullable=True),
        sa.Column("risk", _checked_enum(*_TOOL_RISK, name="tool_run_risk"), nullable=False),
        sa.Column(
            "status",
            _checked_enum(*_TOOL_RUN_STATUS, name="tool_run_status"),
            nullable=False,
            server_default="STARTED",
        ),
        # Audit/grouping label only, never a security scope (DECISIONS.md section 3.2).
        sa.Column("conversation_ref", sa.String(length=255), nullable=True),
        # Redacted by the writer. `'{}'` so "no arguments" and "not recorded" differ.
        sa.Column(
            "args", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        # Truncated; the full body lives behind the table surface.
        sa.Column("result_summary", sa.String(length=2000), nullable=True),
        # Timing pair. Duration is derived on read so the two cannot disagree.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_tool_runs_requested_by_user_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_tool_runs_tool_name", "tool_runs", ["tool_name"])
    op.create_index("ix_tool_runs_status", "tool_runs", ["status"])
    op.create_index("ix_tool_runs_requested_by_user_id", "tool_runs", ["requested_by_user_id"])
    op.create_index("ix_tool_runs_conversation_ref", "tool_runs", ["conversation_ref"])
    op.create_index("ix_tool_runs_created_at", "tool_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("tool_runs")

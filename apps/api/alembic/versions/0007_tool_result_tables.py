"""tool result tables: large READ bodies parked off the transcript

Revision ID: 0007_result_tables
Revises: 0006_action_receipts
Create Date: 2026-08-09

T56 (V64, V85). A large READ answers with a summary and a URL; the rows land here and an
operator reads them on the table surface behind that URL. `tool_runs.result_summary` is
bounded at 2000 characters on purpose (0003) — this is the body that bound refers to.

Two properties the DDL carries rather than the application:

- `requested_by_user_id` is `SET NULL`, like every user FK since 0003. The reader matches on
  it, so a deleted operator's parked table matches nobody instead of everybody (V27's
  fail-closed direction).
- Neither payload column takes a server default, exactly like `approval_context` (0004) and
  `receipt_data` (0006): an insert that omits the columns or the rows must fail rather than
  park an empty page.

One index, the unique one on `token`. Every reader arrives holding a token, and nothing
filters or orders by anything else — the discipline that kept 0006 at one index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Under 32 characters: `alembic_version.version_num` is `VARCHAR(32)`, and a longer id fails
# after the DDL, on the bookkeeping UPDATE (T37's recorded gotcha).
revision: str = "0007_result_tables"
down_revision: str | None = "0006_action_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "tool_result_tables",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # `secrets.token_urlsafe(32)` is 43 characters; the column is wider so a future
        # widening is a migration rather than a silent truncation.
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("requested_by_user_id", _UUID, nullable=True),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("column_labels", postgresql.JSONB(), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        # Matches before the cut, and whether there was one (V85). Stored rather than derived
        # so the page cannot report `len(rows)` as the total.
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            name="fk_tool_result_tables_requested_by_user_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token", name="uq_tool_result_tables_token"),
    )


def downgrade() -> None:
    op.drop_table("tool_result_tables")

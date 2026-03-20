"""action receipts

Revision ID: 0006_action_receipts
Revises: 0005_workflow_todo_statuses
Create Date: 2026-03-19 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_action_receipts"
down_revision: Union[str, None] = "0005_workflow_todo_statuses"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "action_receipts",
        sa.Column(
            "action_request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("tool_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("terminal_phase", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["action_request_id"],
            ["action_requests.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_run_id"],
            ["tool_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("action_request_id"),
    )
    op.create_index(
        "ix_action_receipts_tool_run_id",
        "action_receipts",
        ["tool_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_action_receipts_terminal_phase",
        "action_receipts",
        ["terminal_phase"],
        unique=False,
    )
    op.create_index(
        "ix_action_receipts_created_at",
        "action_receipts",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_action_receipts_created_at", table_name="action_receipts")
    op.drop_index("ix_action_receipts_terminal_phase", table_name="action_receipts")
    op.drop_index("ix_action_receipts_tool_run_id", table_name="action_receipts")
    op.drop_table("action_receipts")

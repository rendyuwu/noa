"""action requests and tool run tables

Revision ID: 0002_actions_tools
Revises: 0001_init
Create Date: 2026-03-09 00:00:01.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_actions_tools"
down_revision: Union[str, None] = "0001_init"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    action_request_risk = sa.Enum("READ", "CHANGE", name="action_request_risk", native_enum=False)
    action_request_status = sa.Enum("PENDING", "APPROVED", "DENIED", name="action_request_status", native_enum=False)
    tool_run_status = sa.Enum("STARTED", "COMPLETED", "FAILED", name="tool_run_status", native_enum=False)

    op.create_table(
        "action_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column(
            "args",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("risk", action_request_risk, nullable=False),
        sa.Column("status", action_request_status, nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_action_requests_thread_id", "action_requests", ["thread_id"], unique=False)
    op.create_index("ix_action_requests_tool_name", "action_requests", ["tool_name"], unique=False)
    op.create_index("ix_action_requests_risk", "action_requests", ["risk"], unique=False)
    op.create_index("ix_action_requests_status", "action_requests", ["status"], unique=False)
    op.create_index("ix_action_requests_requested_by_user_id", "action_requests", ["requested_by_user_id"], unique=False)
    op.create_index("ix_action_requests_created_at", "action_requests", ["created_at"], unique=False)

    op.create_table(
        "tool_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column(
            "args",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", tool_run_status, nullable=False, server_default=sa.text("'STARTED'")),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("action_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["action_request_id"], ["action_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_runs_thread_id", "tool_runs", ["thread_id"], unique=False)
    op.create_index("ix_tool_runs_tool_name", "tool_runs", ["tool_name"], unique=False)
    op.create_index("ix_tool_runs_status", "tool_runs", ["status"], unique=False)
    op.create_index("ix_tool_runs_action_request_id", "tool_runs", ["action_request_id"], unique=False)
    op.create_index("ix_tool_runs_requested_by_user_id", "tool_runs", ["requested_by_user_id"], unique=False)
    op.create_index("ix_tool_runs_created_at", "tool_runs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tool_runs_created_at", table_name="tool_runs")
    op.drop_index("ix_tool_runs_requested_by_user_id", table_name="tool_runs")
    op.drop_index("ix_tool_runs_action_request_id", table_name="tool_runs")
    op.drop_index("ix_tool_runs_status", table_name="tool_runs")
    op.drop_index("ix_tool_runs_tool_name", table_name="tool_runs")
    op.drop_index("ix_tool_runs_thread_id", table_name="tool_runs")
    op.drop_table("tool_runs")

    op.drop_index("ix_action_requests_created_at", table_name="action_requests")
    op.drop_index("ix_action_requests_requested_by_user_id", table_name="action_requests")
    op.drop_index("ix_action_requests_status", table_name="action_requests")
    op.drop_index("ix_action_requests_risk", table_name="action_requests")
    op.drop_index("ix_action_requests_tool_name", table_name="action_requests")
    op.drop_index("ix_action_requests_thread_id", table_name="action_requests")
    op.drop_table("action_requests")

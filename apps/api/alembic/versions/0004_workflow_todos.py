"""workflow todos

Revision ID: 0004_workflow_todos
Revises: 0003_whm_servers
Create Date: 2026-03-16 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_workflow_todos"
down_revision: Union[str, None] = "0003_whm_servers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_todos",
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("thread_id", "position"),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_workflow_todos_status",
        ),
        sa.CheckConstraint(
            "priority IN ('high', 'medium', 'low')",
            name="ck_workflow_todos_priority",
        ),
    )
    op.create_index(
        "ix_workflow_todos_thread_id", "workflow_todos", ["thread_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_todos_thread_id", table_name="workflow_todos")
    op.drop_table("workflow_todos")

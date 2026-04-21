"""assistant runs

Revision ID: 20260421_assistant_runs
Revises: 20260404_login_rate_limits
Create Date: 2026-04-21 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260421_assistant_runs"
down_revision: Union[str, None] = "20260404_login_rate_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistant_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "STARTING",
                "RUNNING",
                "WAITING_APPROVAL",
                "COMPLETED",
                "FAILED",
                name="assistant_run_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("owner_instance_id", sa.String(length=255), nullable=False),
        sa.Column(
            "sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "live_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "blocking_action_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("action_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_error_reason", sa.Text(), nullable=True),
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
    )
    op.create_index("ix_assistant_runs_thread_id", "assistant_runs", ["thread_id"])
    op.create_index(
        "ix_assistant_runs_owner_user_id", "assistant_runs", ["owner_user_id"]
    )
    op.create_index("ix_assistant_runs_status", "assistant_runs", ["status"])
    op.create_index(
        "ix_assistant_runs_blocking_action_request_id",
        "assistant_runs",
        ["blocking_action_request_id"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_assistant_runs_thread_active
        ON assistant_runs (thread_id)
        WHERE status IN ('STARTING', 'RUNNING', 'WAITING_APPROVAL')
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_assistant_runs_thread_active")
    op.drop_index(
        "ix_assistant_runs_blocking_action_request_id", table_name="assistant_runs"
    )
    op.drop_index("ix_assistant_runs_status", table_name="assistant_runs")
    op.drop_index("ix_assistant_runs_owner_user_id", table_name="assistant_runs")
    op.drop_index("ix_assistant_runs_thread_id", table_name="assistant_runs")
    op.drop_table("assistant_runs")

"""whm servers

Revision ID: 0003_whm_servers
Revises: 0002_actions_tools
Create Date: 2026-03-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_whm_servers"
down_revision: Union[str, None] = "0002_actions_tools"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "whm_servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_username", sa.String(length=255), nullable=False),
        sa.Column("api_token", sa.Text(), nullable=False),
        sa.Column(
            "verify_ssl", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_whm_servers_name", "whm_servers", ["name"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_whm_servers_name", table_name="whm_servers")
    op.drop_table("whm_servers")

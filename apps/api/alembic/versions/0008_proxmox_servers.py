"""proxmox servers

Revision ID: 0008_proxmox_servers
Revises: 0007_whm_ssh_secret
Create Date: 2026-03-31 00:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008_proxmox_servers"
down_revision: Union[str, None] = "0007_whm_ssh_secret"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "proxmox_servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_token_id", sa.String(length=255), nullable=False),
        sa.Column("api_token_secret", sa.Text(), nullable=False),
        sa.Column(
            "verify_ssl", sa.Boolean(), nullable=False, server_default=sa.text("false")
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
    op.create_index(
        "ix_proxmox_servers_name",
        "proxmox_servers",
        ["name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_proxmox_servers_name", table_name="proxmox_servers")
    op.drop_table("proxmox_servers")

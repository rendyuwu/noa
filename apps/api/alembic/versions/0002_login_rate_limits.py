"""login rate limit buckets

Revision ID: 0002_login_rate_limits
Revises: 0001_schema_v1
Create Date: 2026-08-06

T8 (V9). One row per (scope, scope_key) bucket: `ip` + `email` are counted
separately so neither an IP-only nor an email-only limit leaves a hole.

The unique constraint doubles as the lookup index — every query filters on both
columns, so a separate index would only cost writes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_login_rate_limits"
down_revision: str | None = "0001_schema_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "login_rate_limits",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # `ip` | `email`.
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # NULL = counting but not blocked.
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("scope", "scope_key", name="uq_login_rate_limits_scope_key"),
    )


def downgrade() -> None:
    op.drop_table("login_rate_limits")

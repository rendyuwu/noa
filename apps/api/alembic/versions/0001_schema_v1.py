"""schema v1: users, roles, rbac, mcp tokens, managed servers

Revision ID: 0001_schema_v1
Revises: None
Create Date: 2026-08-04

T4. Covers identity + RBAC, MCP token auth with TOFU binding
(V2, V3), and the three managed-server tables whose credential columns hold
Fernet ciphertext.

`gen_random_uuid()` is core in Postgres 13+, so no `pgcrypto` extension is
created here (C3 pins Postgres 16).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_schema_v1"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def _uuid_pk() -> sa.Column:
    return sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _ssh_columns() -> list[sa.Column]:
    """SSH connection + credential columns shared by WHM and PMG."""
    return [
        sa.Column("ssh_username", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=True),
        # Ciphertext only.
        sa.Column("ssh_password", sa.Text(), nullable=True),
        sa.Column("ssh_private_key", sa.Text(), nullable=True),
        sa.Column("ssh_private_key_passphrase", sa.Text(), nullable=True),
        sa.Column("ssh_host_key_fingerprint", sa.String(length=255), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("ldap_dn", sa.Text(), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        # V7: new LDAP users land inactive; an admin activates them.
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "roles",
        _uuid_pk(),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        _created_at(),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    op.create_table(
        "user_roles",
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("role_id", _UUID, nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_roles_user_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_user_roles_role_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_id_role_id"),
    )

    # `tool_name` is deliberately not an FK: the catalog lives in code, and a grant
    # for an unregistered tool must read as "no permission", not dangle.
    op.create_table(
        "role_tool_permissions",
        sa.Column("role_id", _UUID, nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_role_tool_permissions_role_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role_id", "tool_name", name="pk_role_tool_permissions"),
        sa.UniqueConstraint("role_id", "tool_name", name="uq_role_tool_permissions_role_id_tool"),
    )
    op.create_index("ix_role_tool_permissions_tool_name", "role_tool_permissions", ["tool_name"])

    op.create_table(
        "mcp_tokens",
        _uuid_pk(),
        sa.Column("user_id", _UUID, nullable=False),
        # SHA-256 hex digest. Plaintext shown once at mint, never stored.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        # NULL until first bind, then pinned.
        sa.Column("librechat_user_id", sa.String(length=255), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        # Drives LDAP revalidation staleness; LDAP down -> fail closed.
        sa.Column("last_ldap_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_mcp_tokens_user_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )
    op.create_index("ix_mcp_tokens_user_id", "mcp_tokens", ["user_id"])
    op.create_index("ix_mcp_tokens_token_hash", "mcp_tokens", ["token_hash"], unique=True)
    op.create_index("ix_mcp_tokens_librechat_user_id", "mcp_tokens", ["librechat_user_id"])

    op.create_table(
        "whm_servers",
        _uuid_pk(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_username", sa.String(length=255), nullable=False),
        # Ciphertext only.
        sa.Column("api_token", sa.Text(), nullable=False),
        sa.Column("verify_ssl", sa.Boolean(), nullable=False, server_default="true"),
        *_ssh_columns(),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("name", name="uq_whm_servers_name"),
    )
    op.create_index("ix_whm_servers_name", "whm_servers", ["name"], unique=True)

    op.create_table(
        "proxmox_servers",
        _uuid_pk(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_token_id", sa.String(length=255), nullable=False),
        # Ciphertext only.
        sa.Column("api_token_secret", sa.Text(), nullable=False),
        # Proxmox defaults to a self-signed cert, hence false (WHM defaults true).
        sa.Column("verify_ssl", sa.Boolean(), nullable=False, server_default="false"),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("name", name="uq_proxmox_servers_name"),
    )
    op.create_index("ix_proxmox_servers_name", "proxmox_servers", ["name"], unique=True)

    # SSH + `pmgsh` only -> `ssh_host` required, no base_url/verify_ssl.
    op.create_table(
        "pmg_servers",
        _uuid_pk(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ssh_host", sa.String(length=255), nullable=False),
        *_ssh_columns(),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("name", name="uq_pmg_servers_name"),
    )
    op.create_index("ix_pmg_servers_name", "pmg_servers", ["name"], unique=True)


def downgrade() -> None:
    op.drop_table("pmg_servers")
    op.drop_table("proxmox_servers")
    op.drop_table("whm_servers")
    op.drop_table("mcp_tokens")
    op.drop_table("role_tool_permissions")
    op.drop_table("user_roles")
    op.drop_table("roles")
    op.drop_table("users")

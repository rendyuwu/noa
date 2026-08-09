"""Schema v1 model guards (T4).

No DB needed — these assert on `Base.metadata` and on the safe-view helpers.
Live migration behaviour is covered by `test_migrations.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from core.db import Base
from core.db.models import (
    ADMIN_ROLE_NAME,
    INTERNAL_ROLE_PREFIX,
    PMGServer,
    ProxmoxServer,
    WHMServer,
    is_internal_role,
)

SCHEMA_V1_TABLES = {
    "users",
    "roles",
    "user_roles",
    "role_tool_permissions",
    "mcp_tokens",
    "whm_servers",
    "proxmox_servers",
    "pmg_servers",
}

# Added after schema v1 by the task named alongside each.
LATER_TABLES = {
    "login_rate_limits": "T8 (V9)",
    "tool_runs": "T35 (V20, V45-V47)",
    "action_requests": "T34 (V20, V32, V33, V43)",
    "action_receipts": "T36 (V46)",
}

# Named so the assertion below says what it is guarding against rather than only
# failing on a set difference: this arrives with T14, and a table appearing early
# means a migration landed ahead of the task that specifies its columns.
NOT_YET_TABLES = {"audit_log"}

# Columns that hold Fernet ciphertext (C7, V48). None may ever surface in a
# `to_safe_dict()` payload (V2, V8).
SECRET_COLUMNS = {
    "whm_servers": {
        "api_token",
        "ssh_password",
        "ssh_private_key",
        "ssh_private_key_passphrase",
    },
    "proxmox_servers": {"api_token_secret"},
    "pmg_servers": {
        "ssh_password",
        "ssh_private_key",
        "ssh_private_key_passphrase",
    },
}


def test_metadata_declares_schema_v1_plus_only_the_later_tables_landed_so_far() -> None:
    """Every table is accounted for by the task that added it.

    Schema v1 is T4's eight; `LATER_TABLES` names each addition since. An unlisted
    table means a migration landed without its task, which is how `Base.metadata` and
    the migration history start drifting.
    """
    assert set(Base.metadata.tables) == SCHEMA_V1_TABLES | set(LATER_TABLES)


def test_the_audit_log_table_has_not_landed_early() -> None:
    """T14 owns it; a column set defined before its task is a guess.

    `action_requests` left this set at T34 and `action_receipts` at T36 — each time, the
    task that specifies the columns.
    """
    assert set(Base.metadata.tables) & NOT_YET_TABLES == set()


def test_users_default_inactive() -> None:
    """V7: auto-provisioned LDAP users start disabled."""
    is_active = Base.metadata.tables["users"].c.is_active

    assert is_active.nullable is False
    assert is_active.server_default.arg == "false"


def test_mcp_token_stores_hash_not_plaintext() -> None:
    """V2: SHA-256 hex digest (64 chars), unique for lookup; no plaintext column."""
    columns = Base.metadata.tables["mcp_tokens"].c

    assert columns.token_hash.type.length == 64
    assert columns.token_hash.unique is True
    assert "token" not in columns
    assert "token_plaintext" not in columns


def test_mcp_token_carries_tofu_and_revalidation_fields() -> None:
    """V3 binding + V4 staleness live on the token row."""
    columns = Base.metadata.tables["mcp_tokens"].c

    # NULL until the first `X-Noa-LibreChat-User` header binds it (C20).
    assert columns.librechat_user_id.nullable is True
    assert columns.last_ldap_check_at.nullable is True
    assert columns.expires_at.nullable is True


def test_role_tool_permission_tool_name_is_not_a_foreign_key() -> None:
    """V10: the catalog lives in code; an unknown grant must not dangle."""
    tool_name = Base.metadata.tables["role_tool_permissions"].c.tool_name

    assert tool_name.foreign_keys == set()


def test_token_delete_cascades_from_user() -> None:
    """V4: cascade revoke on user delete happens in the DB, not in app code."""
    (fk,) = Base.metadata.tables["mcp_tokens"].c.user_id.foreign_keys

    assert fk.ondelete == "CASCADE"


def test_admin_and_internal_role_names() -> None:
    """V13, V75: `admin` reserved; `user:`-prefixed roles are internal."""
    assert ADMIN_ROLE_NAME == "admin"
    assert INTERNAL_ROLE_PREFIX == "user:"
    assert is_internal_role("user:alice@example.com") is True
    assert is_internal_role("noc-operator") is False
    assert is_internal_role(ADMIN_ROLE_NAME) is False


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (
            WHMServer,
            {
                "name": "whm-1",
                "base_url": "https://whm-1.example.com:2087",
                "api_username": "root",
                "api_token": "enc:v1:fernet:WHM-API-TOKEN",
                "ssh_password": "enc:v1:fernet:WHM-SSH-PASSWORD",
                "ssh_private_key": "enc:v1:fernet:WHM-SSH-KEY",
                "ssh_private_key_passphrase": "enc:v1:fernet:WHM-SSH-PASSPHRASE",
            },
        ),
        (
            ProxmoxServer,
            {
                "name": "pve-1",
                "base_url": "https://pve-1.example.com:8006",
                "api_token_id": "noa@pve!mcp",
                "api_token_secret": "enc:v1:fernet:PVE-TOKEN-SECRET",
            },
        ),
        (
            PMGServer,
            {
                "name": "pmg-1",
                "ssh_host": "pmg-1.example.com",
                "ssh_password": "enc:v1:fernet:PMG-SSH-PASSWORD",
                "ssh_private_key": "enc:v1:fernet:PMG-SSH-KEY",
                "ssh_private_key_passphrase": "enc:v1:fernet:PMG-SSH-PASSPHRASE",
            },
        ),
    ],
)
def test_server_safe_dict_reports_presence_never_values(
    model: type, kwargs: dict[str, str]
) -> None:
    """V2, V8: admin views expose booleans, not credentials."""
    server = model(id=uuid4(), created_at=datetime.now(UTC), **kwargs)

    safe = server.to_safe_dict()
    rendered = str(safe)

    for column in SECRET_COLUMNS[model.__tablename__]:
        assert column not in safe, f"{column} leaked as a key"
    assert "enc:v1:fernet:" not in rendered

    if "ssh_password" in kwargs:
        assert safe["has_ssh_password"] is True
        assert safe["has_ssh_private_key"] is True


def test_secret_columns_are_unbounded_text() -> None:
    """Fernet output length tracks plaintext; a VARCHAR cap would truncate (V48)."""
    for table_name, columns in SECRET_COLUMNS.items():
        table = Base.metadata.tables[table_name]
        for column_name in columns:
            column = table.c[column_name]
            assert column.type.python_type is str
            assert getattr(column.type, "length", None) is None, column_name


def test_pmg_server_is_ssh_only() -> None:
    """V58: PMG is reached over SSH + `pmgsh`; it has no HTTP surface."""
    columns = Base.metadata.tables["pmg_servers"].c

    assert columns.ssh_host.nullable is False
    assert "base_url" not in columns
    assert "verify_ssl" not in columns

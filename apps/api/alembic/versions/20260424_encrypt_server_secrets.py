"""encrypt existing plaintext server secrets

Revision ID: 20260424_encrypt_secrets
Revises: 20260421_assistant_runs
Create Date: 2026-04-24 00:00:00.000000
"""

from __future__ import annotations

import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from noa_api.core.secrets.crypto import SecretCipher, SecretKeyUnavailableError

# revision identifiers, used by Alembic.
revision: str = "20260424_encrypt_secrets"
down_revision: Union[str, None] = "20260421_assistant_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# WHM columns that must be encrypted at rest.
_WHM_SECRET_COLUMNS = (
    "api_token",
    "ssh_password",
    "ssh_private_key",
    "ssh_private_key_passphrase",
)

# Proxmox columns that must be encrypted at rest.
_PROXMOX_SECRET_COLUMNS = ("api_token_secret",)


def _get_cipher() -> SecretCipher:
    key = os.environ.get("NOA_DB_SECRET_KEY", "")
    try:
        return SecretCipher(key=key)
    except SecretKeyUnavailableError as exc:
        raise RuntimeError(
            "NOA_DB_SECRET_KEY must be configured before running "
            "migration 20260424_encrypt_server_secrets"
        ) from exc


def _encrypt_table_columns(
    bind,
    cipher: SecretCipher,
    table: str,
    columns: tuple[str, ...],
) -> None:
    """Encrypt any plaintext values in *columns* for every row in *table*."""
    col_list = ", ".join(["id", *columns])
    rows = bind.execute(sa.text(f"SELECT {col_list} FROM {table}")).mappings().all()  # noqa: S608
    for row in rows:
        updates: dict[str, str] = {}
        for col in columns:
            value = row.get(col)
            if not isinstance(value, str):
                continue
            if cipher.is_encrypted_text(value):
                continue
            updates[col] = cipher.encrypt_text(value)
        if not updates:
            continue
        set_clause = ", ".join(f"{col} = :{col}" for col in updates)
        bind.execute(
            sa.text(f"UPDATE {table} SET {set_clause} WHERE id = :id"),  # noqa: S608
            {"id": row["id"], **updates},
        )


def upgrade() -> None:
    cipher = _get_cipher()
    bind = op.get_bind()
    _encrypt_table_columns(bind, cipher, "whm_servers", _WHM_SECRET_COLUMNS)
    _encrypt_table_columns(bind, cipher, "proxmox_servers", _PROXMOX_SECRET_COLUMNS)


def downgrade() -> None:
    # Decryption on downgrade is intentionally omitted — secrets should
    # remain encrypted.  Operators who need plaintext can decrypt manually
    # using the NOA_DB_SECRET_KEY.
    pass

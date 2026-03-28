"""whm ssh and secret encryption

Revision ID: 0007_whm_ssh_secret
Revises: 0006_action_receipts
Create Date: 2026-03-28 00:00:00.000000
"""

from __future__ import annotations

import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from noa_api.core.secrets.crypto import SecretCipher, SecretKeyUnavailableError

# revision identifiers, used by Alembic.
revision: str = "0007_whm_ssh_secret"
down_revision: Union[str, None] = "0006_action_receipts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _get_cipher() -> SecretCipher:
    key = os.environ.get("NOA_DB_SECRET_KEY", "")
    try:
        return SecretCipher(key=key)
    except SecretKeyUnavailableError as exc:
        raise RuntimeError(
            "NOA_DB_SECRET_KEY must be configured before running migration 0007_whm_ssh_and_secret_encryption"
        ) from exc


def upgrade() -> None:
    op.add_column(
        "whm_servers", sa.Column("ssh_username", sa.String(length=255), nullable=True)
    )
    op.add_column("whm_servers", sa.Column("ssh_port", sa.Integer(), nullable=True))
    op.add_column("whm_servers", sa.Column("ssh_password", sa.Text(), nullable=True))
    op.add_column("whm_servers", sa.Column("ssh_private_key", sa.Text(), nullable=True))
    op.add_column(
        "whm_servers",
        sa.Column("ssh_private_key_passphrase", sa.Text(), nullable=True),
    )
    op.add_column(
        "whm_servers",
        sa.Column("ssh_host_key_fingerprint", sa.String(length=255), nullable=True),
    )

    cipher = _get_cipher()
    bind = op.get_bind()
    rows = (
        bind.execute(sa.text("SELECT id, api_token FROM whm_servers")).mappings().all()
    )
    for row in rows:
        api_token = row.get("api_token")
        if not isinstance(api_token, str) or cipher.is_encrypted_text(api_token):
            continue
        bind.execute(
            sa.text("UPDATE whm_servers SET api_token = :api_token WHERE id = :id"),
            {"id": row["id"], "api_token": cipher.encrypt_text(api_token)},
        )


def downgrade() -> None:
    op.drop_column("whm_servers", "ssh_host_key_fingerprint")
    op.drop_column("whm_servers", "ssh_private_key_passphrase")
    op.drop_column("whm_servers", "ssh_private_key")
    op.drop_column("whm_servers", "ssh_password")
    op.drop_column("whm_servers", "ssh_port")
    op.drop_column("whm_servers", "ssh_username")

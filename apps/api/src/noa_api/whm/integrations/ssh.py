from __future__ import annotations

from typing import Protocol
from urllib.parse import urlsplit

from noa_api.core.remote_exec.ssh import SSHExecutionError
from noa_api.core.remote_exec.types import SSHConnectionConfig
from noa_api.core.secrets.crypto import maybe_decrypt_text
from noa_api.whm.integrations.client import WHMClient


class WHMServerSecretLike(Protocol):
    base_url: str
    api_username: str
    api_token: str
    verify_ssl: bool
    ssh_username: str | None
    ssh_port: int | None
    ssh_password: str | None
    ssh_private_key: str | None
    ssh_private_key_passphrase: str | None
    ssh_host_key_fingerprint: str | None


def build_whm_client(server: WHMServerSecretLike) -> WHMClient:
    return WHMClient(
        base_url=server.base_url,
        api_username=server.api_username,
        api_token=maybe_decrypt_text(server.api_token),
        verify_ssl=server.verify_ssl,
    )


def has_ssh_credentials(server: WHMServerSecretLike) -> bool:
    return bool(server.ssh_password or server.ssh_private_key)


def resolve_whm_ssh_config(
    server: WHMServerSecretLike, *, require_host_key_fingerprint: bool
) -> SSHConnectionConfig:
    hostname = urlsplit(server.base_url).hostname
    if not hostname:
        raise SSHExecutionError(
            code="ssh_invalid_host",
            message="WHM server base URL does not include a valid SSH hostname",
        )

    if not has_ssh_credentials(server):
        raise SSHExecutionError(
            code="ssh_not_configured",
            message="SSH credentials are not configured for this WHM server",
        )

    fingerprint = (server.ssh_host_key_fingerprint or "").strip() or None
    if require_host_key_fingerprint and fingerprint is None:
        raise SSHExecutionError(
            code="ssh_host_key_not_validated",
            message="SSH host key fingerprint is not validated for this WHM server",
        )

    username = (server.ssh_username or "").strip() or "root"
    return SSHConnectionConfig(
        host=hostname,
        port=server.ssh_port or 22,
        username=username,
        password=(
            maybe_decrypt_text(server.ssh_password)
            if server.ssh_password is not None
            else None
        ),
        private_key=(
            maybe_decrypt_text(server.ssh_private_key)
            if server.ssh_private_key is not None
            else None
        ),
        private_key_passphrase=(
            maybe_decrypt_text(server.ssh_private_key_passphrase)
            if server.ssh_private_key_passphrase is not None
            else None
        ),
        host_key_fingerprint=fingerprint,
    )

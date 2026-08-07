"""Doubles for the PMG integration layer (T18).

One thing lives here — a `pmg_servers`-shaped row. The SSH transport doubles are shared and sit
in `support/remote_exec.py`; the cipher is in `support/secrets.py` (V66).
"""

from __future__ import annotations

from dataclasses import dataclass

from support.remote_exec import PINNED_FINGERPRINT, SSH_PASSWORD

PMG_HOST = "pmg.example.com"


@dataclass
class FakePMGServer:
    """A `pmg_servers` row, structurally satisfying `PMGServerSecretLike`.

    Defaults describe a *usable* server — pinned, credentialed, root — so each test overrides
    only the one field it is about. No `base_url` and no `verify_ssl`: PMG is SSH-only (V58), and
    `core.db.models.PMGServer` carries neither.
    """

    ssh_host: str = PMG_HOST
    ssh_username: str | None = None
    ssh_port: int | None = None
    ssh_password: str | None = SSH_PASSWORD
    ssh_private_key: str | None = None
    ssh_private_key_passphrase: str | None = None
    ssh_host_key_fingerprint: str | None = PINNED_FINGERPRINT


__all__ = ["PMG_HOST", "FakePMGServer"]

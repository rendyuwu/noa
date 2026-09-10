"""Doubles for anything that holds an encrypted secret.

`build_cipher` started in `support/whm.py`. Proxmox needs it too, PMG and the admin
server routes will, and importing a WHM-named helper from a Proxmox test would be a lie
about what it is — so it lives here. This is its one home; the re-export `support/whm.py`
carried through the PMG port is gone since the doubles moved to their one home.
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from core.secrets.crypto import SecretCipher


def build_cipher() -> SecretCipher:
    """A cipher on a throwaway key. Real Fernet, so `maybe_decrypt_text` is really exercised."""
    return SecretCipher(key=Fernet.generate_key().decode())


__all__ = ["build_cipher"]

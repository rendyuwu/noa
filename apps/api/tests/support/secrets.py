"""Doubles for anything that holds an encrypted secret (T15, T16, T17).

`build_cipher` started in `support/whm.py` (T16). Proxmox needs it too, PMG (T18) and the admin
server routes (T54) will, and importing a WHM-named helper from a Proxmox test would be a lie
about what it is — so it lives here (V66). This is its one home; the re-export `support/whm.py`
carried through T18 is gone as of T72.
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from core.secrets.crypto import SecretCipher


def build_cipher() -> SecretCipher:
    """A cipher on a throwaway key. Real Fernet, so `maybe_decrypt_text` is really exercised."""
    return SecretCipher(key=Fernet.generate_key().decode())


__all__ = ["build_cipher"]

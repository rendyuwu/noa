"""NOA shared core.

Shared by all three deployables (C12). Landed:

- `core.config`      — pydantic-settings, all env vars (T5)
- `core.db`          — schema v1 models + declarative base (T4)

Still to come:

- `core.remote_exec` — SSH, banner stripping, `sudo -n`, host-key pinning (T14)
- `core.secrets`     — Fernet `SecretCipher`, password gen, yopass (T15)
"""

__all__: list[str] = []

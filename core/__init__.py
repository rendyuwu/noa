"""NOA shared core.

Shared by all three deployables (C12). Submodules land in later tasks:

- `core.config`      — pydantic-settings, all env vars (T5)
- `core.remote_exec` — SSH, banner stripping, `sudo -n`, host-key pinning (T14)
- `core.secrets`     — Fernet `SecretCipher`, password gen, yopass (T15)
"""

__all__: list[str] = []

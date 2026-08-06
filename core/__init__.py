"""NOA shared core.

Shared by all three deployables (C12). Landed:

- `core.config`      — pydantic-settings, all env vars (T5)
- `core.db`          — schema v1 models + declarative base (T4)
- `core.errors`      — `NoaError`, the base every client-visible failure shares (V73)
- `core.auth`        — LDAP, session JWT, login, RBAC engine (T6-T9)
- `core.audit`       — admin audit events (T9, V14)

Still to come:

- `core.remote_exec` — SSH, banner stripping, `sudo -n`, host-key pinning (T14)
- `core.secrets`     — Fernet `SecretCipher`, password gen, yopass (T15)
"""

__all__: list[str] = []

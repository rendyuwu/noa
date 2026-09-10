"""NOA shared core.

Shared by all three deployables. Landed:

- `core.config`      — pydantic-settings, all env vars
- `core.db`          — schema v1 models + declarative base
- `core.errors`      — `NoaError`, the base every client-visible failure shares
- `core.auth`        — LDAP, session JWT, login, RBAC engine
- `core.audit`       — admin audit events
- `core.remote_exec` — SSH, banner stripping, `sudo -n`, host-key pinning

Still to come:

- `core.secrets`     — Fernet `SecretCipher`, password gen, yopass
"""

__all__: list[str] = []

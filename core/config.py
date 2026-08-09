"""Application settings — one source of truth for every env var (T5).

Shared by all three deployables' server-side code (C12). Field names map 1:1 to
env var names, case-insensitively: `postgres_url` ← `POSTGRES_URL`. `.env.example`
is the documented surface; anything added here belongs there too.

Environment-sensitive rules, enforced at construction so a misconfig fails at
startup rather than mid-request:

- `NOA_SECRET_ENCRYPTION_KEY` required in production, auto-generated in dev (V52).
  It encrypts server credentials, not the database — the name says so (C7).
- `AUTH_JWT_SECRET` required in production, ≥32 chars, auto-generated in dev (V53).
- `ldap://` refused in production unless explicitly allowed; dev LDAP bypass
  refused outside development/test (C4).

List-valued vars use JSON arrays (C11): `AUTH_BOOTSTRAP_ADMIN_EMAILS=["a@b.com"]`.
A bare comma-separated string is accepted as a convenience.
"""

from __future__ import annotations

import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from cryptography.fernet import Fernet
from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Environments that get generated secrets and relaxed transport rules. Everything
# else — staging included — is treated as production.
DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "dev", "test"})

# Repo-root marker used to locate `.env` regardless of the process's cwd, so
# `uv run` from `apps/api` and from the root read the same file.
REPO_MARKER = "AGENTS.md"

DEFAULT_POSTGRES_URL = "postgresql+asyncpg://noa:noa@localhost:5432/noa"

MIN_JWT_SECRET_LENGTH = 32


def resolve_env_file(*, start: Path, cwd: Path) -> Path | None:
    """Find the `.env` to load: nearest repo root above `start`, else `cwd`."""
    start_dir = start if start.is_dir() else start.parent

    for current in (start_dir, *start_dir.parents):
        if not (current / REPO_MARKER).exists():
            continue
        repo_env_file = current / ".env"
        return repo_env_file if repo_env_file.exists() else None

    cwd_env_file = cwd / ".env"
    return cwd_env_file if cwd_env_file.exists() else None


def _parse_string_list(value: object) -> object:
    """Coerce a JSON array, or a comma-separated string, into `list[str]` (C11)."""
    if value is None:
        return []

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                # Malformed JSON array: fall through to comma splitting rather than
                # silently dropping the whole value.
                pass
            else:
                if not isinstance(decoded, list):
                    return []
                value = decoded
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]

    if isinstance(value, (list, tuple, set, frozenset)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    return value


class Settings(BaseSettings):
    """Every NOA env var, validated."""

    model_config = SettingsConfigDict(
        env_file=str(resolve_env_file(start=Path(__file__).resolve(), cwd=Path.cwd()) or ""),
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime ---
    environment: str = "development"

    # --- Database (C3) ---
    postgres_url: PostgresDsn = cast(PostgresDsn, DEFAULT_POSTGRES_URL)
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    # --- Secret encryption (C7, V52) ---
    # Encrypts server credentials (SSH creds, API tokens), NOT the database.
    noa_secret_encryption_key: SecretStr | None = None

    # --- Auth / JWT (V6, V53) ---
    auth_jwt_secret: SecretStr | None = None
    auth_jwt_algorithm: str = "HS256"
    auth_jwt_access_token_ttl_seconds: int = Field(default=3600, ge=60)
    auth_session_cookie_name: str = "noa_session"
    # `.noa.internal` so the cookie rides to the embed origin too (V40).
    auth_session_cookie_domain: str | None = None
    auth_session_cookie_path: str = "/"
    auth_session_cookie_samesite: str = "lax"
    auth_session_cookie_secure: bool = True
    auth_bootstrap_admin_emails: list[str] = Field(default_factory=list)
    auth_dev_bypass_ldap: bool = False

    # Login rate limiting (V9)
    auth_login_rate_limit_window_seconds: int = Field(default=60, ge=1)
    auth_login_rate_limit_max_attempts: int = Field(default=5, ge=1)
    auth_login_rate_limit_block_seconds: int = Field(default=600, ge=1)

    # --- LDAP (C4, V7) ---
    ldap_server_uri: str = "ldap://localhost:389"
    ldap_allow_insecure_transport: bool = False
    ldap_bind_dn: str = ""
    ldap_bind_password: SecretStr = SecretStr("")
    ldap_base_dn: str = "dc=example,dc=com"
    ldap_user_filter: str = "(|(mail={email})(userPrincipalName={email}))"
    ldap_timeout_seconds: int = Field(default=5, ge=1)

    # --- MCP tokens (C5, V4) ---
    # Staleness interval for LDAP revalidation; LDAP down -> fail closed (V4).
    mcp_token_ldap_revalidate_seconds: int = Field(default=900, ge=0)
    # None -> non-expiring until revoked (V2: revoke = delete row).
    mcp_token_ttl_seconds: int | None = Field(default=None, ge=60)

    # Failed-MCP-auth rate limiting (V9, T12). Same numbers as login: the buckets are
    # keyed per LibreChat account and per presented token, never per source address
    # (in-cluster it identifies nothing), so a block costs one account, not the fleet.
    mcp_auth_rate_limit_window_seconds: int = Field(default=60, ge=1)
    mcp_auth_rate_limit_max_attempts: int = Field(default=5, ge=1)
    mcp_auth_rate_limit_block_seconds: int = Field(default=600, ge=1)

    # --- Approval gate (V30, V31, V32) ---
    approval_max_inflight_per_user: int = Field(default=1, ge=1)
    approval_pending_ttl_seconds: int = Field(default=3600, ge=60)
    # How often T39's background sweep looks for pending requests past their deadline. Not
    # derived from the TTL: it is a resolution, not a lifetime — how late a request may still
    # read PENDING after it stopped being answerable, which is bounded by this and not by how
    # long the request was given. `ge=1` because a sweeper that never runs is V32 held by
    # nothing at all.
    approval_expiry_sweep_interval_seconds: int = Field(default=60, ge=1)
    # How long a `tool_runs` row may sit STARTED before T38's reaper calls it abandoned. This
    # *is* a lifetime, unlike the interval above, and the number is a judgement about the
    # slowest legitimate change: 15 minutes covers an SSH round trip to an unhappy host with
    # room to spare, and is short enough that V31's cap — which counts STARTED runs — is not
    # spent for hours by one crashed process. `ge=60` because a deadline shorter than a minute
    # would reap changes that are merely slow, and a reaped run reads as an outcome nobody
    # observed.
    approval_stranded_run_reap_after_seconds: int = Field(default=900, ge=60)
    # How often the reaper looks. A resolution, like the sweep interval, and deliberately not
    # derived from the deadline above.
    approval_stranded_run_reap_interval_seconds: int = Field(default=120, ge=1)
    # How many stranded runs one pass may resolve. A bound the pass is held to, not a knob for
    # throughput: without it a pass after a long outage loads every stranded row — each carrying
    # its request's evidence — and issues an UPDATE plus a receipt INSERT for each inside one
    # transaction, holding all of those row locks and that transaction's xmin until the last
    # write lands.
    #
    # Drain rate is this over the interval above: 100 per 120s, so 50 a minute and 3,000 an
    # hour. That beats the rate stranded rows appear at, because appearing costs a process
    # death mid-call or a failed closing audit write (T73) — the population that can strand at
    # one instant is the runs in flight at that instant, and V31's cap bounds the CHANGE half of
    # it per operator. A *sustained* 50 a minute would mean NOA is failing that many calls a
    # minute, which is not a backlog a reaper is the remedy for. The two knobs compose: an
    # operator who needs a faster drain lowers the interval, which is what a resolution is for.
    #
    # `le` as much as `ge`: a batch size with no ceiling is the unbounded pass again, spelled in
    # an env var.
    approval_stranded_run_reap_batch_size: int = Field(default=100, ge=1, le=1000)

    # --- Origins / CORS ---
    api_cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:3001"]
    )
    api_cors_allow_credentials: bool = True
    # Base of the approval URL handed to the operator (V26: carries an id only).
    noa_embed_base_url: str = "http://localhost:3001"
    # LibreChat origin allowed to frame the embed app (V41).
    noa_librechat_origin: str = "https://chat.noa.internal"
    # Server-side proxy target for the web apps; browsers never call the API direct.
    noa_api_url: str = "http://localhost:8000"

    # --- yopass (C15, V50) ---
    # Absent -> the reset tool reports `yopass_not_configured`; the app still boots.
    yopass_base_url: str | None = None
    # Bounded so a misconfig surfaces at startup, not mid-execute.
    yopass_secret_expiration_seconds: int = Field(default=604800, ge=1)
    yopass_one_time: bool = False
    secret_password_length: int = Field(default=24, ge=8)

    # --- Validators ---

    @field_validator("api_cors_allowed_origins", "auth_bootstrap_admin_emails", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: object) -> object:
        return _parse_string_list(value)

    @field_validator("auth_bootstrap_admin_emails", mode="after")
    @classmethod
    def _lowercase_emails(cls, value: list[str]) -> list[str]:
        """Emails compare case-insensitively; normalize once, here (V7)."""
        return [email.lower() for email in value]

    @field_validator(
        "yopass_base_url",
        "noa_embed_base_url",
        "noa_librechat_origin",
        "noa_api_url",
        mode="before",
    )
    @classmethod
    def _normalize_base_url(cls, value: object) -> object:
        """Strip whitespace and trailing slashes so URL joins stay predictable."""
        if isinstance(value, str):
            normalized = value.strip().rstrip("/")
            return normalized or None
        return value

    @field_validator("auth_session_cookie_domain", "mcp_token_ttl_seconds", mode="before")
    @classmethod
    def _empty_string_is_none(cls, value: object) -> object:
        """`VAR=` in `.env` means "unset", not empty string / parse error."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("auth_session_cookie_samesite", mode="after")
    @classmethod
    def _validate_samesite(cls, value: str) -> str:
        """V6 wants `Lax`; `none`/`strict` allowed but must be a real value."""
        normalized = value.strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("auth_session_cookie_samesite must be one of: lax, strict, none")
        return normalized

    @model_validator(mode="after")
    def _apply_environment_rules(self) -> Settings:
        """Secrets and transport rules, per environment (V52, V53, C4)."""
        if self.is_development:
            # Local dev runs over plain HTTP, so a Secure cookie would never be sent.
            self.auth_session_cookie_secure = False
        elif self.auth_dev_bypass_ldap:
            raise ValueError(
                "auth_dev_bypass_ldap is only allowed in development/test environments"
            )

        self._validate_cookie_transport()
        self._resolve_jwt_secret()
        self._resolve_encryption_key()
        self._validate_ldap_transport()
        return self

    def _validate_cookie_transport(self) -> None:
        """`SameSite=None` without `Secure` is a cookie the browser throws away.

        Runs after the development override above, because that override is what
        creates the trap: `samesite=none` is the attribute someone reaches for when
        testing the cross-site embed locally (SameSite=Lax will not ride into a
        third-party iframe, C17/C18), and development forces `secure=False`. The
        combination sets a cookie the browser silently drops, so the approval POST
        arrives unauthenticated with nothing in the logs to say why.
        """
        if self.auth_session_cookie_samesite == "none" and not self.auth_session_cookie_secure:
            raise ValueError(
                "auth_session_cookie_samesite=none requires a Secure cookie; browsers "
                "discard SameSite=None without Secure. Use samesite=lax, or serve this "
                "environment over HTTPS (development forces secure=False)"
            )

    def _resolve_jwt_secret(self) -> None:
        """V53: required in production, ≥32 chars; generated in dev."""
        secret = self.auth_jwt_secret.get_secret_value() if self.auth_jwt_secret else ""

        if not secret.strip():
            if not self.is_development:
                raise ValueError(
                    "auth_jwt_secret is required outside development/test environments"
                )
            self.auth_jwt_secret = SecretStr(secrets.token_urlsafe(48))
            return

        if not self.is_development and len(secret) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"auth_jwt_secret must be at least {MIN_JWT_SECRET_LENGTH} characters "
                "outside development/test environments"
            )

    def _resolve_encryption_key(self) -> None:
        """V52: required in production, generated in dev; always a valid Fernet key.

        A malformed key fails here rather than at the first credential decrypt,
        where it would look like a broken server instead of a broken config.
        """
        key = (
            self.noa_secret_encryption_key.get_secret_value()
            if self.noa_secret_encryption_key
            else ""
        ).strip()

        if not key:
            if not self.is_development:
                raise ValueError(
                    "noa_secret_encryption_key is required outside development/test "
                    "environments (it encrypts server credentials, C7)"
                )
            self.noa_secret_encryption_key = SecretStr(Fernet.generate_key().decode())
            return

        try:
            Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "noa_secret_encryption_key must be a urlsafe-base64-encoded 32-byte "
                "Fernet key; generate one with "
                '`python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"`'
            ) from exc

        self.noa_secret_encryption_key = SecretStr(key)

    def _validate_ldap_transport(self) -> None:
        """C4: LDAP carries a service-account bind — TLS unless explicitly waived."""
        if self.is_development:
            return

        if self.ldap_server_uri.strip().lower().startswith("ldap://") and (
            not self.ldap_allow_insecure_transport
        ):
            raise ValueError("ldaps:// is required for LDAP outside development/test environments")

    # --- Derived accessors ---

    @property
    def is_development(self) -> bool:
        """True for development/dev/test. Staging counts as production."""
        return self.environment.strip().lower() in DEVELOPMENT_ENVIRONMENTS

    @property
    def is_production(self) -> bool:
        return not self.is_development

    @property
    def postgres_url_str(self) -> str:
        """DSN as a string, for SQLAlchemy and Alembic."""
        return str(self.postgres_url)

    @property
    def secret_encryption_key(self) -> str:
        """The resolved Fernet key. Non-empty after validation (V52)."""
        if self.noa_secret_encryption_key is None:  # pragma: no cover - validator guarantees
            raise RuntimeError("noa_secret_encryption_key is not configured")
        return self.noa_secret_encryption_key.get_secret_value()

    @property
    def ldap_bind_password_value(self) -> str:
        """Service-account password, unwrapped at the call site only (V8)."""
        return self.ldap_bind_password.get_secret_value()

    @property
    def jwt_secret(self) -> str:
        """The resolved JWT signing secret. Non-empty after validation (V53)."""
        if self.auth_jwt_secret is None:  # pragma: no cover - validator guarantees
            raise RuntimeError("auth_jwt_secret is not configured")
        return self.auth_jwt_secret.get_secret_value()

    @property
    def yopass_configured(self) -> bool:
        """False -> the reset tool reports `yopass_not_configured` (C15)."""
        return bool(self.yopass_base_url)

    def session_cookie_kwargs(self) -> dict[str, Any]:
        """Cookie attributes for set/clear, so both paths cannot drift (V6)."""
        return {
            "key": self.auth_session_cookie_name,
            "domain": self.auth_session_cookie_domain,
            "path": self.auth_session_cookie_path,
            "samesite": self.auth_session_cookie_samesite,
            "secure": self.auth_session_cookie_secure,
            "httponly": True,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, built once.

    Cached rather than module-level so importing `core.config` cannot fail on a bad
    environment, and so tests can call `get_settings.cache_clear()`.
    """
    return Settings()

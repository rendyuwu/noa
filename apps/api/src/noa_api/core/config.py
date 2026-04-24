import json
import secrets
from pathlib import Path
from typing import Annotated, cast

from fastapi import FastAPI
from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def resolve_env_file(*, start: Path, cwd: Path) -> Path | None:
    start_dir = start if start.is_dir() else start.parent

    for current in (start_dir, *start_dir.parents):
        repo_marker = current / "AGENTS.md"
        if not repo_marker.exists():
            continue

        repo_env_file = current / ".env"
        if repo_env_file.exists():
            return repo_env_file
        break

    cwd_env_file = cwd / ".env"
    if cwd_env_file.exists():
        return cwd_env_file

    return None


_resolved_env_file = resolve_env_file(start=Path(__file__).resolve(), cwd=Path.cwd())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_resolved_env_file) if _resolved_env_file else None,
        extra="ignore",
    )

    environment: str = "development"
    postgres_url: PostgresDsn = cast(
        PostgresDsn, "postgresql+asyncpg://postgres:postgres@localhost:5432/noa"
    )
    noa_db_secret_key: SecretStr | None = None
    telemetry_enabled: bool = False
    telemetry_service_name: str = "noa-api"
    telemetry_otlp_endpoint: str | None = None
    telemetry_otlp_headers: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict
    )
    telemetry_traces_enabled: bool = True
    telemetry_metrics_enabled: bool = True
    auth_jwt_secret: SecretStr | None = None
    auth_jwt_algorithm: str = "HS256"
    auth_jwt_access_token_ttl_seconds: int = 3600
    auth_login_rate_limit_window_seconds: int = 60
    auth_login_rate_limit_max_attempts: int = 5
    auth_login_rate_limit_block_seconds: int = 600
    auth_bootstrap_admin_emails: set[str] = Field(default_factory=set)
    auth_dev_bypass_ldap: bool = False
    auth_cookie_name: str = "noa_session"
    auth_cookie_secure: bool = True
    auth_cookie_domain: str | None = None
    auth_cookie_path: str = "/"
    auth_cookie_samesite: str = "lax"
    api_cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    api_cors_allow_credentials: bool = True
    ldap_server_uri: str = "ldap://localhost:389"
    ldap_allow_insecure_transport: bool = False
    ldap_bind_dn: str = ""
    ldap_bind_password: SecretStr = SecretStr("")
    ldap_base_dn: str = "dc=example,dc=com"
    ldap_user_filter: str = "(|(mail={email})(userPrincipalName={email}))"
    ldap_timeout_seconds: int = 5
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_system_prompt: str | None = None
    llm_system_prompt_path: str | None = None
    llm_system_prompt_extra_paths: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )

    @field_validator("telemetry_otlp_headers", mode="before")
    @classmethod
    def _normalize_telemetry_otlp_headers(cls, value: object) -> object:
        if value is None:
            return {}
        if isinstance(value, str):
            headers: dict[str, str] = {}
            for item in value.split(","):
                entry = item.strip()
                if not entry:
                    continue
                if "=" not in entry:
                    continue
                key, _, raw_value = entry.partition("=")
                header_name = key.strip()
                if not header_name:
                    continue
                headers[header_name] = raw_value.strip()
            return headers
        return value

    @field_validator("api_cors_allowed_origins", mode="before")
    @classmethod
    def _normalize_cors_origins(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    value = raw
                else:
                    if not isinstance(value, list):
                        return []
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [
                item.strip() for item in value if isinstance(item, str) and item.strip()
            ]
        return value

    @field_validator("llm_system_prompt", "llm_system_prompt_path", mode="before")
    @classmethod
    def _normalize_optional_prompt_strings(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("llm_system_prompt_extra_paths", mode="before")
    @classmethod
    def _normalize_prompt_extra_paths(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    value = raw
                else:
                    if not isinstance(value, list):
                        return []
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [
                item.strip() for item in value if isinstance(item, str) and item.strip()
            ]
        return value

    @model_validator(mode="after")
    def validate_auth_settings(self) -> "Settings":
        env = self.environment.lower()
        secret_value = (
            self.auth_jwt_secret.get_secret_value()
            if self.auth_jwt_secret is not None
            else ""
        )

        if self.auth_dev_bypass_ldap and env not in {"development", "dev", "test"}:
            raise ValueError(
                "auth_dev_bypass_ldap can only be enabled in development/test environments"
            )

        if env in {"development", "dev", "test"}:
            self.auth_cookie_secure = False

        if not secret_value:
            if env in {"development", "dev", "test"}:
                self.auth_jwt_secret = SecretStr(secrets.token_urlsafe(48))
                return self
            raise ValueError(
                "auth_jwt_secret is required outside development/test environments"
            )

        if len(secret_value) < 32 and env not in {"development", "dev", "test"}:
            raise ValueError(
                "auth_jwt_secret must be at least 32 characters in non-development environments"
            )

        if env not in {"development", "dev", "test"}:
            server_uri = self.ldap_server_uri.lower().strip()
            if (
                server_uri.startswith("ldap://")
                and not self.ldap_allow_insecure_transport
            ):
                raise ValueError(
                    "ldaps:// is required for LDAP in non-development environments"
                )

        return self


def get_required_llm_api_key(app_settings: Settings) -> str:
    api_key = (
        app_settings.llm_api_key.get_secret_value()
        if app_settings.llm_api_key is not None
        else ""
    )
    if not api_key.strip():
        raise ValueError("llm_api_key is required")
    return api_key


def get_app_settings(app: FastAPI) -> Settings:
    app_settings = getattr(app.state, "settings", None)
    if not isinstance(app_settings, Settings):
        raise RuntimeError("app settings are not configured")
    return app_settings


settings = Settings()

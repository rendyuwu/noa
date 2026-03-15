from typing import Annotated

import secrets

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    postgres_url: PostgresDsn = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/noa"
    )
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
    auth_bootstrap_admin_emails: set[str] = Field(default_factory=set)
    auth_dev_bypass_ldap: bool = False
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
    llm_system_prompt: str = (
        "You are NOA. Use tools when they help answer the user request.\n"
        "\n"
        "When coordinating multi-step operational workflows:\n"
        "- Use update_workflow_todo to create and keep a checklist up to date.\n"
        "- Before running any WHM CHANGE tool, run the relevant WHM preflight tool(s) and summarize evidence.\n"
        "- For WHM CHANGE tools: after preflight and collecting required args, call the CHANGE tool and use the approval card (request_approval); do not ask the user to confirm in chat.\n"
        "- For CSF TTL actions, convert durations to minutes and use duration_minutes.\n"
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
            return [item.strip() for item in value.split(",") if item.strip()]
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


settings = Settings()

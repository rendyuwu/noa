import secrets

from pydantic import Field, PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    postgres_url: PostgresDsn = "postgresql+asyncpg://postgres:postgres@localhost:5432/noa"
    auth_jwt_secret: SecretStr | None = None
    auth_jwt_algorithm: str = "HS256"
    auth_jwt_access_token_ttl_seconds: int = 3600
    auth_bootstrap_admin_emails: set[str] = Field(default_factory=set)
    ldap_server_uri: str = "ldap://localhost:389"
    ldap_bind_dn: str = ""
    ldap_bind_password: SecretStr = SecretStr("")
    ldap_base_dn: str = "dc=example,dc=com"
    ldap_user_filter: str = "(|(mail={email})(userPrincipalName={email}))"
    ldap_timeout_seconds: int = 5

    @model_validator(mode="after")
    def validate_auth_settings(self) -> "Settings":
        env = self.environment.lower()
        secret_value = self.auth_jwt_secret.get_secret_value() if self.auth_jwt_secret is not None else ""

        if not secret_value:
            if env in {"development", "dev", "test"}:
                self.auth_jwt_secret = SecretStr(secrets.token_urlsafe(48))
                return self
            raise ValueError("auth_jwt_secret is required outside development/test environments")

        if len(secret_value) < 32 and env not in {"development", "dev", "test"}:
            raise ValueError("auth_jwt_secret must be at least 32 characters in non-development environments")

        return self


settings = Settings()

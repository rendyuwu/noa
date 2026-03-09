from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    postgres_url: PostgresDsn = "postgresql+asyncpg://postgres:postgres@localhost:5432/noa"


settings = Settings()

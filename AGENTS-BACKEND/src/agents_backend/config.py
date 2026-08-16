from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_anon_key: SecretStr = Field(alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: SecretStr = Field(alias="SUPABASE_SERVICE_ROLE_KEY")
    database_url: SecretStr = Field(alias="DATABASE_URL")
    openai_api_key: SecretStr = Field(alias="OPENAI_API_KEY")
    openai_model_extraction: str = Field(alias="OPENAI_MODEL_EXTRACTION")
    openai_model_answering: str = Field(alias="OPENAI_MODEL_ANSWERING")
    openai_reasoning_effort_extraction: Literal["none", "low", "medium", "high", "xhigh", "max"] = (
        Field(default="none", alias="OPENAI_REASONING_EFFORT_EXTRACTION")
    )
    openai_reasoning_effort_answering: Literal["none", "low", "medium", "high", "xhigh", "max"] = (
        Field(default="none", alias="OPENAI_REASONING_EFFORT_ANSWERING")
    )
    openai_model_embedding: str = Field(
        default="text-embedding-3-small", alias="OPENAI_MODEL_EMBEDDING"
    )
    worker_poll_interval_seconds: float = Field(
        default=2.0, alias="WORKER_POLL_INTERVAL_SECONDS", ge=0.1
    )
    worker_max_attempts: int = Field(default=3, alias="WORKER_MAX_ATTEMPTS", ge=1, le=10)

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, value: str) -> str:
        clean = value.rstrip("/")
        if not clean.startswith("https://") or not clean.endswith(".supabase.co"):
            raise ValueError("SUPABASE_URL deve ser uma URL HTTPS de projeto Supabase")
        return clean

    @property
    def sqlalchemy_url(self) -> str:
        value = self.database_url.get_secret_value()
        if value.startswith("postgresql+asyncpg://"):
            return value
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        return value

    @property
    def supabase_issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"

    @property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_issuer}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    app_timezone: str = Field(default="America/Sao_Paulo", alias="APP_TIMEZONE")
    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_anon_key: SecretStr = Field(alias="SUPABASE_ANON_KEY")
    supabase_service_role_key: SecretStr = Field(alias="SUPABASE_SERVICE_ROLE_KEY")
    database_url: SecretStr = Field(alias="DATABASE_URL")
    database_pooler_url: SecretStr | None = Field(default=None, alias="DATABASE_POOLER_URL")
    openai_api_key: SecretStr = Field(alias="OPENAI_API_KEY")
    openai_model_extraction: str = Field(alias="OPENAI_MODEL_EXTRACTION")
    openai_model_answering: str = Field(alias="OPENAI_MODEL_ANSWERING")
    openai_model_conversation: str = Field(
        default="gpt-5.6-luna", alias="OPENAI_MODEL_CONVERSATION"
    )
    openai_model_orchestration: str = Field(
        default="gpt-5.6-terra", alias="OPENAI_MODEL_ORCHESTRATION"
    )
    openai_reasoning_effort_extraction: Literal["none", "low", "medium", "high", "xhigh", "max"] = (
        Field(default="none", alias="OPENAI_REASONING_EFFORT_EXTRACTION")
    )
    openai_reasoning_effort_answering: Literal["none", "low", "medium", "high", "xhigh", "max"] = (
        Field(default="none", alias="OPENAI_REASONING_EFFORT_ANSWERING")
    )
    openai_reasoning_effort_conversation: Literal[
        "none", "low", "medium", "high", "xhigh", "max"
    ] = Field(default="none", alias="OPENAI_REASONING_EFFORT_CONVERSATION")
    openai_reasoning_effort_orchestration: Literal[
        "none", "low", "medium", "high", "xhigh", "max"
    ] = Field(default="medium", alias="OPENAI_REASONING_EFFORT_ORCHESTRATION")
    openai_model_embedding: str = Field(
        default="text-embedding-3-small", alias="OPENAI_MODEL_EMBEDDING"
    )
    worker_poll_interval_seconds: float = Field(
        default=0.5, alias="WORKER_POLL_INTERVAL_SECONDS", ge=0.1
    )
    worker_max_attempts: int = Field(default=3, alias="WORKER_MAX_ATTEMPTS", ge=1, le=10)
    conversation_history_messages: int = Field(
        default=12, alias="CONVERSATION_HISTORY_MESSAGES", ge=2, le=100
    )
    profile_context_max_characters: int = Field(
        default=2400, alias="PROFILE_CONTEXT_MAX_CHARACTERS", ge=400, le=8000
    )
    profile_context_max_items: int = Field(
        default=8, alias="PROFILE_CONTEXT_MAX_ITEMS", ge=1, le=30
    )
    conversation_max_steps: int = Field(default=6, alias="CONVERSATION_MAX_STEPS", ge=1, le=12)
    conversation_max_tool_calls: int = Field(
        default=6, alias="CONVERSATION_MAX_TOOL_CALLS", ge=1, le=20
    )
    conversation_max_output_tokens: int = Field(
        default=1200, alias="CONVERSATION_MAX_OUTPUT_TOKENS", ge=100, le=8000
    )
    orchestration_max_steps: int = Field(default=8, alias="ORCHESTRATION_MAX_STEPS", ge=1, le=20)
    orchestration_max_tool_calls: int = Field(
        default=10, alias="ORCHESTRATION_MAX_TOOL_CALLS", ge=1, le=40
    )
    orchestration_max_output_tokens: int = Field(
        default=1800, alias="ORCHESTRATION_MAX_OUTPUT_TOKENS", ge=100, le=12000
    )
    orchestration_task_max_attempts: int = Field(
        default=3, alias="ORCHESTRATION_TASK_MAX_ATTEMPTS", ge=1, le=10
    )
    pending_action_ttl_seconds: int = Field(
        default=600, alias="PENDING_ACTION_TTL_SECONDS", ge=60, le=3600
    )
    messaging_provider: Literal["telegram", "meta_whatsapp"] = Field(
        default="telegram", alias="MESSAGING_PROVIDER"
    )
    telegram_bot_token: SecretStr | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str | None = Field(default=None, alias="TELEGRAM_BOT_USERNAME")
    telegram_webhook_secret: SecretStr | None = Field(default=None, alias="TELEGRAM_WEBHOOK_SECRET")
    telegram_api_base_url: str = Field(
        default="https://api.telegram.org", alias="TELEGRAM_API_BASE_URL"
    )
    whatsapp_verify_token: SecretStr | None = Field(default=None, alias="WHATSAPP_VERIFY_TOKEN")
    whatsapp_app_secret: SecretStr | None = Field(default=None, alias="WHATSAPP_APP_SECRET")
    whatsapp_access_token: SecretStr | None = Field(default=None, alias="WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_number_id: str | None = Field(default=None, alias="WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_graph_api_version: str | None = Field(default=None, alias="WHATSAPP_GRAPH_API_VERSION")
    whatsapp_graph_api_base_url: str = Field(
        default="https://graph.facebook.com", alias="WHATSAPP_GRAPH_API_BASE_URL"
    )
    composio_enabled: bool = Field(default=False, alias="COMPOSIO_ENABLED")
    composio_api_key: SecretStr | None = Field(default=None, alias="COMPOSIO_API_KEY")
    composio_callback_url: str | None = Field(default=None, alias="COMPOSIO_CALLBACK_URL")
    composio_user_id_secret: SecretStr | None = Field(default=None, alias="COMPOSIO_USER_ID_SECRET")
    composio_gmail_auth_config_id: str | None = Field(
        default=None, alias="COMPOSIO_GMAIL_AUTH_CONFIG_ID"
    )
    composio_googlecalendar_auth_config_id: str | None = Field(
        default=None, alias="COMPOSIO_GOOGLECALENDAR_AUTH_CONFIG_ID"
    )
    composio_whatsapp_auth_config_id: str | None = Field(
        default=None, alias="COMPOSIO_WHATSAPP_AUTH_CONFIG_ID"
    )
    composio_timeout_seconds: float = Field(
        default=20.0, alias="COMPOSIO_TIMEOUT_SECONDS", ge=1, le=120
    )

    @field_validator("supabase_url")
    @classmethod
    def validate_supabase_url(cls, value: str) -> str:
        clean = value.rstrip("/")
        if not clean.startswith("https://") or not clean.endswith(".supabase.co"):
            raise ValueError("SUPABASE_URL deve ser uma URL HTTPS de projeto Supabase")
        return clean

    @field_validator("telegram_bot_username")
    @classmethod
    def normalize_telegram_bot_username(cls, value: str | None) -> str | None:
        return value.lstrip("@") if value else None

    @field_validator("app_timezone")
    @classmethod
    def validate_app_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("APP_TIMEZONE deve ser um identificador IANA válido") from exc
        return value

    @model_validator(mode="after")
    def validate_composio_configuration(self) -> Settings:
        if not self.composio_enabled:
            return self
        required = {
            "COMPOSIO_API_KEY": self.composio_api_key,
            "COMPOSIO_CALLBACK_URL": self.composio_callback_url,
            "COMPOSIO_USER_ID_SECRET": self.composio_user_id_secret,
            "COMPOSIO_GMAIL_AUTH_CONFIG_ID": self.composio_gmail_auth_config_id,
            "COMPOSIO_GOOGLECALENDAR_AUTH_CONFIG_ID": (self.composio_googlecalendar_auth_config_id),
            "COMPOSIO_WHATSAPP_AUTH_CONFIG_ID": self.composio_whatsapp_auth_config_id,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"Configuração Composio incompleta: {', '.join(missing)}")
        if not str(self.composio_callback_url).startswith("https://"):
            raise ValueError("COMPOSIO_CALLBACK_URL deve usar HTTPS")
        return self

    @property
    def sqlalchemy_url(self) -> str:
        direct_url = make_url(self.database_url.get_secret_value())
        if self.database_pooler_url is not None:
            selected_url = make_url(self.database_pooler_url.get_secret_value())
            if selected_url.password is None:
                selected_url = selected_url.set(password=direct_url.password)
        else:
            selected_url = direct_url
        value = selected_url.render_as_string(hide_password=False)
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

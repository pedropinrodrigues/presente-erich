from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
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
    database_connect_timeout_seconds: float = Field(
        default=10.0, alias="DATABASE_CONNECT_TIMEOUT_SECONDS", ge=1, le=60
    )
    database_command_timeout_seconds: float = Field(
        default=30.0, alias="DATABASE_COMMAND_TIMEOUT_SECONDS", ge=1, le=300
    )
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
    web_research_enabled: bool = Field(default=True, alias="WEB_RESEARCH_ENABLED")
    web_research_search_context_size: Literal["low", "medium", "high"] = Field(
        default="medium", alias="WEB_RESEARCH_SEARCH_CONTEXT_SIZE"
    )
    web_research_max_tool_calls: int = Field(
        default=3, alias="WEB_RESEARCH_MAX_TOOL_CALLS", ge=1, le=10
    )
    web_research_max_sources: int = Field(default=5, alias="WEB_RESEARCH_MAX_SOURCES", ge=1, le=20)
    web_research_max_output_tokens: int = Field(
        default=1600, alias="WEB_RESEARCH_MAX_OUTPUT_TOKENS", ge=100, le=8000
    )
    web_research_country: str = Field(
        default="BR", alias="WEB_RESEARCH_COUNTRY", min_length=2, max_length=2
    )
    worker_poll_interval_seconds: float = Field(
        default=0.5, alias="WORKER_POLL_INTERVAL_SECONDS", ge=0.1
    )
    worker_max_attempts: int = Field(default=3, alias="WORKER_MAX_ATTEMPTS", ge=1, le=10)
    worker_cycle_timeout_seconds: float = Field(
        default=600.0, alias="WORKER_CYCLE_TIMEOUT_SECONDS", ge=30, le=3600
    )
    worker_max_consecutive_infra_failures: int = Field(
        default=5, alias="WORKER_MAX_CONSECUTIVE_INFRA_FAILURES", ge=1, le=100
    )
    worker_heartbeat_interval_seconds: float = Field(
        default=30.0, alias="WORKER_HEARTBEAT_INTERVAL_SECONDS", ge=5, le=300
    )
    queue_lag_warning_seconds: int = Field(
        default=60, alias="QUEUE_LAG_WARNING_SECONDS", ge=10, le=3600
    )
    daily_conversation_memory_enabled: bool = Field(
        default=True, alias="DAILY_CONVERSATION_MEMORY_ENABLED"
    )
    daily_conversation_memory_hour: int = Field(
        default=3, alias="DAILY_CONVERSATION_MEMORY_HOUR", ge=0, le=23
    )
    daily_conversation_memory_lookback_days: int = Field(
        default=7, alias="DAILY_CONVERSATION_MEMORY_LOOKBACK_DAYS", ge=1, le=30
    )
    daily_conversation_memory_scan_interval_seconds: float = Field(
        default=60.0,
        alias="DAILY_CONVERSATION_MEMORY_SCAN_INTERVAL_SECONDS",
        ge=10,
        le=3600,
    )
    daily_conversation_memory_chunk_characters: int = Field(
        default=450_000,
        alias="DAILY_CONVERSATION_MEMORY_CHUNK_CHARACTERS",
        ge=10_000,
        le=480_000,
    )
    deployment_revision: str | None = Field(default=None, alias="DEPLOYMENT_REVISION")
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
    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    scheduler_poll_interval_seconds: float = Field(
        default=1.0, alias="SCHEDULER_POLL_INTERVAL_SECONDS", ge=0.1, le=60
    )
    schedule_max_run_attempts: int = Field(
        default=3, alias="SCHEDULE_MAX_RUN_ATTEMPTS", ge=1, le=10
    )
    schedule_default_misfire_grace_seconds: int = Field(
        default=21600,
        alias="SCHEDULE_DEFAULT_MISFIRE_GRACE_SECONDS",
        ge=0,
        le=604800,
    )
    schedule_max_tool_calls: int = Field(default=12, alias="SCHEDULE_MAX_TOOL_CALLS", ge=1, le=40)
    schedule_max_concurrent_runs_per_user: int = Field(
        default=2, alias="SCHEDULE_MAX_CONCURRENT_RUNS_PER_USER", ge=1, le=20
    )
    pending_action_ttl_seconds: int = Field(
        default=600, alias="PENDING_ACTION_TTL_SECONDS", ge=60, le=3600
    )
    registration_mode: Literal["invite_only"] = Field(
        default="invite_only", alias="REGISTRATION_MODE"
    )
    invitation_policy: Literal["admin_only", "all_active_users"] = Field(
        default="admin_only", alias="INVITATION_POLICY"
    )
    invite_ttl_hours: int = Field(default=24, alias="INVITE_TTL_HOURS", ge=1, le=168)
    platform_admin_user_ids: str = Field(default="", alias="PLATFORM_ADMIN_USER_IDS")
    messaging_provider: Literal["telegram", "meta_whatsapp"] = Field(
        default="telegram", alias="MESSAGING_PROVIDER"
    )
    telegram_bot_token: SecretStr | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_bot_username: str | None = Field(default=None, alias="TELEGRAM_BOT_USERNAME")
    telegram_webhook_secret: SecretStr | None = Field(default=None, alias="TELEGRAM_WEBHOOK_SECRET")
    telegram_api_base_url: str = Field(
        default="https://api.telegram.org", alias="TELEGRAM_API_BASE_URL"
    )
    assembly_ai_api_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ASSEMBLY_AI_API_TOKEN", "ASSEMBLYAI_API_KEY"),
    )
    assemblyai_api_base_url: str = Field(
        default="https://api.assemblyai.com", alias="ASSEMBLYAI_API_BASE_URL"
    )
    assemblyai_model: str = Field(default="universal-2", alias="ASSEMBLYAI_MODEL")
    assemblyai_language_code: str = Field(default="pt", alias="ASSEMBLYAI_LANGUAGE_CODE")
    assemblyai_timeout_seconds: float = Field(
        default=30.0, alias="ASSEMBLYAI_TIMEOUT_SECONDS", ge=5, le=120
    )
    assemblyai_poll_interval_seconds: float = Field(
        default=1.0, alias="ASSEMBLYAI_POLL_INTERVAL_SECONDS", ge=0.5, le=30
    )
    assemblyai_max_audio_seconds: int = Field(
        default=120, alias="ASSEMBLYAI_MAX_AUDIO_SECONDS", ge=1, le=3600
    )
    assemblyai_max_audio_bytes: int = Field(
        default=20_000_000, alias="ASSEMBLYAI_MAX_AUDIO_BYTES", ge=1024, le=20_000_000
    )
    assemblyai_min_confidence: float = Field(
        default=0.65, alias="ASSEMBLYAI_MIN_CONFIDENCE", ge=0, le=1
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
    bitrix24_mcp_enabled: bool = Field(default=False, alias="BITRIX24_MCP_ENABLED")
    bitrix24_mcp_url: str = Field(default="https://mcp.bitrix24.com/mcp/", alias="BITRIX24_MCP_URL")
    bitrix24_public_base_url: str | None = Field(default=None, alias="BITRIX24_PUBLIC_BASE_URL")
    bitrix24_credential_encryption_key: SecretStr | None = Field(
        default=None, alias="BITRIX24_CREDENTIAL_ENCRYPTION_KEY"
    )
    bitrix24_auth_scheme: Literal["bearer", "raw"] = Field(
        default="bearer", alias="BITRIX24_AUTH_SCHEME"
    )
    bitrix24_timeout_seconds: float = Field(
        default=20.0, alias="BITRIX24_TIMEOUT_SECONDS", ge=1, le=120
    )
    bitrix24_connection_ttl_seconds: int = Field(
        default=600, alias="BITRIX24_CONNECTION_TTL_SECONDS", ge=60, le=3600
    )
    bitrix24_connection_max_attempts: int = Field(
        default=5, alias="BITRIX24_CONNECTION_MAX_ATTEMPTS", ge=1, le=10
    )
    bitrix24_expiration_scan_interval_seconds: float = Field(
        default=60.0,
        alias="BITRIX24_EXPIRATION_SCAN_INTERVAL_SECONDS",
        ge=10,
        le=3600,
    )
    bitrix24_tool_search_deals: str | None = Field(default=None, alias="BITRIX24_TOOL_SEARCH_DEALS")
    bitrix24_tool_get_deal: str | None = Field(default=None, alias="BITRIX24_TOOL_GET_DEAL")
    bitrix24_tool_update_deal: str | None = Field(default=None, alias="BITRIX24_TOOL_UPDATE_DEAL")
    bitrix24_tool_list_tasks: str | None = Field(default=None, alias="BITRIX24_TOOL_LIST_TASKS")
    bitrix24_tool_get_task: str | None = Field(default=None, alias="BITRIX24_TOOL_GET_TASK")
    bitrix24_tool_create_task: str | None = Field(default=None, alias="BITRIX24_TOOL_CREATE_TASK")
    bitrix24_tool_update_task: str | None = Field(default=None, alias="BITRIX24_TOOL_UPDATE_TASK")
    macwhisper_webhook_enabled: bool = Field(
        default=False, alias="MACWHISPER_WEBHOOK_ENABLED"
    )
    macwhisper_public_base_url: str | None = Field(
        default=None, alias="MACWHISPER_PUBLIC_BASE_URL"
    )
    macwhisper_max_payload_bytes: int = Field(
        default=600_000, alias="MACWHISPER_MAX_PAYLOAD_BYTES", ge=1024, le=2_000_000
    )
    macwhisper_default_language: str = Field(
        default="pt-BR", alias="MACWHISPER_DEFAULT_LANGUAGE", min_length=2, max_length=20
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

    @field_validator("platform_admin_user_ids")
    @classmethod
    def validate_platform_admin_user_ids(cls, value: str) -> str:
        for candidate in (item.strip() for item in value.split(",")):
            if candidate:
                uuid.UUID(candidate)
        return value

    @field_validator("app_timezone")
    @classmethod
    def validate_app_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("APP_TIMEZONE deve ser um identificador IANA válido") from exc
        return value

    @field_validator("web_research_country")
    @classmethod
    def normalize_web_research_country(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("WEB_RESEARCH_COUNTRY deve ser um código ISO de duas letras")
        return value.upper()

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

    @model_validator(mode="after")
    def validate_bitrix24_configuration(self) -> Settings:
        if not self.bitrix24_mcp_enabled:
            return self
        required = {
            "BITRIX24_PUBLIC_BASE_URL": self.bitrix24_public_base_url,
            "BITRIX24_CREDENTIAL_ENCRYPTION_KEY": self.bitrix24_credential_encryption_key,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"Configuração Bitrix24 MCP incompleta: {', '.join(missing)}")
        if self.bitrix24_mcp_url != "https://mcp.bitrix24.com/mcp/":
            raise ValueError("BITRIX24_MCP_URL deve apontar para o endpoint MCP oficial")
        if not str(self.bitrix24_public_base_url).startswith("https://"):
            raise ValueError("BITRIX24_PUBLIC_BASE_URL deve usar HTTPS")
        return self

    @model_validator(mode="after")
    def validate_macwhisper_configuration(self) -> Settings:
        if not self.macwhisper_webhook_enabled:
            return self
        if not self.macwhisper_public_base_url:
            raise ValueError(
                "MACWHISPER_PUBLIC_BASE_URL é obrigatória quando a integração está ativa"
            )
        if not self.macwhisper_public_base_url.startswith("https://"):
            raise ValueError("MACWHISPER_PUBLIC_BASE_URL deve usar HTTPS")
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

    @property
    def configured_platform_admin_ids(self) -> frozenset[uuid.UUID]:
        values = [value.strip() for value in self.platform_admin_user_ids.split(",")]
        return frozenset(uuid.UUID(value) for value in values if value)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

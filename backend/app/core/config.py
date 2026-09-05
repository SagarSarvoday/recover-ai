from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    app_environment: str = "development"
    legacy_password_bootstrap_enabled: bool = False
    development_scheduler_test_enabled: bool = False
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    recovery_max_attempts: int = Field(default=3, ge=1)
    recovery_scheduler_enabled: bool = True
    recovery_scheduler_poll_seconds: int = Field(default=15, ge=1, le=300)
    recovery_agent_worker_enabled: bool = True
    recovery_agent_poll_seconds: int = Field(default=10, ge=1, le=300)
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr | None = None
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=60, ge=1, le=1440)
    email_enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = True
    email_from: str = "RecoverAI <noreply@recoverai.local>"
    frontend_base_url: str = "http://localhost:3000"
    password_reset_token_expire_minutes: int = Field(default=20, ge=5, le=60)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

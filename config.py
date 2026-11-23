"""Validated environment; never print Settings or validation input values."""
from functools import lru_cache
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: str = "development"
    target_site_url: str = "http://127.0.0.1:5050"
    worker_api_token: SecretStr = SecretStr("")
    worker_poll_interval: int = Field(10, ge=1, le=3600)
    database_path: str = "data/reviews.sqlite3"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    llm_timeout: float = Field(25, ge=1, le=60)
    llm_attempts: int = Field(2, ge=1, le=3)
    llm_daily_requests: int = Field(50, ge=0, le=10000)
    llm_max_output_tokens: int = Field(600, ge=100, le=1500)
    external_processing_authorized: bool = False
    confidence_threshold: float = Field(0.8, ge=0, le=1)
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_user_chat_id: str = ""
    telegram_chat_id: str = ""
    telegram_allowed_user_ids: str = ""
    telegram_include_text: bool = False
    notify_non_negative: bool = False
    reviews_intake_enabled: bool = False
    privacy_policy_url: str = ""
    consent_version: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True, validate_assignment=True)

    @model_validator(mode="after")
    def validate_production(self):
        u = urlsplit(self.target_site_url)
        if u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
            raise ValueError("TARGET_SITE_URL must be a clean origin")
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("Invalid site origin")
        if self.environment == "production":
            token = self.worker_api_token.get_secret_value()
            if len(token) < 43 or len(set(token)) < 16 or token.lower() in {"change-me", "changeme"}:
                raise ValueError("Strong WORKER_API_TOKEN is required")
            if u.scheme != "https":
                raise ValueError("Production requires HTTPS")
        if self.reviews_intake_enabled and (not self.privacy_policy_url.startswith("https://") or not self.consent_version):
            raise ValueError("Intake requires approved privacy URL and consent version")
        if self.telegram_include_text and not self.external_processing_authorized:
            raise ValueError("Telegram review text requires external-processing authorization")
        return self

    @property
    def allowed_telegram_ids(self) -> set[int]:
        return {int(i.strip()) for i in self.telegram_allowed_user_ids.split(",") if i.strip().isdigit()}

    @property
    def chat_id(self):
        return self.telegram_user_chat_id or self.telegram_chat_id


@lru_cache
def get_settings():
    return Settings()

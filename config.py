from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER = "REPLACE"


class Settings(BaseSettings):
    # ------------------------------------------------------------
    # Application
    # ------------------------------------------------------------
    APP_NAME: str = "SolGuard AI"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"  # development | staging | production
    DEBUG: bool = True

    # ------------------------------------------------------------
    # Server
    # ------------------------------------------------------------
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 2
    ENABLE_DOCS: bool = True

    # ------------------------------------------------------------
    # Solana RPC
    # ------------------------------------------------------------
    SOLANA_RPC_URL: str = "https://api.mainnet-beta.solana.com"
    SOLANA_RPC_FALLBACK_URL: str = "https://solana-api.projectserum.com"
    SOLANA_COMMITMENT: str = "confirmed"  # processed | confirmed | finalized
    SOLANA_TIMEOUT_SECONDS: int = 10
    SOLANA_MAX_RETRIES: int = 3

    # ------------------------------------------------------------
    # Database
    # ------------------------------------------------------------
    DATABASE_URL: str = "sqlite:///./solguard.db"
    DB_POOL_SIZE: int = 5
    DB_ECHO_SQL: bool = False

    # ------------------------------------------------------------
    # Redis / Rate limiting
    # ------------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    RATE_LIMIT_PER_MINUTE: int = 60

    # ------------------------------------------------------------
    # AI Configuration
    # ------------------------------------------------------------
    AI_MODEL: str = "basic-risk-engine"
    RISK_SCORE_THRESHOLD: int = 70
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""

    # ------------------------------------------------------------
    # Telegram Alerts
    # ------------------------------------------------------------
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # ------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # ------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------
    ALLOWED_ORIGINS: str = "*"

    # ------------------------------------------------------------
    # Security
    # ------------------------------------------------------------
    SECRET_KEY: str = f"{PLACEHOLDER}_WITH_A_SECURE_RANDOM_SECRET"
    ADMIN_API_KEY: str = f"{PLACEHOLDER}_WITH_A_SECURE_ADMIN_API_KEY"

    # ------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------
    SENTRY_DSN: str = ""

    # ------------------------------------------------------------
    # Future Integrations
    # ------------------------------------------------------------
    DISCORD_WEBHOOK_URL: str = ""
    SLACK_WEBHOOK_URL: str = ""
    EMAIL_SMTP_HOST: str = ""
    EMAIL_SMTP_PORT: int = 587
    EMAIL_USERNAME: str = ""
    EMAIL_PASSWORD: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------
    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}, got '{v}'")
        return v_upper

    @field_validator("SOLANA_COMMITMENT")
    @classmethod
    def validate_commitment(cls, v: str) -> str:
        allowed = {"processed", "confirmed", "finalized"}
        if v not in allowed:
            raise ValueError(f"SOLANA_COMMITMENT must be one of {allowed}, got '{v}'")
        return v

    @field_validator("RISK_SCORE_THRESHOLD")
    @classmethod
    def validate_risk_threshold(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("RISK_SCORE_THRESHOLD must be between 0 and 100")
        return v

    # ------------------------------------------------------------
    # Derived / computed helpers
    # ------------------------------------------------------------
    @property
    def cors_origins(self) -> List[str]:
        if self.ALLOWED_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    def mask(self, value: str, keep: int = 4) -> str:
        """Mask a secret for safe logging/debug output."""
        if not value or len(value) <= keep:
            return "**"
        return value[:keep] + "*" * (len(value) - keep)

    def validate_production_secrets(self) -> None:
        """Call at startup — fail fast if placeholder secrets leak into prod."""
        if not self.is_production:
            return
        for field_name in ("SECRET_KEY", "ADMIN_API_KEY"):
            if PLACEHOLDER in getattr(self, field_name):
                raise RuntimeError(
                    f"{field_name} must be set to a real value in production."
                )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — avoids re-reading .env on every import/call."""
    return Settings()


settings = get_settings()
settings.validate_production_secrets()

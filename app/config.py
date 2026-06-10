from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379"
    SECRET_KEY: str
    FINNHUB_API_KEY: str
    ANTHROPIC_API_KEY: str = ""
    AI_PROVIDER: str = "anthropic"  # "anthropic" | "ollama"
    ENVIRONMENT: str = "development"

    @property
    def async_database_url(self) -> str:
        """Convert standard postgres:// URL to asyncpg format."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()

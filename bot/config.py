from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    BOT_TOKEN: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    DB_HOST: str
    DB_PORT: int
    # AI Integration
    OPENAI_API_KEY: str | None = None
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Redis settings
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379

    # Webhook settings
    WEBHOOK_HOST: str  # e.g. "https://yourdomain.com"
    WEBHOOK_PATH: str = "/webhook"
    WEBAPP_HOST: str = "0.0.0.0"
    WEBAPP_PORT: int = 8000

    # Parser Service
    PARSER_SERVICE_URL: str = "http://parser:8001"

    @property
    def database_url_async(self) -> str:
        # Формируем URL для асинхронного подключения к БД
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.POSTGRES_DB}"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

config = Settings()
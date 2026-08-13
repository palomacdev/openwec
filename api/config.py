"""
OpenWEC API — Configuration
Settings loaded from environment variables with sensible defaults.

Production: set via environment or .env file.
Development: defaults work out of the box with docker-compose.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    db_host: str = "127.0.0.1"
    db_port: int = 5433
    db_name: str = "openwec"
    db_user: str = "openwec"
    db_password: str = "openwec"

    # Redis
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379

    # API Keys
    api_keys: str = ""

    # Pagination
    default_page_size: int = 50
    max_page_size: int = 500

    @property
    def db_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def valid_api_keys(self) -> set[str]:
        if not self.api_keys:
            return set()

        return {
            key.strip()
            for key in self.api_keys.split(",")
            if key.strip()
        }


settings = Settings()
"""
OpenWEC API — Configuration
Settings loaded from environment variables with sensible defaults.

Production: set via environment or .env file.
Development: defaults work out of the box with docker-compose.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    db_host:     str = "127.0.0.1"  # override via DB_HOST env var
    db_port:     int = 5433         # override via DB_HOST env var
    db_name:     str = "openwec"    # override via DB_HOST env var
    db_user:     str = "openwec"    # override via DB_HOST env var
    db_password: str = "openwec"    # override via DB_HOST env var - CHANGE THIS IN PRODUCTION

    # API Keys — comma-separated list of valid keys
    # Example: API_KEYS="key1,key2,key3"
    api_keys: str = ""

    # Pagination
    default_page_size: int = 50
    max_page_size:     int = 500

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
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    class Config:
        env_file = "openwec\.env"
        env_file_encoding = "utf-8"


settings = Settings()
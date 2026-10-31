"""
OpenWEC API — Configuration
Settings loaded from environment variables with sensible defaults.

Production: set via environment or .env file.
Development: defaults work out of the box with docker-compose.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


# An admin credential shorter than this is treated as absent. Short values are
# almost always placeholders left over from a template, and a placeholder must
# never be the thing standing between the public and /admin/*.
ADMIN_KEY_MIN_LENGTH = 32


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

    # Administrative credentials, deliberately a separate variable.
    #
    # A key in `api_keys` may legitimately travel: into a JavaScript bundle, to
    # a third party, into a support ticket. An admin credential may not. The
    # two sets are therefore never derived from one another. Empty means
    # administration is closed — see `valid_admin_keys`.
    admin_api_keys: str = ""

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

    @property
    def valid_admin_keys(self) -> frozenset[str]:
        """
        Credentials that authorize /admin/*. Three rules, all fail-closed:

        - unset or empty -> empty set, and administration stays shut;
        - anything shorter than ADMIN_KEY_MIN_LENGTH is dropped;
        - anything that is *also* an API key is dropped.

        The last rule is the point of this property. Copying a key out of
        API_KEYS into ADMIN_API_KEYS — the natural mistake, and the one the
        old code made structurally — does not open administration. It closes
        it, which is the failure direction we want.
        """
        common = self.valid_api_keys
        return frozenset(
            key
            for key in (part.strip() for part in self.admin_api_keys.split(","))
            if len(key) >= ADMIN_KEY_MIN_LENGTH and key not in common
        )


settings = Settings()
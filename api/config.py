"""
OpenWEC API — Configuration
Settings loaded from environment variables with sensible defaults.

Production: set via environment or .env file.
Development: defaults work out of the box with docker-compose.
"""

import ipaddress

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

    # Proxies whose X-Forwarded-For may be believed.
    #
    # Comma-separated addresses or CIDR blocks. Empty — the default — means no
    # proxy is trusted and the header is ignored entirely, so a forged one
    # cannot move a caller into someone else's rate-limit bucket or out of its
    # own. That is the safe failure, and it is also the wrong production
    # configuration: behind a reverse proxy, every caller then shares the
    # proxy's address. See `trusted_proxy_networks`.
    trusted_proxies: str = ""

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

    @property
    def trusted_proxy_networks(self) -> tuple:
        """
        The parsed form of `trusted_proxies`, as networks.

        A single address is accepted and becomes a /32 or /128. An entry that
        does not parse is dropped rather than raising: a typo in this variable
        must not take the API down, and dropping it only ever *removes* trust,
        which is the direction that fails safe. `invalid_trusted_proxies`
        reports what was dropped so the start-up log can say so.
        """
        redes = []
        for entrada in self._trusted_proxy_entries():
            try:
                redes.append(ipaddress.ip_network(entrada, strict=False))
            except ValueError:
                continue
        return tuple(redes)

    @property
    def invalid_trusted_proxies(self) -> tuple[str, ...]:
        invalidas = []
        for entrada in self._trusted_proxy_entries():
            try:
                ipaddress.ip_network(entrada, strict=False)
            except ValueError:
                invalidas.append(entrada)
        return tuple(invalidas)

    def _trusted_proxy_entries(self) -> list[str]:
        return [p.strip() for p in self.trusted_proxies.split(",") if p.strip()]


settings = Settings()
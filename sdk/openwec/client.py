"""
OpenWEC SDK — Client configuration and HTTP wrapper.
"""

import email.utils
import time

import httpx


# A 429 carries Retry-After. Waiting longer than this is not a retry, it is a
# hang, so the SDK refuses to sleep past it and raises instead.
_RETRY_MAX_WAIT_SECONDS = 60.0


class _Config:
    base_url:    str = "https://api.openwec.com/api/v1"
    api_key:     str | None = None
    max_retries: int = 0
    retry_max_wait: float = _RETRY_MAX_WAIT_SECONDS


_config = _Config()


def configure(
    base_url: str | None = None,
    api_key: str | None = None,
    max_retries: int | None = None,
    retry_max_wait: float | None = None,
):
    """
    Configure the SDK.

    Args:
        base_url: API base URL (default: https://api.openwec.com/api/v1)
        api_key:  API key for protected endpoints (laps, pace, pit-window)
        max_retries: how many times to wait out a 429 before raising.
            Default 0 — a rate-limited call raises OpenWECRateLimitError
            immediately, and you decide what to do. Set it to 2 or 3 in a
            notebook that sweeps many sessions.
        retry_max_wait: never sleep longer than this for one retry, in
            seconds (default 60). A Retry-After above it raises instead.
    """
    if base_url:
        _config.base_url = base_url.rstrip("/")
    if api_key:
        _config.api_key = api_key
    if max_retries is not None:
        if max_retries < 0:
            raise ValueError("max_retries must be zero or more")
        _config.max_retries = max_retries
    if retry_max_wait is not None:
        if retry_max_wait <= 0:
            raise ValueError("retry_max_wait must be positive")
        _config.retry_max_wait = retry_max_wait


def _headers() -> dict:
    headers = {}
    if _config.api_key:
        headers["X-API-Key"] = _config.api_key
    return headers


def _retry_after(resp: httpx.Response) -> float | None:
    """
    Seconds to wait, from the Retry-After header.

    The header is either a number of seconds or an HTTP date; both are legal
    and the API has used both over time. Anything else, or a date already in
    the past, reads as None — the caller then has no guidance, which is
    different from being told to wait zero.
    """
    bruto = resp.headers.get("Retry-After")
    if not bruto:
        return None

    bruto = bruto.strip()
    try:
        return max(0.0, float(bruto))
    except ValueError:
        pass

    try:
        quando = email.utils.parsedate_to_datetime(bruto)
    except (TypeError, ValueError):
        return None
    if quando is None:
        return None
    espera = quando.timestamp() - time.time()
    return espera if espera > 0 else None


def _get(path: str, params: dict | None = None) -> dict | list:
    """Performs a GET request to the OpenWEC API. Raises on error."""
    url = f"{_config.base_url}{path}"
    tentativas = 0

    while True:
        resp = httpx.get(url, params=params, headers=_headers(), timeout=30)

        if resp.status_code == 404:
            raise OpenWECNotFoundError(f"Not found: {path} ({resp.text})")

        if resp.status_code == 401:
            raise OpenWECAuthError(
                "Unauthorized — this endpoint requires an API key. "
                "Call openwec.configure(api_key='...') first."
            )

        if resp.status_code == 403:
            raise OpenWECPermissionError(
                f"Forbidden — the key is valid but not allowed here: {path}"
            )

        if resp.status_code == 429:
            espera = _retry_after(resp)
            podemos = (
                tentativas < _config.max_retries
                and espera is not None
                and espera <= _config.retry_max_wait
            )
            if not podemos:
                raise OpenWECRateLimitError(
                    _mensagem_de_limite(path, espera), retry_after=espera
                )
            time.sleep(espera)
            tentativas += 1
            continue

        if resp.status_code >= 500:
            raise OpenWECServerError(
                f"Server error {resp.status_code} from {path}. "
                "This is the API's problem, not the request's — retry later."
            )

        resp.raise_for_status()
        return resp.json()


def _mensagem_de_limite(path: str, espera: float | None) -> str:
    quando = (
        f"Retry after {espera:.0f}s." if espera is not None
        else "The response gave no Retry-After."
    )
    chave = (
        "Reads without a key share a per-minute budget; configure(api_key=...) "
        "raises it." if not _config.api_key else
        "This key's own per-minute budget is spent."
    )
    return (
        f"Rate limited on {path}. {quando} {chave} "
        "Pass max_retries= to configure() to wait automatically."
    )


class OpenWECError(Exception):
    """Base exception for OpenWEC SDK errors."""


class OpenWECNotFoundError(OpenWECError):
    """Raised when a resource is not found (404)."""


class OpenWECAuthError(OpenWECError):
    """Raised when an API key is required but missing/invalid (401)."""


class OpenWECPermissionError(OpenWECError):
    """Raised when the key is accepted but not allowed here (403)."""


class OpenWECRateLimitError(OpenWECError):
    """
    Raised when the API returns 429.

    `retry_after` is the seconds the API asked for, or None when it did not
    say. Catch this rather than sleeping blind:

        try:
            laps = session.laps()
        except OpenWECRateLimitError as e:
            time.sleep(e.retry_after or 60)
    """

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class OpenWECServerError(OpenWECError):
    """Raised on 5xx — the API failed, the request was fine."""

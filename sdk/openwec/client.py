"""
OpenWEC SDK — Client configuration and HTTP wrapper.
"""

import requests


class _Config:
    base_url: str = "http://localhost:8000/api/v1"
    api_key:  str | None = None


_config = _Config()


def configure(base_url: str | None = None, api_key: str | None = None):
    """
    Configure the SDK.

    Args:
        base_url: API base URL (default: http://localhost:8000/api/v1)
        api_key:  API key for protected endpoints (laps, analytics)
    """
    if base_url:
        _config.base_url = base_url.rstrip("/")
    if api_key:
        _config.api_key = api_key


def _headers() -> dict:
    headers = {}
    if _config.api_key:
        headers["X-API-Key"] = _config.api_key
    return headers


def _get(path: str, params: dict | None = None) -> dict | list:
    """Performs a GET request to the OpenWEC API. Raises on error."""
    url = f"{_config.base_url}{path}"
    resp = requests.get(url, params=params, headers=_headers(), timeout=30)

    if resp.status_code == 404:
        raise OpenWECNotFoundError(f"Not found: {path} ({resp.text})")
    if resp.status_code == 401:
        raise OpenWECAuthError(
            "Unauthorized — this endpoint requires an API key. "
            "Call openwec.configure(api_key='...') first."
        )
    resp.raise_for_status()
    return resp.json()


class OpenWECError(Exception):
    """Base exception for OpenWEC SDK errors."""


class OpenWECNotFoundError(OpenWECError):
    """Raised when a resource is not found (404)."""


class OpenWECAuthError(OpenWECError):
    """Raised when an API key is required but missing/invalid (401)."""
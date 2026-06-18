"""
OpenWEC API — Dependencies
Reusable FastAPI dependencies for DB connection and authentication.
"""

import time
from typing import Generator
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
import psycopg2
import psycopg2.extras

from api.config import settings


# ── Database ─────────────────────────────────────────────────

def get_db() -> Generator:
    """Yields a psycopg2 connection. Closes after request."""
    conn = psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def get_cursor(conn=Depends(get_db)):
    """Yields a RealDictCursor so rows come back as dicts."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        yield cur


# ── Authentication ────────────────────────────────────────────

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Simple in-memory fixed-window rate limiter.
# Fine for a single uvicorn worker (current deployment). If this ever
# scales to multiple workers/processes, move to Redis.
_RATE_WINDOW_SECONDS = 60
_rate_state: dict[str, tuple[int, float]] = {}        # api_key -> (count, window_start)

# Short-lived cache for dynamic key lookups, to avoid a DB round-trip
# on every single request.
_KEY_CACHE_TTL = 30  # seconds
_key_cache: dict[str, tuple[str | None, int, float]] = {}  # api_key -> (status, rpm, cached_at)


def _lookup_dynamic_key(api_key: str, conn) -> tuple[str | None, int]:
    """Returns (status, requests_per_minute) for a key issued via /api-keys/request."""
    now = time.time()
    cached = _key_cache.get(api_key)
    if cached and now - cached[2] < _KEY_CACHE_TTL:
        return cached[0], cached[1]

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT status, requests_per_minute FROM api_key_requests WHERE api_key = %s",
            (api_key,),
        )
        row = cur.fetchone()

    result = (row["status"], row["requests_per_minute"]) if row else (None, 0)
    _key_cache[api_key] = (result[0], result[1], now)
    return result


def _check_rate_limit(api_key: str, limit_per_minute: int):
    now = time.time()
    count, window_start = _rate_state.get(api_key, (0, now))

    if now - window_start >= _RATE_WINDOW_SECONDS:
        count, window_start = 0, now

    count += 1
    _rate_state[api_key] = (count, window_start)

    if count > limit_per_minute:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded ({limit_per_minute} requests/minute). Try again shortly.",
        )


def require_api_key(api_key: str = Security(api_key_header), conn=Depends(get_db)):
    """
    Dependency for protected endpoints (laps, analytics).
    Pass X-API-Key header with a valid key.

    - If no static API_KEYS are configured (development), all requests pass through.
    - Static keys (from API_KEYS env var) bypass rate limiting — admin/personal use.
    - Dynamic keys (issued via POST /api-keys/request, manually approved) are
      subject to per-key rate limiting based on their requests_per_minute.
    """
    valid_static_keys = settings.valid_api_keys

    # Development mode — no static keys configured, allow everything
    if not valid_static_keys:
        return True

    # Static admin key — full access, no rate limit
    if api_key and api_key in valid_static_keys:
        return True

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Pass X-API-Key header.",
        )

    # Dynamic key — must be approved, subject to rate limiting
    key_status, rpm = _lookup_dynamic_key(api_key, conn)

    if key_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, unapproved, or unknown API key.",
        )

    _check_rate_limit(api_key, rpm)
    return True
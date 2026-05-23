"""
OpenWEC API — Dependencies
Reusable FastAPI dependencies for DB connection and authentication.
"""

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


def require_api_key(api_key: str = Security(api_key_header)):
    """
    Dependency for protected endpoints.
    Pass X-API-Key header with a valid key.

    If no API_KEYS are configured (development), all requests pass through.
    """
    valid_keys = settings.valid_api_keys

    # Development mode — no keys configured, allow all
    if not valid_keys:
        return True

    if not api_key or api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key. Pass X-API-Key header.",
        )
    return True
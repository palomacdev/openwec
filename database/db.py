"""
OpenWEC — Database configuration
Reads connection settings from environment variables or .env file.

Variables (with defaults for local development):
    DB_HOST     = 127.0.0.1
    DB_PORT     = 5433
    DB_NAME     = openwec
    DB_USER     = openwec
    DB_PASSWORD = openwec   ← override in production via .env

Usage:
    from database.db import get_connection, DB_CONFIG

    conn = get_connection()
    # or
    import psycopg2
    conn = psycopg2.connect(**DB_CONFIG)
"""

import os
from pathlib import Path

# Load .env file if present (project root)
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST",     "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "5433")),
    "dbname":   os.environ.get("DB_NAME",     "openwec"),
    "user":     os.environ.get("DB_USER",     "openwec"),
    "password": os.environ.get("DB_PASSWORD", "openwec"),
}


def get_connection():
    """Returns a psycopg2 connection using DB_CONFIG."""
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)
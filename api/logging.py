"""
OpenWEC API — Structured Logging
JSON logging middleware for the FastAPI application.

Each request is logged as a JSON object with:
- timestamp, method, path, status_code, duration_ms
- api_key_hash (sha256 prefix, never the full key)
- client_ip

Usage:
    from api.logging import setup_logging, RequestLoggingMiddleware
    setup_logging()
    app.add_middleware(RequestLoggingMiddleware)
"""

import hashlib
import logging
import time
from pythonjsonlogger import jsonlogger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


def setup_logging(level: str = "INFO"):
    """Configure JSON logging for the application."""
    handler = logging.StreamHandler()
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Quieten noisy loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)


def _hash_key(api_key: str | None) -> str | None:
    """Returns first 8 chars of SHA256 hash — enough to identify, not to recover."""
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode()).hexdigest()[:8]


logger = logging.getLogger("openwec.api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs each request as a structured JSON line."""

    SKIP_PATHS = {"/health", "/", "/docs", "/redoc", "/openapi.json"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)

        api_key = request.headers.get("X-API-Key")

        logger.info(
            "request",
            extra={
                "method":       request.method,
                "path":         request.url.path,
                "query":        str(request.url.query) or None,
                "status_code":  response.status_code,
                "duration_ms":  duration_ms,
                "api_key_hash": _hash_key(api_key),
                "client_ip":    request.headers.get("X-Forwarded-For",
                                 getattr(request.client, "host", None)),
            },
        )

        return response
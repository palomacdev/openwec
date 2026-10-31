"""
OpenWEC API — Phase 3
FastAPI application entry point.

Run:
    uvicorn api.main:app --reload --port 8000

Docs:
    http://localhost:8000/docs
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import settings
from api.routers import series, sessions, results, laps, analytics, drivers_teams, api_keys, admin
from api.logging import setup_logging, RequestLoggingMiddleware

setup_logging()


def _check_proxy_configuration() -> None:
    """
    Say, once, at start-up, what the rate limiter is going to believe.

    Both branches below are safe. Neither is silent, because the difference
    between "every caller is metered separately" and "every caller shares one
    bucket" is not visible in any response, and the second one looks like the
    limit being too strict rather than like a misconfiguration.
    """
    log = logging.getLogger("openwec.api")

    invalid = settings.invalid_trusted_proxies
    if invalid:
        log.warning(
            "trusted_proxies_invalid",
            extra={
                "entries": list(invalid),
                "effect":  "entry ignored; it grants no trust",
            },
        )

    if not settings.trusted_proxy_networks:
        log.warning(
            "trusted_proxies_unset",
            extra={
                "effect": ("X-Forwarded-For is ignored; behind a reverse proxy "
                           "every caller shares the proxy's rate-limit bucket"),
                "fix":    "set TRUSTED_PROXIES to the address the proxy connects from",
            },
        )


_check_proxy_configuration()




app = FastAPI(
    title="OpenWEC API",
    description="Historical and live data for endurance racing — WEC, ELMS, ALMS, Le Mans Cup, IMSA.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────
app.include_router(series.router,   prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(results.router,  prefix="/api/v1")
app.include_router(laps.router,     prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(drivers_teams.router, prefix="/api/v1")
app.include_router(api_keys.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")

@app.get("/", tags=["health"])
def root():
    return {
        "name":    "OpenWEC API",
        "version": "0.1.0",
        "docs":    "/docs",
        "status":  "ok",
    }


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
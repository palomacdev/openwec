"""
OpenWEC API — Phase 3
FastAPI application entry point.

Run:
    uvicorn api.main:app --reload --port 8000

Docs:
    http://localhost:8000/docs
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import series, sessions, results, laps, analytics



app = FastAPI(
    title="OpenWEC API",
    description="Historical and live data for endurance racing — WEC, ELMS, ALMS, Le Mans Cup, IMSA.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

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
"""
OpenWEC API — API Key Requests
Public endpoint for requesting an API key for protected endpoints
(laps, analytics). Keys are generated immediately but stay inactive
until manually approved.
"""

import secrets
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from api.deps import get_cursor


router = APIRouter(tags=["API Keys"])


class ApiKeyRequestIn(BaseModel):
    name:          str
    email:         EmailStr
    intended_use:  str | None = None


class ApiKeyRequestOut(BaseModel):
    api_key: str
    status:  str
    message: str


def _generate_key() -> str:
    return f"owec_{secrets.token_urlsafe(24)}"


@router.post("/api-keys/request", response_model=ApiKeyRequestOut)
def request_api_key(payload: ApiKeyRequestIn, cur=Depends(get_cursor)):
    """
    Requests an API key for protected endpoints (laps, analytics).
    Public endpoints (series, sessions, results, drivers, teams) never
    require a key.

    The key is generated immediately and returned in this response —
    save it now, it will not be shown again. It stays inactive until
    manually approved (usually within 24h), then starts working
    automatically with no further action needed.
    """
    key = _generate_key()

    try:
        cur.execute(
            """
            INSERT INTO api_key_requests (name, email, intended_use, api_key, status)
            VALUES (%s, %s, %s, %s, 'pending')
            RETURNING id
            """,
            (payload.name, payload.email, payload.intended_use, key),
        )
        cur.connection.commit()
    except Exception as e:
        cur.connection.rollback()
        raise HTTPException(500, f"Could not create request: {e}")

    return ApiKeyRequestOut(
        api_key=key,
        status="pending",
        message=(
            "Save this key now — it will not be shown again. "
            "It will start working automatically once approved (usually within 24h)."
        ),
    )
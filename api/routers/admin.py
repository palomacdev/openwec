"""
OpenWEC API — Admin Router
Protected admin endpoints. Requires static admin API key (API_KEYS env var).

Endpoints:
    POST /admin/keys/{api_key}/revoke  ← instant key revocation
    GET  /admin/keys                   ← list all keys
    GET  /admin/keys/pending           ← list pending keys
"""

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from api.deps import get_cursor, get_db, _key_cache
from api.config import settings


router = APIRouter(tags=["Admin"])

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_admin_key(api_key: str = Security(api_key_header)):
    """Only static admin keys (API_KEYS env var) can access admin endpoints."""
    valid_static_keys = settings.valid_api_keys
    
    print("DEBUG received:", api_key)
    print("DEBUG valid keys:", valid_static_keys)
    
    if not valid_static_keys or not api_key or api_key not in valid_static_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin API key required.",
        )
    return api_key


class KeyOut(BaseModel):
    id:                  int
    name:                str
    email:               str
    intended_use:        str | None
    api_key:             str
    status:              str
    requests_per_minute: int
    created_at:          str
    approved_at:         str | None


@router.post("/admin/keys/{key}/revoke")
def revoke_key(
    key: str,
    admin_key: str = Depends(require_admin_key),
    cur=Depends(get_cursor),
    conn=Depends(get_db),
):
    """
    Instantly revoke an API key.

    1. Sets status to 'rejected' in DB
    2. Clears the in-process key cache (takes effect immediately)
    3. Deletes Redis rate limit counter for the key

    Admin only — requires static X-API-Key header.
    """
    cur.execute(
        "SELECT id, status FROM api_key_requests WHERE api_key = %s",
        (key,)
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Key not found.")

    if row["status"] == "rejected":
        return {"message": "Key was already revoked.", "key": key}

    # 1. Update DB
    cur.execute(
        "UPDATE api_key_requests SET status = 'rejected' WHERE api_key = %s",
        (key,)
    )
    conn.commit()

    # 2. Clear in-process cache
    _key_cache.pop(key, None)

    # 3. Clear Redis counter (best-effort)
    try:
        from api.deps import _get_redis
        import time
        r = _get_redis()
        if r:
            window = int(time.time() // 60)
            r.delete(f"rl:{key}:{window}")
            r.delete(f"rl:{key}:{window - 1}")  # previous window too
    except Exception:
        pass

    return {"message": "Key revoked successfully.", "key": key}


@router.get("/admin/keys", response_model=list[KeyOut])
def list_keys(
    admin_key: str = Depends(require_admin_key),
    cur=Depends(get_cursor),
):
    """List all API keys. Admin only."""
    cur.execute("""
        SELECT id, name, email, intended_use, api_key, status,
               requests_per_minute,
               created_at::text AS created_at,
               approved_at::text AS approved_at
        FROM api_key_requests
        ORDER BY created_at DESC
        LIMIT 100
    """)
    return [dict(r) for r in cur.fetchall()]


@router.get("/admin/keys/pending", response_model=list[KeyOut])
def list_pending_keys(
    admin_key: str = Depends(require_admin_key),
    cur=Depends(get_cursor),
):
    """List pending API key requests. Admin only."""
    cur.execute("""
        SELECT id, name, email, intended_use, api_key, status,
               requests_per_minute,
               created_at::text AS created_at,
               approved_at::text AS approved_at
        FROM api_key_requests
        WHERE status = 'pending'
        ORDER BY created_at
    """)
    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, "No pending requests.")
    return [dict(r) for r in rows]
"""
OpenWEC API — Admin Router
Admin endpoints. Authorized by ADMIN_API_KEYS, and by nothing else.

Endpoints:
    POST /admin/keys/{api_key}/revoke  ← instant key revocation
    GET  /admin/keys                   ← list all keys
    GET  /admin/keys/pending           ← list pending keys
"""

import hmac

from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from api.deps import get_cursor, get_db, _key_cache
from api.config import settings


router = APIRouter(tags=["Admin"])

# A header of its own. An API key sent in X-API-Key cannot reach this check
# even by accident, and a client written for the public API cannot stumble
# into administration by reusing its own request helper.
admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def require_admin_key(api_key: str = Security(admin_key_header)):
    """
    Authorizes /admin/* against ADMIN_API_KEYS alone.

    Never against API_KEYS. These routes return every issued key in plaintext,
    with the requester's name and e-mail, and can revoke any of them; a
    credential that may legitimately sit in a public JavaScript bundle must not
    reach them. `settings.valid_admin_keys` also drops any value that appears
    in both sets, so the two cannot be silently merged by configuration.

    Fail-closed: an absent, short or unparseable ADMIN_API_KEYS yields an empty
    set, and the loop below then rejects everything, including a correct
    credential. Unavailable is the right answer when we cannot tell who is
    asking.

    Never log the received key or the configured credentials -- not in full,
    not masked, not hashed, not a prefix. Any representation derived from a
    credential still narrows the search space for whoever reads the log.
    """
    credentials = settings.valid_admin_keys
    presented = (api_key or "").encode()

    # Compared in constant time, and without an early exit, so neither the
    # timing of a single comparison nor the number of comparisons performed
    # reveals how far a guess got.
    authorized = False
    for expected in credentials:
        if hmac.compare_digest(presented, expected.encode()):
            authorized = True

    if not authorized:
        # One message for every failure: wrong credential, missing credential
        # and unconfigured server are indistinguishable from outside.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin credential required.",
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
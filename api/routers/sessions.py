"""
OpenWEC API — Sessions Router
Public endpoint for session lookup by ID.
"""

from fastapi import APIRouter, Depends, HTTPException
from api.deps import get_cursor
from api.schemas import SessionOut

router = APIRouter(tags=["Sessions"])


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int, cur=Depends(get_cursor)):
    """Get session details by ID."""
    cur.execute("""
        SELECT s.id, s.raw_id, s.name,
               s.session_type::text AS session_type,
               s.session_at::text   AS session_at,
               s.imsa_series,
               s.snapshot_hour
        FROM sessions s
        WHERE s.id = %s
    """, (session_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Session {session_id} not found.")
    return row
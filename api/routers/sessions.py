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

@router.get("/events/{event_id}", response_model=dict)
def get_event(event_id: int, cur=Depends(get_cursor)):
    """Get event with all sessions grouped."""
    cur.execute("""
        SELECT 
            e.id, e.name AS event_name, e.round,
            se.label AS season, sr.key AS series
        FROM events e
        JOIN seasons se ON se.id = e.season_id
        JOIN series sr  ON sr.id = se.series_id
        WHERE e.id = %s
    """, (event_id,))
    event = cur.fetchone()
    if not event:
        raise HTTPException(404, f"Event {event_id} not found.")

    cur.execute("""
        SELECT 
            s.id, s.name, s.session_type::text AS session_type,
            s.session_at::text AS session_at,
            s.imsa_series, s.snapshot_hour,
            COUNT(DISTINCT r.id) AS result_count,
            COUNT(DISTINCT l.id) AS lap_count
        FROM sessions s
        LEFT JOIN results r ON r.session_id = s.id
        LEFT JOIN laps l    ON l.session_id = s.id
        WHERE s.event_id = %s
        GROUP BY s.id, s.name, s.session_type, s.session_at,
                 s.imsa_series, s.snapshot_hour
        ORDER BY s.session_at NULLS LAST, s.id
    """, (event_id,))
    sessions = cur.fetchall()

    return {
        "id":          event["id"],
        "series":      event["series"],
        "season":      event["season"],
        "name":        event["event_name"],
        "round":       event["round"],
        "sessions":    [dict(s) for s in sessions],
    }
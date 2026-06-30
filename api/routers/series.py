"""
OpenWEC API — Series Router
Public endpoints for navigation: series, seasons, events, sessions.
"""

from fastapi import APIRouter, Depends, HTTPException
import psycopg2.extras
from api.deps import get_cursor
from api.schemas import SeriesOut, SeasonOut, EventOut, SessionOut

router = APIRouter(tags=["Navigation"])


@router.get("/series", response_model=list[SeriesOut])
def list_series(cur=Depends(get_cursor)):
    """List all available racing series."""
    cur.execute("SELECT id, key::text AS key, name FROM series ORDER BY id")
    return cur.fetchall()


@router.get("/series/{series_key}/seasons", response_model=list[SeasonOut])
def list_seasons(series_key: str, cur=Depends(get_cursor)):
    """List all seasons for a series."""
    cur.execute("""
        SELECT se.id, se.raw_id, se.year, se.label
        FROM seasons se
        JOIN series sr ON sr.id = se.series_id
        WHERE sr.key::text = %s
        ORDER BY se.year
    """, (series_key.upper(),))
    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"Series '{series_key}' not found or has no seasons.")
    return rows


@router.get("/series/{series_key}/seasons/{year}/events", response_model=list[EventOut])
def list_events(series_key: str, year: int, cur=Depends(get_cursor)):
    """List all events for a season."""
    cur.execute("""
        SELECT e.id, e.raw_id, e.name, e.round
        FROM events e
        JOIN seasons se ON se.id = e.season_id
        JOIN series sr  ON sr.id = se.series_id
        WHERE sr.key::text = %s AND se.year = %s
        ORDER BY e.round NULLS LAST, e.id
    """, (series_key.upper(), year))
    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No events found for {series_key} {year}.")
    return rows


@router.get("/series/{series_key}/seasons/{year}/events/{event_id}/sessions",
            response_model=list[SessionOut])
def list_sessions(series_key: str, year: int, event_id: int, cur=Depends(get_cursor)):
    """List all sessions for an event."""
    cur.execute("""
        SELECT s.id, s.raw_id, s.name,
               s.session_type::text AS session_type,
               s.session_at::text   AS session_at,
               s.imsa_series,
               s.snapshot_hour
        FROM sessions s
        JOIN events  e  ON e.id = s.event_id
        JOIN seasons se ON se.id = e.season_id
        JOIN series  sr ON sr.id = se.series_id
        WHERE sr.key::text = %s AND se.year = %s AND e.id = %s
        ORDER BY s.session_at NULLS LAST, s.id
    """, (series_key.upper(), year, event_id))
    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No sessions found for event {event_id}.")
    return rows
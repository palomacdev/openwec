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


@router.get("/series/{series_key}/seasons/{year}/stats")
def get_season_stats(series_key: str, year: int, cur=Depends(get_cursor)):
    """
    Aggregated statistics for a season.
    Public — no API key required.

    Returns:
        - total_races, total_laps, total_entries
        - classes, manufacturers
        - top_drivers (by wins), top_teams (by wins)
    """
    # Verify season exists
    cur.execute("""
        SELECT se.id, se.label FROM seasons se
        JOIN series sr ON sr.id = se.series_id
        WHERE sr.key::text = %s AND se.year = %s
    """, (series_key.upper(), year))
    season = cur.fetchone()
    if not season:
        raise HTTPException(404, f"No season found for {series_key} {year}.")

    season_id = season["id"]

    # Base stats
    cur.execute("""
        SELECT
            COUNT(DISTINCT s.id) FILTER (
                WHERE s.session_type = 'Race'
                AND (s.snapshot_hour IS NULL)
            ) AS total_races,
            COUNT(DISTINCT l.id) AS total_laps,
            COUNT(DISTINCT r.id) AS total_entries
        FROM seasons se
        JOIN events e   ON e.season_id = se.id
        JOIN sessions s ON s.event_id = e.id
        LEFT JOIN laps l    ON l.session_id = s.id
        LEFT JOIN results r ON r.session_id = s.id
        WHERE se.id = %s
    """, (season_id,))
    base = cur.fetchone()

    # Classes
    cur.execute("""
        SELECT DISTINCT c.car_class
        FROM results r
        JOIN cars c ON c.id = r.car_id
        JOIN sessions s ON s.id = r.session_id
        JOIN events e ON e.id = s.event_id
        WHERE e.season_id = %s
          AND s.session_type = 'Race'
          AND s.snapshot_hour IS NULL
          AND c.car_class IS NOT NULL
        ORDER BY c.car_class
    """, (season_id,))
    classes = [row["car_class"] for row in cur.fetchall()]

    # Manufacturers
    cur.execute("""
        SELECT DISTINCT SPLIT_PART(c.vehicle, ' ', 1) AS manufacturer
        FROM results r
        JOIN cars c ON c.id = r.car_id
        JOIN sessions s ON s.id = r.session_id
        JOIN events e ON e.id = s.event_id
        WHERE e.season_id = %s
          AND s.session_type = 'Race'
          AND s.snapshot_hour IS NULL
          AND c.vehicle IS NOT NULL AND c.vehicle != ''
        ORDER BY manufacturer
    """, (season_id,))
    manufacturers = [row["manufacturer"] for row in cur.fetchall() if row["manufacturer"]]

    # Top drivers by wins
    cur.execute("""
        SELECT
            d.first_name, d.last_name, d.country,
            COUNT(*) AS wins
        FROM results r
        JOIN result_drivers rd ON rd.result_id = r.id
        JOIN drivers d ON d.id = rd.driver_id
        JOIN sessions s ON s.id = r.session_id
        JOIN events e ON e.id = s.event_id
        WHERE e.season_id = %s
          AND s.session_type = 'Race'
          AND s.snapshot_hour IS NULL
          AND r.position = 1
        GROUP BY d.id, d.first_name, d.last_name, d.country
        ORDER BY wins DESC
        LIMIT 10
    """, (season_id,))
    top_drivers = [dict(row) for row in cur.fetchall()]

    # Top teams by wins
    cur.execute("""
        SELECT
            t.name AS team,
            COUNT(*) AS wins
        FROM results r
        JOIN cars c ON c.id = r.car_id
        JOIN teams t ON t.id = c.team_id
        JOIN sessions s ON s.id = r.session_id
        JOIN events e ON e.id = s.event_id
        WHERE e.season_id = %s
          AND s.session_type = 'Race'
          AND s.snapshot_hour IS NULL
          AND r.position = 1
        GROUP BY t.id, t.name
        ORDER BY wins DESC
        LIMIT 10
    """, (season_id,))
    top_teams = [dict(row) for row in cur.fetchall()]

    return {
        "series":        series_key.upper(),
        "season":        season["label"],
        "year":          year,
        "total_races":   base["total_races"],
        "total_laps":    base["total_laps"],
        "total_entries": base["total_entries"],
        "classes":       classes,
        "manufacturers": manufacturers,
        "top_drivers":   top_drivers,
        "top_teams":     top_teams,
    }
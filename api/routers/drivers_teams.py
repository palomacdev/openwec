"""
OpenWEC API — Drivers & Teams Router
Public endpoints: driver profiles, career history, team profiles.

Endpoints:
    GET /drivers/{id}           ← driver profile + career stats
    GET /drivers/{id}/results   ← full race history
    GET /teams/{id}             ← team profile + season history
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from api.deps import get_cursor


router = APIRouter(tags=["Drivers & Teams"])


# ── Schemas ───────────────────────────────────────────────────

class DriverProfile(BaseModel):
    id:                  int
    first_name:          str
    last_name:           str
    country:             Optional[str]
    imsa_driver_rating:  Optional[str]
    # Career stats
    total_races:         int
    series:              list[str]
    first_race:          Optional[str]
    last_race:           Optional[str]
    classes:             list[str]


class DriverResult(BaseModel):
    series:         str
    season:         str
    event:          str
    session_at:     Optional[str]
    car_number:     str
    car_class:      Optional[str]
    vehicle:        Optional[str]
    team:           Optional[str]
    position:       Optional[int]
    status:         str
    laps_completed: Optional[int]
    fl_time_s:      Optional[float]


class TeamProfile(BaseModel):
    id:           int
    name:         str
    # Career stats
    total_entries: int
    series:        list[str]
    first_entry:   Optional[str]
    last_entry:    Optional[str]
    classes:       list[str]


class TeamSeasonEntry(BaseModel):
    series:     str
    season:     str
    event:      str
    session_at: Optional[str]
    car_number: str
    car_class:  Optional[str]
    vehicle:    Optional[str]
    position:   Optional[int]
    status:     str


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/drivers/{driver_id}", response_model=DriverProfile)
def get_driver(driver_id: int, cur=Depends(get_cursor)):
    """
    Driver profile with career summary stats.
    Public — no API key required.
    """
    cur.execute("""
        SELECT id, first_name, last_name, country,
               imsa_driver_rating::text AS imsa_driver_rating
        FROM drivers WHERE id = %s
    """, (driver_id,))
    driver = cur.fetchone()
    if not driver:
        raise HTTPException(404, f"Driver {driver_id} not found.")

    cur.execute("""
        SELECT
            COUNT(DISTINCT r.id)            AS total_races,
            ARRAY_AGG(DISTINCT sr.key::text) AS series,
            MIN(s.session_at::text)          AS first_race,
            MAX(s.session_at::text)          AS last_race,
            ARRAY_AGG(DISTINCT c.car_class)  AS classes
        FROM result_drivers rd
        JOIN results r  ON r.id = rd.result_id
        JOIN sessions s ON s.id = r.session_id
        JOIN events e   ON e.id = s.event_id
        JOIN seasons se ON se.id = e.season_id
        JOIN series sr  ON sr.id = se.series_id
        JOIN cars c     ON c.id = r.car_id
        WHERE rd.driver_id = %s
          AND s.session_type = 'Race'
          AND (s.snapshot_hour IS NULL OR s.snapshot_hour = 24)
    """, (driver_id,))
    stats = cur.fetchone()

    return DriverProfile(
        id=driver["id"],
        first_name=driver["first_name"] or "",
        last_name=driver["last_name"] or "",
        country=driver["country"],
        imsa_driver_rating=driver["imsa_driver_rating"],
        total_races=stats["total_races"] or 0,
        series=[s for s in (stats["series"] or []) if s],
        first_race=stats["first_race"],
        last_race=stats["last_race"],
        classes=[c for c in (stats["classes"] or []) if c],
    )


@router.get("/drivers/{driver_id}/results", response_model=list[DriverResult])
def get_driver_results(
    driver_id: int,
    series:    Optional[str] = Query(None, description="Filter by series key"),
    limit:     int           = Query(50, ge=1, le=200),
    cur=Depends(get_cursor),
):
    """
    Full race history for a driver.
    Public — no API key required.
    """
    cur.execute("SELECT id FROM drivers WHERE id = %s", (driver_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Driver {driver_id} not found.")

    filters = ["rd.driver_id = %s", "s.session_type = 'Race'",
               "(s.snapshot_hour IS NULL OR s.snapshot_hour = 24)"]
    params  = [driver_id]

    if series:
        filters.append("sr.key::text = %s")
        params.append(series.upper())

    cur.execute(f"""
        SELECT
            sr.key::text    AS series,
            se.label        AS season,
            ev.name         AS event,
            s.session_at::text AS session_at,
            c.number        AS car_number,
            c.car_class,
            c.vehicle,
            t.name          AS team,
            r.position,
            r.status::text  AS status,
            r.laps_completed,
            r.fl_time_s
        FROM result_drivers rd
        JOIN results r  ON r.id = rd.result_id
        JOIN sessions s ON s.id = r.session_id
        JOIN events ev  ON ev.id = s.event_id
        JOIN seasons se ON se.id = ev.season_id
        JOIN series sr  ON sr.id = se.series_id
        JOIN cars c     ON c.id = r.car_id
        LEFT JOIN teams t ON t.id = c.team_id
        WHERE {" AND ".join(filters)}
        ORDER BY s.session_at DESC NULLS LAST
        LIMIT %s
    """, params + [limit])

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No race results found for driver {driver_id}.")

    return [DriverResult(**dict(r)) for r in rows]


@router.get("/teams/{team_id}", response_model=TeamProfile)
def get_team(team_id: int, cur=Depends(get_cursor)):
    """
    Team profile with career summary stats.
    Public — no API key required.
    """
    cur.execute("SELECT id, name FROM teams WHERE id = %s", (team_id,))
    team = cur.fetchone()
    if not team:
        raise HTTPException(404, f"Team {team_id} not found.")

    cur.execute("""
        SELECT
            COUNT(DISTINCT r.id)             AS total_entries,
            ARRAY_AGG(DISTINCT sr.key::text)  AS series,
            MIN(s.session_at::text)           AS first_entry,
            MAX(s.session_at::text)           AS last_entry,
            ARRAY_AGG(DISTINCT c.car_class)   AS classes
        FROM cars c
        JOIN results r  ON r.car_id = c.id
        JOIN sessions s ON s.id = r.session_id
        JOIN events e   ON e.id = s.event_id
        JOIN seasons se ON se.id = e.season_id
        JOIN series sr  ON sr.id = se.series_id
        WHERE c.team_id = %s
          AND s.session_type = 'Race'
          AND (s.snapshot_hour IS NULL OR s.snapshot_hour = 24)
    """, (team_id,))
    stats = cur.fetchone()

    return TeamProfile(
        id=team["id"],
        name=team["name"],
        total_entries=stats["total_entries"] or 0,
        series=[s for s in (stats["series"] or []) if s],
        first_entry=stats["first_entry"],
        last_entry=stats["last_entry"],
        classes=[c for c in (stats["classes"] or []) if c],
    )


@router.get("/teams/{team_id}/history", response_model=list[TeamSeasonEntry])
def get_team_history(
    team_id: int,
    series:  Optional[str] = Query(None),
    limit:   int           = Query(100, ge=1, le=500),
    cur=Depends(get_cursor),
):
    """
    Full entry history for a team.
    Public — no API key required.
    """
    cur.execute("SELECT id FROM teams WHERE id = %s", (team_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Team {team_id} not found.")

    filters = ["c.team_id = %s", "s.session_type = 'Race'",
               "(s.snapshot_hour IS NULL OR s.snapshot_hour = 24)"]
    params  = [team_id]

    if series:
        filters.append("sr.key::text = %s")
        params.append(series.upper())

    cur.execute(f"""
        SELECT
            sr.key::text    AS series,
            se.label        AS season,
            ev.name         AS event,
            s.session_at::text AS session_at,
            c.number        AS car_number,
            c.car_class,
            c.vehicle,
            r.position,
            r.status::text  AS status
        FROM cars c
        JOIN results r  ON r.car_id = c.id
        JOIN sessions s ON s.id = r.session_id
        JOIN events ev  ON ev.id = s.event_id
        JOIN seasons se ON se.id = ev.season_id
        JOIN series sr  ON sr.id = se.series_id
        WHERE {" AND ".join(filters)}
        ORDER BY s.session_at DESC NULLS LAST
        LIMIT %s
    """, params + [limit])

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No history found for team {team_id}.")

    return [TeamSeasonEntry(**dict(r)) for r in rows]
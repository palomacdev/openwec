"""
OpenWEC API — Analytics Router
Protected endpoints: pre-computed analytics data.
Requires X-API-Key header.

Endpoints:
    GET /sessions/{id}/stints       ← stint breakdown per car
    GET /sessions/{id}/pace         ← average green flag pace per car
    GET /sessions/{id}/gaps         ← gap evolution over laps
    GET /drivers/{id}/consistency   ← variance stats across sessions
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from api.deps import get_cursor, require_api_key
from api.config import settings


router = APIRouter(tags=["Analytics"])


# ── Schemas ───────────────────────────────────────────────────

class StintOut(BaseModel):
    car_number:           str
    car_class:            Optional[str]
    team:                 Optional[str]
    stint_number:         int
    start_lap:            int
    end_lap:              int
    lap_count:            int
    tyre_age_laps:        int
    baseline_pace_s:      Optional[float]
    degradation_s_per_lap: Optional[float]
    consistency_s:        Optional[float]
    is_final_stint:       bool


class PaceOut(BaseModel):
    car_number:      str
    car_class:       Optional[str]
    team:            Optional[str]
    total_laps:      int
    green_flag_laps: int
    pit_stops:       int
    best_lap_s:      Optional[float]
    avg_pace_s:      Optional[float]
    consistency_s:   Optional[float]


class GapPoint(BaseModel):
    lap_number:   int
    car_number:   str
    car_class:    Optional[str]
    lap_time_s:   Optional[float]
    cumulative_s: Optional[float]


class DriverConsistency(BaseModel):
    session_id:      int
    series:          str
    season:          str
    event:           str
    session_name:    str
    session_type:    str
    car_number:      str
    car_class:       Optional[str]
    consistency_s:   Optional[float]
    avg_pace_s:      Optional[float]
    best_lap_s:      Optional[float]
    green_flag_laps: int


# ── Endpoints ─────────────────────────────────────────────────

@router.get(
    "/sessions/{session_id}/stints",
    response_model=list[StintOut],
    dependencies=[Depends(require_api_key)],
)
def get_stints(
    session_id: int,
    car:        Optional[str] = Query(None, description="Filter by car number"),
    car_class:  Optional[str] = Query(None, description="Filter by class e.g. HYPERCAR"),
    cur=Depends(get_cursor),
):
    """
    Stint breakdown for all cars in a session.
    Protected — requires X-API-Key header.
    """
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    # Check analytics exist
    cur.execute(
        "SELECT COUNT(*) AS n FROM analytics_stints WHERE session_id = %s",
        (session_id,)
    )
    if cur.fetchone()["n"] == 0:
        raise HTTPException(404, "No analytics data for this session. Run the analytics engine first.")

    filters = ["a.session_id = %s"]
    params  = [session_id]

    if car:
        filters.append("c.number = %s")
        params.append(car)
    if car_class:
        filters.append("c.car_class ILIKE %s")
        params.append(car_class)

    where = " AND ".join(filters)

    cur.execute(f"""
        SELECT
            c.number        AS car_number,
            c.car_class,
            t.name          AS team,
            a.stint_number,
            a.start_lap,
            a.end_lap,
            a.lap_count,
            a.tyre_age_laps,
            a.baseline_pace_s,
            a.degradation_s_per_lap,
            a.consistency_s,
            a.is_final_stint
        FROM analytics_stints a
        JOIN cars c         ON c.id = a.car_id
        LEFT JOIN teams t   ON t.id = c.team_id
        WHERE {where}
        ORDER BY c.number, a.stint_number
    """, params)

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No stint data found for session {session_id}.")

    return [StintOut(**dict(r)) for r in rows]


@router.get(
    "/sessions/{session_id}/pace",
    response_model=list[PaceOut],
    dependencies=[Depends(require_api_key)],
)
def get_pace(
    session_id: int,
    car_class:  Optional[str] = Query(None, description="Filter by class"),
    cur=Depends(get_cursor),
):
    """
    Average green flag pace per car for a session.
    Sorted by avg_pace_s ascending (fastest first within class).
    Protected — requires X-API-Key header.
    """
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    filters = ["a.session_id = %s"]
    params  = [session_id]

    if car_class:
        filters.append("c.car_class ILIKE %s")
        params.append(car_class)

    where = " AND ".join(filters)

    cur.execute(f"""
        SELECT
            c.number        AS car_number,
            c.car_class,
            t.name          AS team,
            a.total_laps,
            a.green_flag_laps,
            a.pit_stops,
            a.best_lap_s,
            a.avg_pace_s,
            a.consistency_s
        FROM analytics_car_session a
        JOIN cars c         ON c.id = a.car_id
        LEFT JOIN teams t   ON t.id = c.team_id
        WHERE {where}
        ORDER BY c.car_class NULLS LAST, a.avg_pace_s NULLS LAST
    """, params)

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No pace data found for session {session_id}.")

    return [PaceOut(**dict(r)) for r in rows]


@router.get(
    "/sessions/{session_id}/gaps",
    response_model=list[GapPoint],
    dependencies=[Depends(require_api_key)],
)
def get_gaps(
    session_id: int,
    car_class:  Optional[str] = Query(None),
    car:        Optional[str] = Query(None),
    max_laps:   int           = Query(50, ge=1, le=500, description="Max laps to return per car"),
    cur=Depends(get_cursor),
):
    """
    Gap evolution over race distance — cumulative lap time per car.
    Returns lap times that can be used to compute gaps between cars.
    Protected — requires X-API-Key header.
    """
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    filters = ["l.session_id = %s", "l.lap_time_s IS NOT NULL",
               "l.lap_time_s < 600", "l.crossing_finish_in_pit = FALSE"]
    params  = [session_id]

    if car:
        filters.append("c.number = %s")
        params.append(car)
    if car_class:
        filters.append("c.car_class ILIKE %s")
        params.append(car_class)

    where = " AND ".join(filters)

    cur.execute(f"""
        SELECT
            l.lap_number,
            c.number    AS car_number,
            c.car_class,
            l.lap_time_s,
            SUM(l.lap_time_s) OVER (
                PARTITION BY l.car_id
                ORDER BY l.lap_number
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS cumulative_s
        FROM laps l
        JOIN cars c ON c.id = l.car_id
        WHERE {where}
        ORDER BY l.lap_number, c.number
        LIMIT %s
    """, params + [max_laps * 60])  # rough limit

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No lap data found for session {session_id}.")

    return [GapPoint(**dict(r)) for r in rows]


@router.get(
    "/drivers/{driver_id}/consistency",
    response_model=list[DriverConsistency],
    dependencies=[Depends(require_api_key)],
)
def get_driver_consistency(
    driver_id:    int,
    series:       Optional[str] = Query(None, description="Filter by series key"),
    session_type: Optional[str] = Query("Race", description="Race, Practice, etc."),
    limit:        int           = Query(20, ge=1, le=100),
    cur=Depends(get_cursor),
):
    """
    Consistency stats for a driver across sessions.
    Uses pre-computed analytics_car_session data.
    Protected — requires X-API-Key header.
    """
    cur.execute("SELECT id, first_name, last_name FROM drivers WHERE id = %s", (driver_id,))
    driver = cur.fetchone()
    if not driver:
        raise HTTPException(404, f"Driver {driver_id} not found.")

    filters = ["rd.driver_id = %s"]
    params  = [driver_id]

    if series:
        filters.append("sr.key::text = %s")
        params.append(series.upper())
    if session_type:
        filters.append("s.session_type::text = %s")
        params.append(session_type)

    where = " AND ".join(filters)

    cur.execute(f"""
        SELECT DISTINCT
            a.session_id,
            sr.key::text    AS series,
            se.label        AS season,
            ev.name         AS event,
            s.name          AS session_name,
            s.session_type::text AS session_type,
            c.number        AS car_number,
            c.car_class,
            a.consistency_s,
            a.avg_pace_s,
            a.best_lap_s,
            a.green_flag_laps
        FROM analytics_car_session a
        JOIN result_drivers rd  ON rd.result_id IN (
            SELECT id FROM results WHERE session_id = a.session_id AND car_id = a.car_id
        )
        JOIN sessions s   ON s.id = a.session_id
        JOIN events ev    ON ev.id = s.event_id
        JOIN seasons se   ON se.id = ev.season_id
        JOIN series sr    ON sr.id = se.series_id
        JOIN cars c       ON c.id = a.car_id
        WHERE {where}
          AND a.consistency_s IS NOT NULL
        ORDER BY a.consistency_s NULLS LAST
        LIMIT %s
    """, params + [limit])

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No consistency data found for driver {driver_id}.")

    return [DriverConsistency(**dict(r)) for r in rows]
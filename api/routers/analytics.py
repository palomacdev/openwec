"""
OpenWEC API — Analytics Router
Protected endpoints: pre-computed analytics data.
Requires X-API-Key header.

Endpoints:
    GET /sessions/{id}/stints       ← stint breakdown per car
    GET /sessions/{id}/pace         ← average green flag pace per car
    GET /sessions/{id}/gaps         ← gap evolution over laps
    GET /sessions/{id}/pit-window   ← estimated pit window per car
    GET /drivers/{id}/consistency   ← variance stats across sessions
"""

from collections import defaultdict
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from api.deps import get_cursor, require_api_key
from api.config import settings


router = APIRouter(tags=["Analytics"])


# ── Pit loss defaults by class (seconds) ─────────────────────
PIT_LOSS_DEFAULTS = {
    "HYPERCAR":   28.0,
    "LMP2":       22.0,
    "LMGT3":      22.0,
    "LMP1":       28.0,
    "GTP":        28.0,
    "LMGTE Pro":  24.0,
    "LMGTE Am":   24.0,
    "GTD":        22.0,
    "GTLM":       24.0,
    "DPi":        26.0,
    "GT3":        22.0,
}
DEFAULT_PIT_LOSS = 25.0


# ── Schemas ───────────────────────────────────────────────────

class StintOut(BaseModel):
    car_number:            str
    car_class:             Optional[str]
    team:                  Optional[str]
    stint_number:          int
    start_lap:             int
    end_lap:               int
    lap_count:             int
    tyre_age_laps:         int
    baseline_pace_s:       Optional[float]
    degradation_s_per_lap: Optional[float]
    consistency_s:         Optional[float]
    is_final_stint:        bool


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


class StintPitWindow(BaseModel):
    stint_number:          int
    start_lap:             int
    end_lap:               int
    tyre_age_laps:         int
    baseline_pace_s:       Optional[float]
    degradation_s_per_lap: Optional[float]
    pit_loss_s:            float
    early_lap:             Optional[int]
    ideal_lap:             Optional[int]
    late_lap:              Optional[int]
    early_lap_abs:         Optional[int]
    ideal_lap_abs:         Optional[int]
    late_lap_abs:          Optional[int]
    recommendation:        str


class CarPitWindow(BaseModel):
    car_number: str
    car_class:  Optional[str]
    team:       Optional[str]
    pit_loss_s: float
    stints:     list[StintPitWindow]


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


class RaceControlPeriod(BaseModel):
    flag:          str    # 'FCY' or 'Other' (Safety Car)
    label:         str    # human-readable label
    start_lap:     int
    end_lap:       int
    duration_laps: int


# ── Helpers ───────────────────────────────────────────────────

def estimate_window(
    stint_start_lap: int,
    tyre_age:        int,
    baseline_pace:   Optional[float],
    degradation:     Optional[float],
    pit_loss_s:      float,
) -> StintPitWindow:
    """Computes optimal pit window for a single stint."""
    window = StintPitWindow(
        stint_number=0,
        start_lap=stint_start_lap,
        end_lap=stint_start_lap + tyre_age - 1,
        tyre_age_laps=tyre_age,
        baseline_pace_s=baseline_pace,
        degradation_s_per_lap=degradation,
        pit_loss_s=pit_loss_s,
        early_lap=None, ideal_lap=None, late_lap=None,
        early_lap_abs=None, ideal_lap_abs=None, late_lap_abs=None,
        recommendation="",
    )

    if not degradation or degradation <= 0:
        window.recommendation = (
            "Pace not degrading — pit window driven by fuel/tyres, not lap time."
        )
        return window

    break_even = pit_loss_s / degradation

    if break_even > tyre_age * 1.5:
        window.recommendation = (
            f"Low degradation ({degradation:+.3f}s/lap) — "
            f"no pace-based reason to pit within current stint length."
        )
        return window

    ideal = int(round(break_even))
    early = max(1, int(break_even * 0.85))
    late  = int(break_even * 1.10)

    window.ideal_lap     = ideal
    window.early_lap     = early
    window.late_lap      = late
    window.ideal_lap_abs = stint_start_lap + ideal - 1
    window.early_lap_abs = stint_start_lap + early - 1
    window.late_lap_abs  = stint_start_lap + late  - 1
    window.recommendation = (
        f"Optimal: laps {early}–{late} of stint "
        f"(deg={degradation:+.3f}s/lap, pit_loss={pit_loss_s:.0f}s)."
    )
    return window


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
    """Stint breakdown for all cars in a session."""
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    cur.execute(
        "SELECT COUNT(*) AS n FROM analytics_stints WHERE session_id = %s",
        (session_id,)
    )
    if cur.fetchone()["n"] == 0:
        raise HTTPException(404, "No analytics data. Run the analytics engine first.")

    filters = ["a.session_id = %s"]
    params  = [session_id]
    if car:
        filters.append("c.number = %s")
        params.append(car)
    if car_class:
        filters.append("c.car_class ILIKE %s")
        params.append(car_class)

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
        WHERE {" AND ".join(filters)}
        ORDER BY c.number, a.stint_number
    """, params)

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No stint data found.")
    return [StintOut(**dict(r)) for r in rows]


@router.get(
    "/sessions/{session_id}/pace",
    response_model=list[PaceOut],
    dependencies=[Depends(require_api_key)],
)
def get_pace(
    session_id: int,
    car_class:  Optional[str] = Query(None),
    cur=Depends(get_cursor),
):
    """Average green flag pace per car, sorted fastest first."""
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    filters = ["a.session_id = %s"]
    params  = [session_id]
    if car_class:
        filters.append("c.car_class ILIKE %s")
        params.append(car_class)

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
        WHERE {" AND ".join(filters)}
        ORDER BY c.car_class NULLS LAST, a.avg_pace_s NULLS LAST
    """, params)

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No pace data found.")
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
    max_laps:   int           = Query(50, ge=1, le=500),
    cur=Depends(get_cursor),
):
    """Gap evolution — cumulative lap time per car over race distance."""
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    filters = [
        "l.session_id = %s",
        "l.lap_time_s IS NOT NULL",
        "l.lap_time_s < 600",
        "l.crossing_finish_in_pit = FALSE",
    ]
    params = [session_id]
    if car:
        filters.append("c.number = %s")
        params.append(car)
    if car_class:
        filters.append("c.car_class ILIKE %s")
        params.append(car_class)

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
        WHERE {" AND ".join(filters)}
        ORDER BY l.lap_number, c.number
        LIMIT %s
    """, params + [max_laps * 60])

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No lap data found.")
    return [GapPoint(**dict(r)) for r in rows]


@router.get(
    "/sessions/{session_id}/pit-window",
    response_model=list[CarPitWindow],
    dependencies=[Depends(require_api_key)],
)
def get_pit_window(
    session_id: int,
    car:        Optional[str]   = Query(None),
    car_class:  Optional[str]   = Query(None),
    pit_loss_s: Optional[float] = Query(None, description="Override pit loss in seconds"),
    cur=Depends(get_cursor),
):
    """
    Estimated pit window per stint per car.
    Based on degradation rate vs pit loss time.
    """
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    filters = ["a.session_id = %s"]
    params  = [session_id]
    if car:
        filters.append("c.number = %s")
        params.append(car)
    if car_class:
        filters.append("c.car_class ILIKE %s")
        params.append(car_class)

    cur.execute(f"""
        SELECT
            c.number        AS car_number,
            c.car_class,
            t.name          AS team,
            a.stint_number,
            a.start_lap,
            a.end_lap,
            a.tyre_age_laps,
            a.baseline_pace_s,
            a.degradation_s_per_lap
        FROM analytics_stints a
        JOIN cars c         ON c.id = a.car_id
        LEFT JOIN teams t   ON t.id = c.team_id
        WHERE {" AND ".join(filters)}
        ORDER BY c.number, a.stint_number
    """, params)

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, "No stint data found.")

    cars: dict = defaultdict(lambda: {"car_class": None, "team": None, "stints": []})

    for row in rows:
        cn = row["car_number"]
        cars[cn]["car_class"] = row["car_class"]
        cars[cn]["team"]      = row["team"]

        loss = pit_loss_s or PIT_LOSS_DEFAULTS.get(
            row["car_class"] or "", DEFAULT_PIT_LOSS
        )

        window = estimate_window(
            stint_start_lap=row["start_lap"],
            tyre_age=       row["tyre_age_laps"],
            baseline_pace=  float(row["baseline_pace_s"]) if row["baseline_pace_s"] else None,
            degradation=    float(row["degradation_s_per_lap"]) if row["degradation_s_per_lap"] else None,
            pit_loss_s=     loss,
        )
        window.stint_number = row["stint_number"]
        cars[cn]["stints"].append(window)

    return [
        CarPitWindow(
            car_number=cn,
            car_class= data["car_class"],
            team=      data["team"],
            pit_loss_s=pit_loss_s or PIT_LOSS_DEFAULTS.get(
                data["car_class"] or "", DEFAULT_PIT_LOSS
            ),
            stints=data["stints"],
        )
        for cn, data in cars.items()
    ]


@router.get(
    "/drivers/{driver_id}/consistency",
    response_model=list[DriverConsistency],
    dependencies=[Depends(require_api_key)],
)
def get_driver_consistency(
    driver_id:    int,
    series:       Optional[str] = Query(None),
    session_type: Optional[str] = Query("Race"),
    limit:        int           = Query(20, ge=1, le=100),
    cur=Depends(get_cursor),
):
    """Consistency stats for a driver across sessions."""
    cur.execute("SELECT id FROM drivers WHERE id = %s", (driver_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Driver {driver_id} not found.")

    filters = ["rd.driver_id = %s"]
    params  = [driver_id]
    if series:
        filters.append("sr.key::text = %s")
        params.append(series.upper())
    if session_type:
        filters.append("s.session_type::text = %s")
        params.append(session_type)

    cur.execute(f"""
        SELECT DISTINCT
            a.session_id,
            sr.key::text         AS series,
            se.label             AS season,
            ev.name              AS event,
            s.name               AS session_name,
            s.session_type::text AS session_type,
            c.number             AS car_number,
            c.car_class,
            a.consistency_s,
            a.avg_pace_s,
            a.best_lap_s,
            a.green_flag_laps
        FROM analytics_car_session a
        JOIN result_drivers rd ON rd.result_id IN (
            SELECT id FROM results WHERE session_id = a.session_id AND car_id = a.car_id
        )
        JOIN sessions s   ON s.id = a.session_id
        JOIN events ev    ON ev.id = s.event_id
        JOIN seasons se   ON se.id = ev.season_id
        JOIN series sr    ON sr.id = se.series_id
        JOIN cars c       ON c.id = a.car_id
        WHERE {" AND ".join(filters)}
          AND a.consistency_s IS NOT NULL
        ORDER BY a.consistency_s NULLS LAST
        LIMIT %s
    """, params + [limit])

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No consistency data found for driver {driver_id}.")
    return [DriverConsistency(**dict(r)) for r in rows]


FLAG_LABELS = {
    "FCY":   "Full Course Yellow",
    "Other": "Safety Car",
}


@router.get(
    "/sessions/{session_id}/race-control",
    response_model=list[RaceControlPeriod],
    dependencies=[Depends(require_api_key)],
)
def get_race_control(session_id: int, cur=Depends(get_cursor)):
    """
    Detects SC / FCY periods for a session.
    Uses the most common flag_at_fl value across all cars for each lap
    (track-wide flags should be consistent across the field).
    Returns contiguous non-green periods.
    """
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    cur.execute("""
        SELECT lap_number, flag_at_fl::text AS flag, COUNT(*) AS cnt
        FROM laps
        WHERE session_id = %s AND flag_at_fl IS NOT NULL
        GROUP BY lap_number, flag_at_fl
        ORDER BY lap_number
    """, (session_id,))
    rows = cur.fetchall()

    if not rows:
        raise HTTPException(404, "No flag data found for this session.")

    # Mode (most common flag) per lap
    lap_flags: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for r in rows:
        lap_flags[r["lap_number"]].append((r["flag"], r["cnt"]))

    mode_per_lap = {
        lap: max(flags, key=lambda x: x[1])[0]
        for lap, flags in lap_flags.items()
    }

    # Group contiguous non-GF periods
    periods: list[dict] = []
    current: Optional[dict] = None

    for lap in sorted(mode_per_lap.keys()):
        flag = mode_per_lap[lap]
        if flag == "GF":
            if current:
                periods.append(current)
                current = None
            continue

        if current and current["flag"] == flag and lap == current["end_lap"] + 1:
            current["end_lap"] = lap
        else:
            if current:
                periods.append(current)
            current = {"flag": flag, "start_lap": lap, "end_lap": lap}

    if current:
        periods.append(current)

    if not periods:
        raise HTTPException(404, "No SC/FCY periods found — race was green throughout.")

    return [
        RaceControlPeriod(
            flag=p["flag"],
            label=FLAG_LABELS.get(p["flag"], p["flag"]),
            start_lap=p["start_lap"],
            end_lap=p["end_lap"],
            duration_laps=p["end_lap"] - p["start_lap"] + 1,
        )
        for p in periods
    ]
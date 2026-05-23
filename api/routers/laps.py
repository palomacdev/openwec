"""
OpenWEC API — Laps Router
Protected endpoints: lap-by-lap data.
Requires X-API-Key header.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from api.deps import get_cursor, require_api_key
from api.schemas import LapOut, PaginatedLaps
from api.config import settings

router = APIRouter(tags=["Laps"])


@router.get(
    "/sessions/{session_id}/laps",
    response_model=PaginatedLaps,
    dependencies=[Depends(require_api_key)],
)
def get_laps(
    session_id: int,
    car:      Optional[str] = Query(None, description="Filter by car number"),
    page:     int           = Query(1,    ge=1),
    page_size: int          = Query(settings.default_page_size,
                                    ge=1, le=settings.max_page_size),
    cur=Depends(get_cursor),
):
    """
    Lap-by-lap data for a session.
    Protected — requires X-API-Key header.

    Supports filtering by car number and pagination.
    """
    # Verify session exists
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    # Build WHERE clause
    filters = ["l.session_id = %s"]
    params:  list = [session_id]

    if car:
        filters.append("c.number = %s")
        params.append(car)

    where = " AND ".join(filters)

    # Count total
    cur.execute(f"""
        SELECT COUNT(*) AS total
        FROM laps l
        JOIN cars c ON c.id = l.car_id
        WHERE {where}
    """, params)
    total = cur.fetchone()["total"]

    # Fetch page
    offset = (page - 1) * page_size
    cur.execute(f"""
        SELECT
            c.number            AS car_number,
            d.first_name || ' ' || d.last_name AS driver_name,
            l.lap_number,
            l.lap_time_s,
            l.s1_s,
            l.s2_s,
            l.s3_s,
            l.kph,
            l.top_speed_kph,
            l.lap_improvement,
            l.crossing_finish_in_pit,
            l.flag_at_fl::text  AS flag_at_fl,
            l.pit_time_s,
            l.elapsed_raw,
            l.hour_raw
        FROM laps l
        JOIN cars c         ON c.id = l.car_id
        LEFT JOIN drivers d ON d.id = l.driver_id
        WHERE {where}
        ORDER BY c.number, l.lap_number
        LIMIT %s OFFSET %s
    """, params + [page_size, offset])

    rows = cur.fetchall()

    laps = [
        LapOut(
            car_number=row["car_number"],
            driver_name=row["driver_name"].strip() if row["driver_name"] and row["driver_name"].strip() != " " else None,
            lap_number=row["lap_number"],
            lap_time_s=row["lap_time_s"],
            s1_s=row["s1_s"],
            s2_s=row["s2_s"],
            s3_s=row["s3_s"],
            kph=row["kph"],
            top_speed_kph=row["top_speed_kph"],
            lap_improvement=bool(row["lap_improvement"]),
            crossing_finish_in_pit=bool(row["crossing_finish_in_pit"]),
            flag_at_fl=row["flag_at_fl"],
            pit_time_s=row["pit_time_s"],
            elapsed_raw=row["elapsed_raw"],
            hour_raw=row["hour_raw"],
        )
        for row in rows
    ]

    return PaginatedLaps(
        session_id=session_id,
        car_number=car,
        total=total,
        page=page,
        page_size=page_size,
        results=laps,
    )


@router.get(
    "/sessions/{session_id}/laps/{car_number}",
    response_model=list[LapOut],
    dependencies=[Depends(require_api_key)],
)
def get_car_laps(
    session_id: int,
    car_number: str,
    cur=Depends(get_cursor),
):
    """
    All laps for a specific car in a session.
    Protected — requires X-API-Key header.
    """
    cur.execute("""
        SELECT
            c.number            AS car_number,
            d.first_name || ' ' || d.last_name AS driver_name,
            l.lap_number,
            l.lap_time_s,
            l.s1_s, l.s2_s, l.s3_s,
            l.kph, l.top_speed_kph,
            l.lap_improvement,
            l.crossing_finish_in_pit,
            l.flag_at_fl::text  AS flag_at_fl,
            l.pit_time_s,
            l.elapsed_raw,
            l.hour_raw
        FROM laps l
        JOIN cars c         ON c.id = l.car_id
        LEFT JOIN drivers d ON d.id = l.driver_id
        WHERE l.session_id = %s AND c.number = %s
        ORDER BY l.lap_number
    """, (session_id, car_number))

    rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"No laps found for car {car_number} in session {session_id}.")

    return [
        LapOut(
            car_number=row["car_number"],
            driver_name=row["driver_name"].strip() if row["driver_name"] and row["driver_name"].strip() != " " else None,
            lap_number=row["lap_number"],
            lap_time_s=row["lap_time_s"],
            s1_s=row["s1_s"],
            s2_s=row["s2_s"],
            s3_s=row["s3_s"],
            kph=row["kph"],
            top_speed_kph=row["top_speed_kph"],
            lap_improvement=bool(row["lap_improvement"]),
            crossing_finish_in_pit=bool(row["crossing_finish_in_pit"]),
            flag_at_fl=row["flag_at_fl"],
            pit_time_s=row["pit_time_s"],
            elapsed_raw=row["elapsed_raw"],
            hour_raw=row["hour_raw"],
        )
        for row in rows
    ]
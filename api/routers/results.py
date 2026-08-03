"""
OpenWEC API — Results Router
Public endpoint: final classification per session.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from api.deps import get_cursor
from api.schemas import ResultOut, DriverSlot

router = APIRouter(tags=["Results"])


class PaginatedResults(BaseModel):
    session_id:  int
    total:       int
    page:        int
    page_size:   Optional[int]
    has_more:    bool
    results:     list[ResultOut]


@router.get("/sessions/{session_id}/results")
def get_results(
    session_id: int,
    page:       int           = Query(1, ge=1, description="Page number (1-based)"),
    page_size:  Optional[int] = Query(None, ge=1, le=200,
                                     description="Results per page. If omitted, returns all."),
    car_class:  Optional[str] = Query(None, description="Filter by class, e.g. HYPERCAR"),
    cur=Depends(get_cursor),
):
    """
    Final classification for a session.
    Public — no API key required.

    Pagination is optional — if page_size is omitted, all results are returned
    as a plain list (backwards-compatible with existing SDK and dashboard code).

    When page_size is provided, returns a paginated envelope with metadata.

    Examples:
        GET /sessions/6556/results                      → all results (list)
        GET /sessions/6556/results?page_size=20         → first 20 (paginated)
        GET /sessions/6556/results?car_class=HYPERCAR   → filtered (list)
    """
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    # Build filters
    filters = ["r.session_id = %s"]
    params  = [session_id]
    if car_class:
        filters.append("c.car_class ILIKE %s")
        params.append(car_class)

    where = " AND ".join(filters)

    # Total count
    cur.execute(f"""
        SELECT COUNT(*) AS n FROM results r
        JOIN cars c ON c.id = r.car_id
        WHERE {where}
    """, params)
    total = cur.fetchone()["n"]

    # Pagination
    if page_size:
        offset = (page - 1) * page_size
        limit_clause = f"LIMIT {page_size} OFFSET {offset}"
    else:
        limit_clause = ""

    cur.execute(f"""
        SELECT
            r.id            AS result_id,
            r.position,
            c.number        AS car_number,
            c.car_class,
            c.vehicle,
            c.tyre_supplier,
            t.name          AS team,
            r.status::text  AS status,
            r.laps_completed,
            r.total_time_s,
            r.gap_to_first_s,
            r.fl_lap_number,
            r.fl_time_s,
            r.fl_kph
        FROM results r
        JOIN cars c         ON c.id = r.car_id
        LEFT JOIN teams t   ON t.id = c.team_id
        WHERE {where}
        ORDER BY r.position NULLS LAST, r.id
        {limit_clause}
    """, params)
    results = cur.fetchall()

    if not results and not page_size:
        return []

    # Fetch drivers for all results in one query
    result_ids = [row["result_id"] for row in results]
    drivers_by_result: dict[int, list[DriverSlot]] = {}

    if result_ids:
        cur.execute("""
            SELECT
                rd.result_id,
                rd.slot,
                d.id,
                d.first_name,
                d.last_name,
                d.country,
                d.imsa_driver_rating::text AS imsa_rating
            FROM result_drivers rd
            JOIN drivers d ON d.id = rd.driver_id
            WHERE rd.result_id = ANY(%s)
            ORDER BY rd.result_id, rd.slot
        """, (result_ids,))

        for dr in cur.fetchall():
            rid = dr["result_id"]
            if rid not in drivers_by_result:
                drivers_by_result[rid] = []
            drivers_by_result[rid].append(DriverSlot(
                id=dr["id"],
                slot=dr["slot"],
                first_name=dr["first_name"] or "",
                last_name=dr["last_name"] or "",
                country=dr["country"],
                imsa_rating=dr["imsa_rating"],
            ))

    output = [
        ResultOut(
            position=row["position"],
            car_number=row["car_number"],
            car_class=row["car_class"],
            vehicle=row["vehicle"],
            team=row["team"],
            status=row["status"],
            tyre_supplier=row["tyre_supplier"],
            laps_completed=row["laps_completed"],
            total_time_s=row["total_time_s"],
            gap_to_first_s=row["gap_to_first_s"],
            fl_lap_number=row["fl_lap_number"],
            fl_time_s=row["fl_time_s"],
            fl_kph=row["fl_kph"],
            drivers=drivers_by_result.get(row["result_id"], []),
        )
        for row in results
    ]

    # Without page_size — return plain list (backwards compatible)
    if not page_size:
        return output

    # With page_size — return paginated envelope
    return PaginatedResults(
        session_id=session_id,
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
        results=output,
    )
"""
OpenWEC API — Results Router
Public endpoint: final classification per session.
"""

from fastapi import APIRouter, Depends, HTTPException
from api.deps import get_cursor
from api.schemas import ResultOut, DriverSlot

router = APIRouter(tags=["Results"])


@router.get("/sessions/{session_id}/results", response_model=list[ResultOut])
def get_results(session_id: int, cur=Depends(get_cursor)):
    """
    Final classification for a session.
    Public — no API key required.
    """
    # Verify session exists
    cur.execute("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not cur.fetchone():
        raise HTTPException(404, f"Session {session_id} not found.")

    # Fetch results
    cur.execute("""
        SELECT
            r.id            AS result_id,
            r.position,
            c.number        AS car_number,
            c.car_class,
            c.vehicle,
            t.name          AS team,
            r.status::text  AS status,
            c.tyre_supplier,
            r.laps_completed,
            r.total_time_s,
            r.gap_to_first_s,
            r.fl_lap_number,
            r.fl_time_s,
            r.fl_kph
        FROM results r
        JOIN cars c         ON c.id = r.car_id
        LEFT JOIN teams t   ON t.id = c.team_id
        WHERE r.session_id = %s
        ORDER BY r.position NULLS LAST, r.id
    """, (session_id,))
    results = cur.fetchall()

    if not results:
        return []

    # Fetch drivers for all results in one query
    result_ids = [row["result_id"] for row in results]
    cur.execute("""
        SELECT
            rd.result_id,
            rd.slot,
            d.first_name,
            d.last_name,
            d.country,
            d.imsa_driver_rating::text AS imsa_rating
        FROM result_drivers rd
        JOIN drivers d ON d.id = rd.driver_id
        WHERE rd.result_id = ANY(%s)
        ORDER BY rd.result_id, rd.slot
    """, (result_ids,))
    driver_rows = cur.fetchall()

    # Group drivers by result_id
    drivers_by_result: dict[int, list[DriverSlot]] = {}
    for dr in driver_rows:
        rid = dr["result_id"]
        if rid not in drivers_by_result:
            drivers_by_result[rid] = []
        drivers_by_result[rid].append(DriverSlot(
            slot=dr["slot"],
            first_name=dr["first_name"] or "",
            last_name=dr["last_name"] or "",
            country=dr["country"],
            imsa_rating=dr["imsa_rating"],
        ))

    # Build response
    output = []
    for row in results:
        output.append(ResultOut(
            position=row["position"],
            car_number=row["car_number"],
            car_class=row["car_class"],
            vehicle=row["vehicle"],
            team=row["team"],
            status=row["status"],
            laps_completed=row["laps_completed"],
            total_time_s=row["total_time_s"],
            gap_to_first_s=row["gap_to_first_s"],
            fl_lap_number=row["fl_lap_number"],
            fl_time_s=row["fl_time_s"],
            fl_kph=row["fl_kph"],
            drivers=drivers_by_result.get(row["result_id"], []),
        ))

    return output
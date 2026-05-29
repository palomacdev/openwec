"""
OpenWEC — Phase 5: Analytics Engine

Computes per-session analytics:
  - Stint detection (pit lap flags → stint boundaries)
  - Pace baseline (median green-flag lap time per stint)
  - Degradation rate (linear regression of lap_time vs stint_lap)
  - Driver consistency (std dev of green-flag laps)
  - Gap evolution (position over lap number)

Results are stored in analytics_* tables (created on first run).

Usage:
    python analytics/engine.py --session 662
    python analytics/engine.py --series WEC --session-type Race
    python analytics/engine.py --all
"""

import argparse
import statistics
import psycopg2
import psycopg2.extras
from dataclasses import dataclass, field


DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5433,
    "dbname":   "openwec",
    "user":     "openwec",
    "password": "openwec",
}

# Lap time thresholds
MAX_VALID_LAP_S   = 600.0   # discard laps > 10 min (formation, VSC, etc.)
MIN_VALID_LAP_S   = 60.0    # discard laps < 1 min (incomplete sectors)
OUT_LAP_THRESHOLD = 1.10    # out lap if > 110% of stint baseline


# ── Data classes ──────────────────────────────────────────────

@dataclass
class Lap:
    lap_number:   int
    lap_time_s:   float | None
    s1_s:         float | None
    s2_s:         float | None
    s3_s:         float | None
    in_pit:       bool
    pit_time_s:   float | None
    flag:         str | None
    driver_slot:  int | None
    driver_id:    int | None


@dataclass
class Stint:
    stint_number:   int
    car_id:         int
    session_id:     int
    start_lap:      int
    end_lap:        int
    laps:           list[Lap] = field(default_factory=list)

    # Computed
    lap_count:       int   = 0
    baseline_pace_s: float | None = None
    degradation_s_per_lap: float | None = None
    consistency_s:   float | None = None
    tyre_age_laps:   int   = 0
    is_final_stint:  bool  = False


@dataclass
class CarAnalytics:
    session_id:  int
    car_id:      int
    car_number:  str
    car_class:   str | None
    stints:      list[Stint] = field(default_factory=list)

    # Session-level
    total_laps:       int   = 0
    green_flag_laps:  int   = 0
    pit_stops:        int   = 0
    best_lap_s:       float | None = None
    avg_pace_s:       float | None = None
    consistency_s:    float | None = None


# ── Stint detection ───────────────────────────────────────────

def detect_stints(laps: list[Lap]) -> list[Stint]:
    """
    Splits laps into stints based on pit flags.

    Rules:
    - A lap with crossing_finish_in_pit=True is the LAST lap of a stint
    - The next lap is the OUT lap of the new stint
    - Very slow laps (> MAX_VALID_LAP_S) are excluded from pace calcs
    """
    if not laps:
        return []

    stints = []
    current_stint_laps: list[Lap] = []
    stint_number = 1

    for lap in laps:
        current_stint_laps.append(lap)

        if lap.in_pit:
            # End of stint
            if current_stint_laps:
                stints.append(_build_stint(stint_number, current_stint_laps))
                stint_number += 1
                current_stint_laps = []

    # Last stint (no final pit)
    if current_stint_laps:
        stint = _build_stint(stint_number, current_stint_laps)
        stint.is_final_stint = True
        stints.append(stint)

    return stints


def _build_stint(number: int, laps: list[Lap]) -> Stint:
    stint = Stint(
        stint_number=number,
        car_id=0,
        session_id=0,
        start_lap=laps[0].lap_number,
        end_lap=laps[-1].lap_number,
        laps=laps,
        lap_count=len(laps),
    )

    # Green flag laps only (exclude pit laps, out laps, SC laps)
    green_laps = _green_flag_laps(laps)

    if len(green_laps) >= 3:
        times = [float(l.lap_time_s) for l in green_laps if l.lap_time_s]

        if times:
            stint.baseline_pace_s  = statistics.median(times[:5])  # first 5 green laps
            stint.consistency_s    = statistics.stdev(times) if len(times) > 1 else 0.0
            stint.degradation_s_per_lap = _linear_regression(
                list(range(len(times))), times
            )

    stint.tyre_age_laps = len([l for l in laps if not l.in_pit])
    return stint


def _green_flag_laps(laps: list[Lap]) -> list[Lap]:
    """Returns laps that are valid for pace analysis."""
    result = []
    for i, lap in enumerate(laps):
        if lap.in_pit:
            continue
        if lap.lap_time_s is None:
            continue
        if lap.lap_time_s > MAX_VALID_LAP_S:
            continue
        if lap.lap_time_s < MIN_VALID_LAP_S:
            continue
        if lap.flag and lap.flag not in ("GF", None):
            continue
        # Skip out laps (first lap of stint)
        if i == 0:
            continue
        result.append(lap)
    return result


def _linear_regression(x,y) -> float | None:
    """Returns slope of linear regression (seconds per lap)."""
    
    x = [float(xi) for xi in x]
    y = [float(yi) for yi in y]

    n = len(x)
    if n < 3:
        return None
    x_mean = sum(x) / n
    y_mean = sum(y) / n
    num = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    den = sum((xi - x_mean) ** 2 for xi in x)
    if den == 0:
        return None
    return round(num / den, 4)


# ── Session analytics ─────────────────────────────────────────

def analyze_session(session_id: int, cur) -> list[CarAnalytics]:
    """Computes analytics for all cars in a session."""

    # Fetch all laps for this session
    cur.execute("""
        SELECT
            l.car_id,
            c.number    AS car_number,
            c.car_class,
            l.lap_number,
            l.lap_time_s,
            l.s1_s, l.s2_s, l.s3_s,
            l.crossing_finish_in_pit AS in_pit,
            l.pit_time_s,
            l.flag_at_fl::text AS flag,
            l.driver_slot,
            l.driver_id
        FROM laps l
        JOIN cars c ON c.id = l.car_id
        WHERE l.session_id = %s
        ORDER BY l.car_id, l.lap_number
    """, (session_id,))

    rows = cur.fetchall()
    if not rows:
        return []

    # Group by car
    cars_laps: dict[int, list] = {}
    car_meta: dict[int, dict]  = {}
    for row in rows:
        cid = row["car_id"]
        if cid not in cars_laps:
            cars_laps[cid] = []
            car_meta[cid]  = {
                "car_number": row["car_number"],
                "car_class":  row["car_class"],
            }
        cars_laps[cid].append(Lap(
            lap_number=  row["lap_number"],
            lap_time_s=  row["lap_time_s"],
            s1_s=        row["s1_s"],
            s2_s=        row["s2_s"],
            s3_s=        row["s3_s"],
            in_pit=      bool(row["in_pit"]),
            pit_time_s=  row["pit_time_s"],
            flag=        row["flag"],
            driver_slot= row["driver_slot"],
            driver_id=   row["driver_id"],
        ))

    results = []
    for car_id, laps in cars_laps.items():
        meta   = car_meta[car_id]
        stints = detect_stints(laps)

        green_laps = _green_flag_laps(laps)
        all_times = [float(l.lap_time_s) for l in green_laps if l.lap_time_s]

        analytics = CarAnalytics(
            session_id=  session_id,
            car_id=      car_id,
            car_number=  meta["car_number"],
            car_class=   meta["car_class"],
            stints=      stints,
            total_laps=  len(laps),
            green_flag_laps= len(green_laps),
            pit_stops=   len(stints) - 1 if stints else 0,
            best_lap_s=  min(all_times) if all_times else None,
            avg_pace_s=  statistics.mean(all_times) if all_times else None,
            consistency_s= statistics.stdev(all_times) if len(all_times) > 1 else None,
        )

        # Attach session/car to stints
        for s in stints:
            s.session_id = session_id
            s.car_id     = car_id

        results.append(analytics)

    return results


# ── Output / storage ──────────────────────────────────────────

def print_session_summary(session_id: int, analytics: list[CarAnalytics]):
    print(f"\n{'='*70}")
    print(f"SESSION {session_id} — Analytics Summary")
    print(f"  Cars analyzed: {len(analytics)}")
    print(f"{'='*70}")
    print(f"\n{'Car':>4} {'Class':>10} {'Laps':>5} {'Pits':>5} "
          f"{'Best':>8} {'Avg Pace':>9} {'Consistency':>12}")
    print("-" * 70)

    # Sort by car class then best lap
    sorted_cars = sorted(
        analytics,
        key=lambda x: (x.car_class or "ZZZ", x.best_lap_s or 999)
    )

    for car in sorted_cars:
        best  = f"{car.best_lap_s:.3f}"   if car.best_lap_s   else "  N/A  "
        avg   = f"{car.avg_pace_s:.3f}"   if car.avg_pace_s   else "  N/A  "
        cons  = f"±{car.consistency_s:.3f}" if car.consistency_s else "  N/A  "
        print(f"{car.car_number:>4} {(car.car_class or ''):>10} "
              f"{car.total_laps:>5} {car.pit_stops:>5} "
              f"{best:>8} {avg:>9} {cons:>12}")

    print(f"\n{'='*70}")
    print("STINT DETAILS (top 5 cars by lap count)")
    print(f"{'='*70}")

    top5 = sorted(analytics, key=lambda x: -x.total_laps)[:5]
    for car in top5:
        print(f"\n  Car #{car.car_number} ({car.car_class}) — {len(car.stints)} stints")
        for s in car.stints:
            base  = f"{s.baseline_pace_s:.3f}s" if s.baseline_pace_s else "N/A"
            deg   = f"{s.degradation_s_per_lap:+.4f}s/lap" if s.degradation_s_per_lap else "N/A"
            cons  = f"±{s.consistency_s:.3f}s" if s.consistency_s else "N/A"
            final = " [FINAL]" if s.is_final_stint else ""
            print(f"    Stint {s.stint_number}: laps {s.start_lap}-{s.end_lap}"
                  f" ({s.tyre_age_laps} laps on tyre){final}")
            print(f"      baseline={base}  deg={deg}  consistency={cons}")


def save_to_db(analytics: list[CarAnalytics], cur, conn):
    """Persists analytics results to DB (creates tables if needed)."""

    cur.execute("""
        CREATE TABLE IF NOT EXISTS analytics_stints (
            id              SERIAL PRIMARY KEY,
            session_id      INT NOT NULL,
            car_id          INT NOT NULL,
            stint_number    SMALLINT NOT NULL,
            start_lap       SMALLINT,
            end_lap         SMALLINT,
            lap_count       SMALLINT,
            tyre_age_laps   SMALLINT,
            baseline_pace_s DECIMAL(8,3),
            degradation_s_per_lap DECIMAL(8,4),
            consistency_s   DECIMAL(8,3),
            is_final_stint  BOOLEAN DEFAULT FALSE,
            computed_at     TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (session_id, car_id, stint_number)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS analytics_car_session (
            id              SERIAL PRIMARY KEY,
            session_id      INT NOT NULL,
            car_id          INT NOT NULL,
            total_laps      SMALLINT,
            green_flag_laps SMALLINT,
            pit_stops       SMALLINT,
            best_lap_s      DECIMAL(8,3),
            avg_pace_s      DECIMAL(8,3),
            consistency_s   DECIMAL(8,3),
            computed_at     TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (session_id, car_id)
        )
    """)
    conn.commit()

    for car in analytics:
        # Car-session summary
        cur.execute("""
            INSERT INTO analytics_car_session
                (session_id, car_id, total_laps, green_flag_laps,
                 pit_stops, best_lap_s, avg_pace_s, consistency_s)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (session_id, car_id) DO UPDATE
                SET total_laps=EXCLUDED.total_laps,
                    green_flag_laps=EXCLUDED.green_flag_laps,
                    pit_stops=EXCLUDED.pit_stops,
                    best_lap_s=EXCLUDED.best_lap_s,
                    avg_pace_s=EXCLUDED.avg_pace_s,
                    consistency_s=EXCLUDED.consistency_s,
                    computed_at=NOW()
        """, (car.session_id, car.car_id, car.total_laps, car.green_flag_laps,
              car.pit_stops, car.best_lap_s, car.avg_pace_s, car.consistency_s))

        # Stints
        for s in car.stints:
            cur.execute("""
                INSERT INTO analytics_stints
                    (session_id, car_id, stint_number, start_lap, end_lap,
                     lap_count, tyre_age_laps, baseline_pace_s,
                     degradation_s_per_lap, consistency_s, is_final_stint)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (session_id, car_id, stint_number) DO UPDATE
                    SET baseline_pace_s=EXCLUDED.baseline_pace_s,
                        degradation_s_per_lap=EXCLUDED.degradation_s_per_lap,
                        consistency_s=EXCLUDED.consistency_s,
                        computed_at=NOW()
            """, (s.session_id, s.car_id, s.stint_number,
                  s.start_lap, s.end_lap, s.lap_count, s.tyre_age_laps,
                  s.baseline_pace_s, s.degradation_s_per_lap,
                  s.consistency_s, s.is_final_stint))

    conn.commit()


# ── CLI ───────────────────────────────────────────────────────

def run(session_id: int | None = None, series: str | None = None,
        session_type: str | None = None, save: bool = True, all_sessions: bool = False):

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Build list of sessions to process
    if session_id:
        session_ids = [session_id]
    else:
        query = """
            SELECT s.id FROM sessions s
            JOIN events e   ON e.id = s.event_id
            JOIN seasons se ON se.id = e.season_id
            JOIN series sr  ON sr.id = se.series_id
            WHERE 1=1
        """
        params = []
        if series:
            query += " AND sr.key::text = %s"
            params.append(series.upper())
        if session_type:
            query += " AND s.session_type::text = %s"
            params.append(session_type)
        if not all_sessions:
            query += " AND s.session_type = 'Race'"
        query += " ORDER BY s.id"

        cur.execute(query, params)
        session_ids = [r["id"] for r in cur.fetchall()]

    print(f"Processing {len(session_ids)} sessions...")

    for i, sid in enumerate(session_ids, 1):
        print(f"\n[{i}/{len(session_ids)}] Session {sid}")
        analytics = analyze_session(sid, cur)

        if not analytics:
            print("  No lap data found.")
            continue

        if len(session_ids) == 1:
            print_session_summary(sid, analytics)

        if save:
            save_to_db(analytics, cur, conn)
            total_stints = sum(len(c.stints) for c in analytics)
            print(f"  Saved: {len(analytics)} cars, {total_stints} stints")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenWEC Analytics Engine")
    parser.add_argument("--session",      type=int,  help="Single session ID")
    parser.add_argument("--series",       type=str,  help="WEC, ELMS, etc.")
    parser.add_argument("--session-type", type=str,  help="Race, Practice, etc.")
    parser.add_argument("--all",          action="store_true", help="All sessions")
    parser.add_argument("--no-save",      action="store_true", help="Print only, no DB write")
    args = parser.parse_args()

    run(
        session_id=   args.session,
        series=       args.series,
        session_type= args.session_type,
        save=         not args.no_save,
        all_sessions= args.all,
    )
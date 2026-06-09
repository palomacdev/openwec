# debug_race_session.py

from pathlib import Path
import sys
import psycopg2

from load_laps import read_csv, get_car_id

DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5433,
    "dbname":   "openwec",
    "user":     "openwec",
    "password": "openwec",
}

SESSION_ID = 2034

CSV_PATH = Path(
    r"C:\dev\openwec\raw\alms\01_2022\02_Yas Marina\Race 1\other\Hour 4\23_Analysis_Race 1.CSV"
)

def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    rows = read_csv(CSV_PATH)

    print("=" * 60)
    print("DEBUG RACE SESSION")
    print("=" * 60)
    print("session_id:", SESSION_ID)
    print("csv rows:", len(rows))
    print()

    total = 0
    resolved = 0
    failed = 0

    failed_cars = {}

    for row in rows:
        car_number = row.get("NUMBER", "").strip()

        if not car_number:
            continue

        total += 1

        car_id = get_car_id(
            cur,
            SESSION_ID,
            car_number,
        )

        if car_id:
            resolved += 1
        else:
            failed += 1
            failed_cars[car_number] = (
                failed_cars.get(car_number, 0) + 1
            )

    print(f"Rows with NUMBER: {total}")
    print(f"Resolved cars:   {resolved}")
    print(f"Failed cars:     {failed}")
    print()

    if failed_cars:
        print("=" * 60)
        print("FAILED CARS")
        print("=" * 60)

        for car, count in sorted(
            failed_cars.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            print(
                f"Car {car:<5} "
                f"missing {count} laps"
            )

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
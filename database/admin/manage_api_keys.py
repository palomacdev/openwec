"""
OpenWEC — API Key Admin
List, approve, or reject pending API key requests.

Usage:
    python database/admin/manage_api_keys.py --list-pending
    python database/admin/manage_api_keys.py --approve 5
    python database/admin/manage_api_keys.py --approve 5 --rate-limit 120
    python database/admin/manage_api_keys.py --reject 7
"""

import argparse
import psycopg2
import psycopg2.extras


from database.db import DB_CONFIG


def list_pending(cur):
    cur.execute("""
        SELECT id, name, email, intended_use, api_key, created_at
        FROM api_key_requests
        WHERE status = 'pending'
        ORDER BY created_at
    """)
    rows = cur.fetchall()
    if not rows:
        print("No pending requests.")
        return
    for r in rows:
        print(f"[{r['id']:4d}] {r['name']} <{r['email']}>")
        print(f"        use:       {r['intended_use'] or '—'}")
        print(f"        key:       {r['api_key']}")
        print(f"        requested: {r['created_at']}")
        print()


def approve(cur, conn, request_id: int, rpm: int):
    cur.execute("""
        UPDATE api_key_requests
        SET status = 'approved', approved_at = NOW(), requests_per_minute = %s
        WHERE id = %s AND status = 'pending'
        RETURNING api_key, name, email
    """, (rpm, request_id))
    row = cur.fetchone()
    if not row:
        print(f"No pending request with id {request_id}.")
        return
    conn.commit()
    print(f"Approved [{request_id}] {row['name']} <{row['email']}>")
    print(f"Rate limit: {rpm} req/min")
    print(f"Key now active: {row['api_key']}")


def reject(cur, conn, request_id: int):
    cur.execute("""
        UPDATE api_key_requests SET status = 'rejected'
        WHERE id = %s AND status = 'pending'
        RETURNING name, email
    """, (request_id,))
    row = cur.fetchone()
    if not row:
        print(f"No pending request with id {request_id}.")
        return
    conn.commit()
    print(f"Rejected [{request_id}] {row['name']} <{row['email']}>")


def list_all(cur):
    cur.execute("""
        SELECT id, name, email, status, requests_per_minute, created_at, approved_at
        FROM api_key_requests
        ORDER BY created_at DESC
        LIMIT 50
    """)
    rows = cur.fetchall()
    for r in rows:
        print(f"[{r['id']:4d}] {r['status']:9s} {r['name']:20s} <{r['email']}>  "
              f"({r['requests_per_minute']} req/min)")


def run():
    parser = argparse.ArgumentParser(description="OpenWEC API Key Admin")
    parser.add_argument("--list-pending", action="store_true")
    parser.add_argument("--list-all",     action="store_true")
    parser.add_argument("--approve",      type=int, metavar="ID")
    parser.add_argument("--reject",       type=int, metavar="ID")
    parser.add_argument("--rate-limit",   type=int, default=60,
                         help="Requests per minute for approval (default 60)")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if args.list_pending:
        list_pending(cur)
    elif args.list_all:
        list_all(cur)
    elif args.approve:
        approve(cur, conn, args.approve, args.rate_limit)
    elif args.reject:
        reject(cur, conn, args.reject)
    else:
        parser.print_help()

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
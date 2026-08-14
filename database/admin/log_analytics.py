#!/usr/bin/env python3
"""
OpenWEC — Log Analytics
Parses structured JSON logs from the API container and generates a usage report.

Usage (on VM):
    python3 database/admin/log_analytics.py
    python3 database/admin/log_analytics.py --hours 48
    python3 database/admin/log_analytics.py --container openwec-api
    python3 database/admin/log_analytics.py --json > report.json

Output:
    - Total requests, unique keys, error rate
    - Top endpoints by request count
    - Top API keys by usage (hashed)
    - Status code distribution
    - Average response time by endpoint
    - Rate limit hits (429s)
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone


def fetch_logs(container: str, hours: int) -> list[dict]:
    """Fetch and parse JSON log lines from docker logs."""
    result = subprocess.run(
        ["docker", "logs", container, f"--since={hours}h"],
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines() + result.stderr.splitlines()
    records = []
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
            if data.get("message") == "request":
                records.append(data)
        except json.JSONDecodeError:
            continue
    return records


def analyze(records: list[dict]) -> dict:
    if not records:
        return {}

    total       = len(records)
    errors      = sum(1 for r in records if r.get("status_code", 0) >= 500)
    rate_limits = sum(1 for r in records if r.get("status_code") == 429)
    not_found   = sum(1 for r in records if r.get("status_code") == 404)

    # Status codes
    status_counts = defaultdict(int)
    for r in records:
        status_counts[r.get("status_code", "?")] += 1

    # Top endpoints
    endpoint_counts = defaultdict(int)
    endpoint_times  = defaultdict(list)
    for r in records:
        path = r.get("path", "?")
        endpoint_counts[path] += 1
        if r.get("duration_ms") is not None:
            endpoint_times[path].append(r["duration_ms"])

    top_endpoints = sorted(endpoint_counts.items(), key=lambda x: x[1], reverse=True)[:15]

    avg_times = {}
    for path, times in endpoint_times.items():
        avg_times[path] = round(sum(times) / len(times), 1)

    # Top API keys (by hash)
    key_counts = defaultdict(int)
    for r in records:
        key_hash = r.get("api_key_hash") or "anonymous"
        key_counts[key_hash] += 1

    top_keys = sorted(key_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Overall avg response time
    all_times = [r["duration_ms"] for r in records if r.get("duration_ms") is not None]
    avg_response = round(sum(all_times) / len(all_times), 1) if all_times else None

    return {
        "summary": {
            "total_requests":   total,
            "error_count":      errors,
            "error_rate_pct":   round(errors / total * 100, 1),
            "rate_limit_hits":  rate_limits,
            "not_found_count":  not_found,
            "avg_response_ms":  avg_response,
            "unique_keys":      len(key_counts),
        },
        "status_codes":   dict(sorted(status_counts.items())),
        "top_endpoints":  [{"path": p, "count": c, "avg_ms": avg_times.get(p)} for p, c in top_endpoints],
        "top_keys":       [{"key_hash": k, "requests": c} for k, c in top_keys],
    }


def print_report(data: dict, hours: int, container: str):
    s = data.get("summary", {})
    print(f"\n{'='*60}")
    print(f"OpenWEC API — Usage Report")
    print(f"Container: {container}  |  Last {hours}h")
    print(f"{'='*60}\n")

    print(f"SUMMARY")
    print(f"  Total requests:   {s.get('total_requests', 0):,}")
    print(f"  Unique keys:      {s.get('unique_keys', 0)}")
    print(f"  Avg response:     {s.get('avg_response_ms', '—')} ms")
    print(f"  Errors (5xx):     {s.get('error_count', 0)} ({s.get('error_rate_pct', 0)}%)")
    print(f"  Rate limit (429): {s.get('rate_limit_hits', 0)}")
    print(f"  Not found (404):  {s.get('not_found_count', 0)}")

    print(f"\nSTATUS CODES")
    for code, count in data.get("status_codes", {}).items():
        bar = "█" * min(count // 5, 40)
        print(f"  {code}  {count:5d}  {bar}")

    print(f"\nTOP ENDPOINTS")
    for ep in data.get("top_endpoints", [])[:10]:
        avg = f"{ep['avg_ms']}ms" if ep.get("avg_ms") else "—"
        print(f"  {ep['count']:5d}  {avg:8s}  {ep['path']}")

    print(f"\nTOP API KEYS (by hash)")
    for k in data.get("top_keys", []):
        label = "anonymous" if k["key_hash"] == "anonymous" else k["key_hash"]
        print(f"  {k['requests']:5d}  {label}")

    print()


def run():
    parser = argparse.ArgumentParser(description="OpenWEC Log Analytics")
    parser.add_argument("--hours",     type=int, default=24,           help="Hours of logs to analyze (default: 24)")
    parser.add_argument("--container", default="openwec-api",          help="Docker container name")
    parser.add_argument("--json",      action="store_true",            help="Output raw JSON instead of formatted report")
    args = parser.parse_args()

    records = fetch_logs(args.container, args.hours)

    if not records:
        print(f"No structured log records found in the last {args.hours}h.", file=sys.stderr)
        sys.exit(0)

    data = analyze(records)

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print_report(data, args.hours, args.container)


if __name__ == "__main__":
    run()
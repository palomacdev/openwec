"""
OpenWEC — IMSA Collector
Collects from both Al Kamel IMSA portals and merges the catalog.

Domains:
  - http://imsa.alkamelsystems.com/        (historical, ~2016+)
  - https://imsa.results.alkamelcloud.com/ (current,    ~2024+)

Both use identical architecture (same selects, same file structure).
The script collects from both and deduplicates by URL.

URL pattern (note: year-prefixed season ID, not sequential):
  Results/{yy}_{yyyy}/{event}/{championship}/{session}/{file}.CSV
  Example: Results/24_2024/06_Long Beach.../01_IMSA WeatherTech.../

Usage:
    python collectors/imsa/imsa_collect.py --action catalog
    python collectors/imsa/imsa_collect.py --action download --workers 4
    python collectors/imsa/imsa_collect.py --action catalog download
"""

import asyncio
import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin

import aiohttp
import aiofiles
from bs4 import BeautifulSoup


# ── Config ───────────────────────────────────────────────────
DOMAINS = [
    "http://imsa.alkamelsystems.com/",
    "https://imsa.results.alkamelcloud.com/",
]

SERIES_KEY  = "imsa"
CATALOG_DIR = Path(r"C:\dev\openwec\catalog/imsa")
RAW_DIR     = Path(r"C:\dev\openwec\raw/imsa")
CATALOG_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# IMSA has multiple championships per event — we want all of them
# Unlike WEC where we filtered only "FIA WEC", here we keep everything
IMSA_CHAMPIONSHIP_KEYWORDS = [
    "imsa",           # WeatherTech, Pilot Challenge, VP Racing, etc.
    "weathertech",
    "michelin",
    "endurance",
    "daytona",
    "prototype",
]

FILE_FILTERS = {
    "classification": ["03_classification", "03_results"],
    "analysis":       ["23_analysis", "23_analysisenduran"],
    "weather":        ["26_weather"],
}


# ── Parsers ──────────────────────────────────────────────────

def parse_results_href(href: str, base_url: str) -> dict | None:
    clean = unquote(href)
    m = re.match(
        r"Results?/([^/]+)/([^/]+)/([^/]+)/([^/]+)/(.+\.(CSV|PDF|csv|pdf))$",
        clean, re.IGNORECASE
    )
    if not m:
        return None
    return {
        "season_raw":       m.group(1),
        "event_raw":        m.group(2),
        "championship_raw": m.group(3),
        "session_raw":      m.group(4),
        "file_path":        m.group(5),
        "file_ext":         m.group(6).upper(),
        "full_url":         urljoin(base_url, href),
        "source_domain":    base_url,
    }


def parse_season_year(raw: str) -> str:
    """'24_2024' → '2024', '16_2016' → '2016'"""
    parts = raw.split("_", 1)
    return parts[1] if len(parts) == 2 else raw


def clean_id_name(raw: str) -> tuple[str, str]:
    parts = raw.split("_", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("0", raw)


def parse_session_datetime(raw: str) -> tuple[str, str]:
    m = re.match(r"(\d{12})_(.*)", raw)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y%m%d%H%M")
            return dt.isoformat(sep=" ", timespec="minutes"), m.group(2)
        except Exception:
            pass
    return "", raw


def infer_session_type(name: str) -> str:
    n = name.lower()
    if "race" in n:                         return "Race"
    if "qualifying" in n or "quali" in n:   return "Qualifying"
    if "practice" in n or "fp" in n:        return "Practice"
    if "test" in n or "roar" in n:          return "Test"
    if "warm" in n:                         return "WarmUp"
    return "Other"


def infer_imsa_series(championship_name: str) -> str:
    """Maps championship name to IMSA sub-series."""
    n = championship_name.lower()
    if "weathertech" in n or "weather tech" in n:  return "WeatherTech"
    if "pilot challenge" in n:                      return "PilotChallenge"
    if "vp racing" in n or "vprc" in n:             return "VPRacing"
    if "endurance" in n or "airbnb" in n:           return "Endurance"
    if "prototype challenge" in n:                  return "PrototypeChallenge"
    if "imsa" in n:                                 return "IMSA"
    return "Other"


def file_category(filename: str) -> str | None:
    name = filename.lower()
    for cat, prefixes in FILE_FILTERS.items():
        if any(name.startswith(p) for p in prefixes):
            return cat
    return None


def local_path(url: str) -> Path:
    m = re.search(r"Results?/(.+)", unquote(url), re.IGNORECASE)
    if not m:
        return RAW_DIR / "misc" / url.split("/")[-1]
    parts = m.group(1).split("/")
    if len(parts) < 5:
        return RAW_DIR / "/".join(parts)
    season      = parts[0]
    event       = parts[1]
    champ       = parts[2]
    session_raw = parts[3]
    filename    = "/".join(parts[4:])
    m2 = re.match(r"\d{12}_(.*)", session_raw)
    session_name = m2.group(1) if m2 else session_raw
    # Include championship in path (IMSA has multiple per event)
    champ_clean = re.sub(r"[^\w\s-]", "", champ).strip()[:40]
    cat = file_category(filename) or "other"
    return RAW_DIR / season / event / champ_clean / session_name / cat / filename


# ── HTTP ─────────────────────────────────────────────────────

async def fetch_page(
    session: aiohttp.ClientSession,
    base_url: str,
    season: str,
    evvent: str | None = None,
) -> str | None:
    params = {"season": season}
    if evvent:
        params["evvent"] = evvent
    for attempt in range(3):
        try:
            async with session.get(
                base_url, params=params,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200:
                    return await resp.text(encoding=None, errors="replace")
        except Exception as e:
            if attempt == 2:
                print(f"  [ERROR] {base_url} season={season}: {e}")
            await asyncio.sleep(1.5 * (attempt + 1))
    return None


# ── Stage 1: Catalog ─────────────────────────────────────────

async def discover_from_domain(
    http: aiohttp.ClientSession,
    base_url: str,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Returns (all_seasons, seasons_events) for one domain."""
    label = "OLD" if "alkamelsystems" in base_url else "NEW"
    print(f"\n  [{label}] {base_url}")

    # Seed page to get season list
    html = await fetch_page(http, base_url, "16_2016")
    if not html:
        return [], {}

    soup = BeautifulSoup(html, "html.parser")
    season_sel = soup.find("select", {"name": "season"})
    if not season_sel:
        print(f"  [{label}] <select name='season'> not found")
        return [], {}

    all_seasons = [
        {"value": opt["value"], "label": opt.get_text(strip=True)}
        for opt in season_sel.find_all("option")
        if opt.get("value")
    ]
    print(f"  [{label}] {len(all_seasons)} seasons: {[s['value'] for s in all_seasons]}")

    async def get_events(s: dict) -> tuple[str, list[dict]]:
        html = await fetch_page(http, base_url, s["value"])
        if not html:
            return s["value"], []
        soup = BeautifulSoup(html, "html.parser")
        ev_sel = soup.find("select", {"name": "evvent"})
        if not ev_sel:
            return s["value"], []
        return s["value"], [
            {"value": opt["value"], "label": opt.get_text(strip=True)}
            for opt in ev_sel.find_all("option")
            if opt.get("value")
        ]

    seasons_events: dict[str, list[dict]] = {}
    for i in range(0, len(all_seasons), 4):
        chunk = all_seasons[i:i+4]
        results = await asyncio.gather(*[get_events(s) for s in chunk])
        for sk, evs in results:
            seasons_events[sk] = evs
            print(f"    {sk:15s} → {len(evs)} events")
        await asyncio.sleep(0.5)

    return all_seasons, seasons_events


async def scrape_event(
    http: aiohttp.ClientSession,
    base_url: str,
    season_key: str,
    event: dict,
) -> list[dict]:
    html = await fetch_page(http, base_url, season_key, event["value"])
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().startswith("results"):
            continue
        parsed = parse_results_href(href, base_url)
        if parsed:
            links.append({**parsed, "link_text": a.get_text(strip=True)})
    return links


def merge_seasons_events(
    domain_results: list[tuple[list[dict], dict[str, list[dict]]]]
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Merges seasons and events from multiple domains, deduplicating."""
    merged_seasons: dict[str, dict] = {}
    merged_events:  dict[str, list[dict]] = {}

    for seasons, events in domain_results:
        for s in seasons:
            merged_seasons[s["value"]] = s
        for sk, evs in events.items():
            if sk not in merged_events:
                merged_events[sk] = evs
            else:
                # Merge events, dedup by value
                existing = {e["value"] for e in merged_events[sk]}
                for ev in evs:
                    if ev["value"] not in existing:
                        merged_events[sk].append(ev)
                        existing.add(ev["value"])

    all_seasons = sorted(merged_seasons.values(), key=lambda s: s["value"])
    return all_seasons, merged_events


def build_catalog(all_links: list[dict]) -> dict:
    catalog: dict = {}
    for f in all_links:
        s_id, s_name = clean_id_name(f["season_raw"])
        year = parse_season_year(f["season_raw"])
        e_id, e_name = clean_id_name(f["event_raw"])
        c_id, c_name = clean_id_name(f["championship_raw"])
        sess_dt, sess_name = parse_session_datetime(f["session_raw"])

        sk, ek, ck, ssk = f["season_raw"], f["event_raw"], f["championship_raw"], f["session_raw"]

        catalog.setdefault(sk, {
            "id": s_id, "name": year,
            "events": {}
        })
        catalog[sk]["events"].setdefault(ek, {
            "id": e_id, "name": e_name, "championships": {}
        })
        catalog[sk]["events"][ek]["championships"].setdefault(ck, {
            "id": c_id, "name": c_name,
            "imsa_series": infer_imsa_series(c_name),
            "sessions": {}
        })
        sess_store = catalog[sk]["events"][ek]["championships"][ck]["sessions"]
        sess_store.setdefault(ssk, {"datetime": sess_dt, "name": sess_name, "files": []})
        sess_store[ssk]["files"].append({
            "filename": f["file_path"], "ext": f["file_ext"],
            "url": f["full_url"], "label": f.get("link_text", ""),
            "source_domain": f.get("source_domain", ""),
        })
    return catalog


def flatten_sessions(catalog: dict) -> list[dict]:
    flat = []
    for sk, season in catalog.items():
        for ek, event in season["events"].items():
            for ck, champ in event["championships"].items():
                for ssk, session in champ["sessions"].items():
                    csvs = [f for f in session["files"] if f["ext"] == "CSV"]
                    pdfs = [f for f in session["files"] if f["ext"] == "PDF"]
                    flat.append({
                        "series":            SERIES_KEY,
                        "imsa_series":       champ.get("imsa_series", ""),
                        "season_raw":        sk,
                        "season_name":       season["name"],
                        "event_raw":         ek,
                        "event_name":        event["name"],
                        "championship_raw":  ck,
                        "championship_name": champ["name"],
                        "session_raw":       ssk,
                        "session_name":      session["name"],
                        "session_type":      infer_session_type(session["name"]),
                        "session_datetime":  session["datetime"],
                        "csv_count":         len(csvs),
                        "pdf_count":         len(pdfs),
                        "csv_files":         [f["url"] for f in csvs],
                        "pdf_files":         [f["url"] for f in pdfs],
                    })
    flat.sort(key=lambda x: (x["season_raw"], x["event_raw"], x["session_raw"]))
    return flat


async def run_catalog(http: aiohttp.ClientSession):
    print("[CATALOG] Discovering IMSA seasons and events...")

    # Collect from both domains
    domain_results = []
    for base_url in DOMAINS:
        result = await discover_from_domain(http, base_url)
        domain_results.append(result)

    all_seasons, seasons_events = merge_seasons_events(domain_results)
    print(f"\n  Merged: {len(all_seasons)} seasons, "
          f"{sum(len(v) for v in seasons_events.values())} total events")

    # Save seasons index
    with open(CATALOG_DIR / "seasons_events.json", "w") as f:
        json.dump([
            {"season_value": s["value"], "season_label": s["label"],
             "events": seasons_events.get(s["value"], [])}
            for s in all_seasons
        ], f, indent=2, ensure_ascii=False)

    # Scrape files per event per domain
    print(f"\n[FILES] Scraping file links...")
    all_links: list[dict] = []
    seen_urls: set[str] = set()

    for base_url in DOMAINS:
        label = "OLD" if "alkamelsystems" in base_url else "NEW"
        print(f"\n  [{label}]")
        for season_key, events in sorted(seasons_events.items()):
            if not events:
                continue
            for i in range(0, len(events), 4):
                chunk = events[i:i+4]
                results = await asyncio.gather(*[
                    scrape_event(http, base_url, season_key, ev) for ev in chunk
                ])
                for ev, links in zip(chunk, results):
                    new_links = [l for l in links if l["full_url"] not in seen_urls]
                    seen_urls.update(l["full_url"] for l in new_links)
                    all_links.extend(new_links)
                    if new_links:
                        print(f"    {season_key} / {ev['value'][:35]:35s} → {len(new_links):3d} files")
                await asyncio.sleep(0.4)

    print(f"\n[BUILD] {len(all_links)} unique links")
    catalog  = build_catalog(all_links)
    sessions = flatten_sessions(catalog)

    with open(CATALOG_DIR / "catalog.json", "w") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
    with open(CATALOG_DIR / "sessions.json", "w") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)

    # Summary
    total_ev   = sum(len(s["events"]) for s in catalog.values())
    races      = [s for s in sessions if s["session_type"] == "Race"]
    series_cnt = Counter(s["imsa_series"] for s in sessions)

    print(f"\n{'='*50}")
    print(f"IMSA CATALOG")
    print(f"  Seasons:  {len(catalog)}")
    print(f"  Events:   {total_ev}")
    print(f"  Sessions: {len(sessions)}")
    print(f"  Races:    {len(races)}")
    print(f"\n  By IMSA sub-series:")
    for series, n in sorted(series_cnt.items(), key=lambda x: -x[1]):
        print(f"    {series:20s} {n:4d} sessions")
    print(f"\n  [SAVED] catalog/imsa/")


# ── Stage 2: Download ─────────────────────────────────────────

async def download_file(
    http: aiohttp.ClientSession,
    url: str,
    dest: Path,
    sem: asyncio.Semaphore,
) -> dict:
    async with sem:
        try:
            async with http.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    return {"url": url, "status": "http_error", "code": resp.status}
                raw = await resp.read()
                content = None
                for enc in ["utf-8", "latin-1", "cp1252"]:
                    try:
                        content = raw.decode(enc); break
                    except UnicodeDecodeError:
                        pass
                if content is None:
                    return {"url": url, "status": "decode_error"}
                dest.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(dest, "w", encoding="utf-8") as f:
                    await f.write(content)
                return {
                    "url": url, "status": "ok", "dest": str(dest),
                    "size": len(raw), "lines": content.count("\n"),
                    "at": datetime.now().isoformat(),
                }
        except asyncio.TimeoutError:
            return {"url": url, "status": "timeout"}
        except Exception as e:
            return {"url": url, "status": "error", "msg": str(e)}


async def run_download(http: aiohttp.ClientSession, workers: int = 4):
    sessions_path = CATALOG_DIR / "sessions.json"
    if not sessions_path.exists():
        print("[ERROR] Run --action catalog first.")
        return

    with open(sessions_path) as f:
        sessions: list[dict] = json.load(f)

    log_path = CATALOG_DIR / "ingest_log.json"
    done_urls: set[str] = set()
    if log_path.exists():
        with open(log_path) as f:
            log_data = json.load(f)
        done_urls = {e["url"] for e in log_data if e.get("status") == "ok"}
        print(f"  [RESUME] {len(done_urls)} already downloaded")

    queue = []
    for sess in sessions:
        if sess.get("imsa_series") not in ("WeatherTech", "Endurance"):
            continue
        for url in sess.get("csv_files", []):
            if url in done_urls:
                continue
            cat = file_category(url.split("/")[-1].lower())
            if cat is None:
                continue
            queue.append((url, local_path(url)))

    cats = Counter(file_category(url.split("/")[-1].lower()) for url, _ in queue)
    print(f"\n[DOWNLOAD] {len(queue)} files")
    for cat, n in sorted(cats.items()):
        print(f"  {cat:20s} {n:4d}")

    if not queue:
        print("[INFO] Nothing to download.")
        return

    sem = asyncio.Semaphore(workers)
    log: list[dict] = []
    ok = errors = 0

    print(f"\n[START] {workers} workers...\n")
    tasks = [download_file(http, url, dest, sem) for url, dest in queue]
    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        result = await coro
        log.append(result)
        if result["status"] == "ok":
            ok += 1
            if i % 50 == 0 or i <= 3:
                print(f"  [{i:4d}/{len(queue)}] ✓ {result['url'].split('/')[-1][:55]}")
        else:
            errors += 1
            print(f"  [{i:4d}/{len(queue)}] ✗ {result['url'].split('/')[-1][:55]} → {result['status']}")
        if i % 100 == 0:
            with open(log_path, "w") as f:
                json.dump(log, f)

    with open(log_path, "w") as f:
        json.dump(log, f)

    print(f"\n{'='*50}")
    print(f"DOWNLOAD IMSA")
    print(f"  ✓ {ok}  ✗ {errors}")
    print(f"  Output: {RAW_DIR.absolute()}")


# ── Main ─────────────────────────────────────────────────────

async def main(actions: list[str], workers: int):
    connector = aiohttp.TCPConnector(limit=8)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as http:
        if "catalog" in actions:
            await run_catalog(http)
        if "download" in actions:
            await run_download(http, workers)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IMSA Collector")
    parser.add_argument("--action", nargs="+",
                        choices=["catalog", "download"],
                        default=["catalog", "download"])
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(main(args.action, args.workers))
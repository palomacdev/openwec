"""
OpenWEC — ELMS Catalog Spider + Downloader
European Le Mans Series — elms.alkamelsystems.com

Same architecture as WEC. Only BASE_URL and RAW_DIR are different.

Usage:
    python elms_collect.py --action catalog
    python elms_collect.py --action download --workers 4
    python elms_collect.py --action catalog download --workers 4
"""

import asyncio
import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin

import aiohttp
import aiofiles
from bs4 import BeautifulSoup


# ── Config ───────────────────────────────────────────────────
BASE_URL    = "https://elms.alkamelsystems.com/"
SERIES_KEY  = "elms"
CATALOG_DIR = Path("catalog/elms")
RAW_DIR     = Path("raw/elms")
CATALOG_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

FILE_FILTERS = {
    "classification": ["03_classification", "03_results"],
    "analysis":       ["23_analysis", "23_analysisenduran"],
    "weather":        ["26_weather"],
}


# ── Parsers ──────────────────────────────────────────────────

def parse_results_href(href: str) -> dict | None:
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
        "full_url":         urljoin(BASE_URL, href),
    }


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
    if "race" in n:                        return "Race"
    if "hyperpole" in n:                   return "Hyperpole"
    if "qualifying" in n or "quali" in n:  return "Qualifying"
    if "practice" in n or "fp" in n:       return "Practice"
    if "test" in n:                        return "Test"
    if "prologue" in n:                    return "Prologue"
    return "Other"


def file_category(filename: str) -> str | None:
    name = filename.lower()
    for category, prefixes in FILE_FILTERS.items():
        if any(name.startswith(p) for p in prefixes):
            return category
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
    session_raw = parts[3]
    filename    = "/".join(parts[4:])
    m2 = re.match(r"\d{12}_(.*)", session_raw)
    session_name = m2.group(1) if m2 else session_raw
    cat = file_category(filename) or "other"
    return RAW_DIR / season / event / session_name / cat / filename


# ── HTTP ─────────────────────────────────────────────────────

async def fetch_page(
    session: aiohttp.ClientSession,
    season: str,
    evvent: str | None = None,
) -> str | None:
    params = {"season": season}
    if evvent:
        params["evvent"] = evvent
    for attempt in range(3):
        try:
            async with session.get(
                BASE_URL, params=params,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200:
                    return await resp.text(encoding=None, errors="replace")
        except Exception as e:
            if attempt == 2:
                print(f"  [ERRO] season={season} evvent={evvent}: {e}")
            await asyncio.sleep(1.5 * (attempt + 1))
    return None


# ── Stage 1: Catalog ─────────────────────────────────────────

async def discover_seasons_and_events(
    session: aiohttp.ClientSession,
) -> dict[str, list[dict]]:
    print(f"[CATALOG] Descobrindo seasons — {BASE_URL}")

    html = await fetch_page(session, "01_2004")  # seed qualquer
    if not html:
        # tenta sem parâmetro
        async with session.get(BASE_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            html = await resp.text(encoding=None, errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    season_select = soup.find("select", {"name": "season"})
    if not season_select:
        raise RuntimeError("<select name='season'> não encontrado.")

    all_seasons = [
        {"value": opt["value"], "label": opt.get_text(strip=True)}
        for opt in season_select.find_all("option")
        if opt.get("value")
    ]
    print(f"  → {len(all_seasons)} seasons: {[s['value'] for s in all_seasons]}")

    async def get_events(s: dict) -> tuple[str, list[dict]]:
        html = await fetch_page(session, s["value"])
        if not html:
            return s["value"], []
        soup = BeautifulSoup(html, "html.parser")
        ev_sel = soup.find("select", {"name": "evvent"})
        if not ev_sel:
            return s["value"], []
        events = [
            {"value": opt["value"], "label": opt.get_text(strip=True)}
            for opt in ev_sel.find_all("option")
            if opt.get("value")
        ]
        return s["value"], events

    seasons_events: dict[str, list[dict]] = {}
    for i in range(0, len(all_seasons), 4):
        chunk = all_seasons[i:i+4]
        results = await asyncio.gather(*[get_events(s) for s in chunk])
        for sk, evs in results:
            seasons_events[sk] = evs
            label = next((s["label"] for s in all_seasons if s["value"] == sk), "?")
            print(f"  {sk:20s} ({label:12s}) → {len(evs)} eventos")
        await asyncio.sleep(0.5)

    with open(CATALOG_DIR / "seasons_events.json", "w") as f:
        json.dump([
            {"season_value": s["value"], "season_label": s["label"],
             "events": seasons_events.get(s["value"], [])}
            for s in all_seasons
        ], f, indent=2, ensure_ascii=False)

    return seasons_events


async def scrape_event(
    session: aiohttp.ClientSession,
    season_key: str,
    event: dict,
) -> list[dict]:
    html = await fetch_page(session, season_key, event["value"])
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    file_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().startswith("results"):
            continue
        parsed = parse_results_href(href)
        if parsed:
            file_links.append({**parsed, "link_text": a.get_text(strip=True)})
    return file_links


def build_catalog(all_links: list[dict]) -> dict:
    catalog: dict = {}
    for f in all_links:
        s_id, s_name = clean_id_name(f["season_raw"])
        e_id, e_name = clean_id_name(f["event_raw"])
        c_id, c_name = clean_id_name(f["championship_raw"])
        sess_dt, sess_name = parse_session_datetime(f["session_raw"])
        sk, ek, ck, ssk = f["season_raw"], f["event_raw"], f["championship_raw"], f["session_raw"]

        catalog.setdefault(sk, {"id": s_id, "name": s_name, "events": {}})
        catalog[sk]["events"].setdefault(ek, {"id": e_id, "name": e_name, "championships": {}})
        catalog[sk]["events"][ek]["championships"].setdefault(ck, {"id": c_id, "name": c_name, "sessions": {}})
        sess_store = catalog[sk]["events"][ek]["championships"][ck]["sessions"]
        sess_store.setdefault(ssk, {"datetime": sess_dt, "name": sess_name, "files": []})
        sess_store[ssk]["files"].append({
            "filename": f["file_path"], "ext": f["file_ext"],
            "url": f["full_url"], "label": f.get("link_text", ""),
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
    seasons_events = await discover_seasons_and_events(http)

    print(f"\n[FILES] Baixando índice de arquivos...")
    all_links: list[dict] = []
    for season_key, events in sorted(seasons_events.items()):
        print(f"\n  [{season_key}]")
        for i in range(0, len(events), 4):
            chunk = events[i:i+4]
            results = await asyncio.gather(*[scrape_event(http, season_key, ev) for ev in chunk])
            for ev, links in zip(chunk, results):
                all_links.extend(links)
                print(f"    {ev['value']:40s} → {len(links):3d} files")
            await asyncio.sleep(0.4)

    print(f"\n[BUILD] {len(all_links)} links totais")
    catalog  = build_catalog(all_links)
    sessions = flatten_sessions(catalog)

    with open(CATALOG_DIR / "catalog.json", "w") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
    with open(CATALOG_DIR / "sessions.json", "w") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)

    # Resumo
    total_ev   = sum(len(s["events"]) for s in catalog.values())
    total_sess = len(sessions)
    races      = [s for s in sessions if s["session_type"] == "Race"]
    print(f"\n{'='*50}")
    print(f"ELMS CATALOG")
    print(f"  Seasons:  {len(catalog)}")
    print(f"  Eventos:  {total_ev}")
    print(f"  Sessões:  {total_sess}")
    print(f"  Corridas: {len(races)}")
    print(f"  [SALVO] catalog/elms/")


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
                return {"url": url, "status": "ok", "dest": str(dest),
                        "size": len(raw), "lines": content.count("\n"),
                        "at": datetime.now().isoformat()}
        except asyncio.TimeoutError:
            return {"url": url, "status": "timeout"}
        except Exception as e:
            return {"url": url, "status": "error", "msg": str(e)}


async def run_download(http: aiohttp.ClientSession, workers: int = 4):
    sessions_path = CATALOG_DIR / "sessions.json"
    if not sessions_path.exists():
        print("[ERRO] Rode --action catalog primeiro.")
        return

    with open(sessions_path) as f:
        sessions: list[dict] = json.load(f)

    log_path = CATALOG_DIR / "ingest_log.json"
    done_urls: set[str] = set()
    if log_path.exists():
        with open(log_path) as f:
            log_data = json.load(f)
        done_urls = {e["url"] for e in log_data if e.get("status") == "ok"}
        print(f"  [RESUME] {len(done_urls)} já baixados")

    queue = []
    for sess in sessions:
        for url in sess.get("csv_files", []):
            if url in done_urls:
                continue
            cat = file_category(url.split("/")[-1].lower())
            if cat is None:
                continue
            queue.append((url, local_path(url)))

    from collections import Counter
    cats = Counter(file_category(url.split("/")[-1].lower()) for url, _ in queue)
    print(f"\n[DOWNLOAD] {len(queue)} arquivos")
    for cat, n in sorted(cats.items()):
        print(f"  {cat:20s} {n:4d}")

    if not queue:
        print("[INFO] Nada pra baixar.")
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
                print(f"  [{i:4d}/{len(queue)}] ✓ {result['url'].split('/')[-1][:50]}")
        else:
            errors += 1
            print(f"  [{i:4d}/{len(queue)}] ✗ {result['url'].split('/')[-1][:50]} → {result['status']}")
        if i % 100 == 0:
            with open(log_path, "w") as f:
                json.dump(log, f)

    with open(log_path, "w") as f:
        json.dump(log, f)

    print(f"\n{'='*50}")
    print(f"DOWNLOAD ELMS")
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
    parser = argparse.ArgumentParser(description="ELMS Collector")
    parser.add_argument("--action", nargs="+", choices=["catalog", "download"],
                        default=["catalog", "download"],
                        help="catalog, download, ou os dois")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(main(args.action, args.workers))
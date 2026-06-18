"""
OpenWEC — Catalog Spider v3 

DOM diagnostics revealed:
- Navigation done by `<select name="season">` and `<select name="evvent">`
- Files are in `<a href="Results/...">` as relative paths
- The page is rendered on the server for the season + event combination of the query string
- Therefore: just do a GET on `?season=X&evvent=Y` for each combination

Advantages compared to Playwright:
- 10 times faster (without headless browser)
- No dependency on JS rendering time
- Works with pure aiohttp + BeautifulSoup

Bonus discovered: seasons range from 2011 to 2026, not just 2018 onwards.

Usage:
    pip install aiohttp beautifulsoup4 aiofiles
    python catalog_spider_v3.py [--season 13_2024] [--from-season 12_2023]

Output:
    catalog/wec_catalog.json
    catalog/sessions.json
    catalog/seasons_events.json   ← Full index of seasons × events
"""

import asyncio
import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, quote, unquote

import aiohttp
from bs4 import BeautifulSoup


CATALOG_DIR = Path("catalog/wec")
CATALOG_DIR.mkdir(exist_ok=True)

BASE_URL  = "https://fiawec.alkamelsystems.com/"
HEADERS   = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# ──────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────

def parse_results_href(href: str) -> dict | None:
    """
    Parseia hrefs do tipo:
      Results/13_2024/08_BAHRAIN%20INTERNATIONAL%20CIRCUIT/575_FIA%20WEC/202411021200_Race/03_Classification_Race.CSV
    """
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
    if "race" in n:                    return "Race"
    if "hyperpole" in n:               return "Hyperpole"
    if "qualifying" in n or "quali" in n: return "Qualifying"
    if "practice" in n or "fp" in n:   return "Practice"
    if "test" in n:                    return "Test"
    if "prologue" in n:                return "Prologue"
    return "Other"


# ──────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────

async def fetch_page(
    session: aiohttp.ClientSession,
    season: str,
    evvent: str | None = None,
    retries: int = 3,
) -> str | None:
    """Faz GET em ?season=X[&evvent=Y] e retorna o HTML."""
    params = {"season": season}
    if evvent:
        params["evvent"] = evvent

    for attempt in range(retries):
        try:
            async with session.get(
                BASE_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    return await resp.text(encoding=None, errors="replace")
                print(f"  [HTTP {resp.status}] season={season} evvent={evvent}")
        except Exception as e:
            if attempt < retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
            else:
                print(f"  [ERRO] season={season} evvent={evvent}: {e}")
    return None


# ──────────────────────────────────────────────
# Stage 1: Discover all seasons and their events
# ──────────────────────────────────────────────

async def discover_seasons_and_events(
    session: aiohttp.ClientSession,
) -> dict[str, list[dict]]:
    """
    Carrega a página inicial e lê os dois <select>:
      - name="season"  → todas as temporadas disponíveis
      - name="evvent"  → eventos da temporada atualmente selecionada
    
    Para obter os eventos de CADA temporada, precisamos carregar
    uma página por temporada. Mas podemos fazer tudo em paralelo.
    """
    print("[1/3] Descobrindo seasons e events...")

    # Carrega a página default pra pegar a lista de seasons
    html = await fetch_page(session, "13_2024")
    if not html:
        raise RuntimeError("Não conseguiu carregar a página inicial.")

    soup = BeautifulSoup(html, "html.parser")

    # Extrai todas as seasons do <select name="season">
    season_select = soup.find("select", {"name": "season"})
    if not season_select:
        raise RuntimeError("<select name='season'> não encontrado no DOM.")

    all_seasons = [
        {"value": opt["value"], "label": opt.get_text(strip=True)}
        for opt in season_select.find_all("option")
        if opt.get("value")
    ]
    print(f"  → {len(all_seasons)} seasons encontradas: {[s['value'] for s in all_seasons]}")

    # Para cada season, carrega a página e extrai os events
    async def get_events_for_season(s: dict) -> tuple[str, list[dict]]:
        html = await fetch_page(session, s["value"])
        if not html:
            return s["value"], []
        soup = BeautifulSoup(html, "html.parser")
        ev_select = soup.find("select", {"name": "evvent"})
        if not ev_select:
            return s["value"], []
        events = [
            {"value": opt["value"], "label": opt.get_text(strip=True)}
            for opt in ev_select.find_all("option")
            if opt.get("value")
        ]
        return s["value"], events

    # Paralelo mas gentil (sem sobrecarregar o servidor)
    seasons_events: dict[str, list[dict]] = {}
    chunk_size = 4
    for i in range(0, len(all_seasons), chunk_size):
        chunk = all_seasons[i:i + chunk_size]
        results = await asyncio.gather(*[get_events_for_season(s) for s in chunk])
        for season_key, events in results:
            seasons_events[season_key] = events
            label = next((s["label"] for s in all_seasons if s["value"] == season_key), "?")
            print(f"  {season_key:15s} ({label:10s}) → {len(events)} eventos")
        await asyncio.sleep(0.5)

    # Salva índice
    index_path = CATALOG_DIR / "seasons_events.json"
    index = [
        {
            "season_value": s["value"],
            "season_label": s["label"],
            "events": seasons_events.get(s["value"], []),
        }
        for s in all_seasons
    ]
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(f"  [SALVO] {index_path}")

    return seasons_events


# ──────────────────────────────────────────────
# Stage 2: Scrape file links per season+event
# ──────────────────────────────────────────────

async def scrape_event(
    session: aiohttp.ClientSession,
    season_key: str,
    event: dict,
) -> list[dict]:
    """Carrega ?season=X&evvent=Y e extrai todos os hrefs de Result."""
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
            file_links.append({
                **parsed,
                "link_text": a.get_text(strip=True),
            })

    return file_links


# ──────────────────────────────────────────────
# Build catalog
# ──────────────────────────────────────────────

def build_catalog(all_file_links: list[dict]) -> dict:
    catalog: dict = {}
    for f in all_file_links:
        s_id, s_name = clean_id_name(f["season_raw"])
        e_id, e_name = clean_id_name(f["event_raw"])
        c_id, c_name = clean_id_name(f["championship_raw"])
        sess_dt, sess_name = parse_session_datetime(f["session_raw"])

        sk = f["season_raw"]
        ek = f["event_raw"]
        ck = f["championship_raw"]
        ssk = f["session_raw"]

        catalog.setdefault(sk, {"id": s_id, "name": s_name, "events": {}})
        catalog[sk]["events"].setdefault(ek, {"id": e_id, "name": e_name, "championships": {}})
        catalog[sk]["events"][ek]["championships"].setdefault(ck, {"id": c_id, "name": c_name, "sessions": {}})

        sess_store = catalog[sk]["events"][ek]["championships"][ck]["sessions"]
        sess_store.setdefault(ssk, {"datetime": sess_dt, "name": sess_name, "files": []})
        sess_store[ssk]["files"].append({
            "filename": f["file_path"],
            "ext":      f["file_ext"],
            "url":      f["full_url"],
            "label":    f.get("link_text", ""),
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


def print_summary(catalog: dict, sessions: list[dict]):
    print("\n" + "=" * 60)
    print("CATALOG SUMMARY v3")
    print("=" * 60)

    total_events = sum(len(s["events"]) for s in catalog.values())
    total_all_sess = sum(
        len(c["sessions"])
        for s in catalog.values()
        for e in s["events"].values()
        for c in e["championships"].values()
    )
    wec = [s for s in sessions if "FIA WEC" in s.get("championship_name", "")]
    races = [s for s in wec if s["session_type"] == "Race"]

    print(f"  Temporadas:       {len(catalog)}")
    print(f"  Eventos:          {total_events}")
    print(f"  Sessões (todas):  {total_all_sess}")
    print(f"  Sessões WEC:      {len(wec)}")
    print(f"  Corridas:         {len(races)}")

    print("\n  Por temporada:")
    for sk, season in sorted(catalog.items()):
        n_ev = len(season["events"])
        n_s  = sum(
            len(c["sessions"])
            for e in season["events"].values()
            for c in e["championships"].values()
        )
        wec_s = [s for s in wec if s["season_raw"] == sk]
        print(f"    {season['name']:30s} → {n_ev:2d} eventos  {n_s:3d} sessões  ({len(wec_s)} WEC)")

    from collections import Counter
    print("\n  Tipos de sessão (WEC):")
    for t, n in sorted(Counter(s["session_type"] for s in wec).items(), key=lambda x: -x[1]):
        print(f"    {t:15s} {n:3d}")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

async def run(season_filter: str | None = None, from_season: str | None = None):
    print("=" * 60)
    print("OpenWEC — Catalog Spider v3 (aiohttp + BeautifulSoup)")
    print("=" * 60)

    connector = aiohttp.TCPConnector(limit=6)
    async with aiohttp.ClientSession(
        headers=HEADERS,
        connector=connector,
    ) as session:

        # Stage 1: descobrir seasons e events
        seasons_events = await discover_seasons_and_events(session)

        # Filtra se pedido
        if season_filter:
            seasons_events = {k: v for k, v in seasons_events.items() if k == season_filter}
            print(f"\n[FILTRO] season={season_filter}")
        elif from_season:
            seasons_events = {
                k: v for k, v in seasons_events.items()
                if k >= from_season
            }
            print(f"\n[FILTRO] from_season={from_season} → {len(seasons_events)} seasons")

        # Stage 2: scrape cada event
        print(f"\n[2/3] Baixando catálogo de arquivos...")
        total_pairs = sum(len(evs) for evs in seasons_events.values())
        print(f"  Total de combinações season×event: {total_pairs}")

        all_links: list[dict] = []
        done = 0

        for season_key, events in sorted(seasons_events.items()):
            season_label = season_key
            print(f"\n  [{season_key}]")

            # Scrape em paralelo por evento (máx 4 ao mesmo tempo)
            for i in range(0, len(events), 4):
                chunk = events[i:i+4]
                tasks = [scrape_event(session, season_key, ev) for ev in chunk]
                results = await asyncio.gather(*tasks)

                for ev, links in zip(chunk, results):
                    wec_links = [l for l in links if "FIA WEC" in l.get("championship_raw", "")
                                 or "FIA%20WEC" in l.get("full_url", "")]
                    all_links.extend(links)  # guarda tudo, filtra depois
                    done += 1
                    print(f"    {ev['value']:40s} → {len(links):3d} files ({len(wec_links)} WEC)")

                await asyncio.sleep(0.4)

    print(f"\n[3/3] Construindo catálogo com {len(all_links)} links...")

    # Filtra só WEC
    wec_links = [
        l for l in all_links
        if "FIA WEC" in l.get("championship_raw", "")
    ]
    print(f"  Links WEC: {len(wec_links)} | Outros: {len(all_links) - len(wec_links)}")

    catalog  = build_catalog(wec_links)
    sessions = flatten_sessions(catalog)

    # Salva
    with open(CATALOG_DIR / "wec_catalog.json", "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

    with open(CATALOG_DIR / "sessions.json", "w", encoding="utf-8") as f:
        json.dump(sessions, f, indent=2, ensure_ascii=False)

    print_summary(catalog, sessions)
    print(f"\n  [SALVO] catalog/wec_catalog.json")
    print(f"  [SALVO] catalog/sessions.json")
    print(f"  [SALVO] catalog/seasons_events.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenWEC Catalog Spider v3")
    parser.add_argument("--season", help="Só essa temporada. Ex: 13_2024")
    parser.add_argument("--from-season", help="A partir desta. Ex: 12_2023")
    args = parser.parse_args()

    asyncio.run(run(
        season_filter=args.season,
        from_season=args.from_season,
    ))
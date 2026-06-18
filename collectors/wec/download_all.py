"""
OpenWEC — Historical Downloader v2
Download Classification + Analysis CSVs of all seasons/events.

Output structure:
    raw/
      wec/
        13_2024/
          04_LE MANS/
            classification/   ← 03_Classification_*.CSV
            analysis/         ← 23_Analysis_*.CSV
            weather/          ← 26_Weather_*.CSV

Usage:
    pip install aiohttp aiofiles beautifulsoup4
    python download_all.py [--season 13_2024] [--workers 4] [--dry-run]

Flags:
    --season   only this season
    --workers   parallel downloads (default: 4, max recommended: 6)
    --dry-run   shows what would be downloaded without downloading
    --resume    skips files that are already downloaded (default: True)
"""

import asyncio
import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import aiohttp
import aiofiles


CATALOG_PATH = Path("catalog/wec/sessions.json")
RAW_DIR      = Path("raw/wec")
INGEST_LOG   = Path("catalog/wec/ingest_log.json")

BASE_URL = "https://fiawec.alkamelsystems.com/"
HEADERS  = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Prefixos de arquivo que nos interessam
FILE_FILTERS = {
    "classification": ["03_classification"],
    "analysis":       ["23_analysis"],
    "weather":        ["26_weather"],
}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def file_category(filename: str) -> str | None:
    """Retorna 'classification', 'analysis', 'weather' ou None."""
    name = filename.lower()
    for category, prefixes in FILE_FILTERS.items():
        if any(name.startswith(p) or f"/{p}" in name for p in prefixes):
            return category
    return None


def local_path(url: str) -> Path:
    """
    Converte URL em path local estruturado.
    https://.../Results/13_2024/04_LE MANS/575_FIA WEC/202406151000_Race/23_Analysis_Race.CSV
    → raw/wec/13_2024/04_LE MANS/Race/analysis/23_Analysis_Race.CSV
    """
    # Extrai a parte depois de Results/
    m = re.search(r"Results?/(.+)", unquote(url), re.IGNORECASE)
    if not m:
        return RAW_DIR / "misc" / url.split("/")[-1]

    parts = m.group(1).split("/")
    # parts: [season, event, championship, session, filename]
    if len(parts) < 5:
        return RAW_DIR / "/".join(parts)

    season      = parts[0]      # 13_2024
    event       = parts[1]      # 04_LE MANS
    # skip championship (parts[2])
    session_raw = parts[3]      # 202406151000_Race
    filename    = "/".join(parts[4:])

    # Nome limpo da sessão
    m2 = re.match(r"\d{12}_(.*)", session_raw)
    session_name = m2.group(1) if m2 else session_raw

    # Categoria do arquivo
    cat = file_category(filename) or "other"

    return RAW_DIR / season / event / session_name / cat / filename


def load_ingest_log() -> set[str]:
    """Carrega URLs já baixadas com sucesso."""
    if INGEST_LOG.exists():
        with open(INGEST_LOG) as f:
            data = json.load(f)
        return {entry["url"] for entry in data if entry.get("status") == "ok"}
    return set()


def save_ingest_log(log: list[dict]):
    with open(INGEST_LOG, "w") as f:
        json.dump(log, f, indent=2)


# ──────────────────────────────────────────────
# Download
# ──────────────────────────────────────────────

async def download_file(
    session: aiohttp.ClientSession,
    url: str,
    dest: Path,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Baixa um arquivo e salva em dest. Retorna entry de log."""
    async with semaphore:
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return {"url": url, "status": "http_error", "code": resp.status}

                raw = await resp.read()

                # Detecta encoding
                content = None
                for enc in ["utf-8", "latin-1", "cp1252"]:
                    try:
                        content = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue

                if content is None:
                    return {"url": url, "status": "decode_error"}

                dest.parent.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(dest, "w", encoding="utf-8") as f:
                    await f.write(content)

                lines = content.count("\n")
                return {
                    "url":    url,
                    "status": "ok",
                    "dest":   str(dest),
                    "size":   len(raw),
                    "lines":  lines,
                    "at":     datetime.utcnow().isoformat(),
                }

        except asyncio.TimeoutError:
            return {"url": url, "status": "timeout"}
        except Exception as e:
            return {"url": url, "status": "error", "msg": str(e)}


# ──────────────────────────────────────────────
# Build download queue from catalog
# ──────────────────────────────────────────────

def build_queue(
    sessions: list[dict],
    season_filter: str | None,
    already_done: set[str],
) -> list[tuple[str, Path, str]]:
    """
    Retorna lista de (url, local_path, category) pra baixar.
    Filtra: só WEC, só classification+analysis+weather, pula já baixados.
    """
    queue = []

    for sess in sessions:
        if season_filter and sess["season_raw"] != season_filter:
            continue
        if "FIA WEC" not in sess.get("championship_name", ""):
            continue

        for url in sess.get("csv_files", []):
            filename = url.split("/")[-1].lower()
            cat = file_category(filename)
            if cat is None:
                continue
            if url in already_done:
                continue
            dest = local_path(url)
            queue.append((url, dest, cat))

    return queue


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

async def run(
    season_filter: str | None = None,
    workers: int = 4,
    dry_run: bool = False,
    resume: bool = True,
):
    if not CATALOG_PATH.exists():
        print(f"[ERRO] {CATALOG_PATH} não encontrado.")
        print("  → Rode catalog_spider_v3.py primeiro.")
        return

    with open(CATALOG_PATH) as f:
        sessions: list[dict] = json.load(f)

    print("=" * 60)
    print("OpenWEC — Historical Downloader v2")
    print(f"  Catálogo:  {len(sessions)} sessões")
    print(f"  Workers:   {workers}")
    print(f"  Resume:    {resume}")
    if season_filter:
        print(f"  Filtro:    season={season_filter}")
    print("=" * 60)

    already_done = load_ingest_log() if resume else set()
    if already_done:
        print(f"  [RESUME] {len(already_done)} arquivos já baixados, pulando.\n")

    queue = build_queue(sessions, season_filter, already_done)

    # Estatísticas da fila
    from collections import Counter
    cat_counts = Counter(cat for _, _, cat in queue)
    print(f"  Fila de download: {len(queue)} arquivos")
    for cat, n in sorted(cat_counts.items()):
        print(f"    {cat:20s} {n:4d} arquivos")

    if dry_run:
        print(f"\n[DRY RUN] Primeiros 20 da fila:")
        for url, dest, cat in queue[:20]:
            print(f"  [{cat:14s}] {url.split('/')[-1]}")
            print(f"              → {dest}")
        return

    if not queue:
        print("\n[INFO] Nada pra baixar. Tudo já está em dia.")
        return

    print(f"\n[START] Baixando {len(queue)} arquivos com {workers} workers...\n")

    semaphore = asyncio.Semaphore(workers)
    ingest_log: list[dict] = []
    ok = errors = skipped = 0

    connector = aiohttp.TCPConnector(limit=workers + 2)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as http:
        tasks = [
            download_file(http, url, dest, semaphore)
            for url, dest, _ in queue
        ]

        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            result = await coro
            ingest_log.append(result)

            status = result["status"]
            url_short = result["url"].split("/")[-1][:50]

            if status == "ok":
                ok += 1
                lines = result.get("lines", 0)
                size  = result.get("size", 0) // 1024
                if i % 50 == 0 or i <= 5:
                    print(f"  [{i:4d}/{len(queue)}] ✓ {url_short} ({lines} linhas, {size}kb)")
            else:
                errors += 1
                print(f"  [{i:4d}/{len(queue)}] ✗ {url_short} → {status}")

            # Salva log a cada 100 arquivos
            if i % 100 == 0:
                save_ingest_log(ingest_log)

    # Salva log final
    save_ingest_log(ingest_log)

    # Resumo
    print("\n" + "=" * 60)
    print("DOWNLOAD COMPLETO")
    print("=" * 60)
    print(f"  ✓ Sucesso:  {ok}")
    print(f"  ✗ Erros:    {errors}")
    print(f"  Output:     {RAW_DIR.absolute()}")

    # Estrutura de diretórios criada
    seasons_dirs = [d for d in RAW_DIR.iterdir() if d.is_dir()] if RAW_DIR.exists() else []
    print(f"\n  Temporadas no disco: {len(seasons_dirs)}")
    for s in sorted(seasons_dirs):
        events = [d for d in s.iterdir() if d.is_dir()]
        total_files = sum(1 for _ in s.rglob("*.CSV"))
        print(f"    {s.name:20s} → {len(events):2d} eventos, {total_files:4d} CSVs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenWEC Downloader v2")
    parser.add_argument("--season",   help="Só esta temporada. Ex: 13_2024")
    parser.add_argument("--workers",  type=int, default=4)
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--no-resume", action="store_true",
                        help="Baixa tudo de novo mesmo que já exista")
    args = parser.parse_args()

    asyncio.run(run(
        season_filter=args.season,
        workers=args.workers,
        dry_run=args.dry_run,
        resume=not args.no_resume,
    ))
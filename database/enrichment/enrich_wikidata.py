"""
OpenWEC — Phase 4: Driver Wikidata Enrichment
Fetches nationality and birth year for drivers without country.

Uses requests (sync) — Wikidata blocks aiohttp with 403.

Usage:
    python database/enrichment/enrich_wikidata.py --dry-run
    python database/enrichment/enrich_wikidata.py
    python database/enrichment/enrich_wikidata.py --limit 20
"""

import argparse
import json
import time
import psycopg2
import psycopg2.extras
import requests
from pathlib import Path


DB_CONFIG = {
    "host":     "127.0.0.1",
    "port":     5433,
    "dbname":   "openwec",
    "user":     "openwec",
    "password": "openwec",
}

WIKIDATA_API  = "https://www.wikidata.org/w/api.php"
CACHE_FILE    = Path("database/enrichment/wikidata_cache.json")
NOTFOUND_FILE = Path("database/enrichment/wikidata_not_found.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

PROP_COUNTRY_CITIZENSHIP = "P27"
PROP_COUNTRY_SPORT       = "P1532"
PROP_BIRTH_DATE          = "P569"
PROP_OCCUPATION          = "P106"

RACING_OCCUPATIONS = {
    "Q10841764",  # racing driver
    "Q2309784",   # auto racing driver
    "Q11769055",  # motorcycle racer
}

COUNTRY_QIDS = {
    "Q30":   "USA", "Q145":  "GBR", "Q38":   "ITA", "Q142":  "FRA",
    "Q183":  "DEU", "Q17":   "JPN", "Q155":  "BRA", "Q39":   "CHE",
    "Q55":   "NLD", "Q408":  "AUS", "Q96":   "MEX", "Q35":   "DNK",
    "Q40":   "AUT", "Q29":   "ESP", "Q36":   "POL", "Q33":   "FIN",
    "Q34":   "SWE", "Q20":   "NOR", "Q31":   "BEL", "Q211":  "LVA",
    "Q45":   "PRT", "Q16":   "CAN", "Q668":  "IND", "Q148":  "CHN",
    "Q884":  "KOR", "Q736":  "ECU", "Q298":  "CHL", "Q414":  "ARG",
    "Q717":  "VEN", "Q739":  "COL", "Q419":  "PER", "Q77":   "URY",
    "Q159":  "RUS", "Q28":   "HUN", "Q191":  "EST", "Q37":   "LTU",
    "Q219":  "BGR", "Q41":   "GRC", "Q218":  "ROU", "Q258":  "ZAF",
    "Q664":  "NZL", "Q754":  "TTO", "Q5765": "MCO", "Q32":   "LUX",
    "Q27":   "IRL", "Q403":  "SRB", "Q222":  "ALB", "Q217":  "MDA",
    "Q227":  "AZE", "Q229":  "GEO", "Q232":  "KAZ", "Q928":  "PHL",
    "Q869":  "THA", "Q846":  "QAT", "Q878":  "ARE", "Q804":  "PAN",
    "Q800":  "CRI", "Q730":  "SUR", "Q724":  "GUY", "Q399":  "ARM",
    "Q252":  "IDN", "Q236":  "MNE", "Q224":  "HRV", "Q213":  "CZE",
    "Q215":  "SVN", "Q1028": "MAR", "Q262":  "DZA", "Q804":  "KWT",
}


def search_entity(first_name: str, last_name: str) -> str | None:
    try:
        r = requests.get(
            WIKIDATA_API,
            params={
                "action":   "wbsearchentities",
                "search":   f"{first_name} {last_name}",
                "language": "en",
                "type":     "item",
                "limit":    "5",
                "format":   "json",
            },
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            results = r.json().get("search", [])
            if results:
                return results[0]["id"]
    except Exception:
        pass
    return None


def get_entity_claims(qid: str) -> dict:
    try:
        r = requests.get(
            WIKIDATA_API,
            params={
                "action": "wbgetentities",
                "ids":    qid,
                "props":  "claims",
                "format": "json",
            },
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("entities", {}).get(qid, {})
    except Exception:
        pass
    return {}


def extract_qid(claim: dict) -> str | None:
    try:
        return claim["mainsnak"]["datavalue"]["value"]["id"]
    except (KeyError, TypeError):
        return None


def extract_time(claim: dict) -> str | None:
    try:
        t = claim["mainsnak"]["datavalue"]["value"]["time"]
        return t[1:11]
    except (KeyError, TypeError):
        return None


def parse_entity(entity: dict) -> dict:
    claims = entity.get("claims", {})
    result = {"country": None, "birth_date": None, "is_driver": False}

    if PROP_COUNTRY_SPORT in claims:
        qid = extract_qid(claims[PROP_COUNTRY_SPORT][0])
        result["country"] = COUNTRY_QIDS.get(qid)

    if not result["country"] and PROP_COUNTRY_CITIZENSHIP in claims:
        qid = extract_qid(claims[PROP_COUNTRY_CITIZENSHIP][0])
        result["country"] = COUNTRY_QIDS.get(qid)

    if PROP_BIRTH_DATE in claims:
        result["birth_date"] = extract_time(claims[PROP_BIRTH_DATE][0])

    if PROP_OCCUPATION in claims:
        for occ in claims[PROP_OCCUPATION]:
            if extract_qid(occ) in RACING_OCCUPATIONS:
                result["is_driver"] = True
                break

    return result


def enrich_drivers(drivers: list[dict], limit: int | None = None):
    if limit:
        drivers = drivers[:limit]

    cache: dict = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        print(f"  [CACHE] {len(cache)} entries loaded")

    enriched   = []
    not_found  = []
    from_cache = 0

    for i, driver in enumerate(drivers, 1):
        first = (driver["first_name"] or "").title()
        last  = (driver["last_name"] or "").title()
        key   = f"{first.lower()}_{last.lower()}"

        if i % 50 == 0:
            print(f"  [{i}/{len(drivers)}] {len(enriched)} found so far...")
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)

        if key in cache:
            from_cache += 1
            if cache[key].get("country"):
                enriched.append({**driver, **cache[key]})
            else:
                not_found.append(driver)
            continue

        qid = search_entity(first, last)

        if not qid:
            cache[key] = {"country": None, "birth_date": None, "qid": None}
            not_found.append(driver)
            time.sleep(0.2)
            continue

        entity = get_entity_claims(qid)
        parsed = parse_entity(entity)
        parsed["qid"] = qid
        cache[key] = parsed

        if parsed["country"]:
            enriched.append({**driver, **parsed})
        else:
            not_found.append(driver)

        time.sleep(0.2)

    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    print(f"  [CACHE] {len(cache)} entries (from_cache: {from_cache})")

    return enriched, not_found


def apply_enrichment(enriched: list[dict], dry_run: bool):
    if dry_run:
        print(f"\n[DRY RUN] Would update {len(enriched)} drivers")
        for d in enriched[:15]:
            print(f"  [{d['id']:5d}] {d['first_name']} {d['last_name']:20s}"
                  f" → country={d['country']}  birth={d.get('birth_date', '')}")
        return

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    updated = 0
    for d in enriched:
        cur.execute("""
            UPDATE drivers SET country = %s
            WHERE id = %s AND country IS NULL
        """, (d["country"], d["id"]))
        updated += 1
    conn.commit()
    cur.close()
    conn.close()
    print(f"  ✓ {updated} drivers updated")


def run(dry_run: bool = False, limit: int | None = None):
    print("=" * 60)
    print("OpenWEC — Phase 4: Wikidata Driver Enrichment")
    print(f"  Dry run: {dry_run}")
    if limit:
        print(f"  Limit:   {limit}")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, first_name, last_name
        FROM drivers
        WHERE country IS NULL
          AND first_name IS NOT NULL AND first_name != ''
          AND last_name  IS NOT NULL AND last_name  != ''
        ORDER BY last_name, first_name
    """)
    drivers = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    print(f"\n  Drivers without country: {len(drivers)}")

    enriched, not_found = enrich_drivers(drivers, limit)

    print(f"\n  Found:     {len(enriched)}")
    print(f"  Not found: {len(not_found)}")

    NOTFOUND_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(NOTFOUND_FILE, "w") as f:
        json.dump([
            {"id": d["id"], "name": f"{d['first_name']} {d['last_name']}"}
            for d in not_found
        ], f, indent=2, ensure_ascii=False)

    apply_enrichment(enriched, dry_run)
    print(f"  [SAVED] {NOTFOUND_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit",   type=int)
    args = parser.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)
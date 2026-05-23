# OpenWEC

Open endurance racing data platform.
Collects, normalizes, stores and exposes historical data from WEC, ELMS, ALMS, Le Mans Cup and IMSA.

---

## What is this

OpenWEC is a data engineering project built around endurance motorsport.
The goal is to create the most complete open database of endurance racing results,
lap times and analytics — and expose it through a public API.

No paywalls. No proprietary formats. Just data.

---

## Current status

### Phase 1 — Data Collection ✅

Reverse engineered the Al Kamel Systems timing portal used by FIA WEC, ELMS, ALMS,
Le Mans Cup and IMSA. Built automated collectors for all five series.

**Discovery:**
- Navigation is done via two server-side `<select>` elements (`season` and `evvent`)
- Each `GET /?season=X&evvent=Y` renders file links for that event
- Files are public, no authentication required

**URL pattern:**
```
https://{series}.alkamelsystems.com/Results/{season}/{event}/{championship}/{session}/{file}.CSV
```

**Files collected per session:**
| Prefix | Type | Content |
|---|---|---|
| `03_Classification_*` | classification | Final result — position, gap, fastest lap per car |
| `23_Analysis_*` | analysis | Lap-by-lap — every lap of every car |
| `26_Weather_*` | weather | Track conditions throughout the session |

**Volume:**

| Series | Seasons | Events | Sessions | Files |
|---|---|---|---|---|
| WEC | 15 (2011–2026) | 127 | 856 | 2,188 |
| ELMS | 22 (2005–2026) | 96 | 1,166 | 2,864 |
| Asian LMS | 5 | 13 | 155 | 357 |
| Le Mans Cup | 10 | 64 | 503 | 1,241 |
| IMSA | 11 (2016–2026) | 225 | 3,004 | 3,418 |
| **Total** | | | | **10,068** |

Note: IMSA does not publish lap-by-lap analysis files.

---

### Phase 2 — Database ✅

PostgreSQL 16 + TimescaleDB running in Docker.
Unified schema covering all five series.

**Tables:**
```
series → seasons → events → sessions
                                ↓
                    results ← cars ← teams
                       ↓
                 result_drivers → drivers
                       ↓
                      laps
```

**Volume loaded:**

| Table | Rows |
|---|---|
| series | 5 |
| seasons | 56 |
| events | 509 |
| sessions | 5,684 |
| teams | 1,249 |
| cars | 159,986 |
| drivers | 1,664 |
| results | 159,986 |
| result_drivers | 293,398 |
| laps | 1,613,721 |

**Key design decisions:**
- `result_drivers` join table supports up to 6 drivers per car (IMSA Daytona)
- `snapshot_hour` on sessions handles Le Mans hourly classification snapshots
- `S1_SECONDS`, `S2_SECONDS`, `S3_SECONDS` stored as float directly from CSV
- WEC/ELMS use `DRIVER_1..5` (full name); IMSA uses `DRIVER1_FIRSTNAME` + `DRIVER1_SECONDNAME` (separate)
- `imsa_driver_rating` stores Platinum/Gold/Silver/Bronze per driver

---

### Phase 3 — API ✅

FastAPI + Uvicorn. Swagger UI at `/docs`.

**Public endpoints (no auth required):**
```
GET /api/v1/series
GET /api/v1/series/{series}/seasons
GET /api/v1/series/{series}/seasons/{year}/events
GET /api/v1/series/{series}/seasons/{year}/events/{event_id}/sessions
GET /api/v1/sessions/{id}
GET /api/v1/sessions/{id}/results
```

**Protected endpoints (X-API-Key header required):**
```
GET /api/v1/sessions/{id}/laps
GET /api/v1/sessions/{id}/laps/{car_number}
```

**Running locally:**
```bash
uvicorn api.main:app --reload --port 8000
```

---

## Project structure

```
openwec/
│
├── collectors/
│   ├── wec/
│   │   ├── catalog_spider_v3.py    # discovers all sessions
│   │   └── download_all.py         # downloads CSVs
│   ├── elms/
│   │   └── elms_collect.py
│   ├── alms/
│   │   └── alms_collect.py
│   ├── lemanscup/
│   │   └── lemanscup_collect.py
│   └── imsa/
│       └── imsa_collect.py         # dual-domain collector
│
├── database/
│   ├── schema.sql                  # PostgreSQL + TimescaleDB schema
│   ├── migrations/
│   │   ├── 001_fix_car_number.sql
│   │   └── 002_laps_nullable_timestamp.sql
│   └── loader/
│       ├── load_metadata.py        # pass 1: seasons, events, sessions
│       ├── load_classification.py  # pass 2: teams, cars, drivers, results
│       └── load_laps.py            # pass 3: lap-by-lap data
│
├── api/
│   ├── main.py
│   ├── config.py
│   ├── deps.py
│   ├── schemas.py
│   └── routers/
│       ├── series.py
│       ├── sessions.py
│       ├── results.py
│       └── laps.py
│
├── docker/
│   └── docker-compose.yml
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Setup

### Requirements

- Python 3.12+
- Docker Desktop

### Install

```bash
git clone https://github.com/your-username/openwec
cd openwec

python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt
playwright install chromium
```

### Start the database

```bash
docker compose -f docker/docker-compose.yml up -d
```

### Collect data

```bash
# WEC (all seasons, 2011-2026)
python collectors/wec/catalog_spider_v3.py
python collectors/wec/download_all.py

# ELMS
python collectors/elms/elms_collect.py

# Asian LMS
python collectors/alms/alms_collect.py

# Le Mans Cup
python collectors/lemanscup/lemanscup_collect.py

# IMSA (WeatherTech + Endurance only)
python collectors/imsa/imsa_collect.py
```

### Load database

```bash
python database/loader/load_metadata.py
python database/loader/load_classification.py
python database/loader/load_laps.py
```

### Start API

```bash
uvicorn api.main:app --reload --port 8000
```

Swagger UI: `http://localhost:8000/docs`

---

## Known issues and future improvements

### Data quality
- Driver country is null for WEC/ELMS (not present in classification CSV)
- Compound names parsed incorrectly: "Nyck DE VRIES" → `first: "Nyck DE"`, `last: "VRIES"`
- IMSA 2016 has different CSV schema (older format, fewer fields)
- `laps.lap_recorded_at` is currently null (wall clock derivation pending)

### Missing data
- IMSA does not publish lap-by-lap analysis files
- WEC 2011 has no digital files on Al Kamel portal
- Some sessions have trailing spaces in filenames (server-side issue, 26 files affected)

---

## Roadmap

```
Phase 1 — Data Collection          ✅ complete
  · Al Kamel reverse engineering
  · 5 series collected
  · 10,068 CSVs on disk

Phase 2 — Database                 ✅ complete
  · PostgreSQL + TimescaleDB
  · 1,613,721 laps loaded
  · 159,986 race results

Phase 3 — Public API               ✅ complete
  · FastAPI REST API
  · Public + protected endpoints
  · Swagger UI

Phase 4 — Analytics Engine         ← next
  · Stint degradation model
  · Driver consistency (lap time variance)
  · Pit window estimator
  · Class comparison across sessions

Phase 5 — Live Ingestion           ← planned
  · WebSocket connection to Al Kamel during races
  · Kafka pipeline: raw → normalized → events
  · Real-time leaderboard endpoint

Phase 6 — Dashboard                ← planned
  · Grafana (MVP)
  · Next.js (v2)
  · Lap delta visualization
  · Tire degradation charts
  · Stint analysis

Phase 7 — Enrichment               ← planned
  · Driver profiles from RacingSportsCars
  · Driver country and nationality mapping
  · Chassis and team history
  · Career statistics per driver
```

---

## Data sources

| Source | URL | Usage |
|---|---|---|
| Al Kamel Systems (WEC) | fiawec.alkamelsystems.com | Classification, analysis, weather |
| Al Kamel Systems (ELMS) | elms.alkamelsystems.com | Classification, analysis, weather |
| Al Kamel Systems (ALMS) | alms.alkamelsystems.com | Classification, analysis, weather |
| Al Kamel Systems (LMC) | lemanscup.alkamelsystems.com | Classification, analysis, weather |
| Al Kamel Systems (IMSA) | imsa.alkamelsystems.com | Classification, weather |
| Al Kamel Cloud (IMSA) | imsa.results.alkamelcloud.com | Classification, weather (2024+) |

---

## Tech stack

| Layer | Technology |
|---|---|
| Data collection | Python, aiohttp, BeautifulSoup, Playwright |
| Database | PostgreSQL 16, TimescaleDB |
| Infrastructure | Docker, Docker Compose |
| API | FastAPI, Uvicorn, Pydantic |
| Analytics (planned) | Pandas, NumPy, scikit-learn |
| Dashboard (planned) | Grafana → Next.js |
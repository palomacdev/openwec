# OpenWEC

**Open endurance racing data platform — WEC, ELMS, ALMS, Le Mans Cup, IMSA.**

Historical lap-by-lap data, stint analytics, and race results from 2012 to the current season, accessible via a REST API, a Python SDK, and a live dashboard.

→ **[openwec.com](https://openwec.com)** — Live dashboard and data explorer  
→ **[api.openwec.com/docs](https://api.openwec.com/docs)** — Interactive API documentation

---

## What's inside

```
openwec/
├── collectors/          # Data collection (Al Kamel timing exports)
│   ├── wec/
│   ├── elms/
│   ├── alms/
│   ├── lemanscup/
│   └── imsa/
├── database/
│   ├── loader/          # CSV → PostgreSQL loaders
│   ├── enrichment/      # Driver/team normalization, Wikidata
│   ├── migrations/      # SQL schema migrations
│   └── admin/           # API key management CLI
├── api/                 # FastAPI REST API
│   └── routers/
├── analytics/           # Stint detection, pace, degradation engine
├── sdk/                 # Python SDK (openwec package)
│   └── openwec/
├── dashboard/           # React dashboard (openwec.com)
│   └── src/
│       ├── pages/
│       └── components/
└── docker/              # Docker Compose (PostgreSQL + TimescaleDB)
```

---

## Coverage

| Series | Seasons | Events | Sessions |
|--------|---------|--------|----------|
| FIA WEC | 2012–2026 | 103 | 1,100+ |
| ELMS | 2012–2026 | 110 | 1,100+ |
| ALMS (Asian) | 2022–2026 | 15 | 400+ |
| Le Mans Cup | 2017–2026 | 55 | 500+ |
| IMSA | 2014–2026 | 237 | 2,000+ |

**1.77M+ laps** across all series. Data sourced from [Al Kamel Systems](https://www.alkamelsystems.com/) timing exports.

---

## Quick start

### REST API

Public endpoints — no key required:

```bash
# List all series
curl https://api.openwec.com/api/v1/series

# Get Le Mans 2026 results
curl https://api.openwec.com/api/v1/sessions/6556/results
```

Protected endpoints (laps, analytics) require an API key — [request one here](https://openwec.com/api-keys).

```bash
curl https://api.openwec.com/api/v1/sessions/6556/stints \
  -H "X-API-Key: your-key-here"
```

### Python SDK

```bash
pip install openwec  
```

```python
import openwec

openwec.configure(
    base_url="https://api.openwec.com/api/v1",
    api_key="your-key-here"
)

session = openwec.Session("WEC", 2026, "Le Mans", "Race")
print(session)
# Session(WEC 2026 LE MANS — Race, id=6556)

results = session.results()
laps    = session.laps(car="7")
stints  = session.stints()

session.plot_stint_chart()
session.plot_gap_to_leader()
```

---

## Running locally

### Prerequisites

- Python 3.12+
- Docker Desktop
- Node.js 20+ (dashboard only)

### 1. Database

```bash
# Start PostgreSQL + TimescaleDB
docker compose -f docker/docker-compose.yml up -d

# Apply schema
docker exec -i openwec-db psql -U openwec -d openwec < database/schema.sql
```

### 2. Data collection

```bash
pip install -r requirements.txt
playwright install chromium

# WEC (adjust series as needed)
python collectors/wec/catalog_spider_v3.py
python collectors/wec/download_all.py
```

### 3. Load data

```bash
# Order matters: metadata → classification → laps
python database/loader/load_metadata.py
python database/loader/load_classification.py
python database/loader/load_laps.py

# Enrichment
python database/enrichment/normalize_drivers.py
python database/enrichment/merge_drivers.py
python database/enrichment/normalize_teams.py

# Analytics
python analytics/engine.py --series WEC --session-type Race
```

### 4. API

```bash
pip install -r requirements-api.txt

# Development (no API key required)
uvicorn api.main:app --reload --port 8000

# Docs at http://localhost:8000/docs
```

### 5. Dashboard

```bash
cd dashboard
npm install
npm run dev
# Open http://localhost:5173
```

---

## API reference

Full interactive documentation: **[api.openwec.com/docs](https://api.openwec.com/docs)**

### Public endpoints (no key)

| Endpoint | Description |
|----------|-------------|
| `GET /series` | List all series |
| `GET /series/{key}/seasons` | List seasons |
| `GET /series/{key}/seasons/{year}/events` | List events |
| `GET /sessions/{id}/results` | Race classification |
| `GET /drivers/{id}` | Driver profile + career stats |
| `GET /teams/{id}` | Team profile + history |
| `GET /events/{id}` | Event with all sessions |

### Protected endpoints (API key required)

| Endpoint | Description |
|----------|-------------|
| `GET /sessions/{id}/laps/{car}` | Lap-by-lap data |
| `GET /sessions/{id}/stints` | Stint breakdown |
| `GET /sessions/{id}/pace` | Green flag pace comparison |
| `GET /sessions/{id}/gaps` | Gap to leader evolution |
| `GET /sessions/{id}/pit-window` | Pit window estimator |
| `GET /sessions/{id}/race-control` | SC / FCY periods |
| `GET /drivers/{id}/consistency` | Driver consistency stats |

---

## Stack

| Layer | Technology |
|-------|-----------|
| Database | PostgreSQL 16 + TimescaleDB |
| API | FastAPI (Python 3.12) |
| SDK | Python — pandas-native |
| Dashboard | React + Recharts |
| Hosting | Docker, self-hosted |
| Data source | Al Kamel Systems timing exports |

---

## Data refresh

When new races happen, run:

```bash
# 1. Collect new data
python collectors/wec/catalog_spider_v3.py
python collectors/wec/download_all.py

# 2. Load
python database/loader/load_metadata.py
python database/loader/load_classification.py --series WEC
python database/loader/load_laps.py --series WEC

# 3. Clean phantom sessions
# DELETE FROM sessions WHERE session_type = 'Other'
# AND NOT EXISTS (SELECT 1 FROM laps WHERE session_id = sessions.id)
# AND NOT EXISTS (SELECT 1 FROM results WHERE session_id = sessions.id);

# 4. Enrich
python database/enrichment/normalize_drivers.py
python database/enrichment/merge_drivers.py
python database/enrichment/normalize_teams.py

# 5. Analytics
python analytics/engine.py --series WEC --session-type Race

# 6. Deploy (see DEPLOYMENT.md)
```

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full roadmap.

**Coming next:**
- PyPI package (`pip install openwec`)
- Live timing ingestion
- Track map visualization (if position data available in live stream)

---

## License

MIT — data sourced from Al Kamel Systems public timing exports.  
Not affiliated with ACO, FIA, or any racing organization.
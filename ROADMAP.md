# OpenWEC — Roadmap

## Phase 1 — Data Collection ✅
- Collectors for WEC, ELMS, ALMS, Le Mans Cup, IMSA
- Al Kamel Systems timing export ingestion
- 10,000+ CSVs across 5 series

## Phase 2 — Database ✅
- PostgreSQL 16 + TimescaleDB schema
- Series / Season / Event / Session / Car / Driver / Result / Lap model
- Docker Compose setup

## Phase 3 — Public API ✅
- FastAPI REST API
- Public endpoints: series, seasons, events, sessions, results, drivers, teams
- Protected endpoints: laps, analytics (X-API-Key)
- Interactive docs at api.openwec.com/docs

## Phase 4 — Enrichment ✅
- Driver normalization (1,800+ compound name fixes, capitalisation)
- Driver deduplication (WEC vs IMSA cross-series merges)
- Wikidata nationality enrichment
- Team normalization and deduplication
- Tyre supplier mapping

## Phase 5 — Analytics Engine ✅
- Stint detection via pit lap flags
- Baseline pace (median green-flag laps per stint)
- Degradation rate (linear regression s/lap)
- Driver consistency (std dev green-flag laps)
- Pit window estimator
- Gap to leader evolution
- Race control periods (SC / FCY detection)

## Phase 5.5 — Python SDK Alpha ✅
- `openwec.Session("WEC", 2026, "Le Mans", "Race")`
- `.results()`, `.laps()`, `.stints()`, `.pace()`, `.gaps()`, `.pit_window()`
- `.plot_lap_evolution()`, `.plot_stint_chart()`, `.plot_gap_to_leader()`

## Phase 6 — Dashboard ✅
- React + Recharts, timing tower aesthetic
- Leaderboard, Stint Chart, Lap Evolution, Gap to Leader
- Race Control overlay (SC/FCY bands on charts)
- Class filter (HYPERCAR / LMP2 / LMGT3)

## Phase 7 — Deploy ✅
- DigitalOcean droplet (Ubuntu 24.04, Docker)
- openwec.com + api.openwec.com + www.openwec.com
- HTTPS via Let's Encrypt (auto-renewing)
- Nginx reverse proxy

## Phase 7.5 — Marketing Site ✅
- Home page with live timing tower (real API data)
- About page
- Explore Data (series → season → event → session → results)
- API Key request form (POST /api-keys/request)
- React Router (/, /about, /explore, /api-keys, /dashboard)

## Phase 7.6 — Data Refresh ✅
- WEC 2026: Imola, Spa, Le Mans (Qatar cancelled — geopolitical)
- Laps deduplication fix + UNIQUE constraint
- Enrichment and analytics updated

## Phase 7.7 — API Key System ✅
- Request form → pending key (instant generation)
- Manual approval via CLI (manage_api_keys.py)
- Dynamic key validation + rate limiting (fixed-window, per-key rpm)
- Static admin keys (env var, no rate limit)

## Phase 7.8 — Documentation ✅
- README.md with quick start, API reference, stack
- CONTRIBUTING.md
- MAINTENANCE.md (data refresh + deploy playbook)
- requirements split: base / api / dev
- Repository made public

---

## Phase 8 — Python Library (stable, PyPI) ✅
- Stable public API (semver)
- `pip install openwec`
- Published to PyPI
- Full docstrings + Sphinx docs
- Example Jupyter notebooks

## Phase 9 — Live Timing ← next
- Connect to Al Kamel WebSocket during live sessions
- Parse real-time payload format
- Investigate whether live payload includes track position (X/Y/GPS)
- If available: build track map visualization (à la FastF1)
- Kafka pipeline: raw → normalized → events
- Store lap-by-lap as race progresses

## Phase 10 — Ecosystem
- ELMS/ALMS/Le Mans Cup/IMSA data refresh (ongoing)
- Multi-race comparison endpoints
- Season championship standings
- Driver/team career analytics
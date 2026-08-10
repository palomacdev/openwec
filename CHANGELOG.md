# Changelog

All notable changes to OpenWEC are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.2.3] — 2026-07-28

### API
- `GET /series/{key}/seasons/{year}/stats` — season aggregate stats
  (total races, laps, entries, classes, manufacturers, top drivers/teams)

### SDK
- `examples/season_comparison.ipynb` — WEC season comparison notebook
  (2024 vs 2025 vs 2026: manufacturer presence, pace, lap counts)

---

## [0.2.2] — 2026-07-28

### API
- `GET /drivers/search?name=` — search drivers by partial name (public)
- `GET /sessions/{id}/results` — optional pagination and car_class filter
- `DriverSlot` now includes driver `id` for profile page linking

### Dashboard
- Dynamic event selector (series → season → event → session dropdowns)
- Driver profile page `/drivers/:id` with career stats and race history
- Driver names in leaderboard are clickable links

### Infrastructure
- Structured JSON logging (method, path, status, duration_ms, api_key_hash)
- Redis-backed rate limiting with in-memory fallback for development

--- 

## [0.2.0] — 2026-07-01

### SDK
- Comprehensive docstrings on all public methods (Google-style: Args, Returns, Raises, Examples)
- Migrated HTTP client from `requests` to `httpx` (enables mocking in tests)
- New example notebook: `driver_career.ipynb` — cross-series career analysis

### Data
- ELMS 2026: Imola added (4 events total for the season)
- Le Mans Cup 2026: latest classification and analysis files added
- IMSA 2026: 43 new files ingested
- Total: 1,965,051 laps across all series

### Infrastructure
- Automated daily backup to DigitalOcean Spaces (NYC3), 7-day retention
- UptimeRobot monitoring: dashboard, API health, API data (5-min intervals)

### Quality
- pytest test suite: 16 API tests + 13 SDK tests
- GitHub Actions CI: runs on every push and pull request
- Test fixtures: minimal DB seed for CI environment

---

## [0.1.1] — 2026-06-24

### SDK
- Default `base_url` now points to `https://api.openwec.com/api/v1`
- Users no longer need to call `openwec.configure(base_url=...)` for production use
- Only `api_key` is needed for protected endpoints

---

## [0.1.0] — 2026-06-22

### Added
- Initial PyPI release
- `openwec.Session` — resolve series/season/event/session by name
- `.results()` — race classification as pandas DataFrame (public, no key)
- `.laps(car=None)` — lap-by-lap data, paginated (requires API key)
- `.stints()`, `.pace()`, `.gaps()`, `.pit_window()` — analytics DataFrames
- `.plot_lap_evolution()`, `.plot_stint_chart()`, `.plot_gap_to_leader()` — matplotlib charts
- Example notebook: `le_mans_2026.ipynb`

---

## Platform — 2026-06-17

### API
- `POST /api-keys/request` — public endpoint for requesting API keys
- `GET /sessions/{id}/race-control` — SC/FCY period detection
- Dynamic API key validation with per-key rate limiting
- Static admin keys (env var) bypass rate limiting

### Dashboard
- Home page with live timing tower (real API data, Le Mans 2026)
- `/about` — project story, stack, coverage timeline
- `/explore` — browse series → season → event → session → results
- `/api-keys` — API key request form
- React Router: all pages under `openwec.com`

### Data
- WEC 2026: Imola, Spa, Le Mans loaded (Qatar cancelled)
- Bug fix: laps deduplication — `UNIQUE (session_id, car_id, lap_number)` constraint added
- Phantom session cleanup: 460 artifact sessions removed after metadata reload

---

## Platform — 2026-06-14

### Added
- Initial public release at [openwec.com](https://openwec.com)
- REST API at [api.openwec.com](https://api.openwec.com)
- 1,775,200 laps across WEC, ELMS, ALMS, Le Mans Cup, IMSA (2012–2026)
- Analytics engine: stint detection, pace, degradation, pit window, race control
- Driver and team normalization and deduplication
- PostgreSQL + TimescaleDB, Docker, DigitalOcean, Let's Encrypt

[Unreleased]: https://github.com/palomacdev/openwec/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/palomacdev/openwec/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/palomacdev/openwec/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/palomacdev/openwec/releases/tag/v0.1.0
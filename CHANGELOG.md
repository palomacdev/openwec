# Changelog

All notable changes to OpenWEC are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).  
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### SDK 0.3.0 — prepared, not published

The API gained per-minute budgets for reads made without a key, and answers
`429` with `Retry-After`. The SDK read neither.

#### Added
- `OpenWECRateLimitError` (429), carrying `.retry_after` in seconds, or `None`
  when the API gave no guidance — which is not the same as zero
- `OpenWECPermissionError` (403) and `OpenWECServerError` (5xx)
- `configure(max_retries=...)` — wait out a 429 automatically. Defaults to `0`,
  so nothing blocks unless asked
- `configure(retry_max_wait=...)` — ceiling for one wait, default 60s. Sleeping
  past it would be a hang, not a retry, so it raises instead
- `Retry-After` parsed in both legal forms, seconds and HTTP-date; a date in
  the past reads as "no guidance"

#### Changed
- **Behaviour:** 403, 429 and 5xx previously surfaced as a bare
  `httpx.HTTPStatusError`. They now raise typed `OpenWECError` subclasses,
  matching how 401 and 404 have always behaved. Code catching
  `httpx.HTTPStatusError` for these three needs updating; code catching
  `OpenWECError` already covers them
- `test_version` now asserts `pyproject.toml` and `__version__` agree, instead
  of pinning a literal that had to be edited every release

#### Compatibility
- No endpoint changes, and nothing here requires the new API. Verified against
  the contract actually deployed (23 routes) and against `main` (27): every
  deployed route still exists, four relax from required to optional credential,
  and none of the eight routes the SDK calls changes shape

---

## [0.2.4] — 2026-08-18

### API
- `POST /admin/keys/{key}/revoke` — instant key revocation (clears DB, cache, and Redis counter)
- `GET /admin/keys` — list all keys (admin only)
- `GET /admin/keys/pending` — list pending requests (admin only)
- Daily and monthly quota support per API key (NULL = unlimited)

### Infrastructure
- Rate limit alerts: structured WARNING log after 10 hits by same key
- `--daily-limit` and `--monthly-limit` flags on `manage_api_keys.py --approve`
- `database/admin/log_analytics.py` — parse docker logs and generate usage report
- `database/migrations/005_api_key_quotas.sql` — daily_limit, monthly_limit columns

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
# OpenWEC — Product Roadmap

---

## Completed

### Phase 4 — Enrichment
*Goal: turn raw timing data into a real driver and team database.*

#### Driver enrichment
- [x] Scrape RacingSportsCars for driver profiles
- [x] Map nationality, full name, birth year
- [x] Fix compound name parsing (Van/De/Di/El particles)
- [x] Link drivers across series (same person, different spellings)
- [x] Add Driver Database as secondary source

#### Team enrichment
- [x] Normalize team names across seasons (e.g. "AF Corse" vs "Ferrari AF Corse")
- [x] Map manufacturer per team per season
- [x] Add team nationality

#### Car enrichment
- [x] Link car number to chassis ID where available
- [x] Map tyre compound codes (M, P, G → Michelin, Pirelli, Goodyear)

#### API additions
- [ ] `GET /drivers/{id}` — driver profile + career stats
- [ ] `GET /drivers/{id}/results` — full race history
- [ ] `GET /teams/{id}` — team profile + season history

---

### Phase 5 — Analytics Engine
*Goal: generate insights that analysts currently do manually in Excel.*

#### Stint analysis
- [x] Detect stint boundaries from pit lap flags
- [ ] Calculate stint length, average pace, degradation rate
- [x] Expose via `GET /sessions/{id}/stints`

#### Lap time analytics
- [x] Driver consistency score (lap time variance per stint)
- [ ] Pace delta between drivers in same car
- [x] Green flag vs yellow flag lap filtering

#### Race analytics
- [x] Pit window estimator (optimal lap range for stops)
- [x] Gap evolution over race distance
- [ ] Class comparison (HYPERCAR vs LMGT3 relative pace)

#### Pre-built queries (API)
- [x] `GET /sessions/{id}/stints` — stint breakdown per car
- [x] `GET /sessions/{id}/pace` — average green flag pace per car
- [x] `GET /sessions/{id}/gaps` — gap evolution over laps
- [x] `GET /drivers/{id}/consistency` — variance stats across sessions

---

### Phase 5.5 — Python SDK Alpha
*Goal: `import openwec` working locally — the FastF1 moment, early.*

#### Interface target
```python
import openwec

openwec.configure(api_key="your-key")  # optional in dev

session = openwec.Session("WEC", 2024, "Le Mans", "Race")

results = session.results()        # DataFrame
laps    = session.laps()           # DataFrame
laps_50 = session.laps(car="50")  # filtered

session.plot_lap_evolution(car="50") 
session.plot_stint_chart()
session.plot_gap_to_leader()
```

#### Scope (alpha only)
- [ ] `openwec.configure()` — set base URL and API key
- [ ] `openwec.Session` — resolves series/season/event/session to session_id
- [ ] `session.results()` — returns pandas DataFrame
- [ ] `session.laps()` — returns pandas DataFrame with optional car filter
- [ ] `session.plot_lap_evolution()` — basic matplotlib/plotly chart
- [ ] Points to localhost:8000 (no cache, always fresh)
- [ ] Jupyter notebook with usage examples

#### Out of scope for alpha
- PyPI publishing
- Local Parquet cache
- Offline mode
- All plotting methods

---

### Phase 6 — Dashboard Showcase
*Goal: make the data visible and shareable — the "wow" moment.*

#### MVP (Grafana)
- [ ] Live leaderboard panel
- [ ] Lap evolution chart per car
- [ ] Gap to leader over race distance
- [ ] Stint visualization (colored bars per driver)

#### V2 (Next.js + Tailwind)
- [ ] Public dashboard at dashboard.openwec.com
- [ ] Session browser (series → season → event → session)
- [ ] Car detail page (all laps, stints, sector breakdown)
- [ ] Driver career page (results across all series)
- [ ] Class comparison view
- [ ] Shareable URLs per session/car/driver

---

### Phase 7 — Deploy
*Goal: make the project real with a public URL.*

#### Infrastructure
- [ ] Dockerfile for API
- [ ] docker-compose for full stack (API + DB + dashboard)
- [ ] Environment variable config (.env)
- [ ] Health check endpoint

#### Deploy (DigitalOcean)
- [ ] Provision $12 Droplet
- [ ] Configure PostgreSQL + TimescaleDB
- [ ] Deploy API with Gunicorn + Nginx
- [ ] SSL via Let's Encrypt
- [ ] CI/CD via GitHub Actions (push to main → deploy)

#### Public presence
- [ ] openwec.com (or similar domain)
- [ ] docs.openwec.com — API documentation
- [ ] Uptime monitoring

---

### Phase 8 — Python Library
*Goal: `pip install openwec` — the FastF1 moment.*

#### Core library
- [ ] `openwec` package on PyPI
- [ ] Session loader with local cache (Parquet files)
- [ ] Lap DataFrame with pandas integration
- [ ] Result DataFrame

#### Usage target
```python
import openwec

session = openwec.session("WEC", 2024, "Le Mans", "Race")

laps = session.laps()
laps[laps.car == "50"].plot_lap_evolution()

results = session.results()
print(results.head())
```

#### Distribution
- [ ] Publish to PyPI
- [ ] Versioned releases on GitHub
- [ ] Docs with example notebooks (Jupyter)
- [ ] README with quickstart

---

### Phase 9 — Live Timing
*Goal: real-time data during races — the final frontier.*

#### WebSocket ingestion
- [ ] Connect to Al Kamel WebSocket during live sessions
- [ ] Parse real-time payload format
- [ ] Kafka pipeline: raw → normalized → events
- [ ] Store lap-by-lap as race progresses

#### Kafka topics
```
raw.timing
raw.session
normalized.laps
normalized.position
events.pitstop
events.yellowflag
events.safetyCar
```

#### Real-time API
- [ ] `GET /live/sessions` — active sessions right now
- [ ] `GET /live/sessions/{id}/leaderboard` — live standings
- [ ] `WS  /live/sessions/{id}/stream` — WebSocket stream

#### Supported series (live)
- [ ] WEC (Al Kamel)
- [ ] ELMS (Al Kamel)
- [ ] IMSA (Al Kamel Cloud)

---

## Tech stack evolution

| Phase | Added |
|---|---|
| 1-3 (done) | Python, aiohttp, BeautifulSoup, PostgreSQL, FastAPI |
| 4 Enrichment | Playwright, RacingSportsCars scraper |
| 5 Analytics | Pandas, NumPy, scikit-learn |
| 6 Dashboard | Grafana → Next.js, Tailwind, Recharts |
| 7 Deploy | Docker, Nginx, GitHub Actions, DigitalOcean |
| 8 Library | PyPI packaging, Parquet, Jupyter |
| 9 Live | Kafka, WebSocket, Redis |
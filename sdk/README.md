# openwec

**Python SDK for endurance racing data — WEC, ELMS, ALMS, Le Mans Cup, IMSA.**

Inspired by [FastF1](https://github.com/theOehrly/Fast-F1), OpenWEC gives you lap-by-lap timing data, stint analytics, and race results from endurance racing as pandas DataFrames — in one line.

→ **[openwec.com](https://openwec.com)** — Live dashboard  
→ **[api.openwec.com/docs](https://api.openwec.com/docs)** — API reference  
→ **[github.com/palomacdev/openwec](https://github.com/palomacdev/openwec)** — Source

---

## Installation

```bash
pip install openwec

# With plotting support
pip install openwec[plotting]
```

---

## Quick start

```python
import openwec

# Public endpoints work without a key.
# Request a free key at https://openwec.com/api-keys
openwec.configure(api_key="your-key-here")  # required for laps and analytics

# Load any session — WEC, ELMS, ALMS, Le Mans Cup, or IMSA
session = openwec.Session("WEC", 2026, "Le Mans", "Race")
print(session)
# Session(WEC 2026 LE MANS — Race, id=6556)

# Results as a DataFrame (no key needed)
results = session.results()
print(results[["position", "car_number", "car_class", "team", "drivers"]].head(10))

# Lap-by-lap data (key required)
laps = session.laps(car="7")
print(laps[["lap_number", "lap_time_s", "s1_s", "s2_s", "s3_s"]].head())

# Endurance-specific: filter laps by driver within a shared-drive car
# (unique to endurance racing — not possible in FastF1)
conway_laps     = session.driver_laps("Conway", car="7")
kobayashi_laps  = session.driver_laps("Kobayashi", car="7")
print(f"Conway pace:    {conway_laps['lap_time_s'].median():.3f}s")
print(f"Kobayashi pace: {kobayashi_laps['lap_time_s'].median():.3f}s")

# Stints and pace
stints     = session.stints(car_class="HYPERCAR")
pace       = session.pace(car_class="HYPERCAR")
pit_window = session.pit_window(car="7")

# Plots (requires matplotlib)
session.plot_lap_evolution(car="7")
session.plot_stint_chart(car_class="HYPERCAR")
session.plot_gap_to_leader(car_class="HYPERCAR")
```

---

## Coverage

| Series | Seasons |
|--------|---------|
| FIA WEC | 2012–2026 |
| ELMS | 2012–2026 |
| ALMS (Asian) | 2022–2026 |
| Le Mans Cup | 2017–2026 |
| IMSA | 2014–2026 |

**1.96M+ laps** across all series.

---

## API key

Public endpoints (results, driver profiles, team profiles, driver search) require no key.  
Lap-by-lap data and analytics endpoints require a free API key.

→ **[Request a key at openwec.com/api-keys](https://openwec.com/api-keys)**

---

## Session methods

| Method | Description | Key required |
|--------|-------------|-------------|
| `.results()` | Race classification as DataFrame | No |
| `.laps(car=None)` | Lap-by-lap data | Yes |
| `.driver_laps(name, car=None)` | Laps filtered by driver name ⚑ | Yes |
| `.stints(car_class=None)` | Stint breakdown per car | Yes |
| `.pace(car_class=None)` | Average green-flag pace | Yes |
| `.gaps(car_class=None)` | Gap to leader evolution | Yes |
| `.pit_window(car=None)` | Optimal pit window estimate | Yes |
| `.plot_lap_evolution(car)` | Lap time chart | Yes |
| `.plot_stint_chart()` | Strategy/stint chart | Yes |
| `.plot_gap_to_leader()` | Gap to leader chart | Yes |

⚑ **Unique to endurance racing** — multiple drivers share a car. `driver_laps()` has no equivalent in FastF1.

---

## Example notebooks

| Notebook | Description |
|----------|-------------|
| [le_mans_2026.ipynb](examples/le_mans_2026.ipynb) | Full race analysis — results, pace, stints, plots |
| [driver_career.ipynb](examples/driver_career.ipynb) | Cross-series career analysis for a driver |

---

## License

MIT — data sourced from Al Kamel Systems public timing exports.  
Not affiliated with ACO, FIA, or any racing organization.
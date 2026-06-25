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

# Configure — public endpoints work without a key
# Request a free key at https://openwec.com/api-keys
openwec.configure(
    base_url="https://api.openwec.com/api/v1",
    api_key="your-key-here"  # required for laps and analytics
)

# Load any session
session = openwec.Session("WEC", 2026, "Le Mans", "Race")
print(session)
# Session(WEC 2026 LE MANS — Race, id=6556)

# Results as a DataFrame (no key needed)
results = session.results()
print(results[["position", "car_number", "car_class", "team", "drivers"]].head(10))

# Lap-by-lap data (key required)
laps = session.laps(car="7")
print(laps[["lap_number", "lap_time_s", "s1_s", "s2_s", "s3_s"]].head())

# Stints and pace (key required)
stints = session.stints(car_class="HYPERCAR")
pace   = session.pace(car_class="HYPERCAR")

# Pit window estimate
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

**1.77M+ laps** across all series.

---

## API key

Public endpoints (results, driver profiles, team profiles) require no key.  
Lap-by-lap data and analytics endpoints require a free API key.

→ **[Request a key at openwec.com/api-keys](https://openwec.com/api-keys)**

---

## Session methods

| Method | Returns | Key required |
|--------|---------|-------------|
| `.results()` | DataFrame | No |
| `.laps(car=None)` | DataFrame | Yes |
| `.stints(car_class=None)` | DataFrame | Yes |
| `.pace(car_class=None)` | DataFrame | Yes |
| `.gaps(car_class=None)` | DataFrame | Yes |
| `.pit_window(car=None)` | DataFrame | Yes |
| `.plot_lap_evolution(car)` | Figure | Yes |
| `.plot_stint_chart()` | Figure | Yes |
| `.plot_gap_to_leader()` | Figure | Yes |

---

## License

MIT — data sourced from Al Kamel Systems public timing exports.  
Not affiliated with ACO, FIA, or any racing organization.
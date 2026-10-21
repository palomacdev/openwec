# Data Policy

This document explains where OpenWEC data comes from, how it is processed,
what has been normalized or computed, and where inconsistencies may exist.

---

## Data origin

All timing data in OpenWEC is sourced from **Al Kamel Systems** — the official
timing provider for the FIA World Endurance Championship, European Le Mans Series,
Asian Le Mans Series, Michelin Le Mans Cup, and IMSA WeatherTech SportsCar Championship.

Al Kamel publishes CSV exports at the end of each session (or periodically during
longer races). These exports are publicly accessible via the timing portals of each series:

- **WEC:** `fiawec.alkamelsystems.com`
- **ELMS:** `europeanlemansseries.alkamelsystems.com`
- **ALMS:** `alms.alkamelsystems.com`
- **Le Mans Cup:** `lemanscup.alkamelsystems.com`
- **IMSA:** `imsa.alkamelsystems.com`

OpenWEC is **not affiliated** with Al Kamel Systems, ACO, FIA, or IMSA.
Data is used under fair use for non-commercial, educational purposes.

---

## What is raw (unchanged)

The following fields are taken directly from the source CSV with no modification:

- Lap times (seconds, from CSV `LAP_TIME` column, converted from `M:SS.mmm`)
- Sector times (S1, S2, S3) — converted from `M:SS.mmm` to seconds
- Top speed (KPH)
- Pit time (seconds, when available)
- Flag at finish line (`GF`, `FCY`, `SC`, `Other`)
- Lap number
- Driver slot (which driver was driving, 1–5)
- Car number
- Vehicle model name
- Tyre code (`M`, `C`, `G`, `D`, `P`)

---

## What is normalized

### Driver names

Raw CSV data contains driver names in several inconsistent formats:
- ALL-CAPS last names (e.g. `ALBUQUERQUE` → `Albuquerque`)
- Compound name particles split incorrectly (e.g. `"Roman DE" / "ANGELIS"` → `"Roman" / "De Angelis"`)
- Accented vs non-accented variants (e.g. `René` vs `Rene`)

OpenWEC applies rule-based normalization and merges duplicate driver entries
that represent the same person across series (e.g. a driver appearing in both
WEC and IMSA records). Ambiguous merges are reviewed manually before being applied.

### Team names

Team names vary by capitalisation, abbreviation, and year:
- `AF CORSE`, `Af Corse`, `af corse` → `AF Corse`
- Duplicate team entries for the same team are merged using a
  clean key (lowercase, alphanumeric only) to find matches.

### Tyre suppliers

Raw tyre codes are mapped to supplier names:

| Code | Supplier |
|------|----------|
| M | Michelin |
| C | Continental |
| G | Goodyear |
| D | Dunlop |
| P | Pirelli |

---

## What is computed

### Stint detection

Stints are detected from the `crossing_finish_in_pit` flag in the lap data.
A lap with this flag set is treated as the last lap of a stint; the next lap
begins a new stint.

**Limitations:**
- If a car retires in the pit lane without completing another lap, the
  last stint may appear truncated.
- In-lap and out-lap identification is approximate (out-laps are excluded
  from pace calculations based on lap number within the stint).

### Baseline pace

Baseline pace per stint is the **median of the first 5 green-flag laps**
of the stint. Laps under FCY or Safety Car are excluded.

This is a deliberate choice — it reflects the car's natural pace at the
start of a fresh tyre set, before degradation accumulates.

### Degradation rate

Degradation is computed as the **slope of a linear regression** of lap time
vs stint lap number, using green-flag laps only. The unit is seconds per lap.

- A positive value means the car is getting slower (typical tyre degradation).
- A negative value means the car is getting faster (common in the early laps
  of a stint as tyres come up to temperature, or during improving track conditions).

**Limitation:** degradation is only meaningful for stints with at least 5
green-flag laps. Shorter stints return `null`.

### Pit window

The pit window estimator computes the **break-even lap** — the point at which
the accumulated degradation equals the time lost in a pit stop:

```
break_even = pit_loss_s / degradation_s_per_lap
```

Default pit loss times by class (seconds):

| Class | Default pit_loss_s |
|-------|--------------------|
| HYPERCAR | 28 |
| LMP1 / GTP | 28 |
| LMP2 | 22 |
| LMGT3 / GT3 / GTD | 22 |
| LMGTE Pro / LMGTE Am | 24 |
| DPi | 26 |

These are conservative estimates. Actual pit loss depends on pit lane length,
which varies by circuit. Users can override via the `pit_loss_s` parameter.

### Race control periods (SC/FCY)

SC and FCY periods are detected by computing the **mode of `flag_at_fl`**
across all cars for each lap. If the majority of cars report a non-green flag
on a given lap, that lap is classified as a caution period.

**Limitation:** this approach may misclassify isolated incidents (one car
reporting a different flag from the field) as track-wide caution periods.
In practice, this is rare and affects individual laps, not extended periods.

---

## Known limitations and inconsistencies

### Duplicate rows in `results`

`results` holds more than one row for the same car in the same session.

Re-ingestion by the legacy loader is a proven cause. `cars` has no unique
constraint on any business column, and `upsert_car` runs
`ON CONFLICT DO NOTHING` with no conflict target — so no conflict is ever
possible. Every run inserts a new `cars` row, and `results`, whose uniqueness
is `(session_id, car_id)`, gets a new row with it.

It is not the only cause, and several rows sharing a `(session, car number)`
are not necessarily duplicates of one another. Two distinct competitive entries
can share that key: a session can belong to more than one competition, and the
same car number can be entered in each. `docs/competitive-entry-model.md`
describes the model built to tell those apart.

How many rows share a key depends on how many times the loader ran over a given
session and on which of these causes applies, so it varies row by row and
environment by environment. In the development database the number of rows per
`(session, car number)` ranges from 1 to 394, with a median of 3; about 10% of
them have no companion at all. **This has not been measured in any other
environment.**

There is no verified query that removes these duplicates. Keeping one row per
`(session, car number)` would give the right answer for the large majority of
groups, whose rows agree on everything a reader sees, and the wrong answer for
those where the rows describe different entries.

What this means in practice, in any environment where duplicates are present:

- counting rows in `results` overstates the number of results
- summing or averaging over `results` weights each result by its row count
- grouping by `cars.id` splits one car into several groups

`COUNT(DISTINCT ...)` is not a general fix. Whether it corrects a given metric
depends on what is being counted and on which definition of identity that
metric needs — counting distinct `(session_id, car number)` pairs, for
instance, assumes that pair identifies one entry, which is exactly the
assumption that does not always hold.

### Missing data (pre-2016 WEC)

WEC races from 2012 to approximately 2015 have sessions in the database
but no lap or result data. Al Kamel did not publish digital CSV exports
for this period. These sessions are retained as historical reference.

### IMSA coverage

IMSA data is primarily race classifications. Lap-by-lap analysis files
(`23_Analysis_*.CSV`) are rarely published by IMSA's Al Kamel portal.
Most IMSA lap data in the database comes from the few endurance events
where these files are available (Rolex 24, Sebring 12h, Petit Le Mans).

### Session type misclassification

The session type (`Race`, `Qualifying`, `Practice`, etc.) is inferred
from the session name using keyword matching. Sessions with unusual names
(e.g. `"Qualifying - Race 1"`) may have been misclassified in early data
loads. Known misclassifications have been corrected via SQL migrations.

### Driver nationality coverage

~55% of drivers in the database have a confirmed nationality. The remaining
45% are typically amateur and gentleman drivers competing in GT classes who
do not have Wikipedia pages and could not be enriched via Wikidata.

### Snapshot sessions (Le Mans 24h)

For 24-hour races, Al Kamel publishes classification snapshots at each hour
(Hour 1, Hour 2, ...), as separate files inside the race folder.

The classification loader reads only the final hourly file per race, so these
snapshots do **not** become separate sessions: one race produces one session.
In the development database `snapshot_hour` is set on 3 sessions out of 5,422,
none of them a Le Mans race.

Career statistics filter `snapshot_hour IS NULL`. That filter has its own
meaning — it selects sessions that are not hourly snapshots — and it is not a
deduplication mechanism. It can exclude sessions that are legitimate results in
their own right, and it does not address the duplicate rows described under
[Duplicate rows in `results`](#duplicate-rows-in-results), which have a
different cause.

### Team history across seasons

The same real-world team may appear under different names across seasons
(e.g. sponsorship changes). OpenWEC merges obvious duplicates via clean-key
matching, but long-form team histories that changed names may remain split.

---

## Data refresh

The database is updated after each race weekend. Historical data is not
retroactively modified unless a known error is identified and reported.

To report a data error, open a [Data Issue](https://github.com/palomacdev/openwec/issues/new?template=data_issue.md)
on GitHub with a reference to the official timing source.

---

## License

Data sourced from Al Kamel Systems public timing exports.  
OpenWEC code is MIT licensed — see [LICENSE](LICENSE).  
Not affiliated with ACO, FIA, IMSA, or Al Kamel Systems.
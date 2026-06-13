"""
OpenWEC SDK — Session
The main object-style interface, inspired by FastF1.

Usage:
    import openwec

    session = openwec.Session("WEC", 2024, "Le Mans", "Race")
    results = session.results()
    laps    = session.laps()
    laps_50 = session.laps(car="50")
"""

from __future__ import annotations
import pandas as pd

from .client import _get, OpenWECNotFoundError


class Session:
    """
    Represents a single session (e.g. WEC 2024 Le Mans Race).

    Resolution is done by matching series/season/event/session names
    against the OpenWEC catalog — matching is case-insensitive and
    substring-based for event and session names.

    Args:
        series:  series key, e.g. "WEC", "ELMS", "IMSA"
        year:    season year, e.g. 2024
        event:   event name or substring, e.g. "Le Mans"
        session: session name or substring, e.g. "Race", "Qualifying"
    """

    def __init__(self, series: str, year: int, event: str, session: str):
        self.series_key   = series.upper()
        self.year         = year
        self.event_query  = event
        self.session_query = session

        self._session_id: int | None = None
        self._event_id:   int | None = None
        self._event_name: str | None = None
        self._session_name: str | None = None
        self._session_type: str | None = None
        self._session_at:  str | None = None

        self._results_cache: pd.DataFrame | None = None
        self._laps_cache:    dict[str | None, pd.DataFrame] = {}

        self._resolve()

    # ── Resolution ────────────────────────────────────────────

    def _resolve(self):
        """Resolves series/year/event/session to a session_id via the API."""

        # 1. Find the event
        events = _get(f"/series/{self.series_key}/seasons/{self.year}/events")
        if not isinstance(events, list):
            raise OpenWECNotFoundError(f"No events found for {self.series_key} {self.year}")

        event_match = _find_best_match(events, "name", self.event_query)
        if not event_match:
            available = ", ".join(e["name"] for e in events)
            raise OpenWECNotFoundError(
                f"No event matching '{self.event_query}' in {self.series_key} {self.year}. "
                f"Available: {available}"
            )

        self._event_id   = event_match["id"]
        self._event_name = event_match["name"]

        # 2. Find the session within that event
        sessions = _get(
            f"/series/{self.series_key}/seasons/{self.year}/events/{self._event_id}/sessions"
        )
        if not isinstance(sessions, list):
            raise OpenWECNotFoundError(f"No sessions found for event {self._event_name}")

        session_match = _find_best_match(sessions, "name", self.session_query)
        if not session_match:
            available = ", ".join(s["name"] for s in sessions)
            raise OpenWECNotFoundError(
                f"No session matching '{self.session_query}' in {self._event_name}. "
                f"Available: {available}"
            )

        self._session_id   = session_match["id"]
        self._session_name = session_match["name"]
        self._session_type = session_match["session_type"]
        self._session_at   = session_match.get("session_at")

    # ── Properties ────────────────────────────────────────────

    @property
    def id(self) -> int:
        """The resolved session_id."""
        return self._session_id

    @property
    def event_name(self) -> str:
        return self._event_name

    @property
    def name(self) -> str:
        return self._session_name

    @property
    def session_type(self) -> str:
        return self._session_type

    def __repr__(self) -> str:
        return (
            f"Session({self.series_key} {self.year} "
            f"{self._event_name} — {self._session_name}, id={self._session_id})"
        )

    # ── Data ──────────────────────────────────────────────────

    def results(self) -> pd.DataFrame:
        """
        Returns the final classification as a DataFrame.
        One row per car, sorted by position.
        Public endpoint — no API key required.
        """
        if self._results_cache is not None:
            return self._results_cache

        data = _get(f"/sessions/{self._session_id}/results")
        df = pd.json_normalize(data)

        # Flatten drivers list into a readable column
        if "drivers" in df.columns:
            df["drivers"] = df["drivers"].apply(_format_drivers)

        self._results_cache = df
        return df

    def laps(self, car: str | None = None) -> pd.DataFrame:
        """
        Returns lap-by-lap data as a DataFrame.
        Requires API key — call openwec.configure(api_key=...) first.

        Args:
            car: optional car number to filter (e.g. "50").
                 If None, returns all laps (paginated automatically).
        """
        cache_key = car
        if cache_key in self._laps_cache:
            return self._laps_cache[cache_key]

        if car:
            data = _get(f"/sessions/{self._session_id}/laps/{car}")
            df = pd.DataFrame(data)
        else:
            # Paginate through all laps
            all_rows = []
            page = 1
            while True:
                resp = _get(f"/sessions/{self._session_id}/laps", params={
                    "page": page, "page_size": 500
                })
                rows = resp.get("results", [])
                all_rows.extend(rows)
                if len(rows) < 500:
                    break
                page += 1
            df = pd.DataFrame(all_rows)

        self._laps_cache[cache_key] = df
        return df

    # ── Analytics ─────────────────────────────────────────────

    def stints(self, car: str | None = None, car_class: str | None = None) -> pd.DataFrame:
        """
        Returns stint breakdown (baseline pace, degradation, consistency) per car.
        Requires API key.
        """
        params = {}
        if car:
            params["car"] = car
        if car_class:
            params["car_class"] = car_class
        data = _get(f"/sessions/{self._session_id}/stints", params=params)
        return pd.DataFrame(data)

    def pace(self, car_class: str | None = None) -> pd.DataFrame:
        """
        Returns average green-flag pace per car, sorted fastest first.
        Requires API key.
        """
        params = {}
        if car_class:
            params["car_class"] = car_class
        data = _get(f"/sessions/{self._session_id}/pace", params=params)
        return pd.DataFrame(data)

    def gaps(self, car: str | None = None, car_class: str | None = None,
             max_laps: int = 50) -> pd.DataFrame:
        """
        Returns cumulative lap time evolution — useful for gap-to-leader charts.
        Requires API key.
        """
        params = {"max_laps": max_laps}
        if car:
            params["car"] = car
        if car_class:
            params["car_class"] = car_class
        data = _get(f"/sessions/{self._session_id}/gaps", params=params)
        return pd.DataFrame(data)

    def pit_window(self, car: str | None = None, car_class: str | None = None,
                    pit_loss_s: float | None = None) -> pd.DataFrame:
        """
        Returns estimated optimal pit window per stint per car.
        Requires API key.
        """
        params = {}
        if car:
            params["car"] = car
        if car_class:
            params["car_class"] = car_class
        if pit_loss_s:
            params["pit_loss_s"] = pit_loss_s

        data = _get(f"/sessions/{self._session_id}/pit-window", params=params)

        # Flatten nested stints into rows
        rows = []
        for car_data in data:
            for stint in car_data["stints"]:
                rows.append({
                    "car_number": car_data["car_number"],
                    "car_class":  car_data["car_class"],
                    "team":       car_data["team"],
                    "pit_loss_s": car_data["pit_loss_s"],
                    **stint,
                })
        return pd.DataFrame(rows)

    # ── Plots ─────────────────────────────────────────────────

    def plot_lap_evolution(self, car: str, ax=None):
        """
        Plots lap time evolution for a car. Requires matplotlib.
        Requires API key (uses session.laps()).
        """
        from . import plotting
        laps = self.laps(car=car)
        return plotting.plot_lap_evolution(laps, car=car, ax=ax)

    def plot_stint_chart(self, car_class: str | None = None, ax=None):
        """
        Plots a stint chart (strategy overview) for all cars.
        Requires matplotlib and API key.
        """
        from . import plotting
        stints = self.stints(car_class=car_class)
        return plotting.plot_stint_chart(stints, ax=ax)

    def plot_gap_to_leader(self, car_class: str | None = None, max_laps: int = 50, ax=None):
        """
        Plots gap to leader over race distance.
        Requires matplotlib and API key.
        """
        from . import plotting
        gaps = self.gaps(car_class=car_class, max_laps=max_laps)
        return plotting.plot_gap_to_leader(gaps, ax=ax)


# ── Helpers ───────────────────────────────────────────────────

def _find_best_match(items: list[dict], key: str, query: str) -> dict | None:
    """
    Finds the best match for `query` against `item[key]`.
    Priority: exact match (case-insensitive) > substring match.
    """
    query_lower = query.strip().lower()

    # Exact match first
    for item in items:
        if item[key].strip().lower() == query_lower:
            return item

    # Substring match
    for item in items:
        if query_lower in item[key].strip().lower():
            return item

    return None


def _format_drivers(drivers: list[dict]) -> str:
    """Formats a list of driver dicts into 'First Last / First Last' string."""
    if not drivers:
        return ""
    names = []
    for d in sorted(drivers, key=lambda x: x.get("slot", 0)):
        first = d.get("first_name", "")
        last  = d.get("last_name", "")
        names.append(f"{first} {last}".strip())
    return " / ".join(names)
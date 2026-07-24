"""
OpenWEC SDK — Session
The main object-style interface, inspired by FastF1.

Example:
    >>> import openwec
    >>> session = openwec.Session("WEC", 2026, "Le Mans", "Race")
    >>> print(session)
    Session(WEC 2026 LE MANS — Race, id=6556)
    >>> results = session.results()
    >>> laps = session.laps(car="7")
"""

from __future__ import annotations
import pandas as pd

from .client import _get, OpenWECNotFoundError


class Session:
    """
    Represents a single timing session from the OpenWEC database.

    Resolves series/season/event/session names to a unique session_id
    via the OpenWEC API. Matching is case-insensitive and substring-based
    for event and session names, so ``"Le Mans"`` matches ``"LE MANS"`` and
    ``"Race"`` matches the first session whose name contains "race".

    Args:
        series: Series key. One of ``"WEC"``, ``"ELMS"``, ``"ALMS"``,
            ``"LEMANSCUP"``, ``"IMSA"``.
        year: Season year as an integer, e.g. ``2026``.
        event: Event name or substring, e.g. ``"Le Mans"``, ``"Spa"``.
        session: Session name or substring, e.g. ``"Race"``, ``"Qualifying"``,
            ``"Race 1"``, ``"Hyperpole"``.

    Raises:
        OpenWECNotFoundError: If no matching event or session is found.
        OpenWECAuthError: If a protected endpoint is accessed without an API key.

    Example:
        >>> import openwec
        >>> session = openwec.Session("WEC", 2026, "Le Mans", "Race")
        >>> print(session)
        Session(WEC 2026 LE MANS — Race, id=6556)

        >>> # With API key for protected endpoints
        >>> openwec.configure(api_key="owec_...")
        >>> laps = session.laps(car="7")
    """

    def __init__(self, series: str, year: int, event: str, session: str):
        self.series_key    = series.upper()
        self.year          = year
        self.event_query   = event
        self.session_query = session

        self._session_id:   int | None = None
        self._event_id:     int | None = None
        self._event_name:   str | None = None
        self._session_name: str | None = None
        self._session_type: str | None = None
        self._session_at:   str | None = None

        self._results_cache: pd.DataFrame | None = None
        self._laps_cache:    dict[str | None, pd.DataFrame] = {}

        self._resolve()

    # ── Resolution ────────────────────────────────────────────

    def _resolve(self):
        """Resolves series/year/event/session to a session_id via the API."""
        events = _get(f"/series/{self.series_key}/seasons/{self.year}/events")
        if not isinstance(events, list):
            raise OpenWECNotFoundError(
                f"No events found for {self.series_key} {self.year}"
            )

        event_match = _find_best_match(events, "name", self.event_query)
        if not event_match:
            available = ", ".join(e["name"] for e in events)
            raise OpenWECNotFoundError(
                f"No event matching '{self.event_query}' in "
                f"{self.series_key} {self.year}. Available: {available}"
            )

        self._event_id   = event_match["id"]
        self._event_name = event_match["name"]

        sessions = _get(
            f"/series/{self.series_key}/seasons/{self.year}"
            f"/events/{self._event_id}/sessions"
        )
        if not isinstance(sessions, list):
            raise OpenWECNotFoundError(
                f"No sessions found for event {self._event_name}"
            )

        session_match = _find_best_match(sessions, "name", self.session_query)
        if not session_match:
            available = ", ".join(s["name"] for s in sessions)
            raise OpenWECNotFoundError(
                f"No session matching '{self.session_query}' in "
                f"{self._event_name}. Available: {available}"
            )

        self._session_id   = session_match["id"]
        self._session_name = session_match["name"]
        self._session_type = session_match["session_type"]
        self._session_at   = session_match.get("session_at")

    # ── Properties ────────────────────────────────────────────

    @property
    def id(self) -> int:
        """The resolved internal session ID."""
        return self._session_id

    @property
    def event_name(self) -> str:
        """The resolved event name as stored in the database, e.g. ``"LE MANS"``."""
        return self._event_name

    @property
    def name(self) -> str:
        """The resolved session name, e.g. ``"Race"``, ``"Qualifying"``."""
        return self._session_name

    @property
    def session_type(self) -> str:
        """The session type: ``"Race"``, ``"Qualifying"``, ``"Practice"``, etc."""
        return self._session_type

    def __repr__(self) -> str:
        return (
            f"Session({self.series_key} {self.year} "
            f"{self._event_name} — {self._session_name}, id={self._session_id})"
        )

    # ── Data ──────────────────────────────────────────────────

    def results(self) -> pd.DataFrame:
        """
        Return the final classification as a DataFrame.

        One row per car, sorted by finishing position. Results are cached
        after the first call — subsequent calls return instantly.

        Returns:
            DataFrame with columns:

            - ``position`` (int): Finishing position.
            - ``car_number`` (str): Car number as string, e.g. ``"7"``.
            - ``car_class`` (str): Class, e.g. ``"HYPERCAR"``, ``"LMGT3"``.
            - ``vehicle`` (str): Car model name.
            - ``team`` (str): Team name.
            - ``tyre_supplier`` (str): Tyre supplier name, e.g. ``"Michelin"``.
            - ``status`` (str): ``"Classified"``, ``"Not Classified"``, etc.
            - ``laps_completed`` (int): Total laps completed.
            - ``total_time_s`` (float): Total race time in seconds.
            - ``gap_to_first_s`` (float | None): Gap to leader in seconds.
            - ``fl_lap_number`` (int): Lap number of fastest lap.
            - ``fl_time_s`` (float): Fastest lap time in seconds.
            - ``fl_kph`` (float): Fastest lap average speed (km/h).
            - ``drivers`` (str): Driver names formatted as ``"First Last / First Last"``.

        Note:
            This is a public endpoint — no API key required.

        Example:
            >>> session = openwec.Session("WEC", 2026, "Le Mans", "Race")
            >>> df = session.results()
            >>> print(df[["position", "car_number", "team"]].head())
        """
        if self._results_cache is not None:
            return self._results_cache

        data = _get(f"/sessions/{self._session_id}/results")
        df = pd.json_normalize(data)

        if "drivers" in df.columns:
            df["drivers"] = df["drivers"].apply(_format_drivers)

        self._results_cache = df
        return df

    def laps(self, car: str | None = None) -> pd.DataFrame:
        """
        Return lap-by-lap timing data as a DataFrame.

        Lap data is cached per car number after the first call.

        Args:
            car: Car number to filter, e.g. ``"7"`` or ``"50"``.
                If ``None``, returns all laps for all cars (paginated
                automatically, may take a few seconds for long races).

        Returns:
            DataFrame with columns:

            - ``lap_number`` (int): Lap number within the session.
            - ``lap_time_s`` (float | None): Lap time in seconds.
            - ``s1_s``, ``s2_s``, ``s3_s`` (float | None): Sector times in seconds.
            - ``kph`` (float | None): Average lap speed (km/h).
            - ``top_speed_kph`` (float | None): Top speed recorded on the lap.
            - ``crossing_finish_in_pit`` (bool): ``True`` if the car pitted this lap.
            - ``flag_at_fl`` (str | None): Flag status when crossing the line
              (``"GF"`` = green flag, ``"FCY"``, ``"SC"``).
            - ``pit_time_s`` (float | None): Time spent stationary in pit lane.
            - ``driver_name`` (str | None): Name of the driver on this lap.
            - ``car_number`` (str): Car number.

        Raises:
            OpenWECAuthError: If no API key is configured.
                Call ``openwec.configure(api_key="owec_...")`` first.
            OpenWECNotFoundError: If the session has no lap data.

        Example:
            >>> session = openwec.Session("WEC", 2026, "Le Mans", "Race")
            >>> laps = session.laps(car="7")
            >>> green = laps[laps["flag_at_fl"] == "GF"]
            >>> print(f"Best lap: {green['lap_time_s'].min():.3f}s")
        """
        cache_key = car
        if cache_key in self._laps_cache:
            return self._laps_cache[cache_key]

        if car:
            data = _get(f"/sessions/{self._session_id}/laps/{car}")
            df = pd.DataFrame(data)
        else:
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

    def driver_laps(self, driver_name: str, car: str | None = None) -> pd.DataFrame:
        """
        Return lap-by-lap data for a specific driver within the session.

        Filters laps by driver name using a case-insensitive partial match.
        In endurance racing, multiple drivers share a car — this method lets
        you isolate one driver's stints from the shared-drive data.

        Args:
            driver_name: Driver name or partial name to filter by, e.g.
                ``"Conway"``, ``"Mike Conway"``, ``"CONWAY"``.
                Matching is case-insensitive and substring-based.
            car: Optional car number to filter. If ``None``, searches across
                all cars in the session (slower for long races).

        Returns:
            DataFrame with the same columns as :meth:`laps`, filtered to
            laps where ``driver_name`` appears in the ``driver_name`` column.

        Raises:
            OpenWECAuthError: If no API key is configured.
            ValueError: If no laps are found matching the driver name.

        Note:
            This method is unique to endurance racing — it has no equivalent
            in FastF1 because Formula 1 cars have a single driver per race.

        Example:
            >>> session = openwec.Session("WEC", 2026, "Le Mans", "Race")
            >>> conway_laps = session.driver_laps("Conway", car="7")
            >>> print(f"Conway drove {len(conway_laps)} laps")
            >>>
            >>> # Compare two drivers in the same car
            >>> kobayashi_laps = session.driver_laps("Kobayashi", car="7")
            >>> print(f"Pace: Conway {conway_laps['lap_time_s'].median():.3f}s "
            ...       f"vs Kobayashi {kobayashi_laps['lap_time_s'].median():.3f}s")
        """
        all_laps = self.laps(car=car)

        if all_laps.empty or "driver_name" not in all_laps.columns:
            raise ValueError("No lap data available for this session.")

        mask = all_laps["driver_name"].str.contains(
            driver_name, case=False, na=False
        )
        result = all_laps[mask].copy()

        if result.empty:
            raise ValueError(
                f"No laps found for driver matching '{driver_name}'. "
                f"Available drivers: {', '.join(all_laps['driver_name'].dropna().unique()[:10])}"
            )

        return result

    def stints(self, car: str | None = None,
               car_class: str | None = None) -> pd.DataFrame:
        """
        Return stint breakdown per car.

        Stints are detected from pit lap flags. Each row represents a single
        stint — a continuous run between pit stops.

        Args:
            car: Filter by car number, e.g. ``"7"``.
            car_class: Filter by class, e.g. ``"HYPERCAR"``, ``"LMP2"``,
                ``"LMGT3"``.

        Returns:
            DataFrame with columns:

            - ``car_number``, ``car_class``, ``team`` (str): Car identifiers.
            - ``stint_number`` (int): Stint index, starting from 1.
            - ``start_lap``, ``end_lap`` (int): First and last lap of the stint.
            - ``lap_count`` (int): Total laps in the stint.
            - ``tyre_age_laps`` (int): Laps completed on the current tyre set.
            - ``baseline_pace_s`` (float | None): Median of first 5 green-flag
              lap times in the stint (seconds).
            - ``degradation_s_per_lap`` (float | None): Slope of linear regression
              of lap time vs stint lap. Positive = getting slower.
            - ``consistency_s`` (float | None): Standard deviation of green-flag
              lap times in the stint (seconds).
            - ``is_final_stint`` (bool): ``True`` if no pit stop follows.

        Raises:
            OpenWECAuthError: If no API key is configured.
            OpenWECNotFoundError: If no analytics data exists for this session.
                Run the analytics engine first.

        Example:
            >>> stints = session.stints(car_class="HYPERCAR")
            >>> print(stints[["car_number", "stint_number",
            ...                "baseline_pace_s", "degradation_s_per_lap"]])
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
        Return average green-flag pace per car, sorted fastest first.

        Args:
            car_class: Filter by class, e.g. ``"HYPERCAR"``.

        Returns:
            DataFrame with columns:

            - ``car_number``, ``car_class``, ``team`` (str): Car identifiers.
            - ``total_laps`` (int): Total laps in the session.
            - ``green_flag_laps`` (int): Laps under green flag conditions.
            - ``pit_stops`` (int): Number of pit stops.
            - ``best_lap_s`` (float | None): Fastest single lap in seconds.
            - ``avg_pace_s`` (float | None): Mean green-flag lap time in seconds.
            - ``consistency_s`` (float | None): Std dev of green-flag lap times.

        Raises:
            OpenWECAuthError: If no API key is configured.

        Example:
            >>> pace = session.pace(car_class="HYPERCAR")
            >>> print(pace[["car_number", "team", "avg_pace_s"]].head())
        """
        params = {}
        if car_class:
            params["car_class"] = car_class
        data = _get(f"/sessions/{self._session_id}/pace", params=params)
        return pd.DataFrame(data)

    def gaps(self, car: str | None = None,
             car_class: str | None = None,
             max_laps: int = 50) -> pd.DataFrame:
        """
        Return cumulative lap time evolution for gap-to-leader analysis.

        Args:
            car: Filter by car number.
            car_class: Filter by class.
            max_laps: Maximum number of laps to return per car. Defaults to 50.

        Returns:
            DataFrame with columns:

            - ``lap_number`` (int): Lap number within the session.
            - ``car_number``, ``car_class`` (str): Car identifiers.
            - ``lap_time_s`` (float | None): Individual lap time in seconds.
            - ``cumulative_s`` (float | None): Cumulative race time in seconds.
              Subtract the minimum ``cumulative_s`` per lap to get gap to leader.

        Raises:
            OpenWECAuthError: If no API key is configured.

        Example:
            >>> gaps = session.gaps(car_class="HYPERCAR", max_laps=60)
            >>> # Compute gap to leader manually
            >>> leader = gaps.groupby("lap_number")["cumulative_s"].min()
            >>> gaps["gap_s"] = gaps.set_index("lap_number")["cumulative_s"] - leader
        """
        params = {"max_laps": max_laps}
        if car:
            params["car"] = car
        if car_class:
            params["car_class"] = car_class
        data = _get(f"/sessions/{self._session_id}/gaps", params=params)
        return pd.DataFrame(data)

    def pit_window(self, car: str | None = None,
                   car_class: str | None = None,
                   pit_loss_s: float | None = None) -> pd.DataFrame:
        """
        Return estimated optimal pit window per stint per car.

        The pit window is computed from the degradation rate vs pit loss time:
        ``break_even = pit_loss_s / degradation_s_per_lap``.
        The optimal window is ``[break_even * 0.85, break_even, break_even * 1.10]``.

        Args:
            car: Filter by car number.
            car_class: Filter by class.
            pit_loss_s: Override the default pit loss time in seconds.
                Defaults vary by class (HYPERCAR: 28s, LMP2/LMGT3: 22s).

        Returns:
            DataFrame with one row per stint per car, including:

            - ``car_number``, ``car_class``, ``team``, ``pit_loss_s``: Car info.
            - ``stint_number``, ``start_lap``, ``end_lap``, ``tyre_age_laps``: Stint info.
            - ``baseline_pace_s``, ``degradation_s_per_lap``: Pace metrics.
            - ``early_lap``, ``ideal_lap``, ``late_lap``: Optimal window relative
              to stint start.
            - ``early_lap_abs``, ``ideal_lap_abs``, ``late_lap_abs``: Same values
              as absolute lap numbers in the session.
            - ``recommendation`` (str): Human-readable pit window summary.

        Raises:
            OpenWECAuthError: If no API key is configured.

        Example:
            >>> pw = session.pit_window(car="7")
            >>> print(pw[["stint_number", "ideal_lap_abs", "recommendation"]])
        """
        params = {}
        if car:
            params["car"] = car
        if car_class:
            params["car_class"] = car_class
        if pit_loss_s:
            params["pit_loss_s"] = pit_loss_s

        data = _get(f"/sessions/{self._session_id}/pit-window", params=params)

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
        Plot lap time evolution for a single car over the session.

        Pit laps are marked with red X markers. Slow laps (> 600s,
        typically formation or SC laps) are excluded from the plot.

        Args:
            car: Car number to plot, e.g. ``"7"``.
            ax: Optional matplotlib ``Axes`` to draw on. If ``None``,
                a new figure is created.

        Returns:
            matplotlib ``Figure`` object. Call ``plt.show()`` or
            ``fig.savefig("lap_evolution.png")`` to display/save.

        Raises:
            ImportError: If matplotlib is not installed.
                Run ``pip install openwec[plotting]``.
            OpenWECAuthError: If no API key is configured.

        Example:
            >>> import matplotlib.pyplot as plt
            >>> fig = session.plot_lap_evolution(car="7")
            >>> plt.show()
        """
        from . import plotting
        laps = self.laps(car=car)
        return plotting.plot_lap_evolution(laps, car=car, ax=ax)

    def plot_stint_chart(self, car_class: str | None = None, ax=None):
        """
        Plot a horizontal stint/strategy chart for all cars.

        Each row is a car, each colored bar is a stint. Colors cycle
        through stint numbers (stint 1 = amber, stint 2 = blue, etc.).

        Args:
            car_class: Filter to one class, e.g. ``"HYPERCAR"``.
                If ``None``, all classes are shown (may be crowded).
            ax: Optional matplotlib ``Axes``.

        Returns:
            matplotlib ``Figure`` object.

        Raises:
            ImportError: If matplotlib is not installed.
            OpenWECAuthError: If no API key is configured.

        Example:
            >>> fig = session.plot_stint_chart(car_class="HYPERCAR")
            >>> fig.savefig("strategy.png", dpi=150, bbox_inches="tight")
        """
        from . import plotting
        stints = self.stints(car_class=car_class)
        return plotting.plot_stint_chart(stints, ax=ax)

    def plot_gap_to_leader(self, car_class: str | None = None,
                           max_laps: int = 50, ax=None):
        """
        Plot gap to leader over race distance.

        Each line represents one car. Gap is computed as the difference
        between the car's cumulative lap time and the leader's cumulative
        lap time at each lap.

        Args:
            car_class: Filter to one class.
            max_laps: Number of laps to plot. Defaults to 50.
            ax: Optional matplotlib ``Axes``.

        Returns:
            matplotlib ``Figure`` object.

        Raises:
            ImportError: If matplotlib is not installed.
            OpenWECAuthError: If no API key is configured.

        Example:
            >>> fig = session.plot_gap_to_leader(car_class="HYPERCAR", max_laps=60)
            >>> plt.show()
        """
        from . import plotting
        gaps = self.gaps(car_class=car_class, max_laps=max_laps)
        return plotting.plot_gap_to_leader(gaps, ax=ax)


# ── Helpers ───────────────────────────────────────────────────

def _find_best_match(items: list[dict], key: str, query: str) -> dict | None:
    """
    Find the best matching item from a list by comparing ``item[key]`` to ``query``.

    Priority:
        1. Exact match (case-insensitive)
        2. Substring match (``query`` appears anywhere in ``item[key]``)

    Args:
        items: List of dicts to search.
        key: The dict key to match against.
        query: The search string.

    Returns:
        The best matching item, or ``None`` if no match found.
    """
    query_lower = query.strip().lower()

    for item in items:
        if item[key].strip().lower() == query_lower:
            return item

    for item in items:
        if query_lower in item[key].strip().lower():
            return item

    return None


def _format_drivers(drivers: list[dict]) -> str:
    """
    Format a list of driver dicts as a readable string.

    Args:
        drivers: List of driver dicts with ``first_name``, ``last_name``,
            and ``slot`` keys.

    Returns:
        Formatted string, e.g. ``"Mike Conway / Kamui Kobayashi / Jose Gutierrez"``.
    """
    if not drivers:
        return ""
    names = []
    for d in sorted(drivers, key=lambda x: x.get("slot", 0)):
        first = d.get("first_name", "")
        last  = d.get("last_name", "")
        names.append(f"{first} {last}".strip())
    return " / ".join(names)
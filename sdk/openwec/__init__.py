"""
OpenWEC SDK — Alpha

A FastF1-style Python interface for endurance racing data
(WEC, ELMS, ALMS, Le Mans Cup, IMSA).

Usage:
    import openwec

    # Optional: configure API URL and key
    openwec.configure(base_url="http://localhost:8000/api/v1", api_key="your-key")

    # Load a session
    session = openwec.Session("WEC", 2024, "Le Mans", "Race")

    # Results (public, no API key needed)
    results = session.results()
    print(results.head())

    # Laps (requires API key)
    laps = session.laps()
    laps_50 = session.laps(car="50")

    # Analytics (requires API key)
    stints = session.stints()
    pace   = session.pace()

    # Plots (requires matplotlib + API key)
    session.plot_lap_evolution(car="50")
    session.plot_stint_chart()
    session.plot_gap_to_leader()
"""

from .client import configure, OpenWECError, OpenWECNotFoundError, OpenWECAuthError
from .session import Session

__version__ = "0.1.0-alpha"

__all__ = [
    "Session",
    "configure",
    "OpenWECError",
    "OpenWECNotFoundError",
    "OpenWECAuthError",
]
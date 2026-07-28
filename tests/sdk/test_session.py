"""
OpenWEC — SDK Tests
Tests for the openwec Python SDK using respx to mock HTTP calls.

Run:
    pytest tests/sdk/ -v

Requirements:
    pip install pytest respx
"""

import pytest
import respx
import httpx
import pandas as pd

import openwec
from openwec.client import _config, OpenWECNotFoundError, OpenWECAuthError


# ── Fixtures & helpers ────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_config():
    """Reset SDK config before each test."""
    _config.base_url = "https://api.openwec.com/api/v1"
    _config.api_key  = None
    yield
    _config.base_url = "https://api.openwec.com/api/v1"
    _config.api_key  = None


MOCK_SERIES = [
    {"id": 1, "key": "WEC",  "name": "FIA World Endurance Championship"},
    {"id": 2, "key": "ELMS", "name": "European Le Mans Series"},
]

MOCK_SEASONS = [
    {"id": 100, "raw_id": "15_2026", "year": 2026, "label": "2026"},
    {"id": 99,  "raw_id": "14_2025", "year": 2025, "label": "2025"},
]

MOCK_EVENTS = [
    {"id": 621, "raw_id": "03_LE MANS", "name": "LE MANS", "round": 3},
    {"id": 620, "raw_id": "02_SPA",     "name": "SPA FRANCORCHAMPS", "round": 2},
]

MOCK_SESSIONS = [
    {"id": 6556, "raw_id": "20260613_Race", "name": "Race",
     "session_type": "Race", "session_at": "2026-06-14T14:00:00",
     "imsa_series": None, "snapshot_hour": None},
    {"id": 6555, "raw_id": "20260613_WU",   "name": "Warm Up",
     "session_type": "Other", "session_at": "2026-06-14T12:00:00",
     "imsa_series": None, "snapshot_hour": None},
]

MOCK_RESULTS = [
    {
        "position": 1, "car_number": "7", "car_class": "HYPERCAR",
        "vehicle": "Toyota GR010", "team": "Toyota Gazoo Racing",
        "tyre_supplier": "Michelin", "status": "Classified",
        "laps_completed": 381, "total_time_s": 86400.0,
        "gap_to_first_s": None, "fl_lap_number": 200, "fl_time_s": 205.1,
        "fl_kph": 239.0,
        "drivers": [
            {"slot": 1, "first_name": "Mike",  "last_name": "Conway",
             "country": "GBR", "imsa_rating": None},
            {"slot": 2, "first_name": "Kamui", "last_name": "Kobayashi",
             "country": "JPN", "imsa_rating": None},
        ]
    }
]

MOCK_LAPS = [
    {"car_number": "7", "driver_name": "Mike Conway",
     "lap_number": 1, "lap_time_s": 210.5,
     "s1_s": 70.1, "s2_s": 75.2, "s3_s": 65.2,
     "kph": 220.0, "top_speed_kph": 290.0,
     "lap_improvement": False, "crossing_finish_in_pit": False,
     "flag_at_fl": "GF", "pit_time_s": None,
     "elapsed_raw": "3:30.500", "hour_raw": "15:00:00"},
]

BASE = "https://api.openwec.com/api/v1"


# ── configure() ───────────────────────────────────────────────

def test_configure_base_url():
    openwec.configure(base_url="http://localhost:8000/api/v1")
    assert _config.base_url == "http://localhost:8000/api/v1"


def test_configure_api_key():
    openwec.configure(api_key="owec_testkey123")
    assert _config.api_key == "owec_testkey123"


def test_configure_defaults():
    assert _config.base_url == "https://api.openwec.com/api/v1"
    assert _config.api_key is None


# ── Session resolution ────────────────────────────────────────

@respx.mock
def test_session_resolves_correctly():
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )

    session = openwec.Session("WEC", 2026, "Le Mans", "Race")

    assert session.id == 6556
    assert session.event_name == "LE MANS"
    assert session.name == "Race"
    assert session.session_type == "Race"


@respx.mock
def test_session_repr():
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )

    session = openwec.Session("WEC", 2026, "Le Mans", "Race")
    assert "WEC" in repr(session)
    assert "2026" in repr(session)
    assert "6556" in repr(session)


@respx.mock
def test_session_event_not_found():
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )

    with pytest.raises(OpenWECNotFoundError):
        openwec.Session("WEC", 2026, "Monza", "Race")


@respx.mock
def test_session_case_insensitive():
    """Event matching should be case-insensitive."""
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )

    session = openwec.Session("WEC", 2026, "le mans", "race")
    assert session.id == 6556


# ── results() ────────────────────────────────────────────────

@respx.mock
def test_results_returns_dataframe():
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )
    respx.get(f"{BASE}/sessions/6556/results").mock(
        return_value=httpx.Response(200, json=MOCK_RESULTS)
    )

    session = openwec.Session("WEC", 2026, "Le Mans", "Race")
    results = session.results()

    assert isinstance(results, pd.DataFrame)
    assert len(results) == 1
    assert "position" in results.columns
    assert "car_number" in results.columns
    assert "drivers" in results.columns


@respx.mock
def test_results_cached():
    """Second call should not make another HTTP request."""
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )
    results_route = respx.get(f"{BASE}/sessions/6556/results").mock(
        return_value=httpx.Response(200, json=MOCK_RESULTS)
    )

    session = openwec.Session("WEC", 2026, "Le Mans", "Race")
    session.results()
    session.results()  # second call

    assert results_route.call_count == 1  # cached, only one HTTP call


# ── laps() ───────────────────────────────────────────────────

@respx.mock
def test_laps_single_car():
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )
    respx.get(f"{BASE}/sessions/6556/laps/7").mock(
        return_value=httpx.Response(200, json=MOCK_LAPS)
    )

    session = openwec.Session("WEC", 2026, "Le Mans", "Race")
    laps = session.laps(car="7")

    assert isinstance(laps, pd.DataFrame)
    assert len(laps) == 1
    assert "lap_number" in laps.columns
    assert "lap_time_s" in laps.columns
    assert laps.iloc[0]["lap_time_s"] == 210.5


@respx.mock
def test_laps_auth_error():
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )
    respx.get(f"{BASE}/sessions/6556/laps/7").mock(
        return_value=httpx.Response(401, json={"detail": "Missing API key."})
    )

    session = openwec.Session("WEC", 2026, "Le Mans", "Race")

    with pytest.raises(OpenWECAuthError):
        session.laps(car="7")


@respx.mock
def test_driver_laps_filters_by_name():
    """driver_laps() should return only laps for the specified driver."""
    multi_driver_laps = [
        {**MOCK_LAPS[0], "driver_name": "Mike Conway", "lap_number": 1},
        {**MOCK_LAPS[0], "driver_name": "Mike Conway", "lap_number": 2},
        {**MOCK_LAPS[0], "driver_name": "Kamui Kobayashi", "lap_number": 3},
        {**MOCK_LAPS[0], "driver_name": "Kamui Kobayashi", "lap_number": 4},
    ]

    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )
    respx.get(f"{BASE}/sessions/6556/laps/7").mock(
        return_value=httpx.Response(200, json=multi_driver_laps)
    )

    session = openwec.Session("WEC", 2026, "Le Mans", "Race")
    conway_laps = session.driver_laps("Conway", car="7")

    assert len(conway_laps) == 2
    assert all("Conway" in name for name in conway_laps["driver_name"])


@respx.mock
def test_driver_laps_not_found():
    """driver_laps() should raise ValueError if driver not in session."""
    respx.get(f"{BASE}/series/WEC/seasons/2026/events").mock(
        return_value=httpx.Response(200, json=MOCK_EVENTS)
    )
    respx.get(f"{BASE}/series/WEC/seasons/2026/events/621/sessions").mock(
        return_value=httpx.Response(200, json=MOCK_SESSIONS)
    )
    respx.get(f"{BASE}/sessions/6556/laps/7").mock(
        return_value=httpx.Response(200, json=MOCK_LAPS)
    )

    session = openwec.Session("WEC", 2026, "Le Mans", "Race")

    with pytest.raises(ValueError, match="No laps found for driver"):
        session.driver_laps("Albuquerque", car="7")


# ── Exceptions ────────────────────────────────────────────────

def test_version():
    assert openwec.__version__ == "0.2.1"


@respx.mock
def test_not_found_error():
    respx.get(f"{BASE}/series/WEC/seasons/9999/events").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )
    with pytest.raises(OpenWECNotFoundError):
        openwec.Session("WEC", 9999, "Le Mans", "Race")
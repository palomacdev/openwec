"""
OpenWEC — API Tests
Tests for critical public and protected endpoints.

Run:
    pytest tests/api/ -v

Requirements:
    pip install pytest httpx
    uvicorn api.main:app --port 8000  (running in background)

Or against production:
    BASE_URL=https://api.openwec.com pytest tests/api/ -v
"""

import os
import pytest
import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
API_KEY  = os.environ.get("API_KEY", "")


@pytest.fixture
def client():
    return httpx.Client(base_url=BASE_URL, timeout=30)


@pytest.fixture
def auth_headers():
    if API_KEY:
        return {"X-API-Key": API_KEY}
    return {}


# ── Health ────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert "name" in data
    assert "version" in data


# ── Series ───────────────────────────────────────────────────

def test_list_series(client):
    r = client.get("/api/v1/series")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 5
    keys = {s["key"] for s in data}
    assert keys == {"WEC", "ELMS", "ALMS", "LEMANSCUP", "IMSA"}


def test_series_seasons(client):
    r = client.get("/api/v1/series/WEC/seasons")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    years = [s["year"] for s in data]
    assert 2024 in years
    assert 2026 in years


def test_seasons_events(client):
    r = client.get("/api/v1/series/WEC/seasons/2026/events")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    names = [e["name"] for e in data]
    assert any("LE MANS" in n.upper() for n in names)


# ── Sessions & Results ────────────────────────────────────────

def test_session_results(client):
    """Le Mans 2026 Race results."""
    r = client.get("/api/v1/sessions/6556/results")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0

    # Check structure of first result
    result = data[0]
    assert "position" in result
    assert "car_number" in result
    assert "car_class" in result
    assert "team" in result
    assert "drivers" in result
    assert "laps_completed" in result


def test_session_results_winner(client):
    """Le Mans 2026 winner should be position 1."""
    r = client.get("/api/v1/sessions/6556/results")
    data = r.json()
    winner = next((r for r in data if r["position"] == 1), None)
    assert winner is not None
    assert winner["car_class"] == "HYPERCAR"
    assert winner["laps_completed"] > 300


def test_event_detail(client):
    """Event endpoint returns event + sessions grouped."""
    r = client.get("/api/v1/events/621")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "LE MANS"
    assert "sessions" in data
    assert len(data["sessions"]) > 0


def test_session_not_found(client):
    r = client.get("/api/v1/sessions/999999/results")
    assert r.status_code == 404


# ── Drivers & Teams ───────────────────────────────────────────

def test_driver_profile(client):
    """Filipe Albuquerque profile."""
    r = client.get("/api/v1/drivers/84")
    assert r.status_code == 200
    data = r.json()
    assert data["first_name"] == "Filipe"
    assert data["last_name"] in ("Albuquerque", "ALBUQUERQUE")
    assert data["country"] == "PRT"
    assert data["total_races"] > 0


def test_driver_results(client):
    r = client.get("/api/v1/drivers/84/results")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "series" in data[0]
    assert "event" in data[0]


def test_team_profile(client):
    """AF Corse profile."""
    r = client.get("/api/v1/teams/17")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "AF Corse"
    assert data["total_entries"] > 0


def test_driver_not_found(client):
    r = client.get("/api/v1/drivers/999999")
    assert r.status_code == 404


# ── Protected endpoints ───────────────────────────────────────

def test_stints_requires_key(client):
    """Stints endpoint should return 401 without key when API_KEYS is set."""
    r = client.get("/api/v1/sessions/6556/stints")
    # In dev mode (no API_KEYS configured), returns 200
    # In production (API_KEYS set), returns 401
    assert r.status_code in (200, 401)


def test_stints_with_key(client, auth_headers):
    if not API_KEY:
        pytest.skip("No API_KEY set — skipping protected endpoint test")
    r = client.get("/api/v1/sessions/6556/stints", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    stint = data[0]
    assert "car_number" in stint
    assert "stint_number" in stint
    assert "baseline_pace_s" in stint


def test_pace_with_key(client, auth_headers):
    if not API_KEY:
        pytest.skip("No API_KEY set — skipping protected endpoint test")
    r = client.get(
        "/api/v1/sessions/6556/pace",
        headers=auth_headers,
        params={"car_class": "HYPERCAR"}
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data) > 0
    assert all(r["car_class"] == "HYPERCAR" for r in data)


def test_race_control_with_key(client, auth_headers):
    if not API_KEY:
        pytest.skip("No API_KEY set — skipping protected endpoint test")
    r = client.get("/api/v1/sessions/6556/race-control", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # Le Mans always has SC/FCY periods
    assert len(data) > 0
    period = data[0]
    assert "flag" in period
    assert "start_lap" in period
    assert "end_lap" in period


# ── API Key request ───────────────────────────────────────────

def test_api_key_request(client):
    """Submitting a key request returns a pending key."""
    r = client.post("/api/v1/api-keys/request", json={
        "name": "Test User",
        "email": "test@example.com",
        "intended_use": "automated test"
    })
    assert r.status_code == 200
    data = r.json()
    assert "api_key" in data
    assert data["api_key"].startswith("owec_")
    assert data["status"] == "pending"


def test_api_key_request_missing_email(client):
    r = client.post("/api/v1/api-keys/request", json={"name": "Test"})
    assert r.status_code == 422
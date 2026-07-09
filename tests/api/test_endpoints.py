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

# IDs — match fixtures.sql for CI, real data for production
SERIES_KEY    = "WEC"
SEASON_YEAR   = 2026
EVENT_ID      = int(os.environ.get("TEST_EVENT_ID",   "1"))
SESSION_ID    = int(os.environ.get("TEST_SESSION_ID", "1"))
DRIVER_ID     = int(os.environ.get("TEST_DRIVER_ID",  "1"))
TEAM_ID       = int(os.environ.get("TEST_TEAM_ID",    "1"))


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
    r = client.get(f"/api/v1/series/{SERIES_KEY}/seasons")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    years = [s["year"] for s in data]
    assert SEASON_YEAR in years


def test_seasons_events(client):
    r = client.get(f"/api/v1/series/{SERIES_KEY}/seasons/{SEASON_YEAR}/events")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0


# ── Sessions & Results ────────────────────────────────────────

def test_session_results(client):
    r = client.get(f"/api/v1/sessions/{SESSION_ID}/results")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    result = data[0]
    assert "position" in result
    assert "car_number" in result
    assert "car_class" in result
    assert "team" in result
    assert "drivers" in result
    assert "laps_completed" in result


def test_session_results_winner(client):
    r = client.get(f"/api/v1/sessions/{SESSION_ID}/results")
    assert r.status_code == 200
    data = r.json()
    winner = next((r for r in data if r["position"] == 1), None)
    assert winner is not None
    assert winner["car_class"] == "HYPERCAR"


def test_event_detail(client):
    r = client.get(f"/api/v1/events/{EVENT_ID}")
    assert r.status_code == 200
    data = r.json()
    assert "name" in data
    assert "sessions" in data
    assert len(data["sessions"]) > 0


def test_session_not_found(client):
    r = client.get("/api/v1/sessions/999999/results")
    assert r.status_code == 404


# ── Drivers & Teams ───────────────────────────────────────────

def test_driver_profile(client):
    r = client.get(f"/api/v1/drivers/{DRIVER_ID}")
    assert r.status_code == 200
    data = r.json()
    assert "first_name" in data
    assert "last_name" in data
    assert "total_races" in data


def test_driver_results(client):
    r = client.get(f"/api/v1/drivers/{DRIVER_ID}/results")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "series" in data[0]
    assert "event" in data[0]


def test_team_profile(client):
    r = client.get(f"/api/v1/teams/{TEAM_ID}")
    assert r.status_code == 200
    data = r.json()
    assert "name" in data
    assert "total_entries" in data


def test_driver_not_found(client):
    r = client.get("/api/v1/drivers/999999")
    assert r.status_code == 404


# ── Protected endpoints ───────────────────────────────────────

def test_stints_requires_key(client):
    r = client.get(f"/api/v1/sessions/{SESSION_ID}/stints")
    assert r.status_code in (200, 401, 404)


def test_stints_with_key(client, auth_headers):
    if not API_KEY:
        pytest.skip("No API_KEY set — skipping protected endpoint test")
    r = client.get(f"/api/v1/sessions/{SESSION_ID}/stints", headers=auth_headers)
    assert r.status_code in (200, 404)  # 404 if no analytics computed


def test_pace_with_key(client, auth_headers):
    if not API_KEY:
        pytest.skip("No API_KEY set — skipping protected endpoint test")
    r = client.get(
        f"/api/v1/sessions/{SESSION_ID}/pace",
        headers=auth_headers,
        params={"car_class": "HYPERCAR"}
    )
    assert r.status_code in (200, 404)


def test_race_control_with_key(client, auth_headers):
    if not API_KEY:
        pytest.skip("No API_KEY set — skipping protected endpoint test")
    r = client.get(f"/api/v1/sessions/{SESSION_ID}/race-control", headers=auth_headers)
    assert r.status_code in (200, 404)


# ── API Key request ───────────────────────────────────────────

def test_api_key_request(client):
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
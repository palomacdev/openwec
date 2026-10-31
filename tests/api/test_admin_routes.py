"""
OpenWEC — Admin routes, wired through the application

`test_admin_auth.py` tests the check in isolation. This file tests that the
check is actually in the request path: that the routes are mounted behind it,
that the header is the admin one, and that an unauthorized request is turned
away *before* anything touches the database.

No server, no network and no database: the database dependency is replaced by
one that fails loudly if it is ever reached.

    pytest tests/api/test_admin_routes.py -v
"""
import pytest
from fastapi.testclient import TestClient

from api.config import Settings
from api.deps import get_cursor, get_db
from api.main import app
from api.routers import admin


ADMIN_CRED = "SYNTHETIC-ADMIN-CREDENTIAL-AAAA-0001"
COMMON_KEY = "SYNTHETIC-COMMON-API-KEY-CCCC-0003"

ROTAS = ("/api/v1/admin/keys", "/api/v1/admin/keys/pending")

LINHA = {
    "id": 1,
    "name": "Synthetic Requester",
    "email": "synthetic@example.invalid",
    "intended_use": "tests",
    "api_key": "SYNTHETIC-ISSUED-KEY-0007",
    "status": "approved",
    "requests_per_minute": 60,
    "created_at": "2026-01-01 00:00:00",
    "approved_at": "2026-01-02 00:00:00",
}


class _CursorProibido:
    """Any use of the database from an unauthorized request is a failure."""

    def __getattr__(self, nome):
        raise AssertionError(
            f"the database was reached ({nome}) on an unauthorized request"
        )


class _CursorFalso:
    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return [dict(LINHA)]

    def fetchone(self):
        return dict(LINHA)


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(admin, "settings", Settings(
        api_keys=COMMON_KEY,
        admin_api_keys=ADMIN_CRED,
    ))
    app.dependency_overrides[get_db] = _CursorProibido
    app.dependency_overrides[get_cursor] = _CursorProibido
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def cliente_autorizado(cliente):
    app.dependency_overrides[get_cursor] = _CursorFalso
    return cliente


# ── Denial, before the database ───────────────────────────────────

@pytest.mark.parametrize("rota", ROTAS)
def test_sem_credencial_e_negado(cliente, rota):
    assert cliente.get(rota).status_code == 401


@pytest.mark.parametrize("rota", ROTAS)
def test_chave_comum_no_cabecalho_comum_e_negada(cliente, rota):
    r = cliente.get(rota, headers={"X-API-Key": COMMON_KEY})
    assert r.status_code == 401


@pytest.mark.parametrize("rota", ROTAS)
def test_chave_comum_no_cabecalho_administrativo_e_negada(cliente, rota):
    """Even presented in the right header, an API key is still an API key."""
    r = cliente.get(rota, headers={"X-Admin-Key": COMMON_KEY})
    assert r.status_code == 401


def test_revogacao_sem_credencial_e_negada(cliente):
    r = cliente.post("/api/v1/admin/keys/qualquer/revoke")
    assert r.status_code == 401


def test_revogacao_com_chave_comum_e_negada(cliente):
    r = cliente.post("/api/v1/admin/keys/qualquer/revoke",
                     headers={"X-API-Key": COMMON_KEY})
    assert r.status_code == 401


# ── Authorization ─────────────────────────────────────────────────

def test_credencial_administrativa_alcanca_a_rota(cliente_autorizado):
    r = cliente_autorizado.get("/api/v1/admin/keys",
                               headers={"X-Admin-Key": ADMIN_CRED})
    assert r.status_code == 200
    assert r.json()[0]["name"] == LINHA["name"]


def test_credencial_administrativa_no_cabecalho_errado_e_negada(cliente_autorizado):
    r = cliente_autorizado.get("/api/v1/admin/keys",
                               headers={"X-API-Key": ADMIN_CRED})
    assert r.status_code == 401


# ── The corpo of a refusal says nothing useful ────────────────────

def test_recusa_nao_revela_credencial(cliente):
    r = cliente.get("/api/v1/admin/keys", headers={"X-Admin-Key": COMMON_KEY})
    corpo = r.text
    assert COMMON_KEY not in corpo
    assert ADMIN_CRED not in corpo

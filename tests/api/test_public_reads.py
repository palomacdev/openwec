"""
OpenWEC — Public read endpoints

The dashboard is a static bundle. Anything it carries to authenticate with is
downloaded by every visitor, so the routes it draws read without a credential
and are metered per client address instead.

What has to hold, and is asserted here:

- the four routes the dashboard draws answer with no key;
- a key that is *presented* and wrong is still refused — an invalid credential
  is never quietly downgraded to an anonymous request;
- the bulk and strategy routes still require a key;
- anonymous traffic is rate limited, and says when to come back;
- the one public write is rate limited too.

No server, no network, no database: the database dependency is replaced.

    pytest tests/api/test_public_reads.py -v
"""
import pathlib
import types

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import deps
from api.config import Settings
from api.deps import get_cursor, get_db
from api.main import app


STATIC_KEY  = "SYNTHETIC-STATIC-API-KEY-0001"
UNKNOWN_KEY = "SYNTHETIC-UNKNOWN-KEY-0002"

PUBLICAS = (
    "/api/v1/sessions/1/stints",
    "/api/v1/sessions/1/gaps",
    "/api/v1/sessions/1/laps/7",
    "/api/v1/sessions/1/race-control",
)

COM_CHAVE = (
    "/api/v1/sessions/1/laps",
    "/api/v1/sessions/1/pace",
    "/api/v1/sessions/1/pit-window",
    "/api/v1/drivers/1/consistency",
)

# Distinctive, so "the handler was reached" cannot be confused with a real
# failure. Authorization is what is under test, not the SQL behind it.
ALCANCOU_O_HANDLER = 418


class _CursorDoHandler:
    def execute(self, *_a, **_k):
        raise HTTPException(ALCANCOU_O_HANDLER, "handler reached")


class _CursorDeAutenticacao:
    """Stands in for the dynamic-key lookup: the key is not in the table."""

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, *_a, **_k):
        return None

    def fetchone(self):
        return None


class _ConexaoDeAutenticacao:
    def cursor(self, *_a, **_k):
        return _CursorDeAutenticacao()


@pytest.fixture(autouse=True)
def limites_limpos(monkeypatch):
    """Deterministic limiter: in-memory, and empty at the start of each test."""
    monkeypatch.setattr(deps, "_get_redis", lambda: None)
    deps._rate_state.clear()
    deps._rate_limit_hits.clear()
    deps._key_cache.clear()
    yield
    deps._rate_state.clear()
    deps._rate_limit_hits.clear()
    deps._key_cache.clear()


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setattr(deps, "settings", Settings(api_keys=STATIC_KEY))
    app.dependency_overrides[get_db] = _ConexaoDeAutenticacao
    app.dependency_overrides[get_cursor] = _CursorDoHandler
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ── The dashboard's routes read without a credential ──────────────

@pytest.mark.parametrize("rota", PUBLICAS)
def test_rota_publica_dispensa_chave(cliente, rota):
    assert cliente.get(rota).status_code == ALCANCOU_O_HANDLER


@pytest.mark.parametrize("rota", PUBLICAS)
def test_rota_publica_aceita_chave_valida(cliente, rota):
    r = cliente.get(rota, headers={"X-API-Key": STATIC_KEY})
    assert r.status_code == ALCANCOU_O_HANDLER


@pytest.mark.parametrize("rota", PUBLICAS)
def test_chave_errada_continua_sendo_recusada(cliente, rota):
    """
    The point of the whole design: open to no credential, closed to a wrong
    one. Downgrading a bad key to anonymous would hide a consumer's broken
    configuration behind a working page.
    """
    r = cliente.get(rota, headers={"X-API-Key": UNKNOWN_KEY})
    assert r.status_code == 401


# ── Everything else keeps its key ─────────────────────────────────

@pytest.mark.parametrize("rota", COM_CHAVE)
def test_rota_reservada_exige_chave(cliente, rota):
    assert cliente.get(rota).status_code == 401


@pytest.mark.parametrize("rota", COM_CHAVE)
def test_rota_reservada_abre_com_chave(cliente, rota):
    r = cliente.get(rota, headers={"X-API-Key": STATIC_KEY})
    assert r.status_code == ALCANCOU_O_HANDLER


def test_volume_bruto_e_por_carro_divergem(cliente):
    """The split that defines the line: one car is public, a whole session is not."""
    assert cliente.get("/api/v1/sessions/1/laps/7").status_code == ALCANCOU_O_HANDLER
    assert cliente.get("/api/v1/sessions/1/laps").status_code == 401


# ── Metering ──────────────────────────────────────────────────────

def test_trafego_anonimo_e_limitado(cliente, monkeypatch):
    monkeypatch.setattr(deps, "PUBLIC_RATE_LIMIT_PER_MINUTE", 3)
    for _ in range(3):
        assert cliente.get(PUBLICAS[0]).status_code == ALCANCOU_O_HANDLER
    r = cliente.get(PUBLICAS[0])
    assert r.status_code == 429


def test_resposta_429_diz_quando_voltar(cliente, monkeypatch):
    monkeypatch.setattr(deps, "PUBLIC_RATE_LIMIT_PER_MINUTE", 1)
    cliente.get(PUBLICAS[0])
    r = cliente.get(PUBLICAS[0])
    assert r.status_code == 429
    espera = int(r.headers["Retry-After"])
    assert 1 <= espera <= 60


def test_chave_valida_nao_usa_o_limite_anonimo(cliente, monkeypatch):
    """A static key is unmetered; the anonymous ceiling must not reach it."""
    monkeypatch.setattr(deps, "PUBLIC_RATE_LIMIT_PER_MINUTE", 1)
    for _ in range(5):
        r = cliente.get(PUBLICAS[0], headers={"X-API-Key": STATIC_KEY})
        assert r.status_code == ALCANCOU_O_HANDLER


def test_escrita_publica_e_limitada(cliente, monkeypatch):
    monkeypatch.setattr(deps, "PUBLIC_WRITE_LIMIT_PER_MINUTE", 2)
    corpo = {"name": "Synthetic", "email": "synthetic@example.invalid"}
    vistos = [cliente.post("/api/v1/api-keys/request", json=corpo).status_code
              for _ in range(3)]
    assert vistos[-1] == 429
    assert all(s != 429 for s in vistos[:2])


# ── Which address gets metered ────────────────────────────────────

def _pedido(forwarded=None, peer="203.0.113.9"):
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return types.SimpleNamespace(
        headers=headers,
        client=types.SimpleNamespace(host=peer),
    )


def test_endereco_vem_do_ultimo_forwarded_for():
    """
    nginx appends the peer it actually saw. Taking the first entry would meter
    whatever the client invented, which is the same as not metering at all.
    """
    assert deps._client_ip(_pedido("1.2.3.4, 198.51.100.7")) == "198.51.100.7"
    assert deps._client_ip(_pedido("198.51.100.7")) == "198.51.100.7"


def test_endereco_cai_para_o_peer_sem_cabecalho():
    assert deps._client_ip(_pedido()) == "203.0.113.9"
    assert deps._client_ip(_pedido(forwarded="  ")) == "203.0.113.9"


def test_endereco_nunca_fica_vazio():
    sem_cliente = types.SimpleNamespace(headers={}, client=None)
    assert deps._client_ip(sem_cliente) == "unknown"


# ── The bundle may not carry a credential ─────────────────────────

RAIZ = pathlib.Path(__file__).resolve().parents[2]


def test_fonte_do_dashboard_nao_le_credencial():
    """
    A guard, not a style rule. Vite inlines `import.meta.env.VITE_API_KEY` at
    build time, so reading it anywhere in this tree publishes the value to
    every visitor.
    """
    ofensas = []
    for arq in (RAIZ / "dashboard" / "src").rglob("*"):
        if arq.suffix not in (".js", ".jsx"):
            continue
        texto = arq.read_text(encoding="utf-8")
        for numero, linha in enumerate(texto.splitlines(), 1):
            if "VITE_API_KEY" in linha:
                ofensas.append(f"{arq.relative_to(RAIZ)}:{numero}")
    assert ofensas == [], f"VITE_API_KEY read at {ofensas}"


def test_fonte_do_dashboard_nao_envia_cabecalho_de_credencial():
    """
    Matches the header being *set* — `headers['X-API-Key'] = …` or an object
    literal entry — and not the string appearing as text. The key request page
    legitimately shows the visitor how to use the key it just issued them.
    """
    import re

    atribuicao = re.compile(
        r"""\[\s*['"]X-API-Key['"]\s*\]\s*=|['"]X-API-Key['"]\s*:"""
    )
    ofensas = []
    for arq in (RAIZ / "dashboard" / "src").rglob("*"):
        if arq.suffix not in (".js", ".jsx"):
            continue
        for numero, linha in enumerate(arq.read_text(encoding="utf-8").splitlines(), 1):
            if atribuicao.search(linha):
                ofensas.append(f"{arq.relative_to(RAIZ)}:{numero}")
    assert ofensas == [], f"X-API-Key header set at {ofensas}"

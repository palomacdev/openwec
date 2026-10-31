"""
OpenWEC SDK — HTTP error handling

The API gained per-minute budgets for reads made without a key. A notebook
that sweeps sessions will meet 429, and before this the SDK let httpx raise a
bare HTTPStatusError with the Retry-After still sitting unread in the
response. These tests pin what each status does now.

    pytest tests/sdk/test_client_errors.py -v
"""

import email.utils
import time

import httpx
import pytest
import respx

from openwec.client import (
    _config,
    _get,
    _retry_after,
    configure,
    OpenWECAuthError,
    OpenWECNotFoundError,
    OpenWECPermissionError,
    OpenWECRateLimitError,
    OpenWECServerError,
)


BASE = "https://api.openwec.com/api/v1"
ROTA = "/sessions/2226/stints"
URL = BASE + ROTA


@pytest.fixture(autouse=True)
def config_limpa():
    _config.base_url = BASE
    _config.api_key = None
    _config.max_retries = 0
    _config.retry_max_wait = 60.0
    yield
    _config.base_url = BASE
    _config.api_key = None
    _config.max_retries = 0
    _config.retry_max_wait = 60.0


@pytest.fixture
def sem_dormir(monkeypatch):
    """Records what the client would have slept, without spending it."""
    dormidas = []
    monkeypatch.setattr(time, "sleep", lambda s: dormidas.append(s))
    return dormidas


# ── Cada status tem o seu tipo ────────────────────────────────

@respx.mock
def test_404_continua_sendo_nao_encontrado():
    respx.get(URL).mock(return_value=httpx.Response(404, text="no such session"))
    with pytest.raises(OpenWECNotFoundError):
        _get(ROTA)


@respx.mock
def test_401_continua_pedindo_chave():
    respx.get(URL).mock(return_value=httpx.Response(401))
    with pytest.raises(OpenWECAuthError):
        _get(ROTA)


@respx.mock
def test_403_e_distinto_de_401():
    """A valid key refused here is not the same problem as no key at all."""
    respx.get(URL).mock(return_value=httpx.Response(403))
    with pytest.raises(OpenWECPermissionError):
        _get(ROTA)


@respx.mock
def test_500_diz_de_quem_e_o_problema():
    respx.get(URL).mock(return_value=httpx.Response(503))
    with pytest.raises(OpenWECServerError) as e:
        _get(ROTA)
    assert "503" in str(e.value)


@respx.mock
def test_200_continua_devolvendo_o_corpo():
    respx.get(URL).mock(return_value=httpx.Response(200, json=[{"car": 7}]))
    assert _get(ROTA) == [{"car": 7}]


# ── 429 e Retry-After ─────────────────────────────────────────

@respx.mock
def test_429_carrega_o_retry_after():
    respx.get(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "42"})
    )
    with pytest.raises(OpenWECRateLimitError) as e:
        _get(ROTA)
    assert e.value.retry_after == 42.0
    assert "42s" in str(e.value)


@respx.mock
def test_429_sem_cabecalho_nao_inventa_um_numero():
    """None means the API did not say, which is not the same as zero."""
    respx.get(URL).mock(return_value=httpx.Response(429))
    with pytest.raises(OpenWECRateLimitError) as e:
        _get(ROTA)
    assert e.value.retry_after is None
    assert "no Retry-After" in str(e.value)


@respx.mock
def test_429_nao_dorme_por_padrao(sem_dormir):
    """max_retries defaults to 0: the caller decides, the SDK does not block."""
    respx.get(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "5"})
    )
    with pytest.raises(OpenWECRateLimitError):
        _get(ROTA)
    assert sem_dormir == []


@respx.mock
def test_com_max_retries_espera_e_segue(sem_dormir):
    rota = respx.get(URL)
    rota.side_effect = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": True}),
    ]
    configure(max_retries=2)
    assert _get(ROTA) == {"ok": True}
    assert sem_dormir == [3.0]


@respx.mock
def test_as_tentativas_sao_limitadas(sem_dormir):
    respx.get(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "1"})
    )
    configure(max_retries=2)
    with pytest.raises(OpenWECRateLimitError):
        _get(ROTA)
    assert sem_dormir == [1.0, 1.0]


@respx.mock
def test_espera_longa_demais_levanta_em_vez_de_travar(sem_dormir):
    """Sleeping for an hour inside a library call is a hang, not a retry."""
    respx.get(URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3600"})
    )
    configure(max_retries=5, retry_max_wait=60)
    with pytest.raises(OpenWECRateLimitError) as e:
        _get(ROTA)
    assert e.value.retry_after == 3600.0
    assert sem_dormir == []


@respx.mock
def test_sem_retry_after_nao_ha_o_que_esperar(sem_dormir):
    respx.get(URL).mock(return_value=httpx.Response(429))
    configure(max_retries=3)
    with pytest.raises(OpenWECRateLimitError):
        _get(ROTA)
    assert sem_dormir == []


# ── O parser do cabeçalho ─────────────────────────────────────

def _resposta(valor):
    cabecalhos = {"Retry-After": valor} if valor is not None else {}
    return httpx.Response(429, headers=cabecalhos)


def test_retry_after_em_segundos():
    assert _retry_after(_resposta("30")) == 30.0
    assert _retry_after(_resposta("  30  ")) == 30.0
    assert _retry_after(_resposta("0")) == 0.0


def test_retry_after_como_data_http():
    futuro = time.time() + 120
    cabecalho = email.utils.formatdate(futuro, usegmt=True)
    espera = _retry_after(_resposta(cabecalho))
    assert espera is not None and 110 < espera < 130


def test_retry_after_no_passado_nao_e_espera():
    passado = email.utils.formatdate(time.time() - 300, usegmt=True)
    assert _retry_after(_resposta(passado)) is None


def test_retry_after_ilegivel_ou_ausente():
    assert _retry_after(_resposta(None)) is None
    assert _retry_after(_resposta("")) is None
    assert _retry_after(_resposta("logo")) is None


# ── configure() valida o que recebe ───────────────────────────

def test_configure_recusa_valores_sem_sentido():
    with pytest.raises(ValueError):
        configure(max_retries=-1)
    with pytest.raises(ValueError):
        configure(retry_max_wait=0)


def test_configure_zero_tentativas_e_valido():
    configure(max_retries=3)
    configure(max_retries=0)
    assert _config.max_retries == 0


# ── Compatibilidade com o contrato que está no ar ─────────────

@respx.mock
def test_rota_ppublica_funciona_sem_chave():
    """
    `stints` is protected in production today and optional in main. The SDK
    must work either way: it sends no header when no key is configured, and
    the 401 it may still get is already a typed error.
    """
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    assert _get(ROTA) == []
    enviado = respx.calls.last.request
    assert "X-API-Key" not in enviado.headers


@respx.mock
def test_a_chave_e_enviada_quando_configurada():
    respx.get(URL).mock(return_value=httpx.Response(200, json=[]))
    configure(api_key="chave-de-teste")
    _get(ROTA)
    assert respx.calls.last.request.headers["X-API-Key"] == "chave-de-teste"

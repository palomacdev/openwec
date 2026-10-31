"""
OpenWEC — Losing Redis costs shared counting, not availability

Measured against a stopped Redis container, before this fix: the first public
read after the outage returned **500 after 29.6 seconds**, the next one reset
the connection, and no `rate_limit_degraded` warning was written. The fallback
this module advertises never ran.

Two causes, both pinned here:

1. `_get_redis()` caches a working client and hands it back without probing
   again. When Redis goes away, `incr` on that cached client raises
   `redis.exceptions.ConnectionError`, which nothing caught.
2. `socket.create_connection(..., timeout=T)` does not bound the name lookup
   that runs first. In a compose network the host is a name, and it stops
   resolving the moment the container stops.

The blast radius is why this matters: before the public reads landed, the only
callers of `_check_rate_limit` were protected routes with dynamic keys --
production's Redis saw 16 connections in 86 days. Afterwards every anonymous
read goes through it, so a Redis outage would take the public dashboard down.

    pytest tests/api/test_redis_outage.py -v
"""

import time

import pytest
from fastapi import HTTPException

from api import deps


class RedisQueCaiu:
    """A client that was fine and is not any more."""

    def __init__(self, excecao=None):
        self.chamadas = 0
        self.excecao = excecao or ConnectionError(
            "Error -2 connecting to redis:6379. Name or service not known."
        )

    def incr(self, key):
        self.chamadas += 1
        raise self.excecao

    def expire(self, key, seconds):  # pragma: no cover - never reached
        raise AssertionError("expire should not be reached")


class RedisQueConta:
    def __init__(self):
        self.contadores = {}

    def incr(self, key):
        self.contadores[key] = self.contadores.get(key, 0) + 1
        return self.contadores[key]

    def expire(self, key, seconds):
        return True


@pytest.fixture(autouse=True)
def estado_limpo():
    deps._redis_client = None
    deps._redis_unavailable_until = 0.0
    deps._redis_fallback_logged = False
    deps._memory_buckets.clear() if hasattr(deps, "_memory_buckets") else None
    yield
    deps._redis_client = None
    deps._redis_unavailable_until = 0.0
    deps._redis_fallback_logged = False


# ── Um Redis que cai no meio não derruba a rota ───────────────

def test_cliente_cacheado_que_falha_nao_vira_500(monkeypatch):
    caido = RedisQueCaiu()
    monkeypatch.setattr(deps, "_get_redis", lambda: deps._redis_client)
    deps._redis_client = caido

    # Antes da correção isto propagava ConnectionError -> 500.
    deps._check_rate_limit("anon:198.51.100.7", 60)

    assert caido.chamadas == 1, "o cliente caído deve ter sido tentado uma vez"


def test_a_falha_e_lembrada_e_o_cliente_descartado(monkeypatch):
    monkeypatch.setattr(deps, "_get_redis", lambda: deps._redis_client)
    deps._redis_client = RedisQueCaiu()

    antes = time.time()
    deps._check_rate_limit("anon:198.51.100.7", 60)

    assert deps._redis_client is None, "o cliente quebrado continuou em cache"
    assert deps._redis_unavailable_until > antes, "a janela de espera não foi armada"


def test_a_degradacao_e_registrada_uma_vez_por_interrupcao(monkeypatch, caplog):
    monkeypatch.setattr(deps, "_get_redis", lambda: deps._redis_client)

    with caplog.at_level("WARNING"):
        for _ in range(4):
            deps._redis_client = RedisQueCaiu()
            deps._check_rate_limit("anon:198.51.100.7", 60)

    avisos = [r for r in caplog.records if r.message == "rate_limit_degraded"]
    assert len(avisos) == 1, f"esperado um aviso, vieram {len(avisos)}"
    assert avisos[0].reason == "redis_connection_lost"


def test_o_fallback_em_memoria_realmente_conta(monkeypatch):
    """Degradar não pode significar deixar de limitar."""
    monkeypatch.setattr(deps, "_get_redis", lambda: None)

    codigos = []
    for _ in range(5):
        try:
            deps._check_rate_limit("anon:198.51.100.9", 3)
            codigos.append(200)
        except HTTPException as e:
            codigos.append(e.status_code)

    assert codigos == [200, 200, 200, 429, 429], codigos


def test_o_429_atravessa_e_nao_e_confundido_com_queda(monkeypatch):
    """
    O 429 é uma HTTPException levantada de dentro do caminho do Redis. Tratá-la
    como falha daria passe livre a todo chamador limitado — e ainda descartaria
    um Redis que está perfeitamente de pé.
    """
    contando = RedisQueConta()
    monkeypatch.setattr(deps, "_get_redis", lambda: contando)

    for _ in range(3):
        deps._check_rate_limit("anon:198.51.100.11", 3)

    with pytest.raises(HTTPException) as e:
        deps._check_rate_limit("anon:198.51.100.11", 3)
    assert e.value.status_code == 429

    assert deps._redis_client is None or deps._redis_client is contando
    assert deps._redis_unavailable_until == 0.0, \
        "um 429 armou a janela de indisponibilidade; o Redis está de pé"


# ── A sonda é limitada pelo relógio, não pelo resolvedor ──────

def test_a_sonda_desiste_no_prazo_mesmo_com_nome_que_nao_resolve():
    """
    `socket.create_connection(..., timeout=0.5)` contra um nome inexistente
    levou 2,3 s medidos, todos em getaddrinfo. A sonda agora espera no relógio.
    """
    inicio = time.time()
    ok = deps._porta_responde("nome-que-nao-existe.invalid", 6379, 0.5)
    decorrido = time.time() - inicio

    assert ok is False
    assert decorrido < 2.0, f"a sonda levou {decorrido:.2f}s, acima do limite pedido"


def test_a_sonda_responde_falso_para_porta_fechada_rapidamente():
    inicio = time.time()
    ok = deps._porta_responde("127.0.0.1", 9, 0.5)
    assert ok is False
    assert time.time() - inicio < 1.0


def test_a_sonda_reconhece_uma_porta_que_atende():
    import socket as s
    srv = s.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    porta = srv.getsockname()[1]
    try:
        assert deps._porta_responde("127.0.0.1", porta, 1.0) is True
    finally:
        srv.close()


# ── A aquisição continua se comportando ──────────────────────

def test_sem_redis_a_janela_impede_nova_sonda(monkeypatch):
    sondas = []
    monkeypatch.setattr(deps, "_porta_responde",
                        lambda *a, **k: sondas.append(1) or False)

    assert deps._get_redis() is None
    assert deps._get_redis() is None
    assert deps._get_redis() is None

    assert len(sondas) == 1, f"a sonda correu {len(sondas)} vezes dentro da janela"


def test_passada_a_janela_a_sonda_volta_a_acontecer(monkeypatch):
    sondas = []
    monkeypatch.setattr(deps, "_porta_responde",
                        lambda *a, **k: sondas.append(1) or False)

    assert deps._get_redis() is None
    deps._redis_unavailable_until = time.time() - 1
    assert deps._get_redis() is None

    assert len(sondas) == 2

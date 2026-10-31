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
    def zerar():
        deps._redis_client = None
        deps._redis_unavailable_until = 0.0
        deps._redis_fallback_logged = False
        deps._rate_state.clear()
        deps._rate_limit_hits.clear()

    zerar()
    yield
    zerar()


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


# ── O que custa a falha: a politica de retry e o nome ─────────
#
# Instrumentado dentro do contentor, contra um Redis parado:
#
#   getaddrinfo(nome)                          falha em 2.630s
#   create_connection(nome,  timeout=1)        falha em 2.304s
#   create_connection(IP,    timeout=1)        falha em 0.001s
#   INCR, cliente cacheado, retry padrao (10)  falha em 29.387s
#   INCR, retry desligado, pelo nome           falha em  2.581s
#   INCR, retry desligado, pelo IP resolvido   falha em  1.004s
#
# Os 29s nao sao o socket: sao onze tentativas, cada uma pagando um lookup
# falho, com sleeps de backoff entre elas. Os dois numeros que restam sao a
# diferenca entre um limite que e nosso e um que e do resolvedor.

def test_o_cliente_nao_retenta(monkeypatch):
    """
    redis-py 8 traz Retry(ExponentialWithJitterBackoff(), 10) por padrao.
    Num limitador de taxa isso nao tem lugar: ele e consultivo, tem fallback,
    e corre na frente de toda leitura publica.
    """
    criados = {}

    class RedisFalso:
        def __init__(self, **kwargs):
            criados.update(kwargs)

        def ping(self):
            return True

    import sys
    import types
    modulo = types.ModuleType("redis")
    modulo.Redis = RedisFalso
    backoff = types.ModuleType("redis.backoff")
    backoff.NoBackoff = type("NoBackoff", (), {})
    retry = types.ModuleType("redis.retry")
    retry.Retry = lambda b, n: ("Retry", type(b).__name__, n)
    monkeypatch.setitem(sys.modules, "redis", modulo)
    monkeypatch.setitem(sys.modules, "redis.backoff", backoff)
    monkeypatch.setitem(sys.modules, "redis.retry", retry)

    monkeypatch.setattr(deps, "_resolver", lambda *a, **k: "203.0.113.4")
    monkeypatch.setattr(deps, "_porta_responde", lambda *a, **k: True)

    assert deps._get_redis() is not None

    assert criados["retry"][2] == 0, f"tentativas: {criados['retry'][2]}"
    assert criados["retry_on_error"] == []
    assert criados["socket_connect_timeout"] == deps._REDIS_SOCKET_TIMEOUT
    assert criados["socket_timeout"] == deps._REDIS_SOCKET_TIMEOUT


def test_o_cliente_e_construido_com_o_endereco_resolvido(monkeypatch):
    """
    O nome e resolvido uma vez, na aquisicao. Um cliente construido com o
    nome paga o resolvedor a cada operacao depois que o Redis some -- 2.58s
    medidos, contra 1.004s pelo endereco, que e o socket_timeout que pedimos.
    """
    criados = {}

    class RedisFalso:
        def __init__(self, **kwargs):
            criados.update(kwargs)

        def ping(self):
            return True

    import sys
    import types
    modulo = types.ModuleType("redis")
    modulo.Redis = RedisFalso
    backoff = types.ModuleType("redis.backoff")
    backoff.NoBackoff = type("NoBackoff", (), {})
    retry = types.ModuleType("redis.retry")
    retry.Retry = lambda b, n: ("Retry", type(b).__name__, n)
    monkeypatch.setitem(sys.modules, "redis", modulo)
    monkeypatch.setitem(sys.modules, "redis.backoff", backoff)
    monkeypatch.setitem(sys.modules, "redis.retry", retry)

    monkeypatch.setattr(deps, "_resolver", lambda *a, **k: "203.0.113.4")
    monkeypatch.setattr(deps, "_porta_responde", lambda *a, **k: True)
    monkeypatch.setattr(deps.settings, "redis_host", "redis", raising=False)

    deps._get_redis()
    assert criados["host"] == "203.0.113.4", \
        "o cliente foi construido com o nome, nao com o endereco"


def test_nome_que_nao_resolve_nao_vira_cliente(monkeypatch):
    monkeypatch.setattr(deps, "_resolver", lambda *a, **k: None)
    sondas = []
    monkeypatch.setattr(deps, "_porta_responde",
                        lambda *a, **k: sondas.append(1) or True)
    assert deps._get_redis() is None
    assert sondas == [], "sondou um endereco que nao existe"


def test_o_resolvedor_desiste_no_prazo():
    inicio = time.time()
    assert deps._resolver("nome-que-nao-existe.invalid", 0.5) is None
    assert time.time() - inicio < 2.0


def test_o_resolvedor_devolve_um_endereco():
    assert deps._resolver("localhost", 1.0) in ("127.0.0.1", "::1")


# ── Concorrencia ──────────────────────────────────────────────

def test_uma_interrupcao_sonda_uma_vez_e_avisa_uma_vez(monkeypatch, caplog):
    """
    Estas dependencias correm num threadpool. Sem o lock, N threads encontram
    a mesma queda, sondam N vezes e registram N avisos para uma interrupcao.
    """
    import threading as th

    sondas = []
    monkeypatch.setattr(deps, "_resolver", lambda *a, **k: "203.0.113.4")
    monkeypatch.setattr(deps, "_porta_responde",
                        lambda *a, **k: sondas.append(1) or False)

    largada = th.Event()
    resultados = []

    def corredor():
        largada.wait()
        resultados.append(deps._get_redis())

    threads = [th.Thread(target=corredor) for _ in range(12)]
    with caplog.at_level("WARNING"):
        for t in threads:
            t.start()
        largada.set()
        for t in threads:
            t.join(10)

    assert all(r is None for r in resultados)
    assert len(sondas) == 1, f"a sonda correu {len(sondas)} vezes"
    avisos = [r for r in caplog.records if r.message == "rate_limit_degraded"]
    assert len(avisos) == 1, f"{len(avisos)} avisos para uma interrupcao"


def test_o_caminho_rapido_nao_espera_pelo_lock(monkeypatch):
    """Quem ja tem cliente nao pode ficar na fila de quem esta sondando."""
    import threading as th

    deps._redis_client = RedisQueConta()

    segurando = th.Event()
    soltar = th.Event()

    def ocupar():
        with deps._redis_lock:
            segurando.set()
            soltar.wait(5)

    t = th.Thread(target=ocupar, daemon=True)
    t.start()
    assert segurando.wait(5)

    inicio = time.time()
    assert deps._get_redis() is not None
    decorrido = time.time() - inicio
    soltar.set()
    t.join(5)

    assert decorrido < 0.5, f"o caminho rapido esperou {decorrido:.2f}s"


# ── O estado em memoria nao cresce sem limite ─────────────────

def test_janelas_expiradas_sao_descartadas(monkeypatch):
    """
    A chave do limitador passou a ser o endereco do cliente. Numa queda do
    Redis num site publico isso e uma entrada por visitante, guardada para
    sempre -- a menos que as janelas vencidas saiam.
    """
    monkeypatch.setattr(deps, "_get_redis", lambda: None)

    antigo = time.time() - (deps._RATE_WINDOW_SECONDS + 5)
    for i in range(deps._MEMORY_STATE_SOFT_MAX + 10):
        deps._rate_state[f"anon:198.51.100.{i}"] = (1, antigo)
    deps._rate_limit_hits["anon:198.51.100.1"] = 3

    assert len(deps._rate_state) > deps._MEMORY_STATE_SOFT_MAX
    deps._check_rate_limit("anon:203.0.113.77", 60)

    assert len(deps._rate_state) == 1, \
        f"sobraram {len(deps._rate_state)} entradas"
    assert "anon:203.0.113.77" in deps._rate_state
    assert deps._rate_limit_hits == {}


def test_a_poda_nao_descarta_janela_viva(monkeypatch):
    """Podar não pode zerar quem está no meio do seu minuto."""
    monkeypatch.setattr(deps, "_get_redis", lambda: None)

    agora = time.time()
    vivo = "anon:203.0.113.90"
    deps._rate_state[vivo] = (59, agora)
    antigo = agora - (deps._RATE_WINDOW_SECONDS + 5)
    for i in range(deps._MEMORY_STATE_SOFT_MAX + 10):
        deps._rate_state[f"anon:198.51.100.{i}"] = (1, antigo)

    with pytest.raises(HTTPException) as e:
        deps._check_rate_limit(vivo, 59)
    assert e.value.status_code == 429, "a contagem viva foi zerada pela poda"


# ── Infraestrutura nao e a mesma coisa que bug ────────────────

def test_erro_de_programacao_nao_vira_degradacao(monkeypatch, caplog):
    """
    Um TypeError no caminho do Redis e um defeito nosso. Degradar em silencio
    o esconderia atras de um aviso dizendo que a culpa e da infraestrutura, e
    o limitador passaria a contar em memoria para sempre sem ninguem saber.
    """
    class RedisComBug:
        def incr(self, key):
            raise TypeError("unsupported operand type(s)")

    monkeypatch.setattr(deps, "_get_redis", lambda: RedisComBug())

    with caplog.at_level("WARNING"):
        with pytest.raises(TypeError):
            deps._check_rate_limit("anon:198.51.100.21", 60)

    assert not [r for r in caplog.records if r.message == "rate_limit_degraded"]
    assert deps._redis_unavailable_until == 0.0, \
        "um bug armou a janela de indisponibilidade"


def test_erro_de_infraestrutura_degrada(monkeypatch):
    """O contraste do teste acima: OSError e RedisError degradam."""
    import redis

    for excecao in (OSError("connection refused"),
                    redis.exceptions.ConnectionError("gone"),
                    redis.exceptions.TimeoutError("slow")):
        deps._redis_client = None
        deps._redis_unavailable_until = 0.0
        deps._redis_fallback_logged = False

        class RedisQueFalha:
            def incr(self, key):
                raise excecao

        monkeypatch.setattr(deps, "_get_redis", lambda: RedisQueFalha())
        deps._check_rate_limit("anon:198.51.100.22", 60)
        assert deps._redis_unavailable_until > 0, f"{type(excecao).__name__} nao degradou"


def test_a_tupla_de_infra_cobre_o_que_importa():
    import redis
    erros = deps._erros_de_infra()
    assert redis.RedisError in erros
    assert OSError in erros
    assert not issubclass(TypeError, erros)
    assert not issubclass(KeyError, erros)

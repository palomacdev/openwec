"""
OpenWEC — Which address the rate limiter meters, and when Redis is gone

`X-Forwarded-For` is a request header. Any caller can send one, and a caller
that reaches the API without passing through the proxy sends whatever it likes.
Reading it unconditionally — from either end — lets one caller spend another's
budget, or escape its own by rotating a string.

So it is read only when the peer that opened the connection is a proxy named in
`TRUSTED_PROXIES`, and only the part of the chain to the right of the last
untrusted hop is believed.

The second half of this file covers the other way the limiter stops being a
limit: Redis unreachable, counters falling back to one process.

No server, no network, no database.

    pytest tests/api/test_client_identity.py -v
"""
import pathlib
import types

import pytest

from api import deps
from api.config import Settings


PROXY_LOCAL   = "10.0.0.1"      # the reverse proxy, trusted
PROXY_INTERNO = "10.0.0.9"      # a second hop, also trusted
CLIENTE       = "203.0.113.5"   # the real caller
ESTRANHO      = "192.0.2.9"     # a proxy nobody told us about
CONFIAVEIS    = "10.0.0.0/8"


def pedido(peer=PROXY_LOCAL, forwarded=None):
    cabecalhos = {"X-Forwarded-For": forwarded} if forwarded is not None else {}
    return types.SimpleNamespace(
        headers=cabecalhos,
        client=types.SimpleNamespace(host=peer) if peer is not None else None,
    )


@pytest.fixture
def com_proxy(monkeypatch):
    monkeypatch.setattr(deps, "settings", Settings(trusted_proxies=CONFIAVEIS))


@pytest.fixture
def sem_proxy(monkeypatch):
    monkeypatch.setattr(deps, "settings", Settings(trusted_proxies=""))


# ── The cases the policy has to get right ─────────────────────────

def test_cliente_direto_sem_proxy_configurado(sem_proxy):
    """Nothing is trusted, so nothing in the header is read."""
    assert deps._client_ip(pedido(peer=CLIENTE)) == CLIENTE


def test_cabecalho_ignorado_quando_nada_e_confiavel(sem_proxy):
    """The header is present and says something else. It does not matter."""
    assert deps._client_ip(pedido(peer=CLIENTE, forwarded="1.2.3.4")) == CLIENTE
    assert deps._client_ip(pedido(peer=PROXY_LOCAL, forwarded=CLIENTE)) == PROXY_LOCAL


def test_proxy_confiavel_entrega_o_cliente(com_proxy):
    assert deps._client_ip(pedido(peer=PROXY_LOCAL, forwarded=CLIENTE)) == CLIENTE


def test_cliente_direto_nao_forja_sua_identidade(com_proxy):
    """
    The forgery that matters: a caller reaching the API without the proxy, and
    claiming to be someone else. Its own address is what gets metered.
    """
    falso = pedido(peer=CLIENTE, forwarded="198.51.100.200")
    assert deps._client_ip(falso) == CLIENTE


def test_cliente_direto_nao_se_disfarca_de_proxy(com_proxy):
    """Nor can it claim to be the proxy and then name its own client."""
    falso = pedido(peer=CLIENTE, forwarded=f"{PROXY_LOCAL}, 198.51.100.200")
    assert deps._client_ip(falso) == CLIENTE


def test_cadeia_com_varios_proxies_confiaveis(com_proxy):
    encadeado = pedido(peer=PROXY_LOCAL,
                       forwarded=f"{CLIENTE}, {PROXY_INTERNO}, {PROXY_LOCAL}")
    assert deps._client_ip(encadeado) == CLIENTE


def test_hop_confiavel_injetado_pelo_cliente_nunca_e_alcancado(com_proxy):
    """
    The client writes `10.0.0.9` hoping to look like an internal hop; nginx
    appends what it actually saw. The walk stops at the first untrusted
    address from the right, which is the client, and never reads the lie.
    """
    injetado = pedido(peer=PROXY_LOCAL,
                      forwarded=f"{PROXY_INTERNO}, {CLIENTE}, {PROXY_LOCAL}")
    assert deps._client_ip(injetado) == CLIENTE


def test_proxy_desconhecido_e_onde_a_confianca_acaba(com_proxy):
    """
    An intermediate we never named. Everything beyond it was written by
    somebody we have no reason to believe, so it is metered and the rest of
    the chain is ignored.
    """
    desconhecido = pedido(peer=PROXY_LOCAL,
                          forwarded=f"{CLIENTE}, {ESTRANHO}, {PROXY_LOCAL}")
    assert deps._client_ip(desconhecido) == ESTRANHO


def test_cabecalho_vazio_ou_ausente_mede_o_par(com_proxy):
    assert deps._client_ip(pedido(peer=PROXY_LOCAL)) == PROXY_LOCAL
    assert deps._client_ip(pedido(peer=PROXY_LOCAL, forwarded="")) == PROXY_LOCAL
    assert deps._client_ip(pedido(peer=PROXY_LOCAL, forwarded="  ,  ")) == PROXY_LOCAL


def test_todos_os_hops_confiaveis_medem_o_par(com_proxy):
    só_nossos = pedido(peer=PROXY_LOCAL, forwarded=f"{PROXY_INTERNO}, {PROXY_LOCAL}")
    assert deps._client_ip(só_nossos) == PROXY_LOCAL


def test_hop_malformado_interrompe_a_caminhada(com_proxy):
    """
    We cannot tell where the real client sits in the rest of the list, so we
    stop rather than guess, and meter the proxy.
    """
    quebrado = pedido(peer=PROXY_LOCAL, forwarded=f"{CLIENTE}, nao-e-um-endereco")
    assert deps._client_ip(quebrado) == PROXY_LOCAL


def test_ausencia_de_endereco(com_proxy):
    assert deps._client_ip(pedido(peer=None)) == "unknown"


def test_formas_aceitas_de_endereco(com_proxy):
    """IPv6, brackets and a trailing port all appear in the wild."""
    assert deps._parse_address("[2001:db8::1]") == deps.ipaddress.ip_address("2001:db8::1")
    assert deps._parse_address("2001:db8::1") == deps.ipaddress.ip_address("2001:db8::1")
    assert deps._parse_address("198.51.100.7:4321") == deps.ipaddress.ip_address("198.51.100.7")
    assert deps._parse_address("::1") == deps.ipaddress.ip_address("::1")
    assert deps._parse_address("") is None
    assert deps._parse_address("nao") is None


def test_ipv6_atravessa_a_politica(monkeypatch):
    monkeypatch.setattr(deps, "settings", Settings(trusted_proxies="::1"))
    assert deps._client_ip(pedido(peer="::1", forwarded="2001:db8::ff")) == "2001:db8::ff"
    assert deps._client_ip(pedido(peer="2001:db8::ff", forwarded="::1")) == "2001:db8::ff"


def test_entrada_invalida_nao_concede_confianca(monkeypatch):
    """A typo removes trust. It never adds any, and it never raises."""
    monkeypatch.setattr(deps, "settings",
                        Settings(trusted_proxies="nao-e-ip, 10.0.0.0/8"))
    assert deps._client_ip(pedido(peer=PROXY_LOCAL, forwarded=CLIENTE)) == CLIENTE
    monkeypatch.setattr(deps, "settings", Settings(trusted_proxies="nao-e-ip"))
    assert deps._client_ip(pedido(peer=PROXY_LOCAL, forwarded=CLIENTE)) == PROXY_LOCAL


# ── One bucket per client, and not per string ─────────────────────

@pytest.fixture(autouse=True)
def limites_limpos(monkeypatch):
    """State only. Whether Redis is reachable is the subject of some of these
    tests, so it is not decided here."""
    monkeypatch.setattr(deps, "_redis_client", None, raising=False)
    monkeypatch.setattr(deps, "_redis_unavailable_until", 0.0, raising=False)
    monkeypatch.setattr(deps, "_redis_fallback_logged", False, raising=False)
    deps._rate_state.clear()
    deps._rate_limit_hits.clear()
    yield
    deps._rate_state.clear()
    deps._rate_limit_hits.clear()


@pytest.fixture
def sem_redis(monkeypatch):
    """For the tests about buckets, where the counter's home is not the point."""
    monkeypatch.setattr(deps, "_get_redis", lambda: None)


def _gastar(endereco, quantas, limite):
    recusas = 0
    for _ in range(quantas):
        try:
            deps._check_rate_limit(f"anon:{endereco}", limite)
        except Exception:
            recusas += 1
    return recusas


def test_clientes_distintos_tem_orcamentos_distintos(sem_redis):
    assert _gastar(CLIENTE, 3, 3) == 0
    assert _gastar(CLIENTE, 1, 3) == 1          # esgotado
    assert _gastar("198.51.100.8", 3, 3) == 0   # o vizinho nao foi afetado


def test_o_mesmo_cliente_compartilha_o_orcamento_entre_pedidos(com_proxy, sem_redis):
    """
    Two requests through the proxy, same client, same bucket — which only
    holds because the identity comes from the policy and not from the string
    the caller sent.
    """
    a = deps._client_ip(pedido(peer=PROXY_LOCAL, forwarded=CLIENTE))
    b = deps._client_ip(pedido(peer=PROXY_LOCAL, forwarded=f"{CLIENTE}, {PROXY_INTERNO}, {PROXY_LOCAL}"))
    assert a == b == CLIENTE


# ── Redis gone: the limiter degrades, loudly and cheaply ──────────

def test_redis_indisponivel_e_sondado_uma_vez_por_janela(monkeypatch):
    """
    Measured before this cache existed: one failed probe cost **15 seconds**,
    the client library's retries multiplying a one-second timeout, and every
    request paid it. The negative cache is the difference between a degraded
    limiter and an unusable API.
    """
    sondas = []
    monkeypatch.setattr(deps, "_porta_responde",
                        lambda h, p, t: sondas.append((h, p)) or False)

    assert deps._get_redis() is None
    for _ in range(50):
        assert deps._get_redis() is None
    assert len(sondas) == 1, f"probed {len(sondas)} times in one window"


def test_a_sonda_volta_a_acontecer_depois_da_janela(monkeypatch):
    sondas = []
    monkeypatch.setattr(deps, "_porta_responde",
                        lambda h, p, t: sondas.append(1) or False)
    deps._get_redis()
    deps._redis_unavailable_until = 0.0        # a janela expirou
    deps._get_redis()
    assert len(sondas) == 2


def test_a_degradacao_e_registrada_uma_vez(monkeypatch, caplog):
    monkeypatch.setattr(deps, "_porta_responde", lambda h, p, t: False)
    import logging
    with caplog.at_level(logging.WARNING, logger="openwec.api"):
        for _ in range(5):
            deps._redis_unavailable_until = 0.0
            deps._get_redis()
    degradacoes = [r for r in caplog.records if r.message == "rate_limit_degraded"]
    assert len(degradacoes) == 1


def test_a_sonda_e_limitada_por_nos_e_nao_pela_biblioteca():
    """
    `socket_connect_timeout` is not a bound: against a port that drops packets
    the client library turned a 1s timeout into 15s of retries. The probe this
    code uses is a plain connect with our own timeout.
    """
    import inspect
    fonte = inspect.getsource(deps._porta_responde)
    assert "socket.create_connection" in fonte
    assert "timeout=timeout" in fonte
    assert deps._REDIS_PROBE_TIMEOUT <= 1.0


def test_redis_disponivel_usa_o_contador_compartilhado(monkeypatch):
    chamadas = []

    class _FalsoRedis:
        def ping(self):
            return True

        def incr(self, chave):
            chamadas.append(chave)
            return len(chamadas)

        def expire(self, chave, ttl):
            return True

    modulo = types.SimpleNamespace(Redis=lambda **kw: _FalsoRedis())
    monkeypatch.setitem(__import__("sys").modules, "redis", modulo)
    monkeypatch.setattr(deps, "_porta_responde", lambda h, p, t: True)

    cliente = deps._get_redis()
    assert cliente is not None
    deps._check_rate_limit("anon:198.51.100.1", 10)
    assert chamadas, "the shared counter was not used"
    assert chamadas[0].startswith("rl:anon:198.51.100.1:")


# ── Several workers would multiply every limit ────────────────────

RAIZ = pathlib.Path(__file__).resolve().parents[2]


def test_a_imagem_roda_um_unico_processo():
    """
    The in-memory fallback counts per process. With Redis up that does not
    matter; with Redis down, N workers mean N times the limit.

    This guard is the cheap half of that problem: the image starts one uvicorn
    process, so the fallback is consistent today. If `--workers` is ever added,
    this fails and says what has to be true first.
    """
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert "--workers" not in dockerfile, (
        "the image now starts several workers: the in-memory rate-limit "
        "fallback counts per process, so Redis must be a hard dependency "
        "before this is acceptable"
    )
    assert "gunicorn" not in dockerfile


def test_a_imagem_desliga_a_reescrita_do_uvicorn():
    """
    uvicorn ships its own X-Forwarded-For handling, on by default, trusting
    `127.0.0.1` by default. It rewrites `scope["client"]` before any
    dependency runs, so `request.client.host` is already whatever the header
    said and the policy in `_client_ip` inspects a forgery.

    Measured against a server started without this flag, limit 3/min:
    six requests with six different forged headers all returned 200 — six
    budgets bought with a string. With the flag, the same six share one.

    Two trust policies mean the weaker one decides. There is one, and it is
    `TRUSTED_PROXIES`.
    """
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    cmd = [l for l in dockerfile.splitlines() if l.startswith("CMD")]
    assert cmd, "no CMD in the Dockerfile"
    assert "--no-proxy-headers" in cmd[0], (
        "uvicorn would rewrite request.client from X-Forwarded-For before "
        "api.deps._client_ip could apply the trusted-proxy policy"
    )
    assert "--proxy-headers" not in cmd[0].replace("--no-proxy-headers", "")


def test_o_estado_em_memoria_e_por_processo():
    """Stated as a test so the limitation is not folded away into a comment."""
    assert isinstance(deps._rate_state, dict)
    assert deps._rate_state is not getattr(deps, "_shared_rate_state", None)

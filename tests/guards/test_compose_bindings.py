"""
OpenWEC — Published ports stay on loopback

A compose `ports` entry written as `"5433:5432"` publishes on 0.0.0.0 and ::.
The development database carries the development superuser and the development
password — both `openwec`, both written in the compose file — so publishing it
to every interface puts a superuser shell on the network for anyone who can
reach the machine.

Measured on this machine before the fix, from this machine: the historical copy
accepted a connection on the Wi-Fi address with those credentials, and the
clusters bound to `127.0.0.1:` refused the same one. That is the binding doing
exactly what it says. It is not a claim that another machine could reach it --
the host firewall there blocked inbound to the Docker backend, so the exposure
was latent. A latent exposure held open by a firewall rule is still worth
closing at the line that does not depend on the firewall.

The same file also fixes how long the database gets to shut down; see the
second half of this module.

The host field must also be a **literal**. `${DB_BIND_HOST:-127.0.0.1}` looks
careful and is a wider default one environment variable away — set by a stray
export or an inherited shell, with no diff to review and nothing on screen to
say it happened.

The file tests read the compose files and start nothing. The two marked tests
run `docker compose config`, which resolves a file without creating anything,
and skip where docker is not installed.

    pytest tests/guards/test_compose_bindings.py -v
"""
import pathlib
import re
import shutil
import subprocess

import pytest


RAIZ = pathlib.Path(__file__).resolve().parents[2]

# Every compose file in the repository, not a list that a new one can escape.
COMPOSES = sorted(
    set(RAIZ.glob("docker-compose*.yml"))
    | set(RAIZ.glob("docker-compose*.yaml"))
    | set((RAIZ / "docker").glob("*.yml"))
    | set((RAIZ / "docker").glob("*.yaml"))
)

# A literal loopback host, and nothing else. No expansion: the point is that
# the interface cannot be moved from the environment.
LOOPBACK = re.compile(r"^(127\.0\.0\.1|\[::1\]|::1):")

EXPANSAO = re.compile(r"\$\{[^}]*\}")

PUBLICACAO = re.compile(r'^\s*-\s*"?([^"\s#]+)"?\s*$')

COMPOSE_DISPONIVEL = shutil.which("docker") is not None
precisa_docker = pytest.mark.skipif(
    not COMPOSE_DISPONIVEL, reason="docker not installed"
)


def _mascarar(spec: str) -> str:
    """
    Same string, same length, with the colons that are not separators hidden:
    the ones inside `${...}` and inside an IPv6 literal's brackets. Keeping
    the length means an index into the mask is an index into the original.
    """
    mascarado = EXPANSAO.sub(lambda m: "X" * len(m.group(0)), spec)
    return re.sub(r"\[[^\]]*\]", lambda m: "X" * len(m.group(0)), mascarado)


def campos(spec: str) -> int:
    """Fields in a `ports` entry, counting only real separators."""
    return _mascarar(spec).count(":") + 1


def hospedeiro(spec: str) -> str:
    """The host field of a three-field entry, brackets and expansion kept."""
    mascara = _mascarar(spec)
    if ":" not in mascara:
        return spec
    return spec[:mascara.index(":")]


def entradas_de_porta(caminho: pathlib.Path):
    """Every `ports:` list item in the file, with its line number."""
    achadas = []
    dentro = False
    for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
        despido = linha.strip()
        if despido.startswith("#"):
            continue
        if despido == "ports:":
            dentro = True
            continue
        if dentro:
            m = PUBLICACAO.match(linha)
            if m and ":" in m.group(1):
                achadas.append((numero, m.group(1)))
            elif despido and not despido.startswith("-"):
                dentro = False
    return achadas


def test_ha_arquivos_de_compose_para_examinar():
    """A glob that matches nothing would make every test below vacuous."""
    assert COMPOSES, "no compose file found"
    nomes = {p.name for p in COMPOSES}
    assert "docker-compose.prod.yml" in nomes
    assert any(p.parent.name == "docker" for p in COMPOSES)


@pytest.mark.parametrize("caminho", COMPOSES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_toda_porta_publicada_declara_a_interface(caminho):
    """
    `"5433:5432"` is two fields and means every interface. A published port
    must carry a host, so that reaching outside the machine is a decision
    somebody wrote down.
    """
    sem_interface = [
        (numero, spec) for numero, spec in entradas_de_porta(caminho)
        if campos(spec) < 3
    ]
    assert sem_interface == [], (
        f"{caminho.name}: published without a host interface {sem_interface}"
    )


@pytest.mark.parametrize("caminho", COMPOSES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_toda_porta_publicada_fica_no_loopback(caminho):
    fora = [
        (numero, spec) for numero, spec in entradas_de_porta(caminho)
        if not LOOPBACK.match(spec)
    ]
    assert fora == [], f"{caminho.name}: published off loopback {fora}"


@pytest.mark.parametrize("caminho", COMPOSES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_interface_e_literal_e_nao_uma_variavel(caminho):
    """
    The port may come from the environment. The interface may not: a variable
    there is a wider binding that nobody has to approve.
    """
    variaveis = [
        (numero, spec) for numero, spec in entradas_de_porta(caminho)
        if "${" in hospedeiro(spec)
    ]
    assert variaveis == [], (
        f"{caminho.name}: host interface comes from the environment {variaveis}"
    )


def test_o_guarda_reconhece_as_formas_que_importam():
    """The guard is only worth having if it rejects what actually went wrong."""
    aceitas = [
        "127.0.0.1:5433:5432",
        "127.0.0.1:8000:8000",
        "127.0.0.1:${DB_PORT:-5433}:5432",
        "[::1]:5433:5432",
    ]
    recusadas = [
        "0.0.0.0:5433:5432",
        "[::]:5433:5432",
        "::  :5433:5432".replace(" ", ""),
        "192.168.0.61:5433:5432",
        "172.30.224.1:5433:5432",
        "${DB_BIND_HOST:-127.0.0.1}:${DB_PORT:-5433}:5432",
    ]
    for spec in aceitas:
        assert LOOPBACK.match(spec), f"should be accepted: {spec}"
    for spec in recusadas:
        assert not LOOPBACK.match(spec) or "${" in hospedeiro(spec), \
            f"should be refused: {spec}"

    # The two-field form is the original mistake, and the variable's own colon
    # must not be miscounted as a field.
    assert campos("5433:5432") == 2
    assert campos("${DB_PORT:-5433}:5432") == 2
    assert campos("127.0.0.1:${DB_PORT:-5433}:5432") == 3
    assert campos("[::1]:5433:5432") == 3

    assert hospedeiro("127.0.0.1:5433:5432") == "127.0.0.1"
    assert hospedeiro("[::1]:5433:5432") == "[::1]"
    assert hospedeiro("${DB_BIND_HOST:-127.0.0.1}:5433:5432") == "${DB_BIND_HOST:-127.0.0.1}"


# ── What compose actually resolves ────────────────────────────────

DEV = RAIZ / "docker" / "docker-compose.yml"


def _resolve(env_extra=None):
    import os
    env = dict(os.environ)
    env.pop("DB_PORT", None)
    env.pop("DB_BIND_HOST", None)
    env.update(env_extra or {})
    r = subprocess.run(
        ["docker", "compose", "-f", str(DEV), "config"],
        capture_output=True, text=True, env=env, cwd=str(RAIZ), timeout=120,
    )
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout


@precisa_docker
def test_compose_config_resolve_para_loopback():
    saida = _resolve()
    assert "host_ip: 127.0.0.1" in saida
    assert "0.0.0.0" not in saida
    assert "published: \"5433\"" in saida or "published: 5433" in saida


@precisa_docker
def test_compose_config_respeita_a_porta_escolhida():
    saida = _resolve({"DB_PORT": "5599"})
    assert "host_ip: 127.0.0.1" in saida
    assert "5599" in saida


@precisa_docker
def test_db_bind_host_no_ambiente_nao_muda_a_interface():
    """The variable is gone from the file, so setting it is inert."""
    saida = _resolve({"DB_BIND_HOST": "0.0.0.0"})
    assert "host_ip: 127.0.0.1" in saida
    assert "0.0.0.0" not in saida


# ── Shutdown gets time to finish ──────────────────────────────────
#
# The image stops on SIGINT, which Postgres reads as a fast shutdown: it still
# has to flush buffers and write a shutdown checkpoint. Docker sends SIGKILL
# when the grace period runs out, and an undeclared grace period is whatever
# the daemon decides. Measured on the machine that prompted this: one second,
# on every container, and 25 starts reporting automatic recovery against one
# clean shutdown.
#
# Crash recovery has replayed the WAL every time, so the symptom is invisible
# until the once it is not. These tests keep the declaration in the file.

GRACA_MINIMA = 60  # seconds

DURACAO = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def segundos(valor: str) -> int:
    """Seconds in a compose duration: `120`, `120s`, `2m`, `2m0s`, `1h30m`."""
    texto = valor.strip().strip("\"'")
    if texto.isdigit():
        return int(texto)
    m = DURACAO.fullmatch(texto)
    if not m or not any(m.groups()):
        raise ValueError(f"unreadable duration: {valor!r}")
    horas, minutos, segs = (int(g or 0) for g in m.groups())
    return horas * 3600 + minutos * 60 + segs


def chave_do_servico(caminho: pathlib.Path, servico: str, chave: str):
    """
    The value of one top-level key of one service, or None.

    Hand-parsed for the same reason the port reader above is: the guard reads
    the file as written, so a key that only appears after an override or a
    merge does not count as present.
    """
    dentro = False
    recuo = None
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        despido = linha.strip()
        if not despido or despido.startswith("#"):
            continue
        atual = len(linha) - len(linha.lstrip())
        if despido == f"{servico}:":
            dentro, recuo = True, atual
            continue
        if dentro:
            if atual <= recuo:
                break
            if atual == recuo + 2 and despido.startswith(f"{chave}:"):
                return despido.split(":", 1)[1].strip()
    return None


def test_o_banco_declara_quanto_tempo_tem_para_desligar():
    declarado = chave_do_servico(DEV, "db", "stop_grace_period")
    assert declarado is not None, (
        "docker/docker-compose.yml: service `db` declares no stop_grace_period, "
        "so the shutdown window is whatever the daemon defaults to"
    )
    assert segundos(declarado) >= GRACA_MINIMA, (
        f"stop_grace_period is {declarado} — too short for a multi-gigabyte "
        f"cluster to write its shutdown checkpoint"
    )


def test_o_guarda_le_as_formas_de_duracao():
    """The guard is only worth having if it reads what compose accepts."""
    assert segundos("2m") == 120
    assert segundos("2m0s") == 120
    assert segundos("120s") == 120
    assert segundos("120") == 120
    assert segundos("1h30m") == 5400
    assert segundos('"2m"') == 120

    # And it must call the short ones short, which is the regression it exists
    # to catch.
    assert segundos("1s") < GRACA_MINIMA
    assert segundos("10s") < GRACA_MINIMA
    assert segundos("10") < GRACA_MINIMA

    for ruim in ("", "dois minutos", "m", "2x"):
        with pytest.raises(ValueError):
            segundos(ruim)


def test_o_leitor_de_chaves_nao_atravessa_servicos():
    """A key read from the wrong service would make the guard above a lie."""
    assert chave_do_servico(DEV, "db", "container_name") == "openwec-db"
    assert chave_do_servico(DEV, "db", "restart") == "unless-stopped"
    assert chave_do_servico(DEV, "db", "chave_que_nao_existe") is None
    assert chave_do_servico(DEV, "servico_que_nao_existe", "restart") is None


@precisa_docker
def test_compose_config_resolve_o_tempo_de_desligamento():
    saida = _resolve()
    linha = [l for l in saida.splitlines() if "stop_grace_period" in l]
    assert linha, "compose resolved no stop_grace_period for the service"
    valor = linha[0].split(":", 1)[1].strip()
    assert segundos(valor) >= GRACA_MINIMA, f"compose resolved {valor}"

"""
OpenWEC — Published ports stay on loopback

A compose `ports` entry written as `"5433:5432"` publishes on 0.0.0.0 and ::.
The development database carries the development superuser and the development
password — both `openwec`, both written in the compose file — so publishing it
to every interface puts a superuser shell on the network for anyone who can
reach the machine.

Measured on this machine before the fix: the historical copy authenticated over
the Wi-Fi address with those credentials.

These tests read the compose files. They start nothing and connect to nothing.

    pytest tests/guards/test_compose_bindings.py -v
"""
import pathlib
import re

import pytest


RAIZ = pathlib.Path(__file__).resolve().parents[2]

COMPOSES = [
    RAIZ / "docker" / "docker-compose.yml",
    RAIZ / "docker-compose.prod.yml",
]

# "127.0.0.1:5433:5432" or "${DB_BIND_HOST:-127.0.0.1}:${DB_PORT:-5433}:5432"
LOOPBACK = re.compile(r"^(127\.0\.0\.1|\[?::1\]?|\$\{[A-Z_]+:-(127\.0\.0\.1|\[?::1\]?)\}):")

PUBLICACAO = re.compile(r'^\s*-\s*"?([^"\s#]+)"?\s*$')

# `${DB_PORT:-5433}` carries a colon of its own, so counting raw colons would
# read a two-field spec as three and wave the mistake through.
EXPANSAO = re.compile(r"\$\{[^}]*\}")


def campos(spec: str) -> int:
    return EXPANSAO.sub("X", spec).count(":") + 1


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


@pytest.mark.parametrize("caminho", COMPOSES, ids=lambda p: p.name)
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


@pytest.mark.parametrize("caminho", COMPOSES, ids=lambda p: p.name)
def test_toda_porta_publicada_fica_no_loopback(caminho):
    fora = [
        (numero, spec) for numero, spec in entradas_de_porta(caminho)
        if not LOOPBACK.match(spec)
    ]
    assert fora == [], f"{caminho.name}: published off loopback {fora}"


def test_o_guarda_reconhece_as_formas_que_importam():
    """The guard is only worth having if it rejects what actually went wrong."""
    aceitas = [
        "127.0.0.1:5433:5432",
        "127.0.0.1:8000:8000",
        "${DB_BIND_HOST:-127.0.0.1}:${DB_PORT:-5433}:5432",
    ]
    recusadas = [
        "0.0.0.0:5433:5432",
        "192.168.0.61:5433:5432",
        "${DB_BIND_HOST:-0.0.0.0}:5433:5432",
    ]
    for spec in aceitas:
        assert LOOPBACK.match(spec), f"should be accepted: {spec}"
    for spec in recusadas:
        assert not LOOPBACK.match(spec), f"should be refused: {spec}"

    # The two-field form is the original mistake. It is caught by the other
    # test, and only if the variable's own colon is not miscounted as a field.
    assert campos("5433:5432") == 2
    assert campos("${DB_PORT:-5433}:5432") == 2
    assert campos("${DB_BIND_HOST:-127.0.0.1}:${DB_PORT:-5433}:5432") == 3

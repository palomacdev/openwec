"""
The gates that stand between a write test and a real table.

These run everywhere: the rule is a pure function, so it can be checked
against the numbers of a database without connecting to one.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.loader.sandbox import (  # noqa: E402
    DISPOSABLE_ENV,
    FIXTURE_CEILING,
    SANDBOX_TABLES,
    declared_disposable,
    refusal,
)


def test_fixturas_do_ci_passam():
    """tests/fixtures.sql semeia unidades de linhas."""
    assert refusal({"results": 1, "laps": 0, "cars": 1}) is None


def test_banco_vazio_passa():
    assert refusal({"results": 0, "laps": 0, "cars": 0}) is None


def test_o_banco_de_desenvolvimento_e_recusado():
    """
    Os números são os do banco de desenvolvimento, medidos. A recusa não
    depende de nome nem de porta: o CI usa `openwec` na 5433, exatamente como
    ele.
    """
    motivo = refusal({"results": 671_353, "laps": 2_034_223, "cars": 671_353})
    assert motivo is not None
    assert "acervo historico" in motivo
    assert "results=671353" in motivo


def test_a_recusa_nomeia_o_que_a_motivou():
    motivo = refusal({"results": 5, "laps": 2_034_223, "cars": 5})
    assert motivo is not None
    assert "laps=2034223" in motivo
    assert "results" not in motivo.split("(")[1].split(")")[0]


def test_uma_tabela_acima_do_teto_ja_recusa():
    for tabela, teto in FIXTURE_CEILING.items():
        counts = {t: 0 for t in FIXTURE_CEILING}
        counts[tabela] = teto + 1
        assert refusal(counts) is not None, f"{tabela} acima do teto devia recusar"


def test_exatamente_no_teto_ainda_passa():
    assert refusal({t: teto for t, teto in FIXTURE_CEILING.items()}) is None


def test_a_variavel_sozinha_nao_basta():
    """
    A declaração é necessária e não suficiente: a recusa acima vale mesmo com
    ela ligada, e é essa a diferença para o guard que só olhava a variável.
    """
    anterior = os.environ.get(DISPOSABLE_ENV)
    os.environ[DISPOSABLE_ENV] = "1"
    try:
        assert declared_disposable() is True
        assert refusal({"results": 671_353, "laps": 0, "cars": 0}) is not None
    finally:
        if anterior is None:
            os.environ.pop(DISPOSABLE_ENV, None)
        else:
            os.environ[DISPOSABLE_ENV] = anterior


@pytest.mark.parametrize("valor,esperado", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("Yes", True),
    ("", False), ("0", False), ("no", False), ("sim", False), ("  ", False),
])
def test_leitura_da_declaracao(valor, esperado):
    anterior = os.environ.get(DISPOSABLE_ENV)
    os.environ[DISPOSABLE_ENV] = valor
    try:
        assert declared_disposable() is esperado
    finally:
        if anterior is None:
            os.environ.pop(DISPOSABLE_ENV, None)
        else:
            os.environ[DISPOSABLE_ENV] = anterior


def test_sem_a_declaracao_nada_roda():
    anterior = os.environ.get(DISPOSABLE_ENV)
    os.environ.pop(DISPOSABLE_ENV, None)
    try:
        assert declared_disposable() is False
    finally:
        if anterior is not None:
            os.environ[DISPOSABLE_ENV] = anterior


def test_o_sandbox_cobre_as_tabelas_que_o_loader_escreve():
    """
    `upsert_car` escreve em cars, `insert_result` em results, e as duas
    dependem de teams e sessions. Resolver uma sessao le ainda series,
    seasons e events. `upsert_driver` escreve em drivers, e e por isso que
    ela esta aqui: uma tabela de fora faria o teste cair em `public` pelo
    search_path — lendo o acervo real, ou, no caso de drivers, escrevendo
    nele.
    """
    assert set(SANDBOX_TABLES) == {"series", "seasons", "events", "teams",
                                   "drivers", "sessions", "cars", "results"}


def test_a_limpeza_nao_e_por_delete():
    """
    O risco que este ajuste elimina: um DELETE por número e equipe podia
    alcançar linhas preexistentes. A limpeza passou a ser DROP SCHEMA CASCADE,
    e não há DELETE em nenhuma das suítes de loader.
    """
    aqui = Path(__file__).resolve().parent
    for arquivo in sorted(aqui.glob("*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        assert "DELETE FROM" not in texto.upper() or "nao ha DELETE" in texto, (
            f"{arquivo.name} voltou a apagar linhas"
        )
    conftest = (aqui / "conftest.py").read_text(encoding="utf-8")
    assert "DROP SCHEMA" in conftest

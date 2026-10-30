"""
The one published car that may stand without a lap, and everything that may not.

PR #54 measured the ten sessions blocked only by an unmatched published car:
eleven cars, every one of them credited with `LAPS = 0` under a status saying
it did not take part. A car that completed no lap cannot appear in a lap log,
so its absence there is what the two sources agreeing looks like.

What these tests guard is the narrowness. The exception has to admit that one
shape and keep refusing every other unmatched number, so the positive cases
are outnumbered here on purpose.

No database: the cursor answers from a queue and records every statement.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.recover_classification import (  # noqa: E402
    Plan,
    RecoveryBlocked,
    assert_complete_match,
    assert_zero_lap_identity,
    plan_recovery,
    recover_session,
    zero_lap_exception,
)

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402


def carro(numero: str, laps: str = "0", status: str = "Not classified",
          **extra) -> dict:
    linha = {"NUMBER": numero, "LAPS": laps, "STATUS": status}
    linha.update(extra)
    return linha


def com_voltas(**por_numero: list[int]) -> CursorFalso:
    return CursorFalso([[(n, ids) for n, ids in por_numero.items()]])


# ── The predicate ────────────────────────────────────────────

@pytest.mark.parametrize("status", [
    "Not classified", "Not Classified", "NOT CLASSIFIED",
    "Not started", "Not Started", "DNS",
])
def test_zero_voltas_com_status_de_quem_nao_correu_e_admitido(status):
    assert zero_lap_exception(carro("47", "0", status)) is True


@pytest.mark.parametrize("laps,status", [
    ("1", "Not classified"),      # correu: a ausencia no log e desacordo
    ("62", "Classified"),
    ("0", "Classified"),          # classificado sem volta nenhuma: nao
    ("0", "Retired"),             # abandonou, o que e outra coisa
    ("0", "DNF"),
    ("0", "DSQ"),
    ("0", "Other"),
    ("0", ""),                    # sem status: "Other"
    ("", "Not classified"),       # LAPS ausente nao e LAPS zero
    ("  ", "Not classified"),
    ("abc", "Not classified"),    # ilegivel nao e zero
])
def test_tudo_o_mais_continua_recusado(laps, status):
    assert zero_lap_exception(carro("47", laps, status)) is False


def test_laps_vazio_nao_e_zero():
    """
    `as_int` answers None for an empty field and 0 only for a written zero.

    The distinction is the whole safety of this rule: a source that simply
    does not fill LAPS must not look like one that says the car ran nothing.
    """
    assert zero_lap_exception(carro("47", "", "Not classified")) is False
    assert zero_lap_exception(carro("47", "0", "Not classified")) is True


# ── Planning ─────────────────────────────────────────────────

def test_o_carro_de_zero_voltas_sai_dos_nao_pareados():
    plano = plan_recovery(com_voltas(**{"3": [10]}), 1,
                          [carro("3", "62", "Classified"), carro("47")])
    assert plano.matched == {"3": 10}
    assert plano.zero_lap == ["47"]
    assert plano.source_without_laps == []
    assert_complete_match(plano)          # nao levanta


def test_o_carro_que_correu_e_sumiu_continua_bloqueando():
    plano = plan_recovery(com_voltas(**{"3": [10]}), 1,
                          [carro("3", "62", "Classified"),
                           carro("88", "40", "Classified")])
    assert plano.zero_lap == []
    assert plano.source_without_laps == ["88"]
    with pytest.raises(RecoveryBlocked) as exc:
        assert_complete_match(plano)
    assert "88" in str(exc.value)


def test_zero_voltas_com_status_de_quem_correu_continua_bloqueando():
    plano = plan_recovery(com_voltas(**{"3": [10]}), 1,
                          [carro("3", "62", "Classified"),
                           carro("47", "0", "Classified")])
    assert plano.zero_lap == []
    with pytest.raises(RecoveryBlocked):
        assert_complete_match(plano)


def test_a_volta_sem_publicacao_nao_e_perdoada_pela_excecao():
    """
    The exception is one-sided, and has to stay so.

    A car the lap log knows and the source does not is a missing result, not
    a car that did not run; admitting the session would delete its rows and
    put nothing back.
    """
    plano = plan_recovery(com_voltas(**{"3": [10], "88": [11]}), 1,
                          [carro("3", "62", "Classified"), carro("47")])
    assert plano.zero_lap == ["47"]
    assert plano.laps_without_source == {"88": 11}
    with pytest.raises(RecoveryBlocked) as exc:
        assert_complete_match(plano)
    assert "sem linha publicada" in str(exc.value)


def test_o_numero_repetido_continua_bloqueando_antes_da_excecao():
    with pytest.raises(RecoveryBlocked) as exc:
        plan_recovery(com_voltas(**{"3": [10]}), 1,
                      [carro("3", "62", "Classified"), carro("47"), carro("47")])
    assert "mais de uma vez" in str(exc.value)


def test_o_numero_em_dois_car_id_continua_bloqueando_antes_da_excecao():
    with pytest.raises(RecoveryBlocked) as exc:
        plan_recovery(com_voltas(**{"3": [10, 11]}), 1,
                      [carro("3", "62", "Classified"), carro("47")])
    assert "mais de um car_id" in str(exc.value)


def test_varios_carros_de_zero_voltas_sao_admitidos_juntos():
    plano = plan_recovery(com_voltas(**{"3": [10]}), 1,
                          [carro("3", "62", "Classified"),
                           carro("5"), carro("98", "0", "Not started")])
    assert plano.zero_lap == ["5", "98"]
    assert_complete_match(plano)


# ── Identity for the admitted car ────────────────────────────

def identidade(car_id: int | None, team_id: int = 7) -> list:
    """
    The answers upsert_team and upsert_car consume, in order.

    Since R2-B the car lookup is scoped by session and answers two columns —
    the id and whether that car already carries this session's laps. A
    zero-lap car never carries one, so the flag is always False here.
    """
    respostas = [[(team_id,)], []]            # team insert, advisory lock
    if car_id is None:
        respostas += [[], [(99,)]]            # lookup misses, insert returns
    else:
        respostas += [[(car_id, False)]]      # lookup finds, sem voltas
    return respostas


def test_o_carro_admitido_ganha_identidade():
    cur = CursorFalso(identidade(None))
    plano = Plan(matched={"3": 10}, zero_lap=["47"])
    resolvidos = assert_zero_lap_identity(
        cur, plano, {"47": carro("47", TEAM="Lanan Racing")}, "ELMS", 2226)
    assert resolvidos == {"47": 99}
    assert "INSERT INTO cars" in cur.sql


def test_a_identidade_existente_e_reaproveitada():
    """Second run: the lookup finds the row the first run made."""
    cur = CursorFalso(identidade(99))
    resolvidos = assert_zero_lap_identity(
        cur, Plan(matched={"3": 10}, zero_lap=["47"]),
        {"47": carro("47", TEAM="Lanan Racing")}, "ELMS", 2226)
    assert resolvidos == {"47": 99}
    assert "INSERT INTO cars" not in cur.sql


def test_a_identidade_que_colide_com_um_pareado_e_recusada():
    """
    Two published rows cannot share a `car_id`: `results` is keyed on it.

    If the zero-lap car resolves to a car already carrying one of this
    session's matched results, writing both would make the second overwrite
    the first in silence.
    """
    cur = CursorFalso(identidade(10))
    with pytest.raises(RecoveryBlocked) as exc:
        assert_zero_lap_identity(
            cur, Plan(matched={"3": 10}, zero_lap=["47"]),
            {"47": carro("47", TEAM="Lanan Racing")}, "ELMS", 2226)
    assert "mesma identidade" in str(exc.value)


def test_sem_identidade_possivel_a_recuperacao_para():
    cur = CursorFalso([[(7,)], [], [], []])   # insert devolve nada
    with pytest.raises(RecoveryBlocked) as exc:
        assert_zero_lap_identity(
            cur, Plan(zero_lap=["47"]), {"47": carro("47")}, "ELMS", 2226)
    assert "identidade criada" in str(exc.value)


def test_sem_carros_de_zero_voltas_nada_e_consultado():
    cur = CursorFalso()
    assert assert_zero_lap_identity(
        cur, Plan(matched={"3": 10}), {}, "ELMS", 2226) == {}
    assert cur.executados == []


def test_a_busca_do_carro_de_zero_voltas_e_escopada_pela_sessao():
    """
    The lookup names the session, and names it as the only scope: a car of
    another session can never answer it, whatever its attributes.
    """
    cur = CursorFalso(identidade(99))
    assert_zero_lap_identity(
        cur, Plan(matched={"3": 10}, zero_lap=["47"]),
        {"47": carro("47", TEAM="Lanan Racing")}, "ELMS", 2226)

    busca = [(s, p) for s, p in cur.executados if "FROM cars c" in s]
    assert len(busca) == 1
    sql, params = busca[0]
    assert "r.session_id = %(sessao)s" in sql
    assert "l.session_id = %(sessao)s" in sql
    assert params["sessao"] == 2226


# ── The write path ───────────────────────────────────────────

LINHAS = [carro("3", "62", "Classified"), carro("47")]


def sequencia_de_escrita(car_id_sem_voltas: int) -> list:
    return (
        identidade(car_id_sem_voltas)         # team + car do carro 47
        + [[]]                                # DELETE results, nada a remover
        + [[(None, None, None, None)], [(500,)]]   # carro 3: colunas, result
        + [[(None, None, None, None)], [(501,)]]   # carro 47: colunas, result
    )


def test_o_carro_admitido_entra_no_keep_do_delete():
    """
    The reason it is resolved before the delete and not after.

    A zero-lap car has no lap to protect it: if its id were missing from
    `keep`, the next run would delete the result this one wrote and mint a
    new car for it.
    """
    cur = CursorFalso(sequencia_de_escrita(99))
    recover_session(cur, 2226, LINHAS, "ELMS",
                    Plan(matched={"3": 10}, zero_lap=["47"]))

    apagar = [(sql, p) for sql, p in cur.executados
              if sql.startswith("DELETE FROM results")]
    assert len(apagar) == 1
    assert apagar[0][1] == (2226, [10, 99])


def test_o_carro_admitido_recebe_resultado():
    cur = CursorFalso(sequencia_de_escrita(99))
    relatorio = recover_session(cur, 2226, LINHAS, "ELMS",
                                Plan(matched={"3": 10}, zero_lap=["47"]))
    assert relatorio["results_gravados"] == 2
    assert relatorio["cars_sem_voltas"] == {"47": 99}

    inseridos = [p for sql, p in cur.executados
                 if sql.startswith("INSERT INTO results")]
    assert len(inseridos) == 2
    assert [p[1] for p in inseridos] == [10, 99]


def test_nenhuma_volta_e_inventada():
    """The exception adds a result. It must not add a lap."""
    cur = CursorFalso(sequencia_de_escrita(99))
    recover_session(cur, 2226, LINHAS, "ELMS",
                    Plan(matched={"3": 10}, zero_lap=["47"]))
    for sql, _ in cur.executados:
        assert not sql.upper().startswith(("INSERT INTO LAPS", "UPDATE LAPS",
                                           "DELETE FROM LAPS"))


def test_o_plano_incompleto_ainda_para_antes_do_delete():
    cur = CursorFalso()
    with pytest.raises(RecoveryBlocked):
        recover_session(cur, 2226, LINHAS, "ELMS",
                        Plan(matched={"3": 10}, source_without_laps=["88"]))
    assert cur.executados == []

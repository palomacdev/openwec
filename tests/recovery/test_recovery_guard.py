"""
The three gates that stand between this module and a database.

Recovery is the first routine in the project that deletes rows. The gates are
therefore tested the way the merge guard is: by asserting over the statements
a fake cursor records, so what is checked is what would be sent, not what a
run happened to leave behind.

No database. The cursor here answers from a queue.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.recover_classification import (  # noqa: E402
    DISPOSABLE_ENV,
    MARKER_TABLE,
    REHEARSAL_ID_ENV,
    Plan,
    RecoveryBlocked,
    assert_complete_match,
    assert_writable,
    drop_lap_shaped_results,
    fill_car_gaps,
    identity_refusal,
    marker_sql,
    plan_recovery,
    recover_session,
    refusal,
    target_config,
    team_divergence,
)

ALVO = {"host": "127.0.0.1", "port": 5455, "dbname": "openwec",
        "user": "openwec", "password": "x"}

ID = "a1b2c3d4"
CLUSTER = "7461923100000000001"

# to_regclass answers first, then the identity query.
MARCA = [(f"public.{MARKER_TABLE}",)]
IDENTIDADE = [(ID, CLUSTER, CLUSTER)]


class CursorFalso:
    """Records every statement and answers from a queue of results."""

    def __init__(self, respostas: list | None = None):
        self.respostas = list(respostas or [])
        self.executados: list[tuple[str, object]] = []
        self.rowcount = 0
        self._atual: list = []

    def execute(self, sql, params=None):
        self.executados.append((" ".join(str(sql).split()), params))
        self._atual = self.respostas.pop(0) if self.respostas else []
        self.rowcount = len(self._atual)

    def fetchone(self):
        return self._atual[0] if self._atual else None

    def fetchall(self):
        return self._atual

    @property
    def sql(self) -> str:
        return " ".join(s for s, _ in self.executados)


@pytest.fixture
def ambiente(monkeypatch):
    for nome, valor in (("RECOVERY_DB_HOST", "127.0.0.1"),
                        ("RECOVERY_DB_PORT", "5455"),
                        ("RECOVERY_DB_NAME", "openwec"),
                        ("RECOVERY_DB_USER", "openwec"),
                        ("RECOVERY_DB_PASSWORD", "x")):
        monkeypatch.setenv(nome, valor)
    monkeypatch.setenv(DISPOSABLE_ENV, "1")
    monkeypatch.setenv(REHEARSAL_ID_ENV, ID)
    return monkeypatch


@pytest.fixture
def longe_do_historico(monkeypatch):
    """Pins the historical endpoint so gate 2 is not what a test measures."""
    import database.db as db
    monkeypatch.setitem(db.DB_CONFIG, "host", "127.0.0.1")
    monkeypatch.setitem(db.DB_CONFIG, "port", 5433)


# ── Where the connection comes from ──────────────────────────

def test_a_conexao_vem_so_das_variaveis_do_ensaio(ambiente):
    assert target_config() == ALVO


def test_sem_as_variaveis_nao_ha_alvo_nenhum(monkeypatch):
    for nome in ("RECOVERY_DB_HOST", "RECOVERY_DB_PORT",
                 "RECOVERY_DB_NAME", "RECOVERY_DB_USER"):
        monkeypatch.delenv(nome, raising=False)
    with pytest.raises(RecoveryBlocked) as exc:
        target_config()
    assert "RECOVERY_DB_HOST" in str(exc.value)


def test_a_configuracao_do_projeto_nao_serve_de_padrao(monkeypatch):
    """
    The historical settings must not be able to fill a gap here.

    `DB_HOST` and friends are set in this project's `.env`; if recovery read
    them when its own are missing, forgetting one variable would point the
    delete at the instance it exists to stay away from.
    """
    for nome in ("RECOVERY_DB_HOST", "RECOVERY_DB_PORT",
                 "RECOVERY_DB_NAME", "RECOVERY_DB_USER"):
        monkeypatch.delenv(nome, raising=False)
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5433")
    monkeypatch.setenv("DB_NAME", "openwec")
    monkeypatch.setenv("DB_USER", "openwec")
    with pytest.raises(RecoveryBlocked):
        target_config()


# ── The three gates ──────────────────────────────────────────

def test_sem_declaracao_nao_se_escreve(ambiente):
    ambiente.delenv(DISPOSABLE_ENV, raising=False)
    cur = CursorFalso([MARCA, IDENTIDADE])
    assert DISPOSABLE_ENV in refusal(ALVO, cur)


def test_o_endereco_historico_e_recusado(ambiente, longe_do_historico):
    cur = CursorFalso([MARCA, IDENTIDADE])
    historico = dict(ALVO, port=5433)
    assert "historico" in refusal(historico, cur)


def test_sem_a_marca_nao_se_escreve(ambiente, longe_do_historico):
    cur = CursorFalso([[(None,)]])
    assert MARKER_TABLE in refusal(ALVO, cur)


def test_com_as_cinco_abertas_o_alvo_e_aceito(ambiente, longe_do_historico):
    assert refusal(ALVO, CursorFalso([MARCA, IDENTIDADE])) is None
    assert_writable(ALVO, CursorFalso([MARCA, IDENTIDADE]))


def test_a_marca_e_consultada_sem_ser_criada(ambiente, longe_do_historico):
    cur = CursorFalso([MARCA, IDENTIDADE])
    refusal(ALVO, cur)
    assert "CREATE" not in cur.sql.upper()
    assert "INSERT" not in cur.sql.upper()
    assert "UPDATE" not in cur.sql.upper()
    assert "to_regclass" in cur.sql


# ── The identity of the target, which no name can supply ─────

def test_sem_identidade_declarada_nao_se_escreve(ambiente):
    ambiente.delenv(REHEARSAL_ID_ENV, raising=False)
    cur = CursorFalso([IDENTIDADE])
    assert REHEARSAL_ID_ENV in identity_refusal(cur)


def test_a_identidade_vazia_no_alvo_nao_se_escreve(ambiente):
    """The table exists and declares nothing: that is not a declaration."""
    cur = CursorFalso([[]])
    assert "nao declarou identidade" in identity_refusal(cur)


def test_a_identidade_divergente_e_recusada(ambiente):
    cur = CursorFalso([[("outro-ensaio", CLUSTER, CLUSTER)]])
    motivo = identity_refusal(cur)
    assert REHEARSAL_ID_ENV in motivo and "nao e a que o alvo guarda" in motivo


def test_o_cluster_incompativel_e_recusado(ambiente):
    """
    The case a copied marker produces.

    `system_identifier` is minted by `initdb` and travels in no dump, so a
    rehearsal restored into the historical cluster — or into production —
    arrives carrying a row whose identifier the cluster does not answer to.
    """
    cur = CursorFalso([[(ID, CLUSTER, "9999999999999999999")]])
    assert "nao e aquele em que esta identidade foi criada" in identity_refusal(cur)


def test_mais_de_uma_identidade_e_recusada(ambiente):
    cur = CursorFalso([[(ID, CLUSTER, CLUSTER), ("outro", CLUSTER, CLUSTER)]])
    assert "tem de ser uma so" in identity_refusal(cur)


@pytest.mark.parametrize("linha", [
    (None, CLUSTER, CLUSTER),
    (ID, None, CLUSTER),
    ("", CLUSTER, CLUSTER),
])
def test_a_identidade_incompleta_e_recusada(ambiente, linha):
    assert "incompleta" in identity_refusal(CursorFalso([[linha]]))


def test_o_cluster_sem_resposta_e_recusado(ambiente):
    cur = CursorFalso([[(ID, CLUSTER, None)]])
    assert "nao e aquele em que esta identidade foi criada" in identity_refusal(cur)


def test_a_identidade_completa_e_aceita(ambiente):
    assert identity_refusal(CursorFalso([IDENTIDADE])) is None


def test_nao_ha_argumento_que_pule_a_identidade():
    """
    The refusal depends on nothing the caller passes.

    `identity_refusal` takes only a cursor: there is no flag, no keyword and
    no environment value that turns it off, which is why `refusal` can end
    with it rather than around it.
    """
    import inspect
    assinatura = inspect.signature(identity_refusal)
    assert list(assinatura.parameters) == ["cur"]


# ── The statement that declares a copy ───────────────────────

def test_a_marca_e_declarada_lendo_o_proprio_cluster():
    """The identifier is read inside the target, not supplied from outside."""
    sql = marker_sql(ID)
    assert "FROM pg_control_system()" in sql
    assert f"'{ID}'" in sql


def test_um_identificador_fora_do_hexadecimal_e_recusado():
    with pytest.raises(RecoveryBlocked):
        marker_sql("'; DROP TABLE results; --")


def test_uma_nota_com_aspas_e_recusada():
    with pytest.raises(RecoveryBlocked):
        marker_sql(ID, note="o'brien")


def test_cada_chamada_gera_um_identificador_novo():
    assert marker_sql() != marker_sql()


# ── Matching, and what it refuses to guess ───────────────────

def linhas(*numeros: str) -> list[dict]:
    return [{"NUMBER": n} for n in numeros]


def test_o_pareamento_e_por_numero(ambiente):
    cur = CursorFalso([[("3", [10]), ("79", [11])]])
    plano = plan_recovery(cur, 1, linhas("3", "79"))
    assert plano.matched == {"3": 10, "79": 11}
    assert plano.source_without_laps == []
    assert plano.laps_without_source == {}


def test_a_linha_publicada_sem_voltas_nao_e_pareada(ambiente):
    cur = CursorFalso([[("3", [10])]])
    plano = plan_recovery(cur, 1, linhas("3", "88"))
    assert plano.matched == {"3": 10}
    assert plano.source_without_laps == ["88"]


def test_o_carro_com_voltas_sem_linha_publicada_e_relatado(ambiente):
    cur = CursorFalso([[("3", [10]), ("88", [12])]])
    plano = plan_recovery(cur, 1, linhas("3"))
    assert plano.laps_without_source == {"88": 12}


def test_o_numero_repetido_na_fonte_interrompe(ambiente):
    cur = CursorFalso([[("3", [10])]])
    with pytest.raises(RecoveryBlocked) as exc:
        plan_recovery(cur, 1, linhas("3", "3"))
    assert "mais de uma vez" in str(exc.value)


def test_o_numero_espalhado_por_dois_carros_interrompe(ambiente):
    """
    The condition a recovery must not settle on its own.

    If the laps of number 3 hang off two car rows, choosing either one
    attaches half a session's laps to a result that does not own them.
    """
    cur = CursorFalso([[("3", [10, 11])]])
    with pytest.raises(RecoveryBlocked) as exc:
        plan_recovery(cur, 1, linhas("3"))
    assert "mais de um car_id" in str(exc.value)


def test_a_linha_sem_numero_interrompe(ambiente):
    cur = CursorFalso([[("3", [10])]])
    with pytest.raises(RecoveryBlocked) as exc:
        plan_recovery(cur, 1, linhas("3", ""))
    assert "sem NUMBER" in str(exc.value)


# ── A partial match stops the write ──────────────────────────

def test_o_pareamento_completo_passa():
    assert_complete_match(Plan(matched={"3": 10}))


def test_o_publicado_sem_voltas_impede_a_escrita():
    with pytest.raises(RecoveryBlocked) as exc:
        assert_complete_match(Plan(matched={"3": 10},
                                   source_without_laps=["88", "12"]))
    assert "pareamento incompleto" in str(exc.value)
    assert "12, 88" in str(exc.value)


def test_as_voltas_sem_publicacao_impedem_a_escrita():
    with pytest.raises(RecoveryBlocked) as exc:
        assert_complete_match(Plan(matched={"3": 10},
                                   laps_without_source={"88": 12}))
    assert "sem linha publicada" in str(exc.value)
    assert "88" in str(exc.value)


def test_os_dois_lados_aparecem_na_mesma_recusa():
    with pytest.raises(RecoveryBlocked) as exc:
        assert_complete_match(Plan(matched={"3": 10},
                                   source_without_laps=["88"],
                                   laps_without_source={"12": 99}))
    motivo = str(exc.value)
    assert "publicados sem voltas no banco: 88" in motivo
    assert "sem linha publicada: 12" in motivo


def test_a_escrita_para_antes_de_qualquer_delete():
    """
    The check sits on the write path, not around it.

    Whoever calls `recover_session` with an incomplete plan gets a refusal
    before the first statement, so no DELETE can be reached by skipping the
    caller that was supposed to check.
    """
    cur = CursorFalso()
    with pytest.raises(RecoveryBlocked):
        recover_session(cur, 2226, [{"NUMBER": "3"}], "LEMANSCUP",
                        Plan(matched={"3": 10}, source_without_laps=["88"]))
    assert cur.executados == []


# ── The delete, and what it refuses to reach ─────────────────

def test_so_as_linhas_da_propria_sessao_sao_removidas():
    cur = CursorFalso([[(900,), (901,)], []])
    drop_lap_shaped_results(cur, 2226, [10, 11])
    sql, params = cur.executados[0]
    assert sql.startswith("DELETE FROM results")
    assert "session_id = %s" in sql
    assert params == (2226, [10, 11])


def test_o_carro_que_sustenta_voltas_nao_e_removido():
    cur = CursorFalso([[(900,)], []])
    drop_lap_shaped_results(cur, 2226, [10])
    sql, params = cur.executados[1]
    assert sql.startswith("DELETE FROM cars")
    assert "NOT EXISTS (SELECT 1 FROM laps" in sql
    assert "NOT EXISTS (SELECT 1 FROM results" in sql
    assert params == ([900],)


def test_sem_linha_a_remover_nenhum_carro_e_tocado():
    cur = CursorFalso([[]])
    assert drop_lap_shaped_results(cur, 2226, [10]) == (0, 0)
    assert len(cur.executados) == 1
    assert "DELETE FROM cars" not in cur.sql


# ── The car columns: filled where empty, never overwritten ───

LINHA = {"NUMBER": "3", "VEHICLE": "Norma M30 - Nissan", "CLASS": "LMP3",
         "GROUP": "", "TIRES": "M"}


def test_a_coluna_vazia_e_preenchida():
    cur = CursorFalso([[(None, None, None, None)], []])
    preenchidas, divergem = fill_car_gaps(cur, 10, LINHA)
    assert preenchidas == 3            # vehicle, car_class, tires; GROUP vazio
    assert divergem == []
    sql, params = cur.executados[1]
    assert sql.startswith("UPDATE cars SET")
    assert 10 in params


def test_a_coluna_divergente_e_relatada_e_mantida():
    cur = CursorFalso([[("Ligier JS P3 - Nissan", "LMP3", None, "M")]])
    preenchidas, divergem = fill_car_gaps(cur, 10, LINHA)
    assert preenchidas == 0
    assert divergem == ["vehicle"]
    assert "UPDATE" not in cur.sql


def test_a_equipe_divergente_e_relatada_sem_escrita():
    cur = CursorFalso([[("RLR M Sport",)]])
    assert team_divergence(cur, 10, "RLR MSport") == ("RLR M Sport", "RLR MSport")
    assert "UPDATE" not in cur.sql.upper()
    assert "INSERT" not in cur.sql.upper()


def test_a_equipe_igual_nao_vira_divergencia():
    cur = CursorFalso([[("Graff",)]])
    assert team_divergence(cur, 10, "Graff") is None


def test_sem_equipe_na_fonte_nada_e_comparado():
    cur = CursorFalso([[("Graff",)]])
    assert team_divergence(cur, 10, "") is None
    assert cur.executados == []


# ── The plan carries no surprises ────────────────────────────

def test_o_plano_comeca_vazio():
    plano = Plan()
    assert plano.matched == {}
    assert plano.source_without_laps == []
    assert plano.laps_without_source == {}

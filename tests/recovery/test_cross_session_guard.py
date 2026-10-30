"""
A recuperacao nao pode produzir identidade concorrente nem ligar uma sessao
ao carro de outra.

`cars` nao tem coluna de sessao, e a tupla de oito colunas descreve um carro
sem identificar uma inscricao. `MEASURED` no acervo: **20 grupos de tupla
identica com carros de sessoes diferentes**, o maior deles o numero 66, com
**78 carros em 53 sessoes**; nenhum deles carrega volta.

Desde R2-B a *resolucao* ja nao atravessa a sessao: `upsert_car` so procura
entre os carros que a propria sessao aponta (ADR-0003). O que estes testes
guardam e o outro lado, que nenhum escopo resolve — a recuperacao nao pode
**produzir** uma identidade concorrente, nem escolher carro por atributo para
uma inscricao que ja foi pareada pelo numero.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from database.recovery.recover_classification import (  # noqa: E402
    drop_lap_shaped_results, identity_twin, planned_identity,
)

FONTE = (RAIZ / "database" / "recovery" / "recover_classification.py"
         ).read_text(encoding="utf-8")

GUARDADO = ("66", 7, "Oreca 03", "LMP2", None, "M", None, None)


class Cursor:
    def __init__(self, guardado=GUARDADO, gemeo=None):
        self.guardado = guardado
        self.gemeo = gemeo
        self.sql: list[str] = []
        self.params: list = []
        self._um = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        texto = " ".join(sql.split())
        self.sql.append(texto)
        self.params.append(params)
        alvo = texto.upper()
        if alvo.startswith("SELECT NUMBER, TEAM_ID"):
            self._um = self.guardado
        elif "FROM CARS WHERE" in alvo and "ID <> " in alvo:
            self._um = (self.gemeo,) if self.gemeo else None
        else:
            self._um = None
            self.rowcount = 0

    def fetchone(self):
        return self._um


# ── a inscricao pareada nunca e resolvida por atributo ───────

def test_a_recuperacao_nao_escolhe_carro_pareado_por_atributo():
    """
    `recover_session` casa por **portador de voltas**, nunca por tupla. A
    unica chamada a `upsert_car` esta em `assert_zero_lap_identity`, para o
    carro que nao tem volta nenhuma para o proteger — e esse caso ja e
    recusado quando colide com um pareado.
    """
    corpo = FONTE.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    assert "upsert_car" not in corpo

    zero = FONTE.split("def assert_zero_lap_identity(", 1)[1].split("\ndef ", 1)[0]
    assert "upsert_car" in zero


def test_o_pareamento_vem_do_portador_de_voltas():
    plano = FONTE.split("def plan_recovery(", 1)[1].split("\ndef ", 1)[0]
    assert "lap_cars" in plano
    assert "upsert_car" not in plano


# ── nenhuma identidade concorrente sai daqui ─────────────────

def test_identidade_com_gemeo_nao_e_escrita():
    cur = Cursor(gemeo=70571)
    final = planned_identity(cur, 999, {"NUMBER": "66", "VEHICLE": "Oreca 03"})
    assert identity_twin(cur, 999, final, 1300) == 70571
    assert not any(s.upper().startswith("UPDATE CARS") for s in cur.sql)


def test_a_checagem_precede_as_escritas_que_ela_guarda():
    """
    A busca por gemeo vem antes de **toda escrita no carro** e antes da
    gravacao do resultado.

    Ela nao precede o `DELETE`, e nao precisa: o delete alcanca as linhas em
    forma de travessia e os carros que ficaram orfaos, nunca os carros
    preservados que esta checagem protege. Quem guarda o delete sao a
    checagem de pareamento completo e a comporta de pilotos, as duas antes
    dele.
    """
    corpo = FONTE.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    pos_gemeo = corpo.index("identity_twin")
    for depois in ("UPDATE cars SET team_id", "fill_car_gaps(", "insert_result("):
        assert pos_gemeo < corpo.index(depois), depois

    pos_delete = corpo.index("drop_lap_shaped_results")
    for antes in ("assert_complete_match", "assert_drivers_resolvable"):
        assert corpo.index(antes) < pos_delete, antes


def test_o_gemeo_e_reportado_e_nao_silencioso():
    corpo = FONTE.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    assert "gemeos_recusados" in corpo
    assert "cars_nao_atualizados_por_gemeo_de_identidade" in corpo


# ── as voltas nao sao tocadas ────────────────────────────────

def test_o_modulo_nunca_escreve_em_laps():
    """
    Nenhuma volta e removida, movida ou reapontada. `laps.car_id` e
    `ON DELETE NO ACTION`, entao o banco tambem recusaria — mas a recusa do
    banco so aparece em execucao, e isto aparece na leitura.
    """
    for verbo in (r"DELETE\s+FROM\s+laps", r"UPDATE\s+laps",
                  r"INSERT\s+INTO\s+laps"):
        assert not re.search(verbo, FONTE, re.IGNORECASE), verbo


def test_as_3386_voltas_da_970_sobrevivem_por_construcao():
    """
    A sessao 970 tem 21 carros com volta e **3.386** voltas.

    `laps` aparece uma vez no delete, e do lado certo: um `NOT EXISTS` que
    impede remover carro que carregue volta. Nunca como alvo de escrita.
    """
    corpo = FONTE.split("def drop_lap_shaped_results(", 1)[1].split("\ndef ", 1)[0]
    assert "NOT EXISTS (SELECT 1 FROM laps" in corpo
    for verbo in ("DELETE FROM laps", "UPDATE laps", "INSERT INTO laps"):
        assert verbo not in corpo


# ── os 21 carros da 970 ficam no keep ────────────────────────

class CursorDelete:
    def __init__(self):
        self.sql: list[str] = []
        self.params: list = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)
        self.rowcount = 0

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []


def test_os_21_portadores_da_970_entram_no_keep():
    keep = list(range(1, 22))
    cur = CursorDelete()
    drop_lap_shaped_results(cur, 970, keep)
    apagar = [(s, p) for s, p in zip(cur.sql, cur.params)
              if s.upper().startswith("DELETE FROM RESULTS")]
    assert len(apagar) == 1
    sessao, preservados = apagar[0][1]
    assert sessao == 970
    assert preservados == keep
    assert len(preservados) == 21


# ── a recusa por gemeo alcanca so o enriquecimento do carro ──

def test_a_recusa_por_gemeo_nao_impede_resultado_nem_vinculo():
    """
    Na sessao 1300 os 41 carros preservados teriam gemeo, e os 41 resultados
    e os 112 vinculos precisam ser gravados assim mesmo.

    A prova e estrutural: `insert_result` e `insert_result_drivers` ficam
    **fora** do `else` do gemeo, no mesmo nivel do `if`. So as escritas no
    carro — a equipe e `fill_car_gaps` — estao dentro dele.
    """
    corpo = FONTE.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    bloco = corpo.split("if gemeo is not None:", 1)[1]
    dentro = bloco.split("\n        equipe = team_divergence", 1)[0]

    # dentro do ramo: so as escritas no carro
    assert "UPDATE cars SET team_id" in dentro
    assert "fill_car_gaps(" in dentro
    assert "insert_result(" not in dentro
    assert "insert_result_drivers(" not in dentro
    assert "drop_lap_shaped_results" not in dentro

    # fora do ramo: o resultado e os vinculos
    depois = bloco.split("\n        equipe = team_divergence", 1)[1]
    assert "insert_result(" in depois
    assert "insert_result_drivers(" in depois


def test_a_recusa_por_gemeo_e_reportada_por_numero():
    """Cada recusa aparece no relatorio com o carro e o id do gemeo."""
    corpo = FONTE.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    assert "gemeos_recusados[numero] = gemeo" in corpo
    assert '"cars_nao_atualizados_por_gemeo_de_identidade": gemeos_recusados' in corpo


def test_o_guarda_nao_tem_bandeira_que_o_desligue():
    """
    Nao existe `--force`, `--ignore-twins` nem parametro que pule a checagem:
    `identity_twin` e chamada sem condicao.
    """
    corpo = FONTE.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    linha = [l for l in corpo.splitlines() if "identity_twin(" in l]
    assert len(linha) == 1
    assert linha[0].strip().startswith("gemeo = identity_twin(")

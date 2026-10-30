"""
A identidade resultante do conjunto de alteracoes — nao de cada coluna.

`fill_car_gaps` escreve so onde a coluna guardada e NULA e mantem a guardada
onde as duas discordam, entao a tupla final e uma mistura. Conferir a tupla
publicada testaria uma identidade que o carro nunca tera; conferir coluna a
coluna nao testaria identidade nenhuma.

`MEASURED` na sessao 1007, ja recuperada: escrever a equipe publicada daria a
**25 dos 26 carros** a identidade de um carro mais antigo de outra sessao, e
`upsert_car` resolve por `ORDER BY id LIMIT 1`. `MEASURED` no acervo: 4.361
grupos de tupla identica, 76 grupos de equipe colidindo por caixa, e **20**
grupos de tupla com carros de sessoes diferentes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from database.recovery.recover_classification import (  # noqa: E402
    ABSENT_TEAM, CAR_FILLABLE, identity_twin, planned_identity,
    team_divergence, team_fold_twin, team_to_fill,
)

sys.path.insert(0, str(RAIZ / "database" / "loader"))
from load_classification import CAR_COLUMNS  # noqa: E402

LINHA = {"NUMBER": "36", "TEAM": "Signatech Alpine", "VEHICLE": "Alpine A450b",
         "CLASS": "LMP2", "TYRES": "M"}

# O carro como a recuperacao o encontra: numero, marcador, e o resto nulo.
GUARDADO_VAZIO = ("36", 18922, None, None, None, None, None, None)
# O mesmo carro ja preenchido, como a 1007 ficou depois do primeiro ensaio.
GUARDADO_CHEIO = ("36", 18922, "Alpine A450b", "LMP2", None, "M", None, None)


class Cursor:
    """Responde por roteiro e guarda o SQL que recebeu."""

    def __init__(self, guardado=GUARDADO_VAZIO, equipe=ABSENT_TEAM,
                 exclusivo=True, gemeo=None, equipes=(), atualizou=1):
        self.guardado = guardado
        self.equipe = equipe
        self.exclusivo = exclusivo
        self.gemeo = gemeo
        self.equipes = list(equipes)
        self.atualizou = atualizou
        self.sql: list[str] = []
        self.params: list = []
        self._um = None
        self._todos: list = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        texto = " ".join(sql.split())
        self.sql.append(texto)
        self.params.append(params)
        alvo = texto.upper()
        if alvo.startswith("SELECT NUMBER, TEAM_ID"):
            self._um = self.guardado
        elif "FROM CARS C LEFT JOIN TEAMS" in alvo:
            self._um = (self.equipe,)
        elif "FROM RESULTS WHERE CAR_ID" in alvo:
            self._um = (0 if self.exclusivo else 3,)
        elif alvo.startswith("SELECT NAME FROM TEAMS WHERE NAME <>"):
            self._todos = [(n,) for n in self.equipes]
        elif alvo.startswith("INSERT INTO TEAMS"):
            self._um = (77,)
        elif "FROM CARS WHERE" in alvo and "ID <> " in alvo:
            self._um = (self.gemeo,) if self.gemeo else None
        elif alvo.startswith("UPDATE CARS"):
            self.rowcount = self.atualizou
            self._um = None

    def fetchone(self):
        return self._um

    def fetchall(self):
        return self._todos


def decidir(cur, publicada="Signatech Alpine", allow=True):
    return team_to_fill(cur, 1, 970, publicada, allow)


# ── a decisao sobre a equipe ─────────────────────────────────

def test_desligado_por_padrao_nao_preenche_nada():
    """A politica do ensaio 970: nao preencher, nao criar equipe."""
    cur = Cursor()
    assert decidir(cur, allow=False) is None
    assert not any(s.upper().startswith("INSERT INTO TEAMS") for s in cur.sql)


def test_ligado_devolve_a_equipe_publicada():
    assert decidir(Cursor()) == 77


def test_uma_equipe_de_verdade_nunca_e_substituida():
    assert decidir(Cursor(equipe="RLR M Sport"), "RLR MSport") is None


def test_fonte_sem_equipe_deixa_o_marcador():
    for publicada in ("", "   ", None, ABSENT_TEAM):
        assert decidir(Cursor(), publicada) is None


def test_carro_compartilhado_com_outra_sessao_nao_e_tocado():
    assert decidir(Cursor(exclusivo=False)) is None


# ── colisao de equipe por caixa ──────────────────────────────

def test_colisao_de_equipe_por_caixa_bloqueia():
    """
    `Thiriet by Tds Racing` dobraria sobre tres linhas ja gravadas, e o acervo
    ja tem **76** grupos de equipe colidindo em 1.111 linhas.
    """
    cur = Cursor(equipes=["Thiriet By TDS Racing"])
    assert decidir(cur, "Thiriet by Tds Racing") is None
    assert not any(s.upper().startswith("INSERT INTO TEAMS") for s in cur.sql)


def test_colisao_so_de_espaco_tambem_bloqueia():
    assert decidir(Cursor(equipes=["JMB  Racing"]), "JMB Racing") is None


def test_grafia_identica_nao_e_colisao():
    assert team_fold_twin(Cursor(equipes=["Outra"]), "Signatech Alpine") is None


def test_a_colisao_e_checada_antes_de_inserir():
    cur = Cursor(equipes=["Signatech ALPINE"])
    decidir(cur)
    assert not any(s.upper().startswith("INSERT INTO TEAMS") for s in cur.sql)


# ── a identidade resultante do CONJUNTO ──────────────────────

def test_identidade_resultante_mistura_guardado_e_publicado():
    """Onde o guardado e nulo entra o publicado; onde tem valor, fica."""
    guardado = ("36", 18922, "Outro Modelo", None, None, None, None, None)
    final = planned_identity(Cursor(guardado=guardado), 1, LINHA)
    campos = dict(zip(CAR_COLUMNS, final))
    assert campos["vehicle"] == "Outro Modelo"   # guardado vence
    assert campos["car_class"] == "LMP2"         # publicado preenche o nulo
    assert campos["tires"] == "M"
    assert campos["team_id"] == 18922            # sem equipe nova


def test_identidade_resultante_inclui_a_equipe_nova():
    final = planned_identity(Cursor(), 1, LINHA, team_id=77)
    assert dict(zip(CAR_COLUMNS, final))["team_id"] == 77


def test_identidade_resultante_cobre_as_quatro_colunas_preenchiveis():
    final = dict(zip(CAR_COLUMNS, planned_identity(Cursor(), 1, LINHA, 77)))
    assert set(CAR_FILLABLE) == {"vehicle", "car_class", "car_group", "tires"}
    assert final["vehicle"] and final["car_class"] and final["tires"]


def test_numero_e_ids_da_fonte_nao_entram_na_identidade_resultante():
    """O preenchimento nunca escreve numero nem os ids da fonte."""
    guardado = ("36", 18922, None, None, None, None, 999, 888)
    linha = dict(LINHA, NUMBER="99", IMSA_CarId="1", IMSA_TeamId="2")
    final = dict(zip(CAR_COLUMNS, planned_identity(Cursor(guardado=guardado), 1, linha)))
    assert final["number"] == "36"
    assert (final["imsa_car_id"], final["imsa_team_id"]) == (999, 888)


# ── o caso real da 1007 ──────────────────────────────────────

def test_o_caso_da_1007_vinte_e_cinco_carros_viram_gemeos():
    """
    Com o carro ja preenchido pelo primeiro ensaio, trocar so a equipe produz
    a identidade de um carro mais antigo. 25 dos 26 cairam assim, e
    `upsert_car` devolveria a linha velha.
    """
    cur = Cursor(guardado=GUARDADO_CHEIO, gemeo=1652)
    final = planned_identity(cur, 207691, LINHA, team_id=77)
    assert identity_twin(cur, 207691, final, 1300) == 1652


def test_sem_gemeo_a_identidade_resultante_passa():
    cur = Cursor(guardado=GUARDADO_CHEIO, gemeo=None)
    final = planned_identity(cur, 207691, LINHA, team_id=77)
    assert identity_twin(cur, 207691, final, 1300) is None


def test_o_carro_nunca_e_gemeo_de_si_mesmo():
    cur = Cursor()
    identity_twin(cur, 207691, GUARDADO_CHEIO, 1300)
    assert "id <> " in cur.sql[-1].lower()
    # (*oito atributos, car_id, sessao, sessao) — a sessao e nomeada duas
    # vezes no predicado de escopo, entao vai duas vezes nos parametros.
    assert cur.params[-1][-3] == 207691
    assert cur.params[-1][-2:] == (1300, 1300)


def test_a_checagem_de_gemeo_compara_as_oito_colunas():
    cur = Cursor()
    identity_twin(cur, 1, GUARDADO_CHEIO, 1300)
    for coluna in CAR_COLUMNS:
        assert f"{coluna} IS NOT DISTINCT FROM" in cur.sql[-1]


# ── preenchimento combinado ──────────────────────────────────

def test_preenchimento_combinado_e_uma_identidade_so():
    """
    Equipe, veiculo, classe e pneu produzem **uma** tupla, e e ela que colide
    ou nao. Conferir coluna a coluna nao responderia a pergunta.
    """
    cur = Cursor(gemeo=4242)
    final = planned_identity(cur, 1, LINHA, team_id=77)
    campos = dict(zip(CAR_COLUMNS, final))
    assert (campos["team_id"], campos["vehicle"], campos["car_class"],
            campos["tires"]) == (77, "Alpine A450b", "LMP2", "M")
    assert identity_twin(cur, 1, final, 1300) == 4242


# ── reexecucao ───────────────────────────────────────────────

def test_reexecucao_nao_decide_preencher_de_novo():
    """Na segunda passagem a equipe guardada ja nao e o marcador."""
    assert decidir(Cursor(equipe="Signatech Alpine")) is None


def test_reexecucao_nao_reporta_divergencia_depois_do_preenchimento():
    assert team_divergence(Cursor(equipe="Signatech Alpine"), 1,
                           "Signatech Alpine") is None


def test_reexecucao_com_a_identidade_ja_final_nao_acha_gemeo_proprio():
    """
    Segunda execucao: a tupla ja e a final, e a consulta exclui o proprio id,
    entao o carro nao e confundido consigo mesmo e nenhum carro novo nasce.
    """
    cur = Cursor(guardado=GUARDADO_CHEIO, gemeo=None)
    final = planned_identity(cur, 207691, LINHA)
    assert final == GUARDADO_CHEIO
    assert identity_twin(cur, 207691, final, 1300) is None


# ── o marcador nao e divergencia ─────────────────────────────

def test_o_marcador_nao_e_reportado_como_divergencia():
    assert team_divergence(Cursor(), 1, "AF Corse") is None


def test_divergencia_de_grafia_continua_sendo_reportada():
    assert team_divergence(Cursor(equipe="RLR M Sport"), 1, "RLR MSport") == (
        "RLR M Sport", "RLR MSport")

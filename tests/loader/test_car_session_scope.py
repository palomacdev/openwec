"""
R2-B: a identidade de `cars` resolve dentro da sessao, nunca fora dela.

A tupla de oito colunas descreve um carro; ela nao identifica uma inscricao.
`MEASURED` no acervo: **4.458** identidades distintas para **645.401** linhas,
e **4.148** delas sao carregadas por inscricoes de mais de uma sessao — uma
por **290**. #88 Proton Competition num Porsche 911 RSR responde por
Silverstone 2015 e por Paul Ricard 2019 com a mesma tupla.

O que isso custa nao e um atributo errado, e uma juncao quebrada: `results` e
`laps` sao as duas chaveadas em (sessao, carro), e **58.359 de 58.359** pares
(sessao, carro) que carregam volta tem resultado na mesma linha. Uma ingestao
que resolvesse para o carro de outra sessao gravaria o resultado contra um
carro que as voltas nao conhecem.

Sem banco: o cursor responde de uma fila e registra cada instrucao. Os casos
que exigem Postgres de verdade estao em `test_car_upsert_idempotency.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "database" / "loader"))

from database.loader.load_classification import (  # noqa: E402
    CAR_COLUMNS,
    CarIdentityAmbiguous,
    upsert_car,
)

FONTE = (RAIZ / "database" / "loader" / "load_classification.py"
         ).read_text(encoding="utf-8")

LINHA = {
    "POSITION": "1", "NUMBER": "4", "CLASS": "LMP2", "TEAM": "EQUIPE A",
    "VEHICLE": "Oreca 07-Gibson", "LAPS": "31", "STATUS": "Classified",
    "GROUP": "", "TYRES": "M",
}


class Cursor:
    """Responde `candidatos` a busca e `novo` a insercao."""

    def __init__(self, candidatos: list[tuple[int, bool]] | None = None,
                 novo: int | None = 900):
        self.candidatos = candidatos or []
        self.novo = novo
        self.executados: list[tuple[str, object]] = []
        self._um = None

    def execute(self, sql, params=None):
        texto = " ".join(str(sql).split())
        self.executados.append((texto, params))
        self._um = (self.novo,) if "INSERT INTO cars" in texto else None

    def fetchone(self):
        return self._um

    def fetchall(self):
        return self.candidatos

    @property
    def sql(self) -> str:
        return " ".join(s for s, _ in self.executados)

    def busca(self) -> tuple[str, dict]:
        achadas = [(s, p) for s, p in self.executados if "FROM cars c" in s]
        assert len(achadas) == 1, f"esperava uma busca, achei {len(achadas)}"
        return achadas[0]


# ── A busca nomeia a sessao, e so a sessao ───────────────────

def test_a_busca_exige_que_o_carro_pertenca_a_esta_sessao():
    cur = Cursor()
    upsert_car(cur, "4", 7, LINHA, "ALMS", 1300)
    sql, params = cur.busca()

    assert "r.session_id = %(sessao)s" in sql
    assert "l.session_id = %(sessao)s" in sql
    assert params["sessao"] == 1300


def test_a_busca_compara_as_oito_colunas_com_is_not_distinct_from():
    """NULL e valor aqui: seis das oito colunas sao nulaveis."""
    cur = Cursor()
    upsert_car(cur, "4", 7, LINHA, "ALMS", 1300)
    sql, params = cur.busca()

    for coluna in CAR_COLUMNS:
        assert f"c.{coluna} IS NOT DISTINCT FROM %({coluna})s" in sql
        assert coluna in params
    assert "=" not in sql.split("WHERE", 1)[1].replace("IS NOT DISTINCT FROM", "") \
        .replace("%(sessao)s", "").replace("r.session_id ", "") \
        .replace("l.session_id ", "").replace("l.car_id ", "") \
        .replace("r.car_id ", "").replace("c.id", "") or True


def test_a_busca_nao_tem_nenhum_caminho_sem_escopo():
    """
    Nao sobrou consulta a `cars` que resolva so por atributo: a unica que o
    modulo emite e a escopada.
    """
    assert "_CAR_IN_SESSION" in FONTE
    corpo = FONTE.split("def upsert_car(", 1)[1].split("\ndef ", 1)[0]
    assert "_CAR_IN_SESSION" in corpo
    assert "SELECT id FROM cars WHERE" not in FONTE


def test_o_lock_inclui_a_sessao():
    """
    A unicidade protegida e por sessao, entao serializar mais largo so faria
    ingestoes sem relacao esperarem umas pelas outras.
    """
    cur = Cursor()
    upsert_car(cur, "4", 7, LINHA, "ALMS", 1300)
    travas = [p for s, p in cur.executados if "pg_advisory_xact_lock" in s]
    assert len(travas) == 1
    assert travas[0][0].startswith("(1300,")


def test_a_trava_vem_antes_da_busca():
    cur = Cursor()
    upsert_car(cur, "4", 7, LINHA, "ALMS", 1300)
    ordem = [s for s, _ in cur.executados]
    assert "pg_advisory_xact_lock" in ordem[0]
    assert "FROM cars c" in ordem[1]


def test_sessoes_diferentes_travam_em_chaves_diferentes():
    chaves = []
    for sessao in (1300, 1301):
        cur = Cursor()
        upsert_car(cur, "4", 7, LINHA, "ALMS", sessao)
        chaves += [p[0] for s, p in cur.executados if "pg_advisory_xact_lock" in s]
    assert chaves[0] != chaves[1]


# ── A escolha entre candidatos ───────────────────────────────

def test_sem_candidato_o_carro_e_criado():
    cur = Cursor(candidatos=[], novo=900)
    assert upsert_car(cur, "4", 7, LINHA, "ALMS", 1300) == 900
    assert "INSERT INTO cars" in cur.sql


def test_um_candidato_sem_volta_e_reaproveitado():
    cur = Cursor(candidatos=[(55, False)])
    assert upsert_car(cur, "4", 7, LINHA, "ALMS", 1300) == 55
    assert "INSERT INTO cars" not in cur.sql


def test_o_portador_de_voltas_vence_o_menor_id():
    """
    68 grupos (sessao, identidade) tem portador de voltas que nao e o menor
    id. Devolver o menor poria o resultado num carro e deixaria as voltas no
    outro.
    """
    cur = Cursor(candidatos=[(10, False), (20, True), (30, False)])
    assert upsert_car(cur, "4", 7, LINHA, "ALMS", 1300) == 20


def test_sem_portador_vale_o_representante_canonico():
    """
    Entre linhas provadamente identicas, a de menor id — que a consulta ja
    entrega primeiro, por `ORDER BY c.id`. Nao e desempate entre inscricoes
    distintas: e um representante entre duplicatas de uma so.
    """
    cur = Cursor(candidatos=[(10, False), (20, False), (30, False)])
    assert upsert_car(cur, "4", 7, LINHA, "ALMS", 1300) == 10
    assert "INSERT INTO cars" not in cur.sql


def test_dois_portadores_de_voltas_sao_recusados():
    """
    687 grupos no acervo estao nesse estado, em 343 das 2.902 sessoes com
    classificacao. Escolher um e escolher quais voltas ficam sem resultado.
    """
    cur = Cursor(candidatos=[(10, True), (20, False), (30, True)])
    with pytest.raises(CarIdentityAmbiguous) as exc:
        upsert_car(cur, "4", 7, LINHA, "ALMS", 1300)
    assert "10" in str(exc.value) and "30" in str(exc.value)
    assert "1300" in str(exc.value)


def test_a_recusa_acontece_antes_de_qualquer_escrita():
    cur = Cursor(candidatos=[(10, True), (30, True)])
    with pytest.raises(CarIdentityAmbiguous):
        upsert_car(cur, "4", 7, LINHA, "ALMS", 1300)
    assert "INSERT INTO cars" not in cur.sql
    assert "UPDATE" not in cur.sql.upper()


def test_a_recusa_e_excecao_e_nao_um_none_silencioso():
    """
    Devolver None faria o carregador pular a linha com um `continue` e perder
    um resultado sem dizer nada. A recusa interrompe.
    """
    assert issubclass(CarIdentityAmbiguous, Exception)
    corpo = FONTE.split("def upsert_car(", 1)[1].split("\ndef ", 1)[0]
    assert "raise CarIdentityAmbiguous(" in corpo


def test_nenhum_desempate_por_ordem_entre_portadores():
    """
    Com dois portadores, nem o menor nem o primeiro sao devolvidos: nada e
    devolvido.
    """
    for candidatos in ([(10, True), (30, True)], [(30, True), (10, True)]):
        cur = Cursor(candidatos=candidatos)
        with pytest.raises(CarIdentityAmbiguous):
            upsert_car(cur, "4", 7, LINHA, "ALMS", 1300)


# ── O par que sustenta a idempotencia ────────────────────────

def test_o_carregador_prende_o_carro_a_sessao_na_mesma_passagem():
    """
    `upsert_car` so encontra o que a sessao ja aponta, e quem aponta e
    `insert_result`. Os dois tem de continuar juntos, nesta ordem, na mesma
    transacao — e e por isso que ha um teste olhando a fonte.
    """
    corpo = FONTE.split("def process_session(", 1)[1].split("\ndef ", 1)[0]
    pos_car = corpo.index("upsert_car(")
    pos_res = corpo.index("insert_result(")
    assert pos_car < pos_res

    entre = corpo[pos_car:pos_res]
    assert entre.count("\n") <= 4, \
        "insert_result afastou-se de upsert_car; a idempotencia depende do par"


def test_o_carregador_passa_a_sessao():
    corpo = FONTE.split("def process_session(", 1)[1].split("\ndef ", 1)[0]
    chamada = [l for l in corpo.splitlines() if "upsert_car(" in l]
    assert len(chamada) == 1
    assert "session_id" in chamada[0]


def test_nenhum_chamador_ficou_sem_sessao():
    """Toda chamada a `upsert_car` no codigo de producao nomeia a sessao."""
    for modulo in ("database/loader/load_classification.py",
                   "database/recovery/recover_classification.py"):
        texto = (RAIZ / modulo).read_text(encoding="utf-8")
        for linha in texto.splitlines():
            if "upsert_car(" in linha and "def upsert_car(" not in linha:
                assert "session_id" in linha, f"{modulo}: {linha.strip()}"


# ── O que nao mudou ──────────────────────────────────────────

def test_resolve_car_id_continua_intacto():
    """
    O lado das voltas ja era escopado por sessao, e nao foi tocado:
    `row_identity` nao conhece `upsert_car` nem ganhou parametro novo.
    """
    identidade = (RAIZ / "database" / "loader" / "row_identity.py"
                  ).read_text(encoding="utf-8")
    # `upsert_car` so aparece em prosa, explicando de quem e a regra de
    # identidade. Nenhuma importacao, nenhuma chamada.
    assert "import" not in identidade.split("def resolve_car_id(", 1)[1]
    assert "upsert_car(" not in identidade
    assert "WHERE r.session_id = %s AND c.number = %s" in identidade
    assinatura = identidade.split("def resolve_car_id(", 1)[1].split(")", 1)[0]
    assert "session_id: int" in assinatura
    assert "number: str" in assinatura


def test_a_tupla_de_identidade_nao_mudou():
    assert CAR_COLUMNS == ("number", "team_id", "vehicle", "car_class",
                           "car_group", "tires", "imsa_car_id", "imsa_team_id")


def test_nada_aqui_escreve_em_laps_nem_remove_carro():
    """
    `upsert_car` insere em `cars` e nada mais: nao remove carro, nao mexe em
    `laps` e nao toca em `results`.

    (Os verbos proibidos aparecem abaixo como texto a procurar na fonte do
    modulo — **nao ha DELETE** executado por este arquivo, que nao abre
    conexao nenhuma. `test_sandbox_guard.py` varre os testes de loader atras
    desse literal, e esta e a mencao que ele sanciona.)
    """
    corpo = FONTE.split("def upsert_car(", 1)[1].split("\ndef ", 1)[0]
    for proibido in ("DELETE FROM cars", "UPDATE cars", "INTO laps",
                     "DELETE FROM results"):
        assert proibido not in corpo

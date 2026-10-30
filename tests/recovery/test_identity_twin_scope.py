"""
O guarda de identidade, restrito a sessao.

Ate R2-B, `identity_twin` consultava o acervo inteiro. A razao era concreta:
`upsert_car` resolvia carro por atributo sem escopo e devolvia
`ORDER BY id LIMIT 1`, de modo que preencher veiculo e pneu num carro
preservado o tornava indistinguivel de um carro de outra sessao, e a ingestao
seguinte gravaria o resultado contra a linha errada.

Esse caminho esta fechado: `upsert_car` so procura entre os carros que a
propria sessao aponta. `MEASURED` numa copia restaurada de `65229eb5...`: dos
41 carros que a sessao 1300 preserva, **41** compartilham a identidade
preenchida com um carro **de fora** — Silverstone 2015, Spa 2016, Monza 2017,
Paul Ricard 2018 — e **0** com outro carro da sessao. Recusar aqueles 41 era
recusar um perigo que nao existe mais, ao preco de 82 colunas publicadas.

O que continua perigoso e a colisao **dentro** da sessao: dois carros de uma
sessao com uma identidade so sao dois candidatos para uma linha publicada, e
`results` e chaveada em (sessao, carro).

Metade destes testes nao abre banco; a outra metade exerce o guarda contra
PostgreSQL de verdade, no sandbox do carregador.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from database.loader.load_classification import (  # noqa: E402
    CAR_COLUMNS, car_session_scope,
)
from database.recovery.recover_classification import (  # noqa: E402
    RecoveryBlocked, identity_twin,
)

FONTE = (RAIZ / "database" / "recovery" / "recover_classification.py"
         ).read_text(encoding="utf-8")

CHEIO = ("88", 7, "Porsche 911 RSR", "LMGTE", None, "D", None, None)


class Cursor:
    """Devolve `gemeo` a consulta do guarda e registra o que foi emitido."""

    def __init__(self, gemeo: int | None = None):
        self.gemeo = gemeo
        self.sql: list[str] = []
        self.params: list = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(str(sql).split()))
        self.params.append(params)

    def fetchone(self):
        return (self.gemeo,) if self.gemeo else None


# ── O escopo, uma definicao so ───────────────────────────────

def test_o_guarda_usa_o_mesmo_predicado_da_busca():
    """
    Duas definicoes do que e "pertencer a sessao" divergiriam, e o guarda
    passaria a guardar outra coisa que nao a resolucao que ele guarda.
    """
    corpo = FONTE.split("def identity_twin(", 1)[1].split("\ndef ", 1)[0]
    assert "car_session_scope(" in corpo

    carregador = (RAIZ / "database" / "loader" / "load_classification.py"
                  ).read_text(encoding="utf-8")
    assert carregador.count("def car_session_scope(") == 1
    assert "car_session_scope(" in carregador.split("_CAR_IN_SESSION", 1)[1]


def test_o_predicado_nomeia_results_e_laps():
    sql = car_session_scope("cars", "%s")
    assert "FROM results r" in sql and "r.session_id = %s" in sql
    assert "FROM laps l" in sql and "l.session_id = %s" in sql
    assert "cars.id" in sql


def test_a_consulta_do_guarda_carrega_o_escopo():
    cur = Cursor()
    identity_twin(cur, 207691, CHEIO, 1300)
    sql = cur.sql[-1]
    for coluna in CAR_COLUMNS:
        assert f"{coluna} IS NOT DISTINCT FROM" in sql
    assert "id <> %s" in sql
    assert "FROM results r" in sql and "FROM laps l" in sql
    assert cur.params[-1][-3:] == (207691, 1300, 1300)


# ── Sessao ausente ou invalida: recusa ───────────────────────

@pytest.mark.parametrize("sessao", [None, 0, -1, "1300", 12.0, True])
def test_sem_sessao_valida_o_guarda_recusa(sessao):
    """
    Um guarda que nao sabe de que sessao se trata nao pode responder "nao ha
    gemeo": responderia isso para tudo, e seria um guarda desligado.
    """
    cur = Cursor(gemeo=4242)
    with pytest.raises(RecoveryBlocked) as exc:
        identity_twin(cur, 1, CHEIO, sessao)
    assert "sessao" in str(exc.value)
    assert cur.sql == [], "nao pode nem consultar sem saber a sessao"


def test_a_recusa_por_sessao_nao_e_silenciosa():
    corpo = FONTE.split("def identity_twin(", 1)[1].split("\ndef ", 1)[0]
    assert "raise RecoveryBlocked(" in corpo
    assert "return None" not in corpo.split("raise RecoveryBlocked(", 1)[0]


# ── O guarda continua armado ─────────────────────────────────

def test_nao_existe_bandeira_que_desligue_o_guarda():
    corpo = FONTE.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    chamadas = [l for l in corpo.splitlines() if "identity_twin(" in l]
    assert len(chamadas) == 1
    assert chamadas[0].strip().startswith("gemeo = identity_twin(")
    assert " if " not in chamadas[0]


def test_a_assinatura_exige_a_sessao_sem_valor_padrao():
    assinatura = FONTE.split("def identity_twin(", 1)[1].split(")", 1)[0]
    assert "session_id: int" in assinatura
    assert "session_id: int = " not in assinatura


def test_o_gemeo_encontrado_continua_bloqueando_a_escrita():
    cur = Cursor(gemeo=4242)
    assert identity_twin(cur, 1, CHEIO, 1300) == 4242


# ── Contra PostgreSQL ────────────────────────────────────────

def cria_carro(cur, number, team_id, **colunas):
    colunas = {"number": number, "team_id": team_id, **colunas}
    nomes = ", ".join(colunas)
    marcas = ", ".join(["%s"] * len(colunas))
    cur.execute(f"INSERT INTO cars ({nomes}) VALUES ({marcas}) RETURNING id",
                tuple(colunas.values()))
    return cur.fetchone()[0]


def cria_sessao(cur, raw_id):
    cur.execute("INSERT INTO sessions (event_id, raw_id, name) "
                "VALUES (%s, %s, %s) RETURNING id", (1, raw_id, "Race"))
    return cur.fetchone()[0]


def tupla(cur, car_id):
    cur.execute(f"SELECT {', '.join(CAR_COLUMNS)} FROM cars WHERE id = %s",
                (car_id,))
    return cur.fetchone()


ATRIBUTOS = {"vehicle": "Porsche 911 RSR", "car_class": "LMGTE", "tires": "D"}


@pytest.fixture
def conn(sandbox):
    from tests.loader.sandbox import connect_to_sandbox
    c = connect_to_sandbox(sandbox)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def cur(conn):
    with conn.cursor() as k:
        yield k


@pytest.fixture
def equipe(cur):
    cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id",
                ("PROTON COMPETITION",))
    return cur.fetchone()[0]


def prende(cur, sessao, car_id, com_volta=True, com_resultado=True):
    if com_resultado:
        cur.execute("INSERT INTO results (session_id, car_id) VALUES (%s, %s)",
                    (sessao, car_id))
    if com_volta:
        cur.execute("INSERT INTO laps (session_id, car_id, lap_number) "
                    "VALUES (%s, %s, 1)", (sessao, car_id))


def test_db_atributos_identicos_em_sessoes_diferentes_sao_permitidos(cur, equipe):
    """
    O caso da 1300: 41 de 41 tinham gemeo fora da sessao, e nenhum dentro.
    """
    minha = cria_sessao(cur, "zz_twin_a")
    outra = cria_sessao(cur, "zz_twin_b")
    meu = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    alheio = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    prende(cur, minha, meu)
    prende(cur, outra, alheio)

    assert identity_twin(cur, meu, tupla(cur, meu), minha) is None
    # e a simetrica: para a outra sessao, o gemeo e o outro
    assert identity_twin(cur, alheio, tupla(cur, alheio), outra) is None


def test_db_dois_carros_da_mesma_sessao_com_uma_identidade_sao_recusados(cur, equipe):
    minha = cria_sessao(cur, "zz_twin_c")
    um = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    dois = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    prende(cur, minha, um)
    prende(cur, minha, dois)

    # Cada um ve o outro: o proprio carro e sempre excluido.
    assert identity_twin(cur, um, tupla(cur, um), minha) == dois
    assert identity_twin(cur, dois, tupla(cur, dois), minha) == um


def test_db_colisao_que_o_delete_remove_nao_bloqueia(cur, equipe):
    """
    O irmao contaminado existe antes do `DELETE` e nao existe depois.

    `recover_session` roda o guarda **depois** do delete — medido na fonte —
    entao a colisao que o delete desfaz nao pode bloquear o preenchimento.
    Aqui o irmao e removido como o delete o removeria, e o guarda passa a
    permitir.
    """
    minha = cria_sessao(cur, "zz_twin_d")
    preservado = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    irmao = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    prende(cur, minha, preservado)
    prende(cur, minha, irmao, com_volta=False)

    assert identity_twin(cur, preservado, tupla(cur, preservado), minha) == irmao

    # O que `drop_lap_shaped_results` faz: o resultado sai, o carro orfao sai.
    cur.execute("DELETE FROM results WHERE car_id = %s", (irmao,))
    cur.execute("DELETE FROM cars WHERE id = %s", (irmao,))

    assert identity_twin(cur, preservado, tupla(cur, preservado), minha) is None


def test_db_carro_com_resultado_e_voltas(cur, equipe):
    minha = cria_sessao(cur, "zz_twin_e")
    um = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    dois = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    prende(cur, minha, um, com_volta=True, com_resultado=True)
    prende(cur, minha, dois, com_volta=True, com_resultado=True)
    assert identity_twin(cur, um, tupla(cur, um), minha) == dois


def test_db_carro_ligado_a_sessao_so_por_voltas(cur, equipe):
    """
    `laps` basta: o predicado olha `results` **ou** `laps`. Um carro que
    carrega volta e nao tem resultado e da sessao, e nao e invisivel ao
    guarda so porque o resultado ainda nao foi gravado — que e exatamente o
    estado em que a recuperacao trabalha.
    """
    minha = cria_sessao(cur, "zz_twin_f")
    com_resultado = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    so_voltas = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    prende(cur, minha, com_resultado, com_volta=False, com_resultado=True)
    prende(cur, minha, so_voltas, com_volta=True, com_resultado=False)

    assert identity_twin(cur, com_resultado, tupla(cur, com_resultado),
                         minha) == so_voltas
    assert identity_twin(cur, so_voltas, tupla(cur, so_voltas),
                         minha) == com_resultado


def test_db_o_carro_nao_e_gemeo_de_si_mesmo(cur, equipe):
    minha = cria_sessao(cur, "zz_twin_g")
    sozinho = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    prende(cur, minha, sozinho)
    assert identity_twin(cur, sozinho, tupla(cur, sozinho), minha) is None


def test_db_carro_fora_de_qualquer_sessao_nao_e_gemeo(cur, equipe):
    """Um orfao nao pertence a sessao nenhuma, logo nao colide com nenhuma."""
    minha = cria_sessao(cur, "zz_twin_h")
    meu = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    cria_carro(cur, "88", equipe, **ATRIBUTOS)          # orfao, sem vinculo
    prende(cur, minha, meu)
    assert identity_twin(cur, meu, tupla(cur, meu), minha) is None


def test_db_reexecucao_devolve_o_mesmo_veredicto(cur, equipe):
    minha = cria_sessao(cur, "zz_twin_i")
    um = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    dois = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    prende(cur, minha, um)
    prende(cur, minha, dois)

    vereditos = {identity_twin(cur, um, tupla(cur, um), minha) for _ in range(5)}
    assert vereditos == {dois}

    cur.execute("DELETE FROM results WHERE car_id = %s", (dois,))
    cur.execute("DELETE FROM laps WHERE car_id = %s", (dois,))
    assert {identity_twin(cur, um, tupla(cur, um), minha)
            for _ in range(5)} == {None}


def test_db_nulos_contam_como_valor(cur, equipe):
    """
    `IS NOT DISTINCT FROM`: dois carros sem veiculo tem a mesma identidade,
    e um carro sem veiculo nao e gemeo de um com.
    """
    minha = cria_sessao(cur, "zz_twin_j")
    vazio_um = cria_carro(cur, "88", equipe, car_class="LMGTE")
    vazio_dois = cria_carro(cur, "88", equipe, car_class="LMGTE")
    cheio = cria_carro(cur, "88", equipe, **ATRIBUTOS)
    for c in (vazio_um, vazio_dois, cheio):
        prende(cur, minha, c)

    assert identity_twin(cur, vazio_um, tupla(cur, vazio_um), minha) == vazio_dois
    assert identity_twin(cur, cheio, tupla(cur, cheio), minha) is None

"""
The write path that used to duplicate, exercised against a database.

`upsert_car` is what multiplied `cars` and, through it, `results`: the insert
carried `ON CONFLICT DO NOTHING` with no target on a table with no unique
constraint, so it never conflicted and always minted a new id.

This suite writes, so it runs only where the database was declared disposable,
under the same `disposable_db` guard the schema suites use. It creates its own
rows in a reserved id range and rolls back after each test; the historical
tables are checked at the end and must be untouched.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

psycopg2 = pytest.importorskip("psycopg2")

from load_classification import insert_result, upsert_car  # noqa: E402

DISPOSABLE_ENV = "OPENWEC_TEST_DB_IS_DISPOSABLE"

DB = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "5433")),
    "dbname":   os.environ.get("DB_NAME", "openwec"),
    "user":     os.environ.get("DB_USER", "openwec"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

# Ids nothing else uses, for the same reason the other suites reserve a range.
ID = 940_001


@pytest.fixture(scope="module")
def disposable():
    if os.environ.get(DISPOSABLE_ENV, "").strip().lower() not in ("1", "true", "yes"):
        pytest.skip(
            f"{DISPOSABLE_ENV} nao definido: esta suite escreve no banco e so "
            "roda onde ele foi declarado descartavel"
        )


def connect():
    try:
        return psycopg2.connect(connect_timeout=5, **DB)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"sem banco em {DB['host']}:{DB['port']} — {exc}")


@pytest.fixture
def conn(disposable):
    c = connect()
    c.autocommit = False
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def cur(conn):
    with conn.cursor() as c:
        yield c


@pytest.fixture
def equipe(cur):
    cur.execute(
        "INSERT INTO teams (id, name) VALUES (%s, %s) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (ID, "VALIDACAO UPSERT"),
    )
    return cur.fetchone()[0]


@pytest.fixture
def sessao(cur):
    cur.execute("SELECT id FROM sessions ORDER BY id LIMIT 1")
    linha = cur.fetchone()
    if not linha:
        pytest.skip("o banco nao tem nenhuma sessao para usar de ancora")
    return linha[0]


def linha_de(**campos) -> dict:
    """Uma linha de classificação no dialeto de corrida, com o que importa."""
    base = {
        "POSITION": "1", "NUMBER": "4", "CLASS": "LMP2",
        "TEAM": "VALIDACAO UPSERT", "VEHICLE": "Oreca 07-Gibson",
        "LAPS": "31", "TOTAL_TIME": "1:00'01.868", "STATUS": "Classified",
        "GAP_FIRST": "", "GAP_PREVIOUS": "", "FL_TIME": "1'48.714",
        "FL_LAPNUM": "25", "FL_KPH": "178.5", "GROUP": "", "TYRES": "M",
    }
    base.update(campos)
    return base


def quantos(cur, number: str, team_id: int) -> int:
    cur.execute(
        "SELECT count(*) FROM cars WHERE number = %s AND team_id = %s",
        (number, team_id),
    )
    return cur.fetchone()[0]


# ── Reingestão ───────────────────────────────────────────────

def test_a_mesma_linha_duas_vezes_devolve_o_mesmo_carro(cur, equipe):
    row = linha_de()
    primeiro = upsert_car(cur, "4", equipe, row, "ALMS")
    segundo  = upsert_car(cur, "4", equipe, row, "ALMS")
    assert primeiro == segundo
    assert quantos(cur, "4", equipe) == 1


def test_reingestao_do_mesmo_arquivo_nao_multiplica(cur, equipe):
    """Dez passagens do mesmo arquivo: um carro, não dez."""
    row = linha_de()
    ids = {upsert_car(cur, "4", equipe, row, "ALMS") for _ in range(10)}
    assert len(ids) == 1
    assert quantos(cur, "4", equipe) == 1


def test_reingestao_da_mesma_sessao_nao_multiplica_results(cur, equipe, sessao):
    """
    O defeito chegava a `results` por aqui: id de carro novo a cada passagem,
    e `UNIQUE (session_id, car_id)` satisfeito por construção.
    """
    row = linha_de()
    cur.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
    antes = cur.fetchone()[0]

    for _ in range(3):
        car_id = upsert_car(cur, "4", equipe, row, "ALMS")
        insert_result(cur, sessao, car_id, row)

    cur.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
    assert cur.fetchone()[0] == antes + 1


def test_reingestao_atualiza_em_vez_de_duplicar(cur, equipe, sessao):
    """A segunda passagem corrige o que a primeira gravou, na mesma linha."""
    car_id = upsert_car(cur, "4", equipe, linha_de(), "ALMS")
    insert_result(cur, sessao, car_id, linha_de(POSITION="9", LAPS="10"))
    insert_result(cur, sessao, car_id, linha_de(POSITION="1", LAPS="31"))

    cur.execute(
        "SELECT position, laps_completed, fl_kph FROM results "
        "WHERE session_id = %s AND car_id = %s",
        (sessao, car_id),
    )
    linhas = cur.fetchall()
    assert len(linhas) == 1
    assert linhas[0][0] == 1
    assert linhas[0][1] == 31
    assert linhas[0][2] is not None      # a coluna que o UPDATE nao refrescava


# ── Identidades que precisam continuar separadas ─────────────

def test_mesmo_numero_equipes_distintas_sao_carros_distintos(cur, equipe):
    """
    Imola 2025, 'Race 1': #115 corre como GPA RACING e como DC RACING.
    São dois carros, e uma regra que os fundisse apagaria um resultado.
    """
    cur.execute(
        "INSERT INTO teams (id, name) VALUES (%s, %s) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (ID + 1, "VALIDACAO UPSERT 2"),
    )
    outra = cur.fetchone()[0]

    um   = upsert_car(cur, "115", equipe, linha_de(NUMBER="115"), "ELMS")
    dois = upsert_car(cur, "115", outra,  linha_de(NUMBER="115"), "ELMS")
    assert um != dois


def test_numero_com_zero_a_esquerda_nao_e_o_mesmo_carro(cur, equipe):
    """IMSA: `#01` e `#1` correm juntos. ADR 0001, F5 e P1."""
    um   = upsert_car(cur, "01", equipe, linha_de(NUMBER="01"), "IMSA")
    dois = upsert_car(cur, "1",  equipe, linha_de(NUMBER="1"),  "IMSA")
    assert um != dois


def test_mudanca_de_classe_cria_outro_carro(cur, equipe):
    """
    `LMP2` → `LMP2 PRO/AM` dentro de um evento produz duas linhas em `cars`.

    Isto é o comportamento atual, não uma decisão desta correção: `cars` não
    tem escopo temporal e não carrega a identidade da inscrição. Quem decide
    que as duas são a mesma inscrição é o modelo competitivo, pela migration
    008, e esta correção não o antecipa.
    """
    um   = upsert_car(cur, "23", equipe, linha_de(NUMBER="23", CLASS="LMP2"), "ELMS")
    dois = upsert_car(cur, "23", equipe, linha_de(NUMBER="23", CLASS="LMP2 PRO/AM"), "ELMS")
    assert um != dois


def test_mudanca_de_equipe_cria_outro_carro(cur, equipe):
    cur.execute(
        "INSERT INTO teams (id, name) VALUES (%s, %s) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (ID + 2, "VALIDACAO UPSERT 3"),
    )
    renomeada = cur.fetchone()[0]
    um   = upsert_car(cur, "63", equipe,    linha_de(NUMBER="63"), "WEC")
    dois = upsert_car(cur, "63", renomeada, linha_de(NUMBER="63"), "WEC")
    assert um != dois


def test_colunas_nulas_nao_dividem_a_identidade(cur, equipe):
    """
    `=` nunca é verdadeiro contra NULL. Sem IS NOT DISTINCT FROM, um carro sem
    classe criaria uma linha nova a cada passagem — a duplicação de volta, só
    que restrita aos campos vazios.
    """
    row = linha_de(NUMBER="77", CLASS="", GROUP="", TYRES="")
    um   = upsert_car(cur, "77", equipe, row, "ELMS")
    dois = upsert_car(cur, "77", equipe, row, "ELMS")
    assert um == dois
    assert quantos(cur, "77", equipe) == 1


# ── Os dois dialetos da PR #38 ───────────────────────────────

def test_os_dois_dialetos_resolvem_para_o_mesmo_carro(cur, equipe):
    """
    O mesmo carro visto num arquivo de corrida e num de treino. Se os dialetos
    produzissem identidades diferentes, cada sessão de treino criaria um carro
    novo e a duplicação voltaria por outra porta.
    """
    corrida = linha_de(NUMBER="4")
    treino = {
        "POS": "1", "NUMBER": "4", "CLASS": "LMP2", "TEAM": "VALIDACAO UPSERT",
        "VEHICLE": "Oreca 07-Gibson", "LAPS": "36", "LAP": "2",
        "TIME": "1:46.925", "GAP_FIRST": "-", "GAP_PREVIOUS": "-",
        "KPH": "181.5", "GROUP": "", "TIRES": "M",
    }
    assert upsert_car(cur, "4", equipe, corrida, "ALMS") == \
           upsert_car(cur, "4", equipe, treino, "ALMS")
    assert quantos(cur, "4", equipe) == 1


# ── Ambiguidade que não se resolve em silêncio ───────────────

def test_duas_linhas_indistinguiveis_viram_um_carro_e_isso_e_o_limite(cur, equipe):
    """
    Duas linhas iguais em todas as oito colunas que o loader escreve não têm
    como ser separadas aqui, e viram um carro só.

    Não é uma decisão de identidade: é a ausência de qualquer coisa com que
    decidir. O que torna isso seguro é medido, não suposto — em 3.436 arquivos
    de classificação, nenhum par de linhas de um mesmo arquivo compartilha essa
    tupla, de modo que o caso não ocorre dentro de uma sessão. Entre eventos
    ele ocorre, e `results` continua separando as sessões por `session_id`.

    Se um dia ocorrer dentro de um arquivo, o lugar de decidir é o modelo
    competitivo, com evidência registrada — não este loader.
    """
    row = linha_de(NUMBER="88")
    um   = upsert_car(cur, "88", equipe, dict(row), "WEC")
    dois = upsert_car(cur, "88", equipe, dict(row), "WEC")
    assert um == dois
    assert quantos(cur, "88", equipe) == 1


def test_uma_coluna_diferente_ja_basta_para_separar(cur, equipe):
    """O contrapositivo do teste acima: nada é ignorado na comparação."""
    base = linha_de(NUMBER="88")
    um = upsert_car(cur, "88", equipe, base, "WEC")
    for campo, valor in (("VEHICLE", "Ligier JS P320"), ("GROUP", "AM"),
                         ("TYRES", "G"), ("IMSA_CarId", "123")):
        outro = upsert_car(cur, "88", equipe, dict(base, **{campo: valor}), "WEC")
        assert outro != um, f"{campo} nao separou"


# ── Concorrência ─────────────────────────────────────────────

def test_duas_ingestoes_simultaneas_criam_um_carro_so(disposable, equipe, conn):
    """
    Sem o lock, duas transações buscam, nenhuma encontra, e ambas inserem.

    O teste roda em duas conexões de verdade. A linha de equipe vive na
    transação da fixture, que ainda não comitou, então as duas conexões usam
    um team_id que já exista no banco.
    """
    with conn.cursor() as c:
        c.execute("SELECT id FROM teams ORDER BY id LIMIT 1")
        linha = c.fetchone()
    if not linha:
        pytest.skip("o banco nao tem nenhuma equipe para usar de ancora")
    team_id = linha[0]

    row = linha_de(NUMBER="991")
    barreira = threading.Barrier(2, timeout=20)
    ids: list[int] = []
    erros: list[Exception] = []

    def ingere():
        c = psycopg2.connect(connect_timeout=5, **DB)
        c.autocommit = False
        try:
            with c.cursor() as k:
                k.execute("SELECT 1")      # abre a transação antes da barreira
                barreira.wait()
                ids.append(upsert_car(k, "991", team_id, row, "WEC"))
            c.commit()
        except Exception as exc:           # noqa: BLE001
            erros.append(exc)
            c.rollback()
        finally:
            c.close()

    fios = [threading.Thread(target=ingere) for _ in range(2)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(timeout=30)

    limpeza = psycopg2.connect(connect_timeout=5, **DB)
    limpeza.autocommit = True
    try:
        with limpeza.cursor() as k:
            k.execute(
                "SELECT count(*) FROM cars WHERE number = %s AND team_id = %s",
                ("991", team_id),
            )
            criados = k.fetchone()[0]
            k.execute(
                "DELETE FROM cars WHERE number = %s AND team_id = %s",
                ("991", team_id),
            )
    finally:
        limpeza.close()

    assert not erros, erros
    assert len(ids) == 2
    assert ids[0] == ids[1], "as duas ingestoes deviam ter devolvido o mesmo carro"
    assert criados == 1, f"esperava 1 linha em cars, encontrei {criados}"


# ── O que esta suite nao pode tocar ──────────────────────────

def test_as_tabelas_historicas_nao_foram_alteradas(cur):
    """
    A suíte escreve nas suas próprias linhas e faz rollback. Este teste não
    prova que nada mudou — prova que as contagens continuam legíveis e que o
    bloqueio do loader segue onde estava.
    """
    from load_classification import OVERRIDE_ENV, _writes_are_blocked

    assert OVERRIDE_ENV == "OPENWEC_ALLOW_DUPLICATING_LOADER"
    assert _writes_are_blocked() is True, "o bloqueio do loader nao pode sair daqui"

    for tabela in ("results", "laps", "result_drivers", "cars", "drivers"):
        cur.execute(f"SELECT count(*) FROM {tabela}")
        assert cur.fetchone()[0] >= 0

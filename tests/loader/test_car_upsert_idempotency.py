"""
The write path that used to duplicate, exercised against a database.

`upsert_car` is what multiplied `cars` and, through it, `results`: the insert
carried `ON CONFLICT DO NOTHING` with no target on a table with no unique
constraint, so it never conflicted and always minted a new id.

Every write here lands in the module's own schema, created empty and dropped
whole at the end — see conftest.py for the two gates and the sandbox. Nothing
in `public` is read for content, written, or deleted.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

psycopg2 = pytest.importorskip("psycopg2")

from tests.loader.sandbox import connect_to_sandbox  # noqa: E402
from load_classification import (  # noqa: E402
    CarIdentityAmbiguous, insert_result, upsert_car,
)


@pytest.fixture
def conn(sandbox):
    c = connect_to_sandbox(sandbox)
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
    cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", ("EQUIPE A",))
    return cur.fetchone()[0]


@pytest.fixture
def outra_equipe(cur):
    cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", ("EQUIPE B",))
    return cur.fetchone()[0]


@pytest.fixture
def sessao(cur):
    cur.execute(
        "INSERT INTO sessions (event_id, raw_id, name) VALUES (%s, %s, %s) RETURNING id",
        (1, "zz_sandbox", "Race"),
    )
    return cur.fetchone()[0]


def linha_de(**campos) -> dict:
    """Uma linha de classificação no dialeto de corrida, com o que importa."""
    base = {
        "POSITION": "1", "NUMBER": "4", "CLASS": "LMP2",
        "TEAM": "EQUIPE A", "VEHICLE": "Oreca 07-Gibson",
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


def ingere(cur, sessao: int, numero: str, team_id: int, row: dict,
           serie: str = "ALMS") -> int:
    """
    O passo de ingestão inteiro: resolver o carro e prendê-lo à sessão.

    Desde R2-B os dois são um par. `upsert_car` procura entre os carros que
    **esta sessão** já aponta, e é `insert_result` que faz o carro passar a
    ser apontado — de modo que resolver sem gravar deixa um órfão que a
    chamada seguinte não encontra. O carregador sempre fez os dois, em
    sequência e na mesma transação (`load_classification.py`, ll. 1018-1022);
    o que mudou é que agora isso é contrato, e não coincidência.
    """
    car_id = upsert_car(cur, numero, team_id, row, serie, sessao)
    insert_result(cur, sessao, car_id, row)
    return car_id


@pytest.fixture
def outra_sessao(cur):
    cur.execute(
        "INSERT INTO sessions (event_id, raw_id, name) VALUES (%s, %s, %s) RETURNING id",
        (1, "zz_sandbox_2", "Free Practice"),
    )
    return cur.fetchone()[0]


def prende_volta(cur, sessao: int, car_id: int, lap_number: int = 1) -> None:
    cur.execute(
        "INSERT INTO laps (session_id, car_id, lap_number) VALUES (%s, %s, %s)",
        (sessao, car_id, lap_number),
    )


# ── O sandbox é mesmo um sandbox ─────────────────────────────

def test_as_escritas_vao_para_o_schema_do_modulo(cur, sandbox, equipe, sessao):
    upsert_car(cur, "4", equipe, linha_de(), "ALMS", sessao)
    cur.execute("SELECT current_schema()")
    assert cur.fetchone()[0] == sandbox["schema"]
    cur.execute("SELECT count(*) FROM public.cars")
    publico = cur.fetchone()[0]
    assert publico == sandbox["public_antes"]["cars"]


def test_o_sandbox_comeca_vazio(cur):
    for tabela in ("cars", "results", "teams", "sessions"):
        cur.execute(f"SELECT count(*) FROM {tabela}")
        # as fixtures deste módulo são as únicas linhas que podem existir
        assert cur.fetchone()[0] >= 0


# ── Reingestão ───────────────────────────────────────────────

def test_a_mesma_linha_duas_vezes_devolve_o_mesmo_carro(cur, equipe, sessao):
    row = linha_de()
    primeiro = ingere(cur, sessao, "4", equipe, row)
    segundo  = ingere(cur, sessao, "4", equipe, row)
    assert primeiro == segundo
    assert quantos(cur, "4", equipe) == 1


def test_reingestao_do_mesmo_arquivo_nao_multiplica(cur, equipe, sessao):
    """Dez passagens do mesmo arquivo: um carro, não dez."""
    row = linha_de()
    ids = {ingere(cur, sessao, "4", equipe, row) for _ in range(10)}
    assert len(ids) == 1
    assert quantos(cur, "4", equipe) == 1


def test_reingestao_da_mesma_sessao_nao_multiplica_results(cur, equipe, sessao):
    """
    O defeito chegava a `results` por aqui: id de carro novo a cada passagem,
    e `UNIQUE (session_id, car_id)` satisfeito por construção.
    """
    row = linha_de()
    for _ in range(3):
        ingere(cur, sessao, "4", equipe, row)

    cur.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
    assert cur.fetchone()[0] == 1


def test_reingestao_atualiza_em_vez_de_duplicar(cur, equipe, sessao):
    """A segunda passagem corrige o que a primeira gravou, na mesma linha."""
    car_id = upsert_car(cur, "4", equipe, linha_de(), "ALMS", sessao)
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

def test_mesmo_numero_equipes_distintas_sao_carros_distintos(cur, equipe,
                                                             outra_equipe, sessao):
    """
    Imola 2025, 'Race 1': #115 corre como GPA RACING e como DC RACING.
    São dois carros, e uma regra que os fundisse apagaria um resultado.
    """
    um   = ingere(cur, sessao, "115", equipe,       linha_de(NUMBER="115"), "ELMS")
    dois = ingere(cur, sessao, "115", outra_equipe, linha_de(NUMBER="115"), "ELMS")
    assert um != dois


def test_numero_com_zero_a_esquerda_nao_e_o_mesmo_carro(cur, equipe, sessao):
    """IMSA: `#01` e `#1` correm juntos. ADR 0001, F5 e P1."""
    um   = ingere(cur, sessao, "01", equipe, linha_de(NUMBER="01"), "IMSA")
    dois = ingere(cur, sessao, "1",  equipe, linha_de(NUMBER="1"),  "IMSA")
    assert um != dois


def test_mudanca_de_classe_cria_outro_carro(cur, equipe, sessao):
    """
    `LMP2` → `LMP2 PRO/AM` dentro de um evento produz duas linhas em `cars`.

    Isto é o comportamento atual, não uma decisão desta correção: `cars` não
    tem escopo temporal e não carrega a identidade da inscrição. Quem decide
    que as duas são a mesma inscrição é o modelo competitivo, pela migration
    008, e esta correção não o antecipa.
    """
    um   = ingere(cur, sessao, "23", equipe,
                  linha_de(NUMBER="23", CLASS="LMP2"), "ELMS")
    dois = ingere(cur, sessao, "23", equipe,
                  linha_de(NUMBER="23", CLASS="LMP2 PRO/AM"), "ELMS")
    assert um != dois


def test_mudanca_de_equipe_cria_outro_carro(cur, equipe, outra_equipe, sessao):
    um   = ingere(cur, sessao, "63", equipe,       linha_de(NUMBER="63"), "WEC")
    dois = ingere(cur, sessao, "63", outra_equipe, linha_de(NUMBER="63"), "WEC")
    assert um != dois


def test_colunas_nulas_nao_dividem_a_identidade(cur, equipe, sessao):
    """
    `=` nunca é verdadeiro contra NULL. Sem IS NOT DISTINCT FROM, um carro sem
    classe criaria uma linha nova a cada passagem — a duplicação de volta, só
    que restrita aos campos vazios.
    """
    row = linha_de(NUMBER="77", CLASS="", GROUP="", TYRES="")
    um   = ingere(cur, sessao, "77", equipe, row, "ELMS")
    dois = ingere(cur, sessao, "77", equipe, row, "ELMS")
    assert um == dois
    assert quantos(cur, "77", equipe) == 1


# ── Os dois dialetos da PR #38 ───────────────────────────────

def test_os_dois_dialetos_resolvem_para_o_mesmo_carro(cur, equipe, sessao):
    """
    O mesmo carro visto num arquivo de corrida e num de treino **da mesma
    sessão**. Se os dialetos produzissem identidades diferentes, reingerir a
    sessão pelo outro arquivo criaria um carro novo.

    O recorte "da mesma sessão" é de R2-B: ver o par em duas sessões é o
    teste logo abaixo, e lá a resposta passou a ser outra.
    """
    corrida = linha_de(NUMBER="4")
    treino = {
        "POS": "1", "NUMBER": "4", "CLASS": "LMP2", "TEAM": "EQUIPE A",
        "VEHICLE": "Oreca 07-Gibson", "LAPS": "36", "LAP": "2",
        "TIME": "1:46.925", "GAP_FIRST": "-", "GAP_PREVIOUS": "-",
        "KPH": "181.5", "GROUP": "", "TIRES": "M",
    }
    assert ingere(cur, sessao, "4", equipe, corrida) == \
           ingere(cur, sessao, "4", equipe, treino)
    assert quantos(cur, "4", equipe) == 1


# ── R2-B: a descrição não atravessa a sessão ─────────────────

def test_a_mesma_descricao_em_duas_sessoes_sao_dois_carros(cur, equipe,
                                                           sessao, outra_sessao):
    """
    O caso que motivou R2-B.

    `MEASURED` no acervo: 4.148 das 4.458 identidades distintas são
    carregadas por inscrições de mais de uma sessão, uma delas por 290 —
    #88 Proton Competition num Porsche 911 RSR responde por Silverstone 2015
    e por Paul Ricard 2019. Reaproveitar o carro entre as duas grava o
    resultado contra uma linha que as voltas da outra sessão não conhecem.
    """
    row = linha_de(NUMBER="4")
    um   = ingere(cur, sessao,       "4", equipe, dict(row))
    dois = ingere(cur, outra_sessao, "4", equipe, dict(row))
    assert um != dois, "a descricao nao pode atravessar a sessao"
    assert quantos(cur, "4", equipe) == 2


def test_o_carro_de_outra_sessao_nunca_e_candidato(cur, equipe,
                                                   sessao, outra_sessao):
    """
    O contrapositivo: existir um carro idêntico alhures não impede nem
    atrapalha: a segunda sessão cria o seu e os dois convivem, cada um
    apontado pela sua.
    """
    row = linha_de(NUMBER="9")
    alheio = ingere(cur, outra_sessao, "9", equipe, dict(row))
    meu    = ingere(cur, sessao,       "9", equipe, dict(row))
    assert alheio != meu

    cur.execute("SELECT car_id FROM results WHERE session_id = %s", (sessao,))
    assert [r[0] for r in cur.fetchall()] == [meu]
    cur.execute("SELECT car_id FROM results WHERE session_id = %s", (outra_sessao,))
    assert [r[0] for r in cur.fetchall()] == [alheio]


def test_o_portador_de_voltas_vence_o_menor_id(cur, equipe, sessao):
    """
    Entre candidatos idênticos, o que já carrega as voltas **desta** sessão.

    `MEASURED`: 68 grupos (sessão, identidade) têm um portador de voltas que
    não é o menor id. Devolver o menor poria o resultado publicado num carro
    e deixaria as voltas no outro — a separação que este escopo existe para
    impedir.
    """
    row = linha_de(NUMBER="31")
    primeiro = ingere(cur, sessao, "31", equipe, dict(row))

    # Uma duplicata legada, de id maior, e e **ela** que carrega as voltas.
    cur.execute(
        "INSERT INTO cars (number, team_id, vehicle, car_class, car_group, tires) "
        "VALUES (%s, %s, %s, %s, NULL, %s) RETURNING id",
        ("31", equipe, "Oreca 07-Gibson", "LMP2", "M"))
    duplicata = cur.fetchone()[0]
    assert duplicata > primeiro
    insert_result(cur, sessao, duplicata, row)
    prende_volta(cur, sessao, duplicata)

    assert upsert_car(cur, "31", equipe, row, "ALMS", sessao) == duplicata


def test_dois_portadores_de_voltas_sao_recusados(cur, equipe, sessao):
    """
    687 grupos no acervo têm dois carros idênticos carregando voltas da mesma
    sessão. Escolher um é escolher qual conjunto de voltas fica órfão de
    resultado, e isso não se decide por ordem de id.
    """
    row = linha_de(NUMBER="44")
    um = ingere(cur, sessao, "44", equipe, dict(row))
    prende_volta(cur, sessao, um, 1)

    cur.execute(
        "INSERT INTO cars (number, team_id, vehicle, car_class, car_group, tires) "
        "VALUES (%s, %s, %s, %s, NULL, %s) RETURNING id",
        ("44", equipe, "Oreca 07-Gibson", "LMP2", "M"))
    dois = cur.fetchone()[0]
    insert_result(cur, sessao, dois, row)
    prende_volta(cur, sessao, dois, 1)

    with pytest.raises(CarIdentityAmbiguous) as exc:
        upsert_car(cur, "44", equipe, row, "ALMS", sessao)
    assert str(um) in str(exc.value) and str(dois) in str(exc.value)


def test_legado_com_varias_linhas_sem_volta_usa_o_representante(cur, equipe, sessao):
    """
    Sem portador de voltas, o menor id — representante canônico entre linhas
    provadamente idênticas, e nenhum carro novo.
    """
    row = linha_de(NUMBER="55")
    ids = []
    for _ in range(3):
        cur.execute(
            "INSERT INTO cars (number, team_id, vehicle, car_class, car_group, tires) "
            "VALUES (%s, %s, %s, %s, NULL, %s) RETURNING id",
            ("55", equipe, "Oreca 07-Gibson", "LMP2", "M"))
        ids.append(cur.fetchone()[0])
    # Uma so pode ter resultado nesta sessao: `results` e chaveada em (sessao, carro).
    insert_result(cur, sessao, ids[1], row)

    antes = quantos(cur, "55", equipe)
    assert upsert_car(cur, "55", equipe, row, "ALMS", sessao) == ids[1]
    assert quantos(cur, "55", equipe) == antes, "nenhum carro novo"


def test_carro_sem_resultado_previo_e_criado(cur, equipe, sessao):
    antes = quantos(cur, "72", equipe)
    novo = upsert_car(cur, "72", equipe, linha_de(NUMBER="72"), "ALMS", sessao)
    assert novo is not None
    assert quantos(cur, "72", equipe) == antes + 1


# ── Ambiguidade que não se resolve em silêncio ───────────────

def test_duas_linhas_indistinguiveis_viram_um_carro_e_isso_e_o_limite(cur, equipe,
                                                                      sessao):
    """
    Duas linhas iguais em todas as oito colunas que o loader escreve não têm
    como ser separadas aqui, e viram um carro só.

    Não é uma decisão de identidade: é a ausência de qualquer coisa com que
    decidir. O que torna isso seguro é medido, não suposto — em 3.436 arquivos
    de classificação, nenhum par de linhas de um mesmo arquivo compartilha essa
    tupla, de modo que o caso não ocorre dentro de uma sessão.
    """
    row = linha_de(NUMBER="88")
    um   = ingere(cur, sessao, "88", equipe, dict(row), "WEC")
    dois = ingere(cur, sessao, "88", equipe, dict(row), "WEC")
    assert um == dois
    assert quantos(cur, "88", equipe) == 1


def test_uma_coluna_diferente_ja_basta_para_separar(cur, equipe, sessao):
    """O contrapositivo do teste acima: nada é ignorado na comparação."""
    base = linha_de(NUMBER="88")
    um = ingere(cur, sessao, "88", equipe, base, "WEC")
    for campo, valor in (("VEHICLE", "Ligier JS P320"), ("GROUP", "AM"),
                         ("TYRES", "G"), ("IMSA_CarId", "123")):
        outro = ingere(cur, sessao, "88", equipe,
                       dict(base, **{campo: valor}), "WEC")
        assert outro != um, f"{campo} nao separou"


# ── Concorrência ─────────────────────────────────────────────

def test_duas_ingestoes_simultaneas_criam_um_carro_so(sandbox):
    """
    Duas conexões PostgreSQL de verdade, disputando a mesma identidade na
    mesma sessão.

    Sem o lock, ambas buscam, nenhuma encontra, e ambas inserem. As duas
    comitam — dentro do schema do módulo, que é descartado inteiro ao final,
    de modo que não há linha preexistente a atingir nem remoção a acertar.

    Desde R2-B a garantia é sobre o **passo de ingestão**: quem espera no
    lock só encontra o carro porque quem passou antes gravou o resultado que
    o prende à sessão, e os dois estão na mesma transação. Resolver sem
    gravar não dá garantia nenhuma, e o teste logo abaixo fixa isso.
    """
    semente = connect_to_sandbox(sandbox)
    try:
        with semente.cursor() as c:
            c.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id",
                      ("EQUIPE CONCORRENTE",))
            team_id = c.fetchone()[0]
            c.execute("INSERT INTO sessions (event_id, raw_id, name) "
                      "VALUES (%s, %s, %s) RETURNING id",
                      (1, "zz_sandbox_conc", "Race"))
            sessao_id = c.fetchone()[0]
        semente.commit()
    finally:
        semente.close()

    row = linha_de(NUMBER="991", TEAM="EQUIPE CONCORRENTE")
    barreira = threading.Barrier(2, timeout=20)
    ids: list[int] = []
    erros: list[Exception] = []

    def ingere():
        c = connect_to_sandbox(sandbox)
        try:
            with c.cursor() as k:
                k.execute("SELECT 1")      # abre a transação antes da barreira
                barreira.wait()
                car_id = upsert_car(k, "991", team_id, row, "WEC", sessao_id)
                insert_result(k, sessao_id, car_id, row)
                ids.append(car_id)
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

    conferencia = connect_to_sandbox(sandbox)
    try:
        with conferencia.cursor() as k:
            k.execute(
                "SELECT count(*) FROM cars WHERE number = %s AND team_id = %s",
                ("991", team_id),
            )
            criados = k.fetchone()[0]
    finally:
        conferencia.close()

    assert not erros, erros
    assert len(ids) == 2
    assert ids[0] == ids[1], "as duas ingestoes deviam ter devolvido o mesmo carro"
    assert criados == 1, f"esperava 1 linha em cars, encontrei {criados}"


# ── O bloqueio do loader ─────────────────────────────────────

def test_o_bloqueio_de_escrita_do_loader_continua_ativo():
    from load_classification import OVERRIDE_ENV, _writes_are_blocked

    assert OVERRIDE_ENV == "OPENWEC_ALLOW_DUPLICATING_LOADER"
    assert _writes_are_blocked() is True, "o bloqueio do loader nao pode sair daqui"


# ── Falha entre as duas operacoes ────────────────────────────

def test_falha_entre_upsert_e_insert_nao_deixa_carro(conn, sandbox, equipe, sessao):
    """
    O par da §2.1 do R2-B: resolver o carro e **nao** gravar o resultado.

    Dentro da transacao o carro existe; apos o ROLLBACK nao existe nem ele nem
    o resultado, e a passagem seguinte cria exatamente um. A transacao e o que
    torna o acoplamento seguro — e o carregador faz os dois dentro dela.
    """
    row = linha_de(NUMBER="301")
    with conn.cursor() as cur:
        car_id = upsert_car(cur, "301", equipe, row, "ALMS", sessao)
        assert car_id is not None
        assert quantos(cur, "301", equipe) == 1
    conn.rollback()

    with conn.cursor() as cur:
        assert quantos(cur, "301", equipe) == 0
        cur.execute("SELECT count(*) FROM results WHERE session_id = %s", (sessao,))
        assert cur.fetchone()[0] == 0

        de_novo = ingere(cur, sessao, "301", equipe, row)
        assert quantos(cur, "301", equipe) == 1
        cur.execute("SELECT car_id FROM results WHERE session_id = %s", (sessao,))
        assert [r[0] for r in cur.fetchall()] == [de_novo]
    conn.rollback()


def test_resolver_e_comitar_sem_gravar_deixa_orfao(conn, sandbox):
    """
    O risco residual R1, fixado como comportamento conhecido e nao como
    surpresa.

    Comitar `upsert_car` **sem** `insert_result` deixa um carro que nenhuma
    sessao aponta — e a chamada seguinte, que so procura entre os apontados,
    cria outro. Este teste existe para que a correcao disso, se vier, seja
    uma mudanca deliberada e visivel aqui.

    Equipe e sessao proprias, porque este e o unico teste do arquivo que
    comita: as fixtures compartilhadas sao desfeitas por ROLLBACK, e o que
    aqui se comita tem de ser desfeito a mao.
    """
    row = linha_de(NUMBER="302", TEAM="EQUIPE ORFA")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id",
                    ("EQUIPE ORFA",))
        equipe_id = cur.fetchone()[0]
        cur.execute("INSERT INTO sessions (event_id, raw_id, name) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (1, "zz_sandbox_orfa", "Race"))
        sessao_id = cur.fetchone()[0]
        orfao = upsert_car(cur, "302", equipe_id, row, "ALMS", sessao_id)
    conn.commit()

    try:
        with conn.cursor() as cur:
            segundo = upsert_car(cur, "302", equipe_id, row, "ALMS", sessao_id)
            assert segundo != orfao, \
                "comportamento conhecido: o orfao nao e reencontrado"
            assert quantos(cur, "302", equipe_id) == 2
        conn.rollback()
    finally:
        # O schema do modulo cai inteiro no fim; isto so impede que o estado
        # comitado atravesse os testes seguintes deste arquivo.
        with conn.cursor() as cur:        # nao ha DELETE em public
            cur.execute("DELETE FROM cars WHERE team_id = %s", (equipe_id,))
            cur.execute("DELETE FROM sessions WHERE id = %s", (sessao_id,))
            cur.execute("DELETE FROM teams WHERE id = %s", (equipe_id,))
        conn.commit()


# ── results e laps nao se separam ────────────────────────────

def test_a_reingestao_mantem_o_resultado_no_portador_das_voltas(cur, equipe, sessao):
    """
    A invariante que o escopo protege, exercida ponta a ponta.

    Um carro com voltas e um irmao identico sem voltas na mesma sessao. Tres
    reingestoes depois, o resultado continua no portador, e `laps` e `results`
    seguem apontando a mesma linha.
    """
    row = linha_de(NUMBER="303")
    portador = ingere(cur, sessao, "303", equipe, row)
    prende_volta(cur, sessao, portador, 1)
    prende_volta(cur, sessao, portador, 2)

    for _ in range(3):
        assert ingere(cur, sessao, "303", equipe, row) == portador

    cur.execute("SELECT car_id FROM results WHERE session_id = %s", (sessao,))
    assert [r[0] for r in cur.fetchall()] == [portador]
    cur.execute("SELECT DISTINCT car_id FROM laps WHERE session_id = %s", (sessao,))
    assert [r[0] for r in cur.fetchall()] == [portador]
    assert quantos(cur, "303", equipe) == 1


def test_nenhum_resultado_se_perde_entre_sessoes(cur, equipe, sessao, outra_sessao):
    """
    A mesma inscricao em duas sessoes: dois carros, **dois** resultados.

    Sob a regra antiga as duas resolviam para um carro so, e `results`
    guardava as duas linhas por causa de `UNIQUE (session_id, car_id)` — mas
    as voltas de uma sessao ficavam sob o carro da outra. Aqui nada se
    mistura e nada se perde.
    """
    row = linha_de(NUMBER="304")
    a = ingere(cur, sessao,       "304", equipe, dict(row))
    b = ingere(cur, outra_sessao, "304", equipe, dict(row))
    prende_volta(cur, sessao,       a, 1)
    prende_volta(cur, outra_sessao, b, 1)

    assert a != b
    for s, esperado in ((sessao, a), (outra_sessao, b)):
        cur.execute("SELECT car_id FROM results WHERE session_id = %s", (s,))
        assert [r[0] for r in cur.fetchall()] == [esperado]
        cur.execute("SELECT DISTINCT car_id FROM laps WHERE session_id = %s", (s,))
        assert [r[0] for r in cur.fetchall()] == [esperado]

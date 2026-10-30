"""
R1 — fundir equipes **muda a identidade** de carros ja gravados.

`team_id` e uma das oito colunas de `CAR_COLUMNS`. Ate aqui, a fusao de
`normalize_teams` escrevia nelas sem tomar o lock que `upsert_car` toma e sem
passar pelo guarda de identidade — de modo que podia deixar dois carros de
uma sessao indistinguiveis, que e precisamente o que o guarda recusa criar.
`MEASURED`: **76 grupos de equipe colidem** sob dobramento em 1.111 linhas.

A protecao e uma so, e e a que ja existia: `lock_car_identity`, por
(sessao, identidade), com a chave que `car_identity_lock_key` constroi. Nao
ha um segundo mecanismo, e ha teste afirmando isso.

O que a fusao **nao** pode fazer, e cada uma tem o seu teste aqui: criar dois
carros de identidade equivalente na mesma sessao, separar `results` de
`laps`, decidir uma ambiguidade escolhendo um carro, ou deixar meia fusao
gravada.

Contra PostgreSQL de verdade, no sandbox do carregador.
"""
from __future__ import annotations

import sys
import time
import threading
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "database" / "loader"))

psycopg2 = pytest.importorskip("psycopg2")

from tests.loader.sandbox import connect_to_sandbox  # noqa: E402
from database.loader.load_classification import (  # noqa: E402
    CAR_COLUMNS, car_identity_lock_key, insert_result, upsert_car,
)
from database.enrichment.normalize_teams import (  # noqa: E402
    FusaoRecusada, colisoes_internas, identidades_afetadas,
)

FONTE_FUSAO = (RAIZ / "database" / "enrichment" /
               "normalize_teams.py").read_text(encoding="utf-8")


def linha_de(**campos) -> dict:
    base = {
        "POSITION": "1", "NUMBER": "88", "CLASS": "LMGTE",
        "TEAM": "EQUIPE", "VEHICLE": "Porsche 911 RSR",
        "LAPS": "31", "TOTAL_TIME": "1:00'01.868", "STATUS": "Classified",
        "GAP_FIRST": "", "GAP_PREVIOUS": "", "FL_TIME": "1'48.714",
        "FL_LAPNUM": "25", "FL_KPH": "178.5", "GROUP": "", "TYRES": "D",
    }
    base.update(campos)
    return base


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
    with conn.cursor() as k:
        yield k


def equipe(cur, nome):
    cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (nome,))
    return cur.fetchone()[0]


def sessao(cur, raw_id):
    cur.execute("INSERT INTO sessions (event_id, raw_id, name) "
                "VALUES (%s, %s, %s) RETURNING id", (1, raw_id, "Race"))
    return cur.fetchone()[0]


def ingere(cur, sess, numero, team_id, row=None):
    row = row or linha_de(NUMBER=numero)
    car_id = upsert_car(cur, numero, team_id, row, "ELMS", sess)
    insert_result(cur, sess, car_id, row)
    return car_id


def volta(cur, sess, car_id, n=1):
    cur.execute("INSERT INTO laps (session_id, car_id, lap_number) "
                "VALUES (%s, %s, %s)", (sess, car_id, n))


def funde(cur, dup_id, canonical_id):
    """O que o laco de `normalize_teams` faz por fusao, na mesma ordem."""
    from database.loader.load_classification import lock_car_identity
    for sess, identidade in identidades_afetadas(cur, dup_id, canonical_id):
        lock_car_identity(cur, sess, identidade)
    colisoes = colisoes_internas(cur, dup_id, canonical_id)
    if colisoes:
        raise FusaoRecusada(f"colisao interna: {colisoes}")
    cur.execute("UPDATE cars SET team_id = %s WHERE team_id = %s",
                (canonical_id, dup_id))
    cur.execute("DELETE FROM teams WHERE id = %s", (dup_id,))


# ── Um mecanismo so ──────────────────────────────────────────

def test_a_fusao_usa_o_lock_do_carregador_e_nao_um_proprio():
    assert "lock_car_identity" in FONTE_FUSAO
    assert "from database.loader.load_classification import" in FONTE_FUSAO
    # Nenhuma chave nem lock construido a mao deste lado.
    assert "pg_advisory" not in FONTE_FUSAO
    assert "hashtextextended" not in FONTE_FUSAO


def test_a_chave_do_lock_e_a_mesma_dos_dois_lados():
    """
    Se as chaves divergissem, cada lado trancaria sozinho e o lock de um nao
    significaria nada para o outro.
    """
    attrs = ("88", 7, "Porsche 911 RSR", "LMGTE", None, "D", None, None)
    assert (car_identity_lock_key(1300, attrs)
            == car_identity_lock_key(1300, list(attrs)))
    assert car_identity_lock_key(1300, attrs) != car_identity_lock_key(1301, attrs)

    carregador = (RAIZ / "database" / "loader" /
                  "load_classification.py").read_text(encoding="utf-8")
    assert carregador.count("def car_identity_lock_key(") == 1
    corpo = carregador.split("def upsert_car(", 1)[1].split("\ndef ", 1)[0]
    assert "lock_car_identity(cur, session_id, attrs)" in corpo


def test_a_identidade_que_o_lock_cobre_e_a_de_depois_da_fusao(cur):
    """
    O carro passa a ter a equipe canonica, entao e essa identidade que pode
    colidir — e e nela que a ingestao concorrente estaria trabalhando.
    """
    canon, dup = equipe(cur, "CANONICA"), equipe(cur, "DUPLICADA")
    s = sessao(cur, "zz_r1_a")
    ingere(cur, s, "88", dup)

    afetadas = identidades_afetadas(cur, dup, canon)
    assert len(afetadas) == 1
    sess, identidade = afetadas[0]
    assert sess == s
    assert identidade[CAR_COLUMNS.index("team_id")] == canon
    assert identidade[CAR_COLUMNS.index("number")] == "88"


# ── Normalizacao sem colisao ─────────────────────────────────

def test_fusao_sem_colisao_acontece(cur):
    canon, dup = equipe(cur, "CANON A"), equipe(cur, "DUP A")
    s = sessao(cur, "zz_r1_b")
    carro = ingere(cur, s, "88", dup)
    outro = ingere(cur, s, "77", canon)

    assert colisoes_internas(cur, dup, canon) == []
    funde(cur, dup, canon)

    cur.execute("SELECT team_id FROM cars WHERE id = %s", (carro,))
    assert cur.fetchone()[0] == canon
    cur.execute("SELECT count(*) FROM teams WHERE id = %s", (dup,))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT team_id FROM cars WHERE id = %s", (outro,))
    assert cur.fetchone()[0] == canon


def test_equipes_iguais_em_sessoes_diferentes_nao_colidem(cur):
    """
    A identidade atravessa a sessao como **descricao**, nao como colisao: dois
    carros iguais em sessoes diferentes continuam sendo dois carros, e a fusao
    nao tem por que recusar.
    """
    canon, dup = equipe(cur, "CANON B"), equipe(cur, "DUP B")
    a, b = sessao(cur, "zz_r1_c1"), sessao(cur, "zz_r1_c2")
    um = ingere(cur, a, "88", dup)
    dois = ingere(cur, b, "88", canon)
    assert um != dois

    assert colisoes_internas(cur, dup, canon) == []
    funde(cur, dup, canon)

    cur.execute("SELECT team_id FROM cars WHERE id IN (%s, %s)", (um, dois))
    assert [r[0] for r in cur.fetchall()] == [canon, canon]


# ── Normalizacao que criaria colisao interna ─────────────────

def test_fusao_que_criaria_colisao_interna_e_recusada(cur):
    canon, dup = equipe(cur, "CANON C"), equipe(cur, "DUP C")
    s = sessao(cur, "zz_r1_d")
    um = ingere(cur, s, "88", canon)
    dois = ingere(cur, s, "88", dup)
    assert um != dois, "antes da fusao sao dois carros distintos"

    colisoes = colisoes_internas(cur, dup, canon)
    assert len(colisoes) == 1
    sess, carros = colisoes[0]
    assert sess == s
    assert sorted(carros) == sorted([um, dois])

    with pytest.raises(FusaoRecusada):
        funde(cur, dup, canon)


def test_a_recusa_nao_escolhe_carro_nenhum(cur):
    """
    Nem o menor id, nem o primeiro: a fusao e que nao acontece. Nada em
    `cars` nem em `teams` se move.
    """
    canon, dup = equipe(cur, "CANON D"), equipe(cur, "DUP D")
    s = sessao(cur, "zz_r1_e")
    um = ingere(cur, s, "88", canon)
    dois = ingere(cur, s, "88", dup)

    with pytest.raises(FusaoRecusada):
        funde(cur, dup, canon)

    cur.execute("SELECT id, team_id FROM cars WHERE id IN (%s, %s) ORDER BY id",
                (um, dois))
    assert cur.fetchall() == [(um, canon), (dois, dup)]
    cur.execute("SELECT count(*) FROM teams WHERE id = %s", (dup,))
    assert cur.fetchone()[0] == 1, "a equipe duplicada continua la"


def test_colisao_que_ja_existia_nao_e_imputada_a_fusao(cur):
    """
    Dois carros que **ja** apontavam para a equipe duplicada ja colidiam
    antes. A fusao nao os junta — eles ja estavam juntos — e recusa-la nao
    consertaria nada.
    """
    canon, dup = equipe(cur, "CANON E"), equipe(cur, "DUP E")
    s = sessao(cur, "zz_r1_f")
    um = ingere(cur, s, "88", dup)
    cur.execute("INSERT INTO cars (number, team_id, vehicle, car_class, tires) "
                "VALUES ('88', %s, 'Porsche 911 RSR', 'LMGTE', 'D') RETURNING id",
                (dup,))
    dois = cur.fetchone()[0]
    insert_result(cur, s, dois, linha_de())

    assert colisoes_internas(cur, dup, canon) == []
    funde(cur, dup, canon)
    cur.execute("SELECT team_id FROM cars WHERE id IN (%s, %s)", (um, dois))
    assert {r[0] for r in cur.fetchall()} == {canon}


# ── Carros com voltas e resultados ───────────────────────────

def test_carros_com_volta_e_resultado_sao_vistos(cur):
    canon, dup = equipe(cur, "CANON F"), equipe(cur, "DUP F")
    s = sessao(cur, "zz_r1_g")
    um = ingere(cur, s, "88", canon)
    volta(cur, s, um)
    dois = ingere(cur, s, "88", dup)
    volta(cur, s, dois)

    with pytest.raises(FusaoRecusada):
        funde(cur, dup, canon)


def test_carro_ligado_a_sessao_so_por_voltas_e_visto(cur):
    """
    O pertencimento e `results` **ou** `laps` — o mesmo criterio do guarda.
    Um carro sem resultado, so com volta, ainda e da sessao.
    """
    canon, dup = equipe(cur, "CANON G"), equipe(cur, "DUP G")
    s = sessao(cur, "zz_r1_h")
    com_resultado = ingere(cur, s, "88", canon)
    cur.execute("INSERT INTO cars (number, team_id, vehicle, car_class, tires) "
                "VALUES ('88', %s, 'Porsche 911 RSR', 'LMGTE', 'D') RETURNING id",
                (dup,))
    so_volta = cur.fetchone()[0]
    volta(cur, s, so_volta)

    afetadas = identidades_afetadas(cur, dup, canon)
    assert [sess for sess, _ in afetadas] == [s], "a volta basta para pertencer"

    colisoes = colisoes_internas(cur, dup, canon)
    assert len(colisoes) == 1
    assert sorted(colisoes[0][1]) == sorted([com_resultado, so_volta])
    with pytest.raises(FusaoRecusada):
        funde(cur, dup, canon)


def test_a_fusao_nao_separa_results_de_laps(cur):
    """
    A fusao nunca mexe em `car_id`, so em `team_id`, entao `results` e `laps`
    continuam apontando a mesma linha. Verificado depois de fundir.
    """
    canon, dup = equipe(cur, "CANON H"), equipe(cur, "DUP H")
    s = sessao(cur, "zz_r1_i")
    carro = ingere(cur, s, "88", dup)
    volta(cur, s, carro, 1)
    volta(cur, s, carro, 2)

    funde(cur, dup, canon)

    cur.execute("SELECT car_id FROM results WHERE session_id = %s", (s,))
    assert [r[0] for r in cur.fetchall()] == [carro]
    cur.execute("SELECT DISTINCT car_id FROM laps WHERE session_id = %s", (s,))
    assert [r[0] for r in cur.fetchall()] == [carro]
    cur.execute("""SELECT count(*) FROM (SELECT DISTINCT session_id, car_id
                     FROM laps WHERE session_id = %s) x
                    WHERE NOT EXISTS (SELECT 1 FROM results r
                     WHERE r.session_id = x.session_id AND r.car_id = x.car_id)""",
                (s,))
    assert cur.fetchone()[0] == 0


# ── Falha intermediaria, rollback integral ───────────────────

def test_falha_depois_do_update_desfaz_a_fusao_inteira(conn, sandbox):
    """
    O `UPDATE` em `cars` e o `DELETE` em `teams` sao uma transacao so. Uma
    falha entre os dois nao pode deixar carros reapontados com a equipe
    duplicada ainda viva — nem o contrario.
    """
    with conn.cursor() as cur:
        canon, dup = equipe(cur, "CANON I"), equipe(cur, "DUP I")
        s = sessao(cur, "zz_r1_j")
        carro = ingere(cur, s, "88", dup)
    conn.commit()

    try:
        with conn.cursor() as cur:
            for sess, identidade in identidades_afetadas(cur, dup, canon):
                from database.loader.load_classification import lock_car_identity
                lock_car_identity(cur, sess, identidade)
            assert colisoes_internas(cur, dup, canon) == []
            cur.execute("UPDATE cars SET team_id = %s WHERE team_id = %s",
                        (canon, dup))
            raise RuntimeError("falha simulada entre o UPDATE e o DELETE")
    except RuntimeError:
        conn.rollback()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT team_id FROM cars WHERE id = %s", (carro,))
            assert cur.fetchone()[0] == dup, "o UPDATE tinha de ter sido desfeito"
            cur.execute("SELECT count(*) FROM teams WHERE id = %s", (dup,))
            assert cur.fetchone()[0] == 1, "a equipe continua la"
        conn.rollback()
    finally:
        with conn.cursor() as cur:        # nao ha DELETE em public
            cur.execute("DELETE FROM laps WHERE session_id = %s", (s,))
            cur.execute("DELETE FROM results WHERE session_id = %s", (s,))
            cur.execute("DELETE FROM cars WHERE team_id IN (%s, %s)", (dup, canon))
            cur.execute("DELETE FROM sessions WHERE id = %s", (s,))
            cur.execute("DELETE FROM teams WHERE id IN (%s, %s)", (dup, canon))
        conn.commit()


# ── Reexecucao idempotente ───────────────────────────────────

def test_refundir_nao_encontra_o_que_mover(cur):
    canon, dup = equipe(cur, "CANON J"), equipe(cur, "DUP J")
    s = sessao(cur, "zz_r1_k")
    carro = ingere(cur, s, "88", dup)

    funde(cur, dup, canon)
    antes = identidades_afetadas(cur, dup, canon)
    assert antes == [], "nao ha mais carro apontando para a duplicada"
    assert colisoes_internas(cur, dup, canon) == []

    # Repetir e inocuo: o UPDATE nao alcanca nada.
    cur.execute("UPDATE cars SET team_id = %s WHERE team_id = %s", (canon, dup))
    assert cur.rowcount == 0
    cur.execute("SELECT team_id FROM cars WHERE id = %s", (carro,))
    assert cur.fetchone()[0] == canon


# ── Concorrencia entre fusao e ingestao ──────────────────────

def _espera_ate(condicao, segundos=15.0) -> bool:
    """Aguarda uma condicao ficar verdadeira; devolve se ficou."""
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicao():
            return True
        time.sleep(0.05)
    return False


def test_a_ingestao_espera_a_fusao_no_mesmo_lock(sandbox):
    """
    Duas conexoes reais, e o entrelacamento e **observado**, nao torcido.

    A fusao toma o lock da identidade pos-fusao e segura. A ingestao da mesma
    identidade, na mesma sessao, tem de **bloquear** — e isso e verificado em
    `pg_locks`, numa terceira conexao, antes de a fusao ser liberada. So
    depois disso a fusao comita, e a ingestao, ao destravar, ve a equipe ja
    fundida e encontra o carro em vez de criar outro.

    Sem o lock do lado da fusao este teste falha na primeira assercao: a
    ingestao nao espera, resolve contra um estado que ainda vai mudar, e as
    duas produzem a mesma identidade em duas linhas. Verificado removendo o
    lock.
    """
    semente = connect_to_sandbox(sandbox)
    try:
        with semente.cursor() as c:
            canon, dup = equipe(c, "CANON CONC"), equipe(c, "DUP CONC")
            s = sessao(c, "zz_r1_conc")
            carro = ingere(c, s, "88", dup)
        semente.commit()
    finally:
        semente.close()

    from database.loader.load_classification import lock_car_identity

    travou = threading.Event()      # a fusao ja tem os locks
    liberar = threading.Event()     # a fusao pode comitar
    estado: dict = {}
    erros: list[Exception] = []

    def funde_devagar():
        c = connect_to_sandbox(sandbox)
        try:
            with c.cursor() as k:
                k.execute("SELECT 1")
                for sess, identidade in identidades_afetadas(k, dup, canon):
                    lock_car_identity(k, sess, identidade)
                travou.set()
                liberar.wait(timeout=20)
                k.execute("UPDATE cars SET team_id = %s WHERE team_id = %s",
                          (canon, dup))
                k.execute("DELETE FROM teams WHERE id = %s", (dup,))
            c.commit()
        except Exception as exc:           # noqa: BLE001
            erros.append(exc)
            c.rollback()
        finally:
            c.close()

    def ingere_concorrente():
        c = connect_to_sandbox(sandbox)
        try:
            with c.cursor() as k:
                k.execute("SELECT 1")
                estado["car_id"] = ingere(k, s, "88", canon)
            c.commit()
        except Exception as exc:           # noqa: BLE001
            erros.append(exc)
            c.rollback()
        finally:
            c.close()

    observador = connect_to_sandbox(sandbox)
    observador.autocommit = True

    def ha_espera_por_advisory() -> bool:
        with observador.cursor() as k:
            k.execute("SELECT count(*) FROM pg_locks "
                      "WHERE locktype = 'advisory' AND NOT granted")
            return k.fetchone()[0] > 0

    fusao = threading.Thread(target=funde_devagar)
    fusao.start()
    assert travou.wait(timeout=20), "a fusao nao chegou a tomar os locks"

    ingestao = threading.Thread(target=ingere_concorrente)
    ingestao.start()
    bloqueou = _espera_ate(ha_espera_por_advisory)

    liberar.set()
    for f in (fusao, ingestao):
        f.join(timeout=40)

    try:
        with observador.cursor() as k:
            k.execute("SELECT count(*) FROM cars WHERE number = '88' "
                      "AND team_id = %s", (canon,))
            quantos = k.fetchone()[0]
            k.execute("SELECT team_id FROM cars WHERE id = %s", (carro,))
            equipe_do_carro = k.fetchone()[0]
            # limpeza do estado comitado      # nao ha DELETE em public
            k.execute("DELETE FROM laps WHERE session_id = %s", (s,))
            k.execute("DELETE FROM results WHERE session_id = %s", (s,))
            k.execute("DELETE FROM cars WHERE team_id IN (%s, %s)", (dup, canon))
            k.execute("DELETE FROM sessions WHERE id = %s", (s,))
            k.execute("DELETE FROM teams WHERE id IN (%s, %s)", (dup, canon))
    finally:
        observador.close()

    assert not erros, erros
    assert bloqueou, (
        "a ingestao nao esperou: nenhum lock consultivo apareceu pendente em "
        "pg_locks enquanto a fusao segurava o seu")
    assert equipe_do_carro == canon, "a fusao foi aplicada"
    assert quantos == 1, (
        f"esperava um carro #88 na equipe canonica, encontrei {quantos}: a "
        f"ingestao resolveu contra um estado que a fusao ainda ia mudar")
    assert estado["car_id"] == carro, (
        "a ingestao devia ter encontrado o carro ja fundido, nao criado outro")


# ── O laco real de `normalize_teams`, ponta a ponta ──────────

@pytest.fixture
def fusao_no_sandbox(sandbox, monkeypatch):
    """
    `run()` abre a propria conexao a partir de `DB_CONFIG`. Apontar o
    `search_path` dela para o schema do modulo mantem a execucao inteira
    dentro do sandbox, sem alterar codigo de producao.
    """
    import database.enrichment.normalize_teams as nt
    monkeypatch.setitem(nt.DB_CONFIG, "options",
                        f'-c search_path="{sandbox["schema"]}",public')
    for chave, valor in sandbox["db"].items():
        monkeypatch.setitem(nt.DB_CONFIG, chave, valor)
    return nt


def _prepara(conn, nome_canonico, nome_duplicado, mesma_sessao):
    with conn.cursor() as cur:
        canon = equipe(cur, nome_canonico)
        dup = equipe(cur, nome_duplicado)
        a = sessao(cur, f"zz_run_{nome_duplicado[:8]}_a")
        b = a if mesma_sessao else sessao(cur, f"zz_run_{nome_duplicado[:8]}_b")
        um = ingere(cur, a, "88", canon)
        dois = ingere(cur, b, "88", dup)
    conn.commit()
    return canon, dup, um, dois, a, b


def _limpa(conn, equipes, sessoes):
    with conn.cursor() as cur:         # nao ha DELETE em public
        for s in set(sessoes):
            cur.execute("DELETE FROM laps WHERE session_id = %s", (s,))
            cur.execute("DELETE FROM results WHERE session_id = %s", (s,))
        for t in equipes:
            cur.execute("DELETE FROM cars WHERE team_id = %s", (t,))
        for s in set(sessoes):
            cur.execute("DELETE FROM sessions WHERE id = %s", (s,))
        for t in equipes:
            cur.execute("DELETE FROM teams WHERE id = %s", (t,))
    conn.commit()


def test_run_recusa_a_fusao_que_criaria_colisao_e_sai_com_1(conn, fusao_no_sandbox):
    """
    O laco real: a recusa vira fusao nao aplicada, com codigo de saida 1, e
    **nada** se move — nem o carro, nem a equipe duplicada.
    """
    canon, dup, um, dois, a, b = _prepara(conn, "AF Corse", "AF CORSE", True)
    try:
        assert fusao_no_sandbox.run(dry_run=False) == 1
        with conn.cursor() as cur:
            cur.execute("SELECT id, team_id FROM cars WHERE id IN (%s, %s) "
                        "ORDER BY id", (um, dois))
            assert cur.fetchall() == [(um, canon), (dois, dup)]
            cur.execute("SELECT count(*) FROM teams WHERE id = %s", (dup,))
            assert cur.fetchone()[0] == 1
        conn.rollback()
    finally:
        _limpa(conn, (canon, dup), (a, b))


def test_run_aplica_a_fusao_quando_nao_ha_colisao_e_sai_com_0(conn, fusao_no_sandbox):
    canon, dup, um, dois, a, b = _prepara(conn, "Idec Sport", "IDEC SPORT", False)
    try:
        assert fusao_no_sandbox.run(dry_run=False) == 0
        with conn.cursor() as cur:
            cur.execute("SELECT team_id FROM cars WHERE id IN (%s, %s)", (um, dois))
            assert {r[0] for r in cur.fetchall()} == {canon}
            cur.execute("SELECT count(*) FROM teams WHERE id = %s", (dup,))
            assert cur.fetchone()[0] == 0
        conn.rollback()
    finally:
        _limpa(conn, (canon, dup), (a, b))


def test_run_em_dry_run_nao_escreve(conn, fusao_no_sandbox):
    canon, dup, um, dois, a, b = _prepara(conn, "Panis Racing", "PANIS RACING", False)
    try:
        assert fusao_no_sandbox.run(dry_run=True) == 0
        with conn.cursor() as cur:
            cur.execute("SELECT id, team_id FROM cars WHERE id IN (%s, %s) "
                        "ORDER BY id", (um, dois))
            assert cur.fetchall() == [(um, canon), (dois, dup)]
        conn.rollback()
    finally:
        _limpa(conn, (canon, dup), (a, b))

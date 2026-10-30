"""
Os caminhos que escrevem em `cars` sem passar por `upsert_car`.

`upsert_car` resolve a identidade antes de inserir e serializa por identidade
com um advisory lock. Dois outros caminhos reescrevem colunas de identidade e
não fazem nem uma coisa nem outra:

  normalize_teams.run       UPDATE cars SET team_id = %s WHERE team_id = %s
  recover_classification
      .fill_car_gaps        UPDATE cars SET vehicle/car_class/car_group/tires

Estes testes **registram o comportamento observado**, não o desejado. Onde o
comportamento é um risco, a asserção diz qual é e o docstring diz por quê. Não
há correção aqui: alterar os dois caminhos é decisão separada.

Onde vive cada cenário pedido:

  1. reingestão idempotente       test_car_upsert_idempotency.py
                                  (`test_reingestao_do_mesmo_arquivo_nao_multiplica`,
                                   `test_reingestao_da_mesma_sessao_nao_multiplica_results`)
  2. concorrência entre ingestões test_car_upsert_idempotency.py
                                  (`test_duas_ingestoes_simultaneas_criam_um_carro_so`)
  3. colisão por fusão de times   aqui
  4. colisão por fill_car_gaps    aqui
  5. concorrência UPDATE × INSERT aqui
  6. exceção e rollback           aqui
  7. bloqueio de escrita          aqui, estendido

Toda escrita cai no schema do módulo, criado vazio e derrubado inteiro — ver
conftest.py. Nada em `public` é lido por conteúdo, escrito ou apagado.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "database" / "loader"))

psycopg2 = pytest.importorskip("psycopg2")

from tests.loader.sandbox import connect_to_sandbox  # noqa: E402
from load_classification import (  # noqa: E402
    car_identity, insert_result, upsert_car,
)

from database.enrichment import normalize_teams as nt  # noqa: E402
from database.recovery import recover_classification as rc  # noqa: E402


# ── Apoio ────────────────────────────────────────────────────

IDENTIDADE = ("number", "team_id", "vehicle", "car_class", "car_group",
              "tires", "imsa_car_id", "imsa_team_id")


def linha_de(**campos) -> dict:
    base = {
        "POSITION": "1", "NUMBER": "51", "CLASS": "LMGTE Pro",
        "TEAM": "AF Corse", "VEHICLE": "Ferrari 488 GTE EVO",
        "LAPS": "31", "TOTAL_TIME": "1:00'01.868", "STATUS": "Classified",
        "GAP_FIRST": "", "GAP_PREVIOUS": "", "FL_TIME": "1'48.714",
        "FL_LAPNUM": "25", "FL_KPH": "178.5", "GROUP": "", "TYRES": "M",
    }
    base.update(campos)
    return base


def cria_carro(cur, number: str, team_id: int, **colunas) -> int:
    """Uma linha de `cars` posta diretamente, para montar o estado inicial."""
    colunas = {"number": number, "team_id": team_id, **colunas}
    nomes = ", ".join(colunas)
    marcas = ", ".join(["%s"] * len(colunas))
    cur.execute(f"INSERT INTO cars ({nomes}) VALUES ({marcas}) RETURNING id",
                tuple(colunas.values()))
    return cur.fetchone()[0]


def quantos_com_a_identidade(cur, car_id: int) -> int:
    """Quantas linhas de `cars` casam com a identidade desta, do jeito do loader."""
    cur.execute(f"SELECT {', '.join(IDENTIDADE)} FROM cars WHERE id = %s",
                (car_id,))
    valores = cur.fetchone()
    onde = " AND ".join(f"{c} IS NOT DISTINCT FROM %s" for c in IDENTIDADE)
    cur.execute(f"SELECT count(*) FROM cars WHERE {onde}", valores)
    return cur.fetchone()[0]


def config_do_sandbox(sandbox: dict) -> dict:
    """DB_CONFIG apontando para o schema do módulo, pelo `options` da conexão."""
    cfg = dict(sandbox["db"])
    cfg["options"] = f'-c search_path="{sandbox["schema"]}",public'
    return cfg


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


# ── 3. Colisão de identidade após a normalização de equipes ──

def test_a_fusao_de_times_cria_identidade_duplicada(sandbox, conn, cur, capsys,
                                                    monkeypatch):
    """
    Dois carros idênticos exceto o time; os dois times normalizam igual.

    `clean_key` reduz "AF CORSE" e "AF Corse" à mesma chave, e a fusão
    reaponta o `team_id` dos dois carros para o canônico. Nesse instante eles
    passam a ter os mesmos oito atributos — e nada no caminho percebe. Não é
    o `upsert_car` que falha: ele nem é chamado.
    """
    cur.execute("INSERT INTO teams (name) VALUES ('AF Corse') RETURNING id")
    canonico = cur.fetchone()[0]
    cur.execute("INSERT INTO teams (name) VALUES ('AF CORSE') RETURNING id")
    duplicado = cur.fetchone()[0]
    comuns = {"vehicle": "Ferrari 488 GTE EVO", "car_class": "LMGTE Pro",
              "tires": "M"}
    a = cria_carro(cur, "51", canonico, **comuns)
    b = cria_carro(cur, "51", duplicado, **comuns)
    conn.commit()

    assert quantos_com_a_identidade(cur, a) == 1, "a montagem já estava duplicada"
    assert quantos_com_a_identidade(cur, b) == 1

    monkeypatch.setattr(nt, "DB_CONFIG", config_do_sandbox(sandbox))
    nt.run(dry_run=False)
    capsys.readouterr()

    conn.rollback()                                    # enxerga o que a fusão comitou
    cur.execute("SELECT team_id FROM cars WHERE id IN (%s, %s)", (a, b))
    times = {linha[0] for linha in cur.fetchall()}
    assert times == {canonico}, "a fusão devia ter reapontado os dois carros"

    # O registro: depois da fusão existem duas linhas para a mesma identidade.
    assert quantos_com_a_identidade(cur, a) == 2, (
        "comportamento observado: a fusão de times produz identidade duplicada "
        "em cars, sem lock e sem verificação")


def test_a_fusao_em_dry_run_nao_escreve(sandbox, conn, cur, capsys, monkeypatch):
    """O contraponto: com `--dry-run` nada muda, e é o que mantém o teste honesto."""
    cur.execute("INSERT INTO teams (name) VALUES ('Risi Competizione') RETURNING id")
    canonico = cur.fetchone()[0]
    cur.execute("INSERT INTO teams (name) VALUES ('RISI COMPETIZIONE') RETURNING id")
    duplicado = cur.fetchone()[0]
    carro = cria_carro(cur, "62", duplicado, vehicle="Ferrari 488 GTE")
    conn.commit()

    monkeypatch.setattr(nt, "DB_CONFIG", config_do_sandbox(sandbox))
    nt.run(dry_run=True)
    capsys.readouterr()

    conn.rollback()
    cur.execute("SELECT team_id FROM cars WHERE id = %s", (carro,))
    assert cur.fetchone()[0] == duplicado, "o dry-run escreveu"
    cur.execute("SELECT count(*) FROM teams WHERE id = %s", (canonico,))
    assert cur.fetchone()[0] == 1


# ── 4. Colisão de identidade após fill_car_gaps ──────────────

def test_preencher_lacunas_cria_identidade_duplicada(conn, cur):
    """
    Um carro com `vehicle` nulo e outro com `vehicle` preenchido.

    `fill_car_gaps` escreve só onde a coluna está NULL — nunca sobrescreve, e
    nisso está correto. Mas `vehicle` é um dos oito atributos: preenchê-lo
    move a linha de uma identidade para outra, e a outra já está ocupada.
    """
    cur.execute("INSERT INTO teams (name) VALUES ('Porsche GT Team') RETURNING id")
    equipe = cur.fetchone()[0]
    completo = cria_carro(cur, "91", equipe, vehicle="Porsche 911 RSR-19",
                          car_class="LMGTE Pro", tires="M")
    lacunoso = cria_carro(cur, "91", equipe, car_class="LMGTE Pro", tires="M")

    assert quantos_com_a_identidade(cur, completo) == 1
    assert quantos_com_a_identidade(cur, lacunoso) == 1, "a montagem já colidia"

    preenchidas, divergem = rc.fill_car_gaps(
        cur, lacunoso, linha_de(NUMBER="91", VEHICLE="Porsche 911 RSR-19",
                                CLASS="LMGTE Pro", TYRES="M"))
    assert preenchidas == 1 and divergem == []

    assert quantos_com_a_identidade(cur, lacunoso) == 2, (
        "comportamento observado: preencher uma coluna de identidade pode "
        "fundir a linha numa identidade que já existe")


def test_preencher_lacunas_nao_sobrescreve_e_relata_divergencia(conn, cur):
    """O caso em que não há colisão porque não há escrita: o valor já estava lá."""
    cur.execute("INSERT INTO teams (name) VALUES ('WeatherTech Racing') RETURNING id")
    equipe = cur.fetchone()[0]
    carro = cria_carro(cur, "79", equipe, vehicle="Porsche 911 RSR-19",
                       car_class="LMGTE Am", tires="M")

    preenchidas, divergem = rc.fill_car_gaps(
        cur, carro, linha_de(NUMBER="79", VEHICLE="Ferrari 488 GTE EVO",
                             CLASS="LMGTE Am", TYRES="M"))
    assert preenchidas == 0
    assert divergem == ["vehicle"], "a divergência tem de ser relatada"
    cur.execute("SELECT vehicle FROM cars WHERE id = %s", (carro,))
    assert cur.fetchone()[0] == "Porsche 911 RSR-19", "sobrescreveu"


# ── 5. Concorrência entre UPDATE e INSERT ────────────────────

def test_o_update_de_times_e_o_upsert_nao_se_coordenam(sandbox, conn, cur):
    """
    Duas conexões de verdade: uma funde times, a outra ingere.

    O advisory lock de `upsert_car` é tomado sobre a identidade. Quem faz o
    UPDATE não o toma, e a linha que ele está prestes a produzir ainda não
    está visível — então a ingestão procura, não encontra, e insere. As duas
    comitam e a identidade fica com duas linhas.

    A ordem aqui é determinística de propósito: o ponto não é uma corrida
    rara, é que não existe coordenação nenhuma entre os dois caminhos.
    """
    cur.execute("INSERT INTO teams (name) VALUES ('United Autosports') RETURNING id")
    canonico = cur.fetchone()[0]
    cur.execute("INSERT INTO teams (name) VALUES ('UNITED AUTOSPORTS') RETURNING id")
    duplicado = cur.fetchone()[0]
    row = linha_de(NUMBER="22", TEAM="United Autosports",
                   VEHICLE="Oreca 07-Gibson", CLASS="LMP2", TYRES="G")
    alvo = cria_carro(cur, "22", duplicado,
                      **dict(zip(("vehicle", "car_class", "car_group", "tires",
                                  "imsa_car_id", "imsa_team_id"),
                                 car_identity("22", duplicado, row)[2:])))
    # Desde R2-B a busca so alcanca carros que a sessao aponta, entao o alvo
    # tem de estar preso a ela — caso contrario o teste passaria por falta de
    # escopo, e nao pela falta de coordenacao que ele existe para mostrar.
    cur.execute("INSERT INTO sessions (event_id, raw_id, name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (1, "zz_sandbox_fusao", "Race"))
    sessao = cur.fetchone()[0]
    insert_result(cur, sessao, alvo, row)
    conn.commit()

    fusao = connect_to_sandbox(sandbox)
    ingestao = connect_to_sandbox(sandbox)
    try:
        with fusao.cursor() as f:
            # O statement exato de normalize_teams, transação deixada aberta.
            f.execute("UPDATE cars SET team_id = %s WHERE team_id = %s",
                      (canonico, duplicado))

        with ingestao.cursor() as i:
            novo = upsert_car(i, "22", canonico, row, "ELMS", sessao)
        ingestao.commit()

        fusao.commit()
    finally:
        fusao.close()
        ingestao.close()

    conn.rollback()
    assert novo != alvo, "a ingestão não podia ter enxergado a linha em fusão"
    assert quantos_com_a_identidade(cur, novo) == 2, (
        "comportamento observado: o lock por identidade do upsert_car não "
        "alcança o UPDATE, e os dois caminhos produzem a mesma identidade")


# ── Idempotência do reapontamento ────────────────────────────

def test_uma_segunda_fusao_do_mesmo_time_nao_encontra_o_que_mover(sandbox, conn,
                                                                  cur):
    """
    Repetir a fusão não reaponta nada de novo: o UPDATE é idempotente.

    As duas passagens aqui são sequenciais — a primeira comita e fecha antes
    de a segunda abrir — então isto não diz nada sobre espera concorrente, e
    não é o que está sendo afirmado. O que fica registrado é mais estreito: a
    repetição encontra `rowcount == 0` e o carro já está no time canônico.
    """
    cur.execute("INSERT INTO teams (name) VALUES ('Jota Sport') RETURNING id")
    canonico = cur.fetchone()[0]
    cur.execute("INSERT INTO teams (name) VALUES ('JOTA SPORT') RETURNING id")
    duplicado = cur.fetchone()[0]
    carro = cria_carro(cur, "38", duplicado, vehicle="Oreca 07-Gibson")
    conn.commit()

    primeira = connect_to_sandbox(sandbox)
    try:
        with primeira.cursor() as p:
            p.execute("UPDATE cars SET team_id = %s WHERE team_id = %s",
                      (canonico, duplicado))
        primeira.commit()
    finally:
        primeira.close()

    segunda = connect_to_sandbox(sandbox)
    try:
        with segunda.cursor() as s:
            s.execute("UPDATE cars SET team_id = %s WHERE team_id = %s",
                      (canonico, duplicado))
            assert s.rowcount == 0, "a segunda fusão achou linhas para mover"
        segunda.commit()
    finally:
        segunda.close()

    conn.rollback()
    cur.execute("SELECT team_id FROM cars WHERE id = %s", (carro,))
    assert cur.fetchone()[0] == canonico


# ── 6. Exceção e rollback nos caminhos de UPDATE ─────────────

class CursorFalso:
    """Registra cada statement e levanta no que for pedido."""

    def __init__(self, falhar_em: str | None = None):
        self.sql: list[str] = []
        self.falhar_em = falhar_em
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        if self.falhar_em and self.falhar_em in self.sql[-1]:
            raise psycopg2.errors.ForeignKeyViolation("falha simulada")

    def fetchone(self):
        return {"n": 0}

    def fetchall(self):
        return []

    def close(self):
        pass


class ConexaoFalsa:
    def __init__(self, cursor_de_leitura, cursor_de_escrita):
        self._leitura = cursor_de_leitura
        self._escrita = cursor_de_escrita
        self._proximo = [cursor_de_leitura, cursor_de_escrita]
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, **kwargs):
        return self._proximo.pop(0) if self._proximo else self._escrita

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_a_fusao_desfaz_e_segue_quando_um_merge_falha(monkeypatch, capsys):
    """
    `normalize_teams` engole a exceção: desfaz, conta um erro e segue.

    Sem banco — a conexão é falsa e registra os statements, de modo que a
    asserção é sobre o que foi emitido, não sobre contagens que um rollback
    deixaria iguais de qualquer jeito.
    """
    leitura = CursorFalso()
    # So a palavra, e nao o comeco do statement, porque test_sandbox_guard
    # proibe o literal de exclusao nas suites de loader — uma proibicao que
    # vale, ja que uma exclusao aqui alcancaria linhas reais. E e ela, e nao
    # "teams WHERE id", porque desde R1 a fusao tambem **le** essa linha
    # (`toma_a_equipe`), e o trecho antigo falhava naquela leitura.
    escrita = CursorFalso(falhar_em="DELETE")
    conexao = ConexaoFalsa(leitura, escrita)
    monkeypatch.setattr(nt.psycopg2, "connect", lambda **kw: conexao)
    monkeypatch.setattr(nt, "find_duplicate_groups", lambda cur: [
        {"clean_key": "afcorse", "canonical_id": 1, "canonical_name": "AF Corse",
         "duplicates": [{"id": 2, "name": "AF CORSE"}]},
        {"clean_key": "jota", "canonical_id": 3, "canonical_name": "Jota",
         "duplicates": [{"id": 4, "name": "JOTA"}]},
    ])

    nt.run(dry_run=False)
    saida = capsys.readouterr().out

    atualizacoes = [s for s in escrita.sql if s.startswith("UPDATE cars")]
    assert len(atualizacoes) == 2, "a segunda fusão não foi tentada"
    # As linhas sao tomadas sem esperar, antes de qualquer escrita: as duas
    # equipes e os carros da duplicada, em cada uma das duas fusoes.
    tomadas = [s for s in escrita.sql if "FOR UPDATE NOWAIT" in s]
    assert len(tomadas) == 4
    assert sum("FROM teams" in s for s in tomadas) == 2
    assert sum("FROM cars" in s for s in tomadas) == 2
    assert conexao.rollbacks == 2, "a falha não foi desfeita"
    assert conexao.commits == 0, "comitou apesar da falha"
    assert "[ERROR]" in saida
    # O registro: o script termina normalmente, sem sinalizar a falha ao chamador.
    assert "Teams after:" in saida or "[APPLY]" in saida


def test_o_update_rejeitado_aborta_a_transacao_inteira(conn, cur):
    """
    `fill_car_gaps` não trata exceção, e não tem como tratar.

    Um UPDATE que o banco recusa aborta a transação: o que o chamador já
    tinha escrito nela se perde no rollback. Não há isolamento por carro.
    """
    cur.execute("INSERT INTO teams (name) VALUES ('Signatech Alpine') RETURNING id")
    equipe = cur.fetchone()[0]
    antes = cria_carro(cur, "36", equipe, vehicle="Alpine A470-Gibson")
    carro = cria_carro(cur, "35", equipe, car_class="LMP2")

    with pytest.raises(psycopg2.errors.StringDataRightTruncation):
        rc.fill_car_gaps(cur, carro,
                         linha_de(NUMBER="35", VEHICLE="X" * 100))

    with pytest.raises(psycopg2.errors.InFailedSqlTransaction):
        cur.execute("SELECT 1")

    conn.rollback()
    cur.execute("SELECT count(*) FROM cars WHERE id IN (%s, %s)", (antes, carro))
    assert cur.fetchone()[0] == 0, (
        "comportamento observado: a transação inteira é desfeita, incluindo "
        "o que o chamador gravou antes do carro que falhou")


def test_preencher_carro_inexistente_quebra_antes_de_escrever(conn, cur):
    """Um id que não existe não vira UPDATE — vira TypeError no `zip`."""
    with pytest.raises(TypeError):
        rc.fill_car_gaps(cur, 2_000_000_000, linha_de())
    cur.execute("SELECT 1")          # a transação continua utilizável
    assert cur.fetchone()[0] == 1


# ── 7. O bloqueio de escrita ─────────────────────────────────

def test_o_bloqueio_recusa_a_carga_com_saida_2(monkeypatch, capsys):
    """
    Sem a variável, `run` não chega a abrir conexão: recusa com SystemExit(2).

    O teste que já existia conferia o nome da variável e o valor da função.
    Este confere o efeito, que é o que protege o banco.
    """
    import load_classification as lc

    monkeypatch.delenv(lc.OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(lc.psycopg2, "connect", lambda **kw: pytest.fail(
        "o bloqueio deixou a carga abrir conexão"))

    with pytest.raises(SystemExit) as saida:
        lc.run()
    assert saida.value.code == 2
    assert "BLOCKED" in capsys.readouterr().out


def test_a_simulacao_atravessa_o_bloqueio(sandbox, monkeypatch, capsys):
    """`--dry-run` nunca foi bloqueado, e continua não sendo."""
    import load_classification as lc

    ligacao = connect_to_sandbox(sandbox)
    try:
        monkeypatch.delenv(lc.OVERRIDE_ENV, raising=False)
        assert lc._writes_are_blocked() is True
        monkeypatch.setattr(lc.psycopg2, "connect", lambda **kw: ligacao)
        monkeypatch.setattr(lc, "RAW_DIRS", {})
        lc.run(dry_run=True)         # nao levanta
        assert "Dry run: True" in capsys.readouterr().out
    finally:
        if not ligacao.closed:       # `run` fecha a conexao que recebeu
            ligacao.rollback()
            ligacao.close()


@pytest.mark.parametrize("valor, bloqueado", [
    ("1", False), ("true", False), ("TRUE", False), ("yes", False),
    ("0", True), ("", True), ("sim", True), ("nao", True),
])
def test_so_os_valores_declarados_liberam_a_escrita(monkeypatch, valor, bloqueado):
    import load_classification as lc

    monkeypatch.setenv(lc.OVERRIDE_ENV, valor)
    assert lc._writes_are_blocked() is bloqueado

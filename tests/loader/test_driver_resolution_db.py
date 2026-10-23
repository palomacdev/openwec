"""
A resolução de piloto contra um schema próprio deste módulo.

O que estes testes acrescentam aos puros é o que um teste puro não alcança:
que a consulta carrega todos os candidatos, que `upsert_driver` deixa de
criar uma segunda linha para quem já está na tabela sob outra grafia, e que
duas cargas simultâneas do mesmo nome não produzem duas linhas.

Nada aqui escreve em `public`; ver sandbox.py para os dois portões.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

from tests.loader.sandbox import connect_to_sandbox  # noqa: E402

from row_identity import (  # noqa: E402
    DriverOutcome,
    forget_driver_index,
    resolve_driver_id,
)
from load_classification import upsert_driver  # noqa: E402


def fonte(first: str, last: str, **extra) -> dict:
    d = {"first_name": first, "last_name": last, "short_name": None,
         "country": None, "license": None, "hometown": None,
         "imsa_driver_id": None, "imsa_driver_plug_id": None,
         "imsa_driver_rating": None, "slot": 1}
    d.update(extra)
    return d


@pytest.fixture
def mundo(sandbox):
    """
    Uma conexão por teste. O índice de pilotos é por conexão e morre com
    ela, então nada precisa ser invalidado aqui — é essa a propriedade que
    test_o_indice_morre_com_a_conexao verifica.
    """
    conn = connect_to_sandbox(sandbox)
    cur = conn.cursor()

    class Mundo:
        cursor = cur

        def criar(self, first: str, last: str, **extra) -> int:
            cur.execute(
                "INSERT INTO drivers (first_name, last_name, country, imsa_driver_id) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (first, last, extra.get("country"), extra.get("imsa_driver_id")))
            return cur.fetchone()[0]

        def resolver(self, first, last):
            return resolve_driver_id(cur, first, last)

        def contar(self) -> int:
            cur.execute("SELECT count(*) FROM drivers")
            return cur.fetchone()[0]

    try:
        yield Mundo()
    finally:
        conn.rollback()
        conn.close()


# ── Leitura ───────────────────────────────────────────────────

def test_piloto_inexistente(mundo):
    assert mundo.resolver("Alguem", "Desconhecido") == (None, DriverOutcome.NOT_FOUND)


def test_correspondencia_exata(mundo):
    did = mundo.criar("Mark", "Patterson")
    assert mundo.resolver("Mark", "Patterson") == (did, DriverOutcome.EXACT_NAME)


def test_diferenca_de_caixa_resolve(mundo):
    did = mundo.criar("Mark", "Patterson")
    assert mundo.resolver("Mark", "PATTERSON") == (did, DriverOutcome.BY_NORMALIZED_NAME)


def test_diferenca_de_pontuacao_resolve(mundo):
    did = mundo.criar("Jean-Eric", "Vergne")
    assert mundo.resolver("Jean Eric", "VERGNE") == (did, DriverOutcome.BY_NORMALIZED_NAME)


def test_divisao_diferente_resolve(mundo):
    """O caso que `normalize_drivers.py` cria ao reescrever a particula."""
    did = mundo.criar("Giedo", "Van Der Garde")
    assert mundo.resolver("Giedo VAN DER", "GARDE") == (did, DriverOutcome.BY_NORMALIZED_NAME)


def test_duas_grafias_na_tabela_recusam(mundo):
    mundo.criar("Mark", "Patterson")
    mundo.criar("Mark", "PATTERSON")
    did, out = mundo.resolver("MARK", "patterson")
    assert did is None
    assert out is DriverOutcome.MULTIPLE_CANDIDATES


def test_a_grafia_exata_vence_mesmo_havendo_duas(mundo):
    a = mundo.criar("Mark", "Patterson")
    b = mundo.criar("Mark", "PATTERSON")
    assert mundo.resolver("Mark", "Patterson") == (a, DriverOutcome.EXACT_NAME)
    assert mundo.resolver("Mark", "PATTERSON") == (b, DriverOutcome.EXACT_NAME)


def test_abreviacao_nao_resolve(mundo):
    mundo.criar("Nicolas", "Minassian")
    assert mundo.resolver("Nic", "Minassian") == (None, DriverOutcome.NOT_FOUND)


def test_sem_nome(mundo):
    mundo.criar("Mark", "Patterson")
    assert mundo.resolver("", "") == (None, DriverOutcome.NO_NAME)


def test_a_restricao_unica_impede_duas_linhas_com_o_mesmo_nome_exato(mundo):
    """
    Por isso `get_driver_id` nunca foi nao-deterministico: o `LIMIT 1` sem
    `ORDER BY` consultava um conjunto de no maximo uma linha.
    """
    import psycopg2
    mundo.criar("Mark", "Patterson")
    with pytest.raises(psycopg2.IntegrityError):
        mundo.criar("Mark", "Patterson")


# ── Escrita ───────────────────────────────────────────────────

def test_upsert_cria_quando_o_piloto_e_novo(mundo):
    antes = mundo.contar()
    did = upsert_driver(mundo.cursor, fonte("Kamui", "Kobayashi"))[0]
    assert did is not None
    assert mundo.contar() == antes + 1


def test_upsert_reaproveita_a_grafia_normalizada(mundo):
    """
    O ponto da mudanca: a linha ja existe sob outra grafia, e o loader
    deixa de inserir uma segunda para a mesma pessoa.
    """
    did = mundo.criar("Giedo", "Van Der Garde")
    antes = mundo.contar()
    assert upsert_driver(mundo.cursor, fonte("Giedo VAN DER", "GARDE"))[0] == did
    assert mundo.contar() == antes


def test_upsert_nao_renomeia_a_linha_reaproveitada(mundo):
    """Nada aqui estabelece qual grafia e a certa, entao nenhuma e imposta."""
    did = mundo.criar("Giedo", "Van Der Garde", country="NLD")
    upsert_driver(mundo.cursor, fonte("Giedo VAN DER", "GARDE", country="BEL"))[0]
    mundo.cursor.execute(
        "SELECT first_name, last_name, country FROM drivers WHERE id = %s", (did,))
    assert mundo.cursor.fetchone() == ("Giedo", "Van Der Garde", "NLD")


def test_upsert_com_duas_grafias_ambiguas_nao_cria_uma_terceira(mundo):
    """
    Mudou nesta revisao: antes, a ambiguidade caia no INSERT e produzia mais
    uma linha para um nome que ja respondia a varias. Agora nao insere, nao
    escolhe, e devolve o motivo.
    """
    mundo.criar("Mark", "Patterson")
    mundo.criar("Mark", "PATTERSON")
    antes = mundo.contar()
    did, desfecho = upsert_driver(mundo.cursor, fonte("MARK", "patterson"))
    assert did is None
    assert desfecho is DriverOutcome.MULTIPLE_CANDIDATES
    assert mundo.contar() == antes


def test_varias_grafias_ambiguas_nao_aumentam_a_contagem(mundo):
    """Regressao: nenhuma das grafias cria linha enquanto houver ambiguidade."""
    mundo.criar("Mark", "Patterson")
    mundo.criar("Mark", "PATTERSON")
    antes = mundo.contar()
    for grafia in (("MARK", "patterson"), ("Mark", "patterson"),
                   ("mark", "PATTERSON"), ("MARK", "PATTERSON")):
        did, desfecho = upsert_driver(mundo.cursor, fonte(*grafia))
        assert did is None
        assert desfecho is DriverOutcome.MULTIPLE_CANDIDATES
    assert mundo.contar() == antes


def test_a_grafia_exata_continua_resolvendo_mesmo_com_ambiguidade(mundo):
    """
    A recusa e so para grafias novas. Quem casa exatamente continua sendo
    atualizado pelo caminho de sempre.
    """
    a = mundo.criar("Mark", "Patterson")
    mundo.criar("Mark", "PATTERSON")
    antes = mundo.contar()
    did, desfecho = upsert_driver(mundo.cursor, fonte("Mark", "Patterson"))
    assert (did, desfecho) == (a, DriverOutcome.EXACT_NAME)
    assert mundo.contar() == antes


def test_upsert_sem_nome_nao_cria(mundo):
    antes = mundo.contar()
    did, desfecho = upsert_driver(mundo.cursor, fonte("", ""))
    assert did is None
    assert desfecho is DriverOutcome.NO_NAME
    assert mundo.contar() == antes


def test_upsert_e_idempotente_na_reexecucao(mundo):
    primeiro = upsert_driver(mundo.cursor, fonte("Kamui", "Kobayashi"))[0]
    depois = mundo.contar()
    for _ in range(4):
        assert upsert_driver(mundo.cursor, fonte("Kamui", "Kobayashi"))[0] == primeiro
    assert mundo.contar() == depois


def test_upsert_da_mesma_pessoa_noutra_grafia_e_idempotente(mundo):
    primeiro = upsert_driver(mundo.cursor, fonte("Kamui", "Kobayashi"))[0]
    depois = mundo.contar()
    for grafia in (("Kamui", "KOBAYASHI"), ("KAMUI", "kobayashi"), ("Kamui", "Kobayashi")):
        assert upsert_driver(mundo.cursor, fonte(*grafia))[0] == primeiro
    assert mundo.contar() == depois


def test_abreviacao_continua_sendo_pessoa_separada(mundo):
    """`Nic` nao vira `Nicolas` nem na escrita."""
    antes = mundo.contar()
    nicolas = upsert_driver(mundo.cursor, fonte("Nicolas", "Minassian"))[0]
    nic = upsert_driver(mundo.cursor, fonte("Nic", "Minassian"))[0]
    assert nic != nicolas
    assert mundo.contar() == antes + 2


# ── Concorrência ──────────────────────────────────────────────

def test_duas_cargas_simultaneas_nao_criam_duas_linhas(sandbox):
    """
    Duas conexoes reais, nao mocks. A restricao unica em (first_name,
    last_name) serializa o `ON CONFLICT`, entao o nome exato nao pode nascer
    duas vezes — e e so isso que se afirma aqui.
    """
    barreira = threading.Barrier(2)
    resultados: list[tuple[int | None, str | None]] = []
    trava = threading.Lock()

    def carga():
        conn = connect_to_sandbox(sandbox)
        try:
            cur = conn.cursor()
            barreira.wait(timeout=10)
            did = upsert_driver(cur, fonte("Sebastien", "Buemi"))[0]
            conn.commit()
            with trava:
                resultados.append((did, None))
        except Exception as exc:                      # pragma: no cover
            with trava:
                resultados.append((None, repr(exc)))
            conn.rollback()
        finally:
            conn.close()

    fios = [threading.Thread(target=carga) for _ in range(2)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(timeout=30)

    erros = [e for _, e in resultados if e]
    assert not erros, f"carga concorrente falhou: {erros}"
    ids = {d for d, _ in resultados}
    assert len(resultados) == 2
    assert len(ids) == 1, f"as duas cargas criaram linhas diferentes: {ids}"

    # A linha fica: a limpeza deste modulo e o DROP SCHEMA do sandbox, nao
    # ha DELETE em nenhuma suite de loader.
    conferencia = connect_to_sandbox(sandbox)
    try:
        cur = conferencia.cursor()
        cur.execute("SELECT count(*) FROM drivers WHERE last_name = 'Buemi'")
        assert cur.fetchone()[0] == 1
    finally:
        conferencia.close()


# ── Ciclo de vida do índice ───────────────────────────────────

def test_o_indice_nao_atravessa_conexoes(sandbox):
    """
    Uma conexão escreve, outra lê. Sem índice por conexão, a segunda
    responderia com o que dobrou antes da escrita.
    """
    escritor = connect_to_sandbox(sandbox)
    leitor = connect_to_sandbox(sandbox)
    try:
        ce, cl = escritor.cursor(), leitor.cursor()
        # o leitor dobra a tabela antes de a linha existir
        assert resolve_driver_id(cl, "Andre", "Lotterer")[1] is DriverOutcome.NOT_FOUND
        ce.execute("INSERT INTO drivers (first_name, last_name) VALUES (%s, %s) "
                   "RETURNING id", ("Andre", "Lotterer"))
        novo = ce.fetchone()[0]
        escritor.commit()

        # a conexão do escritor enxerga o que ela própria escreveu
        assert resolve_driver_id(ce, "Andre", "LOTTERER") == (
            novo, DriverOutcome.BY_NORMALIZED_NAME)

        # uma conexão nova nasce sem índice e lê a tabela de novo
        terceiro = connect_to_sandbox(sandbox)
        try:
            assert resolve_driver_id(terceiro.cursor(), "Andre", "LOTTERER") == (
                novo, DriverOutcome.BY_NORMALIZED_NAME)
        finally:
            terceiro.close()
    finally:
        leitor.close()
        escritor.rollback()
        escritor.close()


def test_uma_nova_execucao_na_mesma_conexao_rele_a_tabela(sandbox):
    """
    O caso que o descarte por conexão sozinho não cobre: a mesma conexão
    servindo duas execuções. É por isso que os dois `run()` chamam
    `forget_driver_index` ao começar.
    """
    conn = connect_to_sandbox(sandbox)
    outra = connect_to_sandbox(sandbox)
    try:
        cur, co = conn.cursor(), outra.cursor()
        assert resolve_driver_id(cur, "Mike", "Conway")[1] is DriverOutcome.NOT_FOUND

        co.execute("INSERT INTO drivers (first_name, last_name) VALUES (%s, %s) "
                   "RETURNING id", ("Mike", "Conway"))
        criado = co.fetchone()[0]
        outra.commit()

        # sem o descarte, a conexão antiga insistiria no que dobrou antes
        assert resolve_driver_id(cur, "Mike", "Conway")[1] is DriverOutcome.NOT_FOUND
        forget_driver_index(cur)
        assert resolve_driver_id(cur, "Mike", "Conway") == (
            criado, DriverOutcome.EXACT_NAME)
    finally:
        conn.close()
        outra.rollback()
        outra.close()


def test_o_que_a_execucao_atual_escreve_fica_visivel(mundo):
    """`remember_driver` mantém a linha recém-criada ao alcance da mesma conexão."""
    did, desfecho = upsert_driver(mundo.cursor, fonte("Jose Maria", "Lopez"))
    assert desfecho is DriverOutcome.NOT_FOUND
    assert resolve_driver_id(mundo.cursor, "Jose Maria", "LOPEZ") == (
        did, DriverOutcome.BY_NORMALIZED_NAME)


def test_descartar_tudo_nao_quebra_a_proxima_consulta(mundo):
    did = mundo.criar("Brendon", "Hartley")
    forget_driver_index()
    assert resolve_driver_id(mundo.cursor, "Brendon", "HARTLEY") == (
        did, DriverOutcome.BY_NORMALIZED_NAME)

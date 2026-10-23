"""
The SQL behind the resolution, against a schema of this module's own.

What these add over test_row_identity.py is the part a pure test cannot
check: that the candidate queries load every row that could match. A query
that silently loses a candidate would make an ambiguous case look resolved.

Nothing here writes to `public`; see sandbox.py for the two gates.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

from tests.loader.sandbox import connect_to_sandbox  # noqa: E402

from row_identity import (  # noqa: E402
    CarOutcome,
    SessionOutcome,
    resolve_car_id,
    resolve_session_id,
)

SERIE = "ELMS"
TEMPORADA = "16_2016"
EVENTO = "10_Watkins Glen International"
URL = ("http://exemplo/Results/16_2016/10_Watkins%20Glen%20International/"
       "{champ}/{raw}/03_Results.CSV")


@pytest.fixture
def mundo(sandbox):
    """
    Um evento com sessoes, carros e resultados, criado e desfeito por teste.

    Devolve um helper com os ids e as funcoes de montagem, para que cada
    teste descreva o caso que lhe interessa em vez de repetir o cenario.
    """
    conn = connect_to_sandbox(sandbox)
    cur = conn.cursor()

    cur.execute("INSERT INTO series (key, name) VALUES (%s::series_name, %s) "
                "RETURNING id", (SERIE, "European Le Mans Series"))
    serie_id = cur.fetchone()[0]
    cur.execute("INSERT INTO seasons (series_id, raw_id, year, label) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (serie_id, TEMPORADA, 2016, "2016"))
    temporada_id = cur.fetchone()[0]
    cur.execute("INSERT INTO events (season_id, raw_id, name) "
                "VALUES (%s, %s, %s) RETURNING id",
                (temporada_id, EVENTO, "Watkins Glen"))
    evento_id = cur.fetchone()[0]

    class Mundo:
        event_id = evento_id

        def sessao(self, raw_id: str, nome: str, champ: str | None = None) -> int:
            return self.sessao_em(evento_id, raw_id, nome, champ)

        def sessao_em(self, event_id: int, raw_id: str, nome: str,
                      champ: str | None = None) -> int:
            url = None if champ is None else URL.format(
                champ=champ.replace(" ", "%20"), raw=raw_id.replace(" ", "%20"))
            cur.execute(
                "INSERT INTO sessions (event_id, raw_id, name, source_url) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (event_id, raw_id, nome, url))
            return cur.fetchone()[0]

        def outro_evento(self, raw_id: str) -> int:
            cur.execute("INSERT INTO events (season_id, raw_id, name) "
                        "VALUES (%s, %s, %s) RETURNING id",
                        (temporada_id, raw_id, raw_id))
            return cur.fetchone()[0]

        def equipe(self, nome: str) -> int:
            cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (nome,))
            return cur.fetchone()[0]

        def carro(self, numero: str, team_id: int, veiculo: str,
                  classe: str | None = None, grupo: str | None = None) -> int:
            cur.execute(
                "INSERT INTO cars (number, team_id, vehicle, car_class, car_group) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (numero, team_id, veiculo, classe, grupo))
            return cur.fetchone()[0]

        def resultado(self, session_id: int, car_id: int) -> None:
            cur.execute(
                "INSERT INTO results (session_id, car_id) VALUES (%s, %s)",
                (session_id, car_id))

        def resolve_sessao(self, nome: str, champ: str | None = None):
            return resolve_session_id(cur, SERIE, TEMPORADA, EVENTO, nome, champ)

        def resolve_carro(self, session_id: int, numero: str, **kwargs):
            return resolve_car_id(cur, session_id, numero, **kwargs)

    try:
        yield Mundo()
    finally:
        conn.rollback()
        conn.close()


# ── Sessoes ───────────────────────────────────────────────────

def test_sessao_inexistente(mundo):
    assert mundo.resolve_sessao("Nao Existe") == (None, SessionOutcome.NOT_FOUND)


def test_sessao_unica_resolve(mundo):
    sid = mundo.sessao("201606300845_Practice 1", "Practice 1")
    assert mundo.resolve_sessao("Practice 1") == (sid, SessionOutcome.SOLE_CANDIDATE)


def test_nomes_iguais_raw_id_diferentes_sao_carregados_todos(mundo):
    """
    Tres linhas distintas por (event_id, raw_id), um nome so. A consulta tem
    de ver as tres: se trouxesse uma, o caso pareceria resolvido.
    """
    mundo.sessao("201606300845_Practice 1", "Practice 1", "03_Mazda")
    mundo.sessao("201606300930_Practice 1", "Practice 1", "04_Porsche")
    mundo.sessao("201606301015_Practice 1", "Practice 1", "01_WeatherTech")
    sid, out = mundo.resolve_sessao("Practice 1")
    assert sid is None
    assert out is SessionOutcome.MULTIPLE_CANDIDATES


def test_o_campeonato_do_caminho_escolhe_a_sessao_certa(mundo):
    mundo.sessao("201606300845_Practice 1", "Practice 1", "03_Mazda")
    porsche = mundo.sessao("201606300930_Practice 1", "Practice 1", "04_Porsche")
    mundo.sessao("201606301015_Practice 1", "Practice 1", "01_WeatherTech")
    assert mundo.resolve_sessao("Practice 1", "04_Porsche") == (
        porsche, SessionOutcome.BY_CHAMPIONSHIP)


def test_o_nome_de_pasta_truncado_ainda_resolve(mundo):
    mundo.sessao("201606300845_Practice 1", "Practice 1", "03_Mazda Prototype Lites")
    wt = mundo.sessao("201606301015_Practice 1", "Practice 1",
                      "01_IMSA WeatherTech SportsCar Championship")
    sid, out = mundo.resolve_sessao("Practice 1",
                                    "01_IMSA WeatherTech SportsCar Championsh")
    assert (sid, out) == (wt, SessionOutcome.BY_CHAMPIONSHIP)


def test_sessao_unica_contradita_pelo_campeonato_recusa(mundo):
    """
    As 8 ocorrencias medidas no corpus: a unica sessao com esse nome pertence
    a outro campeonato, porque a sessao certa nunca foi criada no banco.
    """
    mundo.sessao("201701061340_Session 2", "Session 2", "02_Continental Tire")
    sid, out = mundo.resolve_sessao("Session 2",
                                    "01_IMSA WeatherTech SportsCar Championsh")
    assert sid is None
    assert out is SessionOutcome.CONTRADICTED_BY_CHAMPIONSHIP


def test_sessao_unica_com_campeonato_correto_resolve(mundo):
    esperada = mundo.sessao("201701061500_Session 2", "Session 2",
                            "01_IMSA WeatherTech SportsCar Championship")
    assert mundo.resolve_sessao("Session 2",
                                "01_IMSA WeatherTech SportsCar Championsh") == (
        esperada, SessionOutcome.BY_CHAMPIONSHIP)


def test_sessao_unica_sem_campeonato_no_caminho_resolve(mundo):
    esperada = mundo.sessao("201701061340_Session 2", "Session 2", "02_Continental Tire")
    assert mundo.resolve_sessao("Session 2") == (esperada, SessionOutcome.SOLE_CANDIDATE)


def test_sessao_unica_sem_source_url_nao_contradiz(mundo):
    """Quem nao declara campeonato nao discorda de campeonato nenhum."""
    esperada = mundo.sessao("201701061340_Session 2", "Session 2", None)
    assert mundo.resolve_sessao("Session 2", "01_IMSA WeatherTech") == (
        esperada, SessionOutcome.SOLE_CANDIDATE)


def test_sessao_sem_source_url_nao_pode_ser_desempatada(mundo):
    mundo.sessao("201606300845_Practice 1", "Practice 1", None)
    mundo.sessao("201606300930_Practice 1", "Practice 1", None)
    sid, out = mundo.resolve_sessao("Practice 1", "04_Porsche")
    assert sid is None
    assert out is SessionOutcome.MULTIPLE_CANDIDATES


def test_sessoes_de_outro_evento_nao_entram(mundo):
    """A chave inclui temporada e evento: um nome igual noutro evento nao conta."""
    sid = mundo.sessao("201606300845_Practice 1", "Practice 1")
    outro = mundo.outro_evento("11_Road America")
    mundo.sessao_em(outro, "201607100845_Practice 1", "Practice 1")
    assert mundo.resolve_sessao("Practice 1") == (sid, SessionOutcome.SOLE_CANDIDATE)


# ── Carros ────────────────────────────────────────────────────

def test_sessao_sem_classificacao_nenhuma(mundo):
    """Ausencia de classificacao nao e ausencia de carro."""
    sid = mundo.sessao("201606301225_Race", "Race")
    cid, out = mundo.resolve_carro(sid, "4")
    assert cid is None
    assert out is CarOutcome.NO_CLASSIFICATION


def test_carro_ausente_de_uma_classificacao_existente(mundo):
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Nielsen Racing")
    mundo.resultado(sid, mundo.carro("4", eq, "Oreca 07-Gibson", "LMP2"))
    cid, out = mundo.resolve_carro(sid, "99")
    assert cid is None
    assert out is CarOutcome.NOT_IN_CLASSIFICATION


def test_varios_car_id_da_mesma_tupla_resolvem_no_menor(mundo):
    """O historico tem mediana de 3 linhas por entrada; sao o mesmo carro."""
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Nielsen Racing")
    ids = [mundo.carro("4", eq, "Oreca 07-Gibson", "LMP2") for _ in range(3)]
    for cid in ids:
        mundo.resultado(sid, cid)
    resolvido, out = mundo.resolve_carro(sid, "4")
    assert (resolvido, out) == (min(ids), CarOutcome.SOLE_IDENTITY)


def test_a_resolucao_do_carro_e_idempotente(mundo):
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Nielsen Racing")
    for _ in range(3):
        mundo.resultado(sid, mundo.carro("4", eq, "Oreca 07-Gibson", "LMP2"))
    primeiro = mundo.resolve_carro(sid, "4")
    for _ in range(4):
        assert mundo.resolve_carro(sid, "4") == primeiro


def test_o_mesmo_numero_em_sessoes_diferentes_sao_carros_diferentes(mundo):
    fp = mundo.sessao("201606300845_Practice 1", "Practice 1")
    race = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Nielsen Racing")
    c1 = mundo.carro("4", eq, "Oreca 07-Gibson", "LMP2")
    c2 = mundo.carro("4", eq, "Oreca 07-Gibson", "LMP2")
    mundo.resultado(fp, c1)
    mundo.resultado(race, c2)
    assert mundo.resolve_carro(fp, "4")[0] == c1
    assert mundo.resolve_carro(race, "4")[0] == c2


def test_duas_identidades_sob_o_mesmo_numero_recusam(mundo):
    """Classe C do diagnostico: 1.235 pares em 266 sessoes."""
    sid = mundo.sessao("201606301225_Race", "Race")
    gpa = mundo.equipe("GPA RACING")
    dc = mundo.equipe("DC RACING")
    mundo.resultado(sid, mundo.carro("115", gpa, "Ligier JS P320", "LMP3"))
    mundo.resultado(sid, mundo.carro("115", dc, "Duqueine M30", "LMP3"))
    cid, out = mundo.resolve_carro(sid, "115")
    assert cid is None
    assert out is CarOutcome.MULTIPLE_IDENTITIES


def test_a_equipe_declarada_na_fonte_de_voltas_desempata(mundo):
    sid = mundo.sessao("201606301225_Race", "Race")
    gpa = mundo.equipe("GPA RACING")
    dc = mundo.equipe("DC RACING")
    mundo.resultado(sid, mundo.carro("115", gpa, "Ligier JS P320", "LMP3"))
    esperado = mundo.carro("115", dc, "Duqueine M30", "LMP3")
    mundo.resultado(sid, esperado)
    cid, out = mundo.resolve_carro(sid, "115", team_name="DC RACING")
    assert (cid, out) == (esperado, CarOutcome.BY_SOURCE_ATTRIBUTES)


def test_sem_a_equipe_o_mesmo_caso_continua_recusado(mundo):
    """11% dos arquivos de voltas nao trazem TEAM."""
    sid = mundo.sessao("201606301225_Race", "Race")
    mundo.resultado(sid, mundo.carro("115", mundo.equipe("GPA RACING"),
                                     "Ligier JS P320", "LMP3"))
    mundo.resultado(sid, mundo.carro("115", mundo.equipe("DC RACING"),
                                     "Duqueine M30", "LMP3"))
    assert mundo.resolve_carro(sid, "115", team_name="")[1] is \
        CarOutcome.MULTIPLE_IDENTITIES


def test_numero_literal_distingue_carros(mundo):
    """`#03` nao e `#3`: ADR 0001, P1."""
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Equipe")
    tres = mundo.carro("3", eq, "Oreca 07-Gibson", "LMP2")
    zero_tres = mundo.carro("03", eq, "Oreca 07-Gibson", "LMP2")
    mundo.resultado(sid, tres)
    mundo.resultado(sid, zero_tres)
    assert mundo.resolve_carro(sid, "3")[0] == tres
    assert mundo.resolve_carro(sid, "03")[0] == zero_tres


def test_equipe_com_diferenca_so_de_caixa_resolve(mundo):
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Idec Sport")
    esperado = mundo.carro("28", eq, "Oreca 07-Gibson", "LMP2")
    mundo.resultado(sid, esperado)
    assert mundo.resolve_carro(sid, "28", team_name="IDEC Sport") == (
        esperado, CarOutcome.SOLE_IDENTITY)


def test_equipe_com_diferenca_so_de_pontuacao_resolve(mundo):
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Dempsey - Proton Racing")
    esperado = mundo.carro("77", eq, "Porsche 911 RSR", "LMGTE")
    mundo.resultado(sid, esperado)
    assert mundo.resolve_carro(sid, "77", team_name="DEMPSEY-PROTON RACING") == (
        esperado, CarOutcome.SOLE_IDENTITY)


def test_equipe_conhecida_e_incompativel_recusa(mundo):
    """`Corvette Racing` contra `Corvette Racing - GM`, um dos 12 medidos."""
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Corvette Racing - GM")
    mundo.resultado(sid, mundo.carro("63", eq, "Corvette C8.R", "LMGTE"))
    cid, out = mundo.resolve_carro(sid, "63", team_name="Corvette Racing")
    assert cid is None
    assert out is CarOutcome.CONTRADICTED_BY_SOURCE


def test_classe_conhecida_e_incompativel_recusa(mundo):
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Equipe")
    mundo.resultado(sid, mundo.carro("68", eq, "Veiculo", "G"))
    cid, out = mundo.resolve_carro(sid, "68", car_class="J")
    assert cid is None
    assert out is CarOutcome.CONTRADICTED_BY_SOURCE


def test_classe_nula_no_banco_e_ausencia_e_nao_conflito(mundo):
    """Seis dos oito casos de CLASS medidos sao este."""
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Equipe")
    esperado = mundo.carro("32", eq, "Veiculo", None)
    mundo.resultado(sid, esperado)
    assert mundo.resolve_carro(sid, "32", car_class="S") == (
        esperado, CarOutcome.SOLE_IDENTITY)


def test_atributo_ausente_na_fonte_nao_impede(mundo):
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Equipe")
    esperado = mundo.carro("32", eq, "Veiculo", "LMP2")
    mundo.resultado(sid, esperado)
    assert mundo.resolve_carro(sid, "32", team_name="", car_class=None) == (
        esperado, CarOutcome.SOLE_IDENTITY)


def test_duas_identidades_que_so_diferem_na_grafia_da_equipe_recusam(mundo):
    """Os 1.058 grupos que a normalizacao deixa de separar."""
    sid = mundo.sessao("201606301225_Race", "Race")
    a = mundo.equipe("Dempsey - Proton Racing")
    b = mundo.equipe("DEMPSEY-PROTON RACING")
    mundo.resultado(sid, mundo.carro("77", a, "Porsche 911 RSR", "LMGTE"))
    mundo.resultado(sid, mundo.carro("77", b, "Porsche 911 GT3", "LMGTE"))
    cid, out = mundo.resolve_carro(sid, "77", team_name="DEMPSEY-PROTON RACING")
    assert cid is None
    assert out is CarOutcome.MULTIPLE_IDENTITIES


def test_equipes_de_fato_distintas_continuam_separaveis(mundo):
    sid = mundo.sessao("201606301225_Race", "Race")
    gpa = mundo.equipe("GPA RACING")
    dc = mundo.equipe("DC RACING")
    mundo.resultado(sid, mundo.carro("115", gpa, "Ligier JS P320", "LMP3"))
    esperado = mundo.carro("115", dc, "Duqueine M30", "LMP3")
    mundo.resultado(sid, esperado)
    assert mundo.resolve_carro(sid, "115", team_name="dc racing") == (
        esperado, CarOutcome.BY_SOURCE_ATTRIBUTES)


def test_um_carro_sem_resultado_na_sessao_nao_e_candidato(mundo):
    """
    A candidatura passa por `results`: um carro que existe na tabela mas nao
    foi classificado nesta sessao nao pode receber as voltas dela.
    """
    sid = mundo.sessao("201606301225_Race", "Race")
    eq = mundo.equipe("Equipe")
    classificado = mundo.carro("4", eq, "Oreca 07-Gibson", "LMP2")
    mundo.carro("4", eq, "Ligier JS P320", "LMP3")   # existe, sem resultado aqui
    mundo.resultado(sid, classificado)
    cid, out = mundo.resolve_carro(sid, "4")
    assert (cid, out) == (classificado, CarOutcome.SOLE_IDENTITY)

"""
The decisions behind resolving a session row and a car row.

Pure: these need no database. What the SQL loads is covered by
test_identity_resolution_db.py, which needs a disposable one.

The rule under test is that a multiplicity is an outcome. Every case here
either names the one row that can match, or refuses — nothing falls through
to the first row a query happened to return.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

from row_identity import (  # noqa: E402
    CarCandidate,
    CarOutcome,
    SessionCandidate,
    SessionOutcome,
    championship_of,
    choose_car,
    choose_session,
    in_championship,
)

# Watkins Glen 2016 holds five sessions named "Practice 1", one per
# championship. The raw/ folder for IMSA keeps the championship level, so a
# file under 03_IMSA Mazda Prototype Lites belongs to exactly one of them.
URL = ("http://imsa.alkamelsystems.com/Results/16_2016/"
       "10_Watkins%20Glen%20International/{champ}/{raw}/03_Results.CSV")


def sessao(sid: int, raw: str, champ: str | None) -> SessionCandidate:
    url = None if champ is None else URL.format(champ=champ.replace(" ", "%20"), raw=raw)
    return SessionCandidate(sid, raw, url)


MAZDA = sessao(2695, "201606300845_Practice 1", "03_IMSA Mazda Prototype Lites")
PORSCHE = sessao(2696, "201606300930_Practice 1", "04_Porsche GT3 Cup Challenge")
WEATHERTECH = sessao(2697, "201606301015_Practice 1",
                     "01_IMSA WeatherTech SportsCar Championship")


# ── Lendo o campeonato da URL do catalogo ─────────────────────

def test_o_campeonato_sai_da_url_sem_codificacao_percentual():
    assert championship_of(MAZDA.source_url) == "03_IMSA Mazda Prototype Lites"


def test_url_ausente_nao_tem_campeonato():
    assert championship_of(None) is None
    assert championship_of("") is None


def test_url_fora_do_formato_nao_inventa_campeonato():
    assert championship_of("http://exemplo/qualquer/coisa.CSV") is None


def test_a_pasta_truncada_pelo_sistema_de_arquivos_ainda_casa():
    """
    O corpus guarda "01_IMSA WeatherTech SportsCar Championsh": o nome foi
    cortado ao ser gravado. Exigir igualdade recusaria a sessao certa.
    """
    assert in_championship(WEATHERTECH.source_url,
                           "01_IMSA WeatherTech SportsCar Championsh") is True


def test_o_campeonato_errado_nao_casa():
    assert in_championship(MAZDA.source_url, "04_Porsche GT3 Cup Challenge") is False


def test_sem_url_nao_casa_campeonato_nenhum():
    assert in_championship(None, "03_IMSA Mazda Prototype Lites") is False


# ── Escolha da sessao ─────────────────────────────────────────

def test_sem_candidata_nao_e_ambiguidade():
    assert choose_session([]) == (None, SessionOutcome.NOT_FOUND)


def test_candidata_unica_resolve():
    sid, out = choose_session([MAZDA])
    assert (sid, out) == (2695, SessionOutcome.SOLE_CANDIDATE)


def test_nomes_iguais_com_raw_id_diferente_sem_campeonato_recusam():
    """
    O caso das series cujo raw/ nao guarda o nivel de campeonato: duas
    sessoes, mesma chave, e nada no caminho diz qual.
    """
    sid, out = choose_session([MAZDA, PORSCHE, WEATHERTECH])
    assert sid is None
    assert out is SessionOutcome.MULTIPLE_CANDIDATES
    assert out.resolved is False


def test_o_campeonato_do_caminho_desempata():
    sid, out = choose_session([MAZDA, PORSCHE, WEATHERTECH],
                              "04_Porsche GT3 Cup Challenge")
    assert (sid, out) == (2696, SessionOutcome.BY_CHAMPIONSHIP)
    assert out.resolved is True


def test_campeonato_que_nao_casa_nenhuma_candidata_recusa():
    sid, out = choose_session([MAZDA, PORSCHE], "09_Campeonato Inexistente")
    assert sid is None
    assert out is SessionOutcome.MULTIPLE_CANDIDATES


def test_campeonato_que_casa_duas_candidatas_recusa():
    """Duas sessoes do mesmo campeonato continuam indistinguiveis."""
    outra = sessao(2698, "201606301500_Practice 1", "03_IMSA Mazda Prototype Lites")
    sid, out = choose_session([MAZDA, outra], "03_IMSA Mazda Prototype Lites")
    assert sid is None
    assert out is SessionOutcome.MULTIPLE_CANDIDATES


def test_o_campeonato_nao_e_usado_quando_a_candidata_ja_e_unica():
    """Uma candidata so resolve mesmo que o campeonato nao bata."""
    sid, out = choose_session([MAZDA], "99_Outro")
    assert (sid, out) == (2695, SessionOutcome.SOLE_CANDIDATE)


def test_a_escolha_da_sessao_e_idempotente():
    cands = [MAZDA, PORSCHE, WEATHERTECH]
    primeiro = choose_session(cands, "03_IMSA Mazda Prototype Lites")
    for _ in range(5):
        assert choose_session(cands, "03_IMSA Mazda Prototype Lites") == primeiro


def test_a_ordem_das_candidatas_nao_muda_a_decisao():
    direto = choose_session([MAZDA, PORSCHE, WEATHERTECH], "04_Porsche GT3 Cup Challenge")
    invertido = choose_session([WEATHERTECH, PORSCHE, MAZDA], "04_Porsche GT3 Cup Challenge")
    assert direto == invertido


# ── Escolha do carro ──────────────────────────────────────────

def carro(cid: int, ident: str, equipe=None, classe=None, grupo=None) -> CarCandidate:
    return CarCandidate(cid, ident, equipe, classe, grupo)


# A mesma inscricao gravada tres vezes por tres execucoes do loader: uma
# tupla, tres ids. E o caso de 93.633 dos 94.868 pares (sessao, numero).
MESMA_TUPLA = [
    carro(900, "(4,7,Oreca 07-Gibson,LMP2,,M,,)", "Nielsen Racing", "LMP2", None),
    carro(410, "(4,7,Oreca 07-Gibson,LMP2,,M,,)", "Nielsen Racing", "LMP2", None),
    carro(1502, "(4,7,Oreca 07-Gibson,LMP2,,M,,)", "Nielsen Racing", "LMP2", None),
]

# Duas inscricoes competitivas distintas sob o mesmo numero na mesma sessao.
DUAS_IDENTIDADES = [
    carro(300, "(115,20,Ligier JS P320,LMP3,,M,,)", "GPA RACING", "LMP3", None),
    carro(301, "(115,33,Duqueine M30,LMP3,,M,,)", "DC RACING", "LMP3", None),
]


def test_sem_candidato_o_carro_nao_esta_na_classificacao():
    assert choose_car([]) == (None, CarOutcome.NOT_IN_CLASSIFICATION)


def test_varios_car_id_de_uma_tupla_so_resolvem():
    """
    Tres linhas, a mesma identidade de oito atributos: sao o mesmo carro
    pela regra do proprio loader, duplicado por execucoes anteriores.
    """
    cid, out = choose_car(MESMA_TUPLA)
    assert out is CarOutcome.SOLE_IDENTITY
    assert cid == 410


def test_o_representante_da_tupla_e_estavel():
    """Idempotencia: a mesma tupla devolve sempre o mesmo id."""
    import random
    escolhas = set()
    for _ in range(10):
        baralhado = MESMA_TUPLA[:]
        random.shuffle(baralhado)
        escolhas.add(choose_car(baralhado)[0])
    assert escolhas == {410}


def test_identidades_distintas_sem_atributos_recusam():
    """O numero sozinho nao identifica o carro."""
    cid, out = choose_car(DUAS_IDENTIDADES)
    assert cid is None
    assert out is CarOutcome.MULTIPLE_IDENTITIES
    assert out.resolved is False


def test_a_equipe_da_fonte_de_voltas_desempata():
    cid, out = choose_car(DUAS_IDENTIDADES, team_name="DC RACING")
    assert (cid, out) == (301, CarOutcome.BY_SOURCE_ATTRIBUTES)


def test_a_classe_desempata_quando_a_equipe_coincide():
    cands = [
        carro(10, "ident-a", "Equipe X", "LMP3", None),
        carro(11, "ident-b", "Equipe X", "LMGTE", None),
    ]
    cid, out = choose_car(cands, team_name="Equipe X", car_class="LMGTE")
    assert (cid, out) == (11, CarOutcome.BY_SOURCE_ATTRIBUTES)


def test_atributo_que_nao_casa_nenhum_candidato_recusa():
    """Uma terceira grafia de equipe nao escolhe um carro ao acaso."""
    cid, out = choose_car(DUAS_IDENTIDADES, team_name="Outra Equipe")
    assert cid is None
    assert out is CarOutcome.MULTIPLE_IDENTITIES


def test_atributo_que_ainda_deixa_duas_identidades_recusa():
    cands = [
        carro(10, "ident-a", "Equipe X", "LMP3", None),
        carro(11, "ident-b", "Equipe X", "LMP3", None),
    ]
    cid, out = choose_car(cands, team_name="Equipe X", car_class="LMP3")
    assert cid is None
    assert out is CarOutcome.MULTIPLE_IDENTITIES


@pytest.mark.parametrize("vazio", ["", "   ", None])
def test_atributo_em_branco_nao_e_atributo(vazio):
    """
    11% dos arquivos de voltas nao trazem TEAM. Uma string vazia nao pode
    virar um filtro que nao casa com nada — nem um que casa com tudo.
    """
    cid, out = choose_car(DUAS_IDENTIDADES, team_name=vazio)
    assert cid is None
    assert out is CarOutcome.MULTIPLE_IDENTITIES


def test_atributos_em_branco_nao_impedem_a_tupla_unica():
    cid, out = choose_car(MESMA_TUPLA, team_name="", car_class=None)
    assert (cid, out) == (410, CarOutcome.SOLE_IDENTITY)


def test_atributos_que_nao_batem_nao_derrubam_a_tupla_unica():
    """
    Havendo uma identidade so, nao ha o que desempatar: o carro e aquele,
    e uma grafia divergente de equipe nao o transforma noutro.
    """
    cid, out = choose_car(MESMA_TUPLA, team_name="NIELSEN RACING")
    assert (cid, out) == (410, CarOutcome.SOLE_IDENTITY)


def test_o_desempate_exige_todos_os_atributos_declarados():
    cands = [
        carro(10, "ident-a", "Equipe X", "LMP3", "Pro"),
        carro(11, "ident-b", "Equipe X", "LMP3", "Am"),
    ]
    assert choose_car(cands, team_name="Equipe X", car_group="Am")[0] == 11
    # classe errada: nenhum candidato satisfaz os tres ao mesmo tempo
    assert choose_car(cands, team_name="Equipe X", car_class="LMGTE",
                      car_group="Am")[1] is CarOutcome.MULTIPLE_IDENTITIES


def test_os_desfechos_resolvidos_sao_so_dois_de_cada_lado():
    assert [o for o in SessionOutcome if o.resolved] == [
        SessionOutcome.SOLE_CANDIDATE, SessionOutcome.BY_CHAMPIONSHIP]
    assert [o for o in CarOutcome if o.resolved] == [
        CarOutcome.SOLE_IDENTITY, CarOutcome.BY_SOURCE_ATTRIBUTES]


def test_nao_ha_limit_1_sem_order_by_na_resolucao():
    """
    A regressao que este modulo existe para impedir. `LIMIT 1` sem `ORDER BY`
    nao e nao-determinismo a ser ordenado: e um empate que ninguem viu.
    """
    import row_identity
    for sql in (row_identity._SESSION_CANDIDATES, row_identity._CAR_CANDIDATES):
        assert "LIMIT" not in sql.upper()
        assert "ORDER BY" in sql.upper()

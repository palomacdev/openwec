"""
Which driver row a source name refers to, and when nothing may be decided.

Pure: these need no database. The SQL and the write path are covered by
test_driver_resolution_db.py.

The table this guards: `drivers` is unique on the raw (first_name,
last_name), so the same person stored under two spellings is two rows and
the constraint cannot see it. Measured on the corpus: 2.845 rows for 1.705
normalised names, 1.079 names held by more than one row.
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

from row_identity import (  # noqa: E402
    DriverCandidate,
    DriverOutcome,
    choose_driver,
    full_name_key,
)


def piloto(did: int, first: str | None, last: str | None) -> DriverCandidate:
    return DriverCandidate(did, first, last)


# Pares reais do corpus.
PATTERSON_A = piloto(32, "Mark", "Patterson")
PATTERSON_B = piloto(329356, "Mark", "PATTERSON")
GARDE_PARTIDO = piloto(20, "Giedo VAN DER", "GARDE")
GARDE_INTEIRO = piloto(19, "Giedo", "Van Der Garde")
NIC = piloto(30285, "Nic", "Minassian")
NICOLAS = piloto(419822, "Nicolas", "Minassian")


# ── A chave de comparação ─────────────────────────────────────

def test_a_divisao_entre_nome_e_sobrenome_nao_altera_a_chave():
    """
    `split_wec_driver_name` corta no ultimo espaco, entao a fronteira e
    artefato do parser. Comparar os dois campos separadamente chamaria um
    piloto de duas pessoas.
    """
    assert full_name_key("Giedo VAN DER", "GARDE") == full_name_key("Giedo", "Van Der Garde")
    assert full_name_key("Antonio Felix", "Da Costa") == full_name_key("Antonio FELIX DA", "COSTA")


def test_caixa_e_pontuacao_saem_da_chave():
    assert full_name_key("Mark", "Patterson") == full_name_key("Mark", "PATTERSON")
    assert full_name_key("Jean-Eric", "Vergne") == full_name_key("Jean Eric", "VERGNE")


def test_a_chave_nao_dobra_acentos():
    """Um acento e uma letra. Conservador, como em `comparable`."""
    assert full_name_key("Jose", "Maria Lopez") != full_name_key("José", "María López")


def test_a_chave_nao_resolve_abreviacao():
    """`Nic` nao vira `Nicolas` em lugar nenhum deste modulo."""
    assert full_name_key("Nic", "Minassian") != full_name_key("Nicolas", "Minassian")


def test_nomes_de_pessoas_diferentes_nao_colidem():
    assert full_name_key("Mark", "Patterson") != full_name_key("Marc", "Patterson")


# ── A escolha ─────────────────────────────────────────────────

def test_piloto_inexistente():
    assert choose_driver([], "Alguem", "Desconhecido") == (None, DriverOutcome.NOT_FOUND)


def test_correspondencia_exata_unica():
    did, out = choose_driver([PATTERSON_A], "Mark", "Patterson")
    assert (did, out) == (32, DriverOutcome.EXACT_NAME)
    assert out.resolved is True


def test_a_grafia_exata_vence_a_normalizada():
    """
    Garantia de nao regressao: havendo a linha exata, e ela que responde —
    exatamente como antes desta mudanca.
    """
    did, out = choose_driver([PATTERSON_A, PATTERSON_B], "Mark", "PATTERSON")
    assert (did, out) == (329356, DriverOutcome.EXACT_NAME)
    did, out = choose_driver([PATTERSON_A, PATTERSON_B], "Mark", "Patterson")
    assert (did, out) == (32, DriverOutcome.EXACT_NAME)


def test_diferenca_so_de_caixa_resolve():
    did, out = choose_driver([PATTERSON_A], "Mark", "PATTERSON")
    assert (did, out) == (32, DriverOutcome.BY_NORMALIZED_NAME)


def test_diferenca_so_de_pontuacao_resolve():
    vergne = piloto(700, "Jean-Eric", "Vergne")
    did, out = choose_driver([vergne], "Jean Eric", "VERGNE")
    assert (did, out) == (700, DriverOutcome.BY_NORMALIZED_NAME)


def test_divisao_diferente_entre_nome_e_sobrenome_resolve():
    did, out = choose_driver([GARDE_INTEIRO], "Giedo VAN DER", "GARDE")
    assert (did, out) == (19, DriverOutcome.BY_NORMALIZED_NAME)


def test_varios_registros_com_o_mesmo_nome_normalizado_recusam():
    """
    1.079 nomes do acervo estao nesta situacao. Escolher entre eles seria
    escolher por id, que nao demonstra nada.
    """
    did, out = choose_driver([PATTERSON_A, PATTERSON_B], "Mark", "Patterson jr")
    assert did is None
    assert out is DriverOutcome.NOT_FOUND     # nome diferente, nao e o caso
    did, out = choose_driver([PATTERSON_A, PATTERSON_B], "MARK", "patterson")
    assert did is None
    assert out is DriverOutcome.MULTIPLE_CANDIDATES
    assert out.resolved is False


def test_possiveis_homonimos_recusam():
    """
    Dois registros com o mesmo nome normalizado podem ser duas pessoas. Nada
    na tabela distingue: no acervo, pais e `imsa_driver_id` divergem entre
    linhas da MESMA pessoa, entao nenhum deles serve de desempate.
    """
    um = piloto(10, "Carlos", "Silva")
    outro = piloto(11, "Carlos", "SILVA")
    did, out = choose_driver([um, outro], "carlos", "silva")
    assert did is None
    assert out is DriverOutcome.MULTIPLE_CANDIDATES


def test_abreviacao_nao_e_unificada():
    """`Nic Minassian` e `Nicolas Minassian`: os 60 pares da PR #44."""
    did, out = choose_driver([NICOLAS], "Nic", "Minassian")
    assert did is None
    assert out is DriverOutcome.NOT_FOUND
    did, out = choose_driver([NIC], "Nicolas", "Minassian")
    assert did is None
    assert out is DriverOutcome.NOT_FOUND


def test_apelido_nao_vira_nome_completo_nem_com_os_dois_presentes():
    did, out = choose_driver([NIC, NICOLAS], "Nic", "Minassian")
    assert (did, out) == (30285, DriverOutcome.EXACT_NAME)
    did, out = choose_driver([NIC, NICOLAS], "Nicolas", "Minassian")
    assert (did, out) == (419822, DriverOutcome.EXACT_NAME)


@pytest.mark.parametrize("first,last", [
    ("", ""), (None, None), ("   ", ""), (None, "   "),
])
def test_sem_nome_nenhum(first, last):
    assert choose_driver([PATTERSON_A], first, last) == (None, DriverOutcome.NO_NAME)


def test_nome_incompleto_so_o_sobrenome():
    """
    `split_wec_driver_name` devolve ("", nome) quando a fonte traz uma
    palavra so. Isso e um nome, e resolve.
    """
    so_sobrenome = piloto(900, "", "Senna")
    assert choose_driver([so_sobrenome], "", "Senna") == (900, DriverOutcome.EXACT_NAME)
    assert choose_driver([so_sobrenome], "", "SENNA") == (900, DriverOutcome.BY_NORMALIZED_NAME)


def test_nome_incompleto_nao_casa_com_o_completo():
    """Sobrenome sozinho nao e o mesmo que nome e sobrenome."""
    did, out = choose_driver([PATTERSON_A], "", "Patterson")
    assert did is None
    assert out is DriverOutcome.NOT_FOUND


def test_a_resolucao_e_idempotente_e_independe_da_ordem():
    cands = [PATTERSON_A, PATTERSON_B]
    primeiro = choose_driver(cands, "Mark", "PATTERSON")
    for _ in range(5):
        assert choose_driver(cands, "Mark", "PATTERSON") == primeiro
        assert choose_driver(list(reversed(cands)), "Mark", "PATTERSON") == primeiro


def test_os_desfechos_resolvidos_sao_dois():
    assert [o for o in DriverOutcome if o.resolved] == [
        DriverOutcome.EXACT_NAME, DriverOutcome.BY_NORMALIZED_NAME]


def test_ausencia_ambiguidade_e_falta_de_nome_sao_tres_fatos():
    for o in (DriverOutcome.NOT_FOUND, DriverOutcome.MULTIPLE_CANDIDATES,
              DriverOutcome.NO_NAME):
        assert o.resolved is False
    assert len({DriverOutcome.NOT_FOUND, DriverOutcome.MULTIPLE_CANDIDATES,
                DriverOutcome.NO_NAME}) == 3


# ── Composicao Unicode ────────────────────────────────────────

def test_a_chave_nao_depende_da_composicao_do_texto():
    """
    `full_name_key` dobra por `comparable`, que agora poe o texto em NFC
    antes de filtrar. Sem isso, a forma decomposta perdia os acentos no
    `isalnum()` e o mesmo nome rendia duas chaves.
    """
    composto = unicodedata.normalize("NFC", "Jérôme")
    decomposto = unicodedata.normalize("NFD", "Jérôme")
    assert composto != decomposto
    assert full_name_key(composto, "De Sadeleer") == \
           full_name_key(decomposto, "De Sadeleer")


def test_o_acento_continua_distinguindo_nomes():
    """A correcao e de equivalencia canonica, nao de remocao de acento."""
    assert full_name_key("Jérôme", "De Sadeleer") != \
           full_name_key("Jerome", "De Sadeleer")


@pytest.mark.parametrize("primeiro,ultimo", [
    ("Antonio Felix", "Da Costa"),
    ("Antonio FELIX DA", "COSTA"),
    ("Antonio", "Felix Da Costa"),
])
def test_segmentacao_diferente_com_as_duas_composicoes(primeiro, ultimo):
    """
    O corte entre os campos e artefato do parser, e a composicao e artefato
    da codificacao. Nenhum dos dois pode criar um segundo piloto.
    """
    def nfd(s):
        return unicodedata.normalize("NFD", s)
    esperado = full_name_key("António Felix", "Da Costa")
    acentuado_primeiro = primeiro.replace("Antonio", "António")
    assert full_name_key(acentuado_primeiro, ultimo) == esperado
    assert full_name_key(nfd(acentuado_primeiro), nfd(ultimo)) == esperado


def test_marca_combinante_na_emenda_entre_os_campos():
    """
    `full_name_key` concatena antes de dobrar, entao uma marca combinante
    no inicio do sobrenome recai sobre a ultima letra do nome. NFC dentro de
    `comparable` acontece depois da concatenacao, e por isso compoe a seam
    em vez de descartar a marca.
    """
    # 'Jose' + combining acute => 'José'
    na_emenda = full_name_key("Jose", "́ Lopez")
    assert na_emenda == full_name_key("José", "Lopez")
    assert na_emenda != full_name_key("Jose", "Lopez")


def test_a_chave_e_idempotente_sob_renormalizacao():
    for primeiro, ultimo in (("Jérôme", "De Sadeleer"),
                             ("Marco", "Sørensen"),
                             ("Salih", "Yoluç")):
        chave = full_name_key(primeiro, ultimo)
        assert full_name_key(chave, "") == chave
        assert unicodedata.normalize("NFC", chave) == chave


def test_as_letras_que_devem_continuar_distintas():
    assert full_name_key("Marco", "Sørensen") != full_name_key("Marco", "Sorensen")
    assert full_name_key("Salih", "Yoluç") != full_name_key("Salih", "Yoluc")
    assert full_name_key("Sven", "Müller") != full_name_key("Sven", "Muller")

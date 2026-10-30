"""
Sufixos de nome ficam com o sobrenome, e o nome publicado sobrevive.

O ensaio da sessao 1007 criou `Horst FELBERMAYR / Sr.` porque o separador
cortava no ultimo espaco e o honorifico virava o sobrenome. A unica
ambiguidade da sessao 1300 e `Horst FELBERMAYR Jr`, da mesma familia e do
mesmo artefato.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "database" / "loader"))

from load_classification import split_wec_driver_name   # noqa: E402
from row_identity import full_name_key                  # noqa: E402


# ── os dois casos que motivaram a correcao ───────────────────

def test_horst_felbermayr_sr():
    assert split_wec_driver_name("Horst FELBERMAYR Sr.") == ("Horst", "FELBERMAYR Sr.")


def test_horst_felbermayr_jr():
    assert split_wec_driver_name("Horst FELBERMAYR Jr") == ("Horst", "FELBERMAYR Jr")


def test_giorgio_roda_jr_como_a_fonte_publica():
    """A sessao 1243 publica `Giorgio RODA JR`, em caixa alta."""
    assert split_wec_driver_name("Giorgio RODA JR") == ("Giorgio", "RODA JR")


# ── os quatro sufixos ────────────────────────────────────────

@pytest.mark.parametrize("sufixo", ["Sr.", "Sr", "sr.", "Jr.", "Jr", "JR",
                                    "II", "III", "IV"])
def test_sufixo_acompanha_o_sobrenome(sufixo):
    primeiro, ultimo = split_wec_driver_name(f"Mario ANDRETTI {sufixo}")
    assert primeiro == "Mario"
    assert ultimo == f"ANDRETTI {sufixo}"


def test_dois_sufixos_seguidos():
    assert split_wec_driver_name("Mario ANDRETTI Jr. III") == ("Mario", "ANDRETTI Jr. III")


# ── nomes sem sufixo: nada muda ──────────────────────────────

@pytest.mark.parametrize("nome,esperado", [
    ("Antonio FUOCO", ("Antonio", "FUOCO")),
    ("Nicklas NIELSEN", ("Nicklas", "NIELSEN")),
    ("Gianluca RODA", ("Gianluca", "RODA")),
    ("MADONNA", ("", "MADONNA")),
    ("", ("", "")),
    ("   ", ("", "")),
])
def test_sem_sufixo_o_corte_e_o_de_sempre(nome, esperado):
    assert split_wec_driver_name(nome) == esperado


def test_o_corte_herdado_nao_foi_mexido():
    """
    "Giedo VAN DER GARDE" continua cortando onde sempre cortou. Mover esse
    limite faria todo nome gravado parar de casar com a fonte que o escreveu.
    """
    assert split_wec_driver_name("Giedo VAN DER GARDE") == ("Giedo VAN DER", "GARDE")


# ── nomes compostos ──────────────────────────────────────────

@pytest.mark.parametrize("nome,esperado", [
    ("Jean-Marc BACHELIER", ("Jean-Marc", "BACHELIER")),
    ("Jose Maria LOPEZ", ("Jose Maria", "LOPEZ")),
    ("Juan Pablo MONTOYA Jr.", ("Juan Pablo", "MONTOYA Jr.")),
    ("Jean-Eric VERGNE", ("Jean-Eric", "VERGNE")),
])
def test_nomes_compostos(nome, esperado):
    assert split_wec_driver_name(nome) == esperado


# ── acentos ──────────────────────────────────────────────────

@pytest.mark.parametrize("nome,esperado", [
    ("Joël CAMATHIAS", ("Joël", "CAMATHIAS")),
    ("Lorenzo CASÉ", ("Lorenzo", "CASÉ")),
    ("Aurélien PANIS", ("Aurélien", "PANIS")),
    ("Moritz MÜLLER-CREPON", ("Moritz", "MÜLLER-CREPON")),
    ("Niclas JÖNSSON Jr.", ("Niclas", "JÖNSSON Jr.")),
])
def test_acentos_preservados(nome, esperado):
    assert split_wec_driver_name(nome) == esperado


# ── o nome publicado sobrevive ao corte ──────────────────────

@pytest.mark.parametrize("nome", [
    "Horst FELBERMAYR Sr.", "Horst FELBERMAYR Jr", "Giorgio RODA JR",
    "Jean-Marc BACHELIER", "Jose Maria LOPEZ", "Joël CAMATHIAS",
    "Giedo VAN DER GARDE", "MADONNA", "Mario ANDRETTI III",
])
def test_juntar_as_duas_metades_devolve_a_fonte(nome):
    primeiro, ultimo = split_wec_driver_name(nome)
    assert " ".join(p for p in (primeiro, ultimo) if p) == " ".join(nome.split())


# ── o cadastro malformado continua alcancavel ────────────────

def test_a_chave_normalizada_nao_muda_com_a_correcao():
    """
    A linha que o ensaio criou — `Horst FELBERMAYR` / `Sr.` — continua sendo
    encontrada depois da correcao, porque `full_name_key` junta as duas
    metades antes de dobrar. A correcao nao orfana o que ela mesma produziu,
    e nao reescreve nada: a proxima leitura reusa a linha por nome
    normalizado em vez de criar uma segunda.
    """
    antigo = full_name_key("Horst FELBERMAYR", "Sr.")
    novo = full_name_key(*split_wec_driver_name("Horst FELBERMAYR Sr."))
    assert antigo == novo


def test_jr_e_sr_continuam_sendo_pessoas_diferentes():
    """Dobrar o sufixo junto e o que mantem pai e filho separados."""
    pai = full_name_key(*split_wec_driver_name("Horst FELBERMAYR Sr."))
    filho = full_name_key(*split_wec_driver_name("Horst FELBERMAYR Jr"))
    assert pai != filho


# ── resolucao: o que a correcao do parser deixou a mostra ────

def _candidatos(*pares):
    from row_identity import DriverCandidate
    return [DriverCandidate(i, f, u) for i, (f, u) in enumerate(pares, start=1)]


def test_alexander_talka_jr_permanece_ambiguo():
    """
    Duas linhas no acervo carregam esse nome — `4953 Alexander Talka / Jr` e
    `340995 Alexander TALKA / JR`, de levas diferentes. O corte antigo casava
    EXATAMENTE com a segunda e resolvia por acaso: o artefato do separador
    encaixava no artefato gravado. Com o corte certo nao ha casamento exato,
    e a duplicacao aparece como o que sempre foi.
    """
    from row_identity import DriverOutcome, choose_driver
    primeiro, ultimo = split_wec_driver_name("Alexander TALKA JR")
    assert (primeiro, ultimo) == ("Alexander", "TALKA JR")
    _id, desfecho = choose_driver(
        _candidatos(("Alexander Talka", "Jr"), ("Alexander TALKA", "JR")),
        primeiro, ultimo)
    assert desfecho is DriverOutcome.MULTIPLE_CANDIDATES
    assert _id is None


def test_horst_jr_nao_e_confundido_com_horst_sr():
    """
    Os dois existem na copia: `5209`/`330133` para o Jr e `504546` para o Sr,
    este criado pelo ensaio da 1007. Dobrar o sufixo junto e o que os mantem
    separados.
    """
    from row_identity import DriverOutcome, choose_driver
    candidatos = _candidatos(("Horst Felbermayr", "Jr"),
                             ("Horst FELBERMAYR", "Sr."))
    sr_id, sr = choose_driver(candidatos, *split_wec_driver_name("Horst FELBERMAYR Sr."))
    jr_id, jr = choose_driver(candidatos, *split_wec_driver_name("Horst FELBERMAYR Jr"))
    assert sr is DriverOutcome.BY_NORMALIZED_NAME
    assert jr is DriverOutcome.BY_NORMALIZED_NAME
    assert sr_id != jr_id


def test_o_parser_nao_escolhe_candidato_por_ordem():
    """
    Com varios candidatos nao se devolve o primeiro, nem o de menor id: nao
    se devolve nenhum. Invertida a ordem, a resposta e a mesma.
    """
    from row_identity import DriverOutcome, choose_driver
    a = _candidatos(("Alexander Talka", "Jr"), ("Alexander TALKA", "JR"))
    b = _candidatos(("Alexander TALKA", "JR"), ("Alexander Talka", "Jr"))
    nome = split_wec_driver_name("Alexander TALKA JR")
    assert choose_driver(a, *nome) == (None, DriverOutcome.MULTIPLE_CANDIDATES)
    assert choose_driver(b, *nome) == (None, DriverOutcome.MULTIPLE_CANDIDATES)


def test_um_candidato_so_continua_resolvendo():
    """A recusa e por pluralidade, nao por sufixo."""
    from row_identity import DriverOutcome, choose_driver
    _id, desfecho = choose_driver(_candidatos(("Horst Felbermayr", "Jr")),
                                  *split_wec_driver_name("Horst FELBERMAYR Jr"))
    assert desfecho is DriverOutcome.BY_NORMALIZED_NAME and _id == 1

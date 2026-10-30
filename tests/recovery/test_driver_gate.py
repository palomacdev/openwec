"""
A sexta comporta: um vinculo que a fonte declara e gravado, ou nada e.

Ela existe por causa do primeiro ensaio da sessao 1243. As cinco comportas
abriram, o plano pareou 40 de 40, e a escrita teria passado deixando **107 de
109** pilotos declarados ligados — duas inscricoes caladamente sem um piloto
que a fonte nomeia. A rotina contava a ambiguidade e seguia.

Sem banco: a resolucao e injetada, porque o que se testa e a decisao, nao a
consulta.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from database.recovery import recover_classification as rc  # noqa: E402
from database.recovery.recover_classification import (      # noqa: E402
    DriverResolution,
    RecoveryBlocked,
    assert_drivers_resolvable,
    resolve_drivers,
)

sys.path.insert(0, str(RAIZ / "database" / "loader"))
from row_identity import DriverOutcome  # noqa: E402


# ── os dois casos de referencia, medidos na sessao 1243 ──────

AMBIGUOS_1243 = [
    ("32", 2, "Hugo DE", "SADELEER"),      # drivers 17006 e 353943
    ("25", 2, "Matthew", "McMURRY"),       # drivers 6463, 343189 e 415015
]


def resolucao(exact=84, normalized=4, missing=(), ambiguous=()):
    return DriverResolution(
        total=exact + normalized + len(missing) + len(ambiguous),
        exact=exact, normalized=normalized,
        missing=list(missing), ambiguous=list(ambiguous))


# ── multiplos candidatos ─────────────────────────────────────

def test_multiplos_candidatos_bloqueiam():
    with pytest.raises(RecoveryBlocked) as erro:
        assert_drivers_resolvable(resolucao(ambiguous=AMBIGUOS_1243))
    assert "multiplos candidatos" in str(erro.value)


def test_a_recusa_nomeia_cada_piloto_ambiguo():
    """Um numero sozinho nao diz a ninguem o que decidir."""
    with pytest.raises(RecoveryBlocked) as erro:
        assert_drivers_resolvable(resolucao(ambiguous=AMBIGUOS_1243))
    texto = str(erro.value)
    assert "SADELEER" in texto and "McMURRY" in texto
    assert "#32 slot 2" in texto and "#25 slot 2" in texto


def test_ambiguidade_nao_e_liberada_por_allow_new_drivers():
    """As duas recusas recusam coisas diferentes; uma nao cobre a outra."""
    with pytest.raises(RecoveryBlocked):
        assert_drivers_resolvable(resolucao(ambiguous=AMBIGUOS_1243),
                                  allow_new_drivers=True)


def test_nenhum_candidato_e_escolhido_automaticamente():
    """
    A recusa nao devolve id nenhum: nao ha caminho em que o primeiro
    candidato seja adotado por ser o primeiro.
    """
    with pytest.raises(RecoveryBlocked) as erro:
        assert_drivers_resolvable(resolucao(ambiguous=AMBIGUOS_1243))
    assert not re.search(r"\b(17006|353943|6463|343189|415015)\b",
                         str(erro.value))


# ── criacao de registro global ───────────────────────────────

def test_piloto_desconhecido_bloqueia_por_padrao():
    """`drivers` e global; crescer por causa de uma sessao exige autorizacao."""
    with pytest.raises(RecoveryBlocked) as erro:
        assert_drivers_resolvable(resolucao(missing=[("7", 1, "Nova", "PESSOA")]))
    assert "global" in str(erro.value)
    assert "--allow-new-drivers" in str(erro.value)


def test_piloto_desconhecido_passa_quando_autorizado():
    assert_drivers_resolvable(resolucao(missing=[("7", 1, "Nova", "PESSOA")]),
                              allow_new_drivers=True)


def test_sem_ambiguidade_e_sem_criacao_a_comporta_abre():
    assert_drivers_resolvable(resolucao())


def test_os_dezenove_sem_candidato_da_1243_bloqueiam_por_padrao():
    """A 1243 tambem para por aqui, e nao so pela ambiguidade."""
    faltantes = [(str(i), 1, f"Piloto{i}", "X") for i in range(19)]
    with pytest.raises(RecoveryBlocked) as erro:
        assert_drivers_resolvable(resolucao(missing=faltantes))
    assert "19 piloto(s)" in str(erro.value)


# ── os quatro desfechos, separados ───────────────────────────

class CursorFalso:
    """Nao consulta nada: a resolucao e injetada por nome."""


def test_resolve_drivers_separa_os_quatro_desfechos(monkeypatch):
    publicados = {
        ("A", "EXATO"): DriverOutcome.EXACT_NAME,
        ("B", "NORMALIZADO"): DriverOutcome.BY_NORMALIZED_NAME,
        ("C", "AUSENTE"): DriverOutcome.NOT_FOUND,
        ("D", "AMBIGUO"): DriverOutcome.MULTIPLE_CANDIDATES,
        ("", ""): DriverOutcome.NO_NAME,
    }

    def falso_resolve(_cur, first, last):
        desfecho = publicados[(first, last)]
        id_ = 1 if desfecho in (DriverOutcome.EXACT_NAME,
                                DriverOutcome.BY_NORMALIZED_NAME) else None
        return id_, desfecho

    def falso_extract(row, _series):
        return [{"slot": i + 1, "first_name": f, "last_name": u}
                for i, (f, u) in enumerate(publicados)]

    monkeypatch.setattr(rc, "resolve_driver_id", falso_resolve)
    monkeypatch.setattr(rc, "extract_drivers", falso_extract)

    res = resolve_drivers(CursorFalso(), [{"NUMBER": "9"}], "ELMS")
    assert res.total == 5
    assert (res.exact, res.normalized, res.unnamed) == (1, 1, 1)
    assert [e[2] for e in res.missing] == ["C"]
    assert [e[2] for e in res.ambiguous] == ["D"]


def test_resolve_drivers_nao_escreve():
    """
    Um cursor que levanta em qualquer chamada: se a resolucao consultasse ou
    escrevesse por conta propria, este teste falharia.
    """
    class CursorQueRecusa:
        def execute(self, *a, **k):
            raise AssertionError("a pre-checagem nao pode emitir SQL propria")

    import database.recovery.recover_classification as mod
    original = mod.resolve_driver_id
    mod.resolve_driver_id = lambda cur, f, u: (1, DriverOutcome.EXACT_NAME)
    try:
        res = resolve_drivers(CursorQueRecusa(), [], "ELMS")
    finally:
        mod.resolve_driver_id = original
    assert res.total == 0


# ── a ordem importa: a comporta vem antes do DELETE ──────────

def test_a_comporta_corre_antes_de_remover_linhas():
    """
    Uma execucao que para aqui nao removeu nada. Se a checagem descesse para
    depois do `drop_lap_shaped_results`, a recusa chegaria com a sessao ja
    esvaziada.
    """
    fonte = (RAIZ / "database" / "recovery" / "recover_classification.py"
             ).read_text(encoding="utf-8")
    corpo = fonte.split("def recover_session(", 1)[1]
    pos_comporta = corpo.index("assert_drivers_resolvable")
    pos_delete = corpo.index("drop_lap_shaped_results")
    assert pos_comporta < pos_delete


def test_a_comporta_esta_no_caminho_de_escrita_e_nao_so_no_cli():
    """Vale para quem chamar `recover_session`, nao so para o CLI."""
    fonte = (RAIZ / "database" / "recovery" / "recover_classification.py"
             ).read_text(encoding="utf-8")
    corpo = fonte.split("def recover_session(", 1)[1].split("\ndef ", 1)[0]
    assert "assert_drivers_resolvable" in corpo


# ── o dry-run mostra tudo o que esta errado, nao a primeira coisa ──

def test_driver_refusals_devolve_os_dois_motivos():
    """
    Quem limpa a ambiguidade e so entao descobre os dezenove desconhecidos
    gastou uma rodada a toa.
    """
    from database.recovery.recover_classification import driver_refusals
    motivos = driver_refusals(
        resolucao(ambiguous=AMBIGUOS_1243,
                  missing=[("7", 1, "Nova", "PESSOA")]))
    assert len(motivos) == 2
    assert any("multiplos candidatos" in m for m in motivos)
    assert any("global" in m for m in motivos)


def test_driver_refusals_vazio_quando_a_comporta_abre():
    from database.recovery.recover_classification import driver_refusals
    assert driver_refusals(resolucao()) == []

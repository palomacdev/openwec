"""
Where a recovered row says it came from.

The R3 rehearsal recovered session 2217 correctly and left `source_url` null
on all nineteen rows: the database could not say which publication produced
them. These are the gates that make the column mean something — a URL is
written only when the bytes that were read hashed to the publication being
claimed, and never otherwise.

No database: the cursor answers from a queue and records every statement.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database" / "loader"))

import database.recovery.recover_classification as rec  # noqa: E402
from database.recovery.recover_classification import (  # noqa: E402
    RecoveryBlocked,
    run,
)
from load_classification import insert_result  # noqa: E402

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402

URL = ("https://lemanscup.alkamelsystems.com/Results/01_2017/"
       "06_AUTODROMO%20DO%20ALGARVE/207_Michelin%20Le%20Mans%20Cup/"
       "201710220900_Race/Hour%202/05_Classification_Race_Hour%202.CSV")

CABECALHO = ("POSITION;NUMBER;TEAM;DRIVER_1;DRIVER_2;DRIVER_3;DRIVER_4;"
             "VEHICLE;TYRES;CLASS;GROUP;DIVISION;STATUS;LAPS;TOTAL_TIME;"
             "GAP_FIRST;GAP_PREVIOUS;FL_LAPNUM;FL_TIME;FL_KPH;DRIVER_5;")
LINHA = ("1;98;Motorsport98;Eric DE DONCKER;Andrew MEYRICK;;;"
         "Ligier JS P3 - Nissan;M;LMP3;;;Classified;64;2:00'59.447;;;"
         "34;1'41.945;164.3;;")


@pytest.fixture
def fonte(tmp_path) -> Path:
    csv = tmp_path / "05_Classification_Race_Hour 2.CSV"
    csv.write_text(f"{CABECALHO}\n{LINHA}\n", encoding="utf-8")
    return csv


def digest_de(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


# ── A URL without proof is not provenance ────────────────────

def test_a_url_sozinha_e_recusada(fonte):
    """
    Writing it would look like provenance and not be any.

    A reader finding a URL on the row has no way to tell whether anyone
    checked that the file at that address is the file that was loaded. The
    refusal is what keeps that reading honest.
    """
    with pytest.raises(RecoveryBlocked) as exc:
        run(2217, fonte, "LEMANSCUP", dry_run=True, publication_url=URL)
    assert "--publication-sha256" in str(exc.value)


def test_o_digest_sozinho_e_recusado(fonte):
    with pytest.raises(RecoveryBlocked) as exc:
        run(2217, fonte, "LEMANSCUP", dry_run=True,
            publication_sha256=digest_de(fonte))
    assert "--publication-url" in str(exc.value)


def test_o_digest_que_nao_confere_interrompe_antes_de_conectar(fonte):
    """
    And it stops before the connection, so a wrong file never reaches a target.
    """
    with pytest.raises(RecoveryBlocked) as exc:
        run(2217, fonte, "LEMANSCUP", dry_run=True, publication_url=URL,
            publication_sha256="0" * 64)
    mensagem = str(exc.value)
    assert "nao e a publicacao declarada" in mensagem
    assert digest_de(fonte) in mensagem


def test_o_arquivo_alterado_depois_da_decisao_e_recusado(fonte):
    """A byte changes, the hash changes, the run stops."""
    declarado = digest_de(fonte)
    fonte.write_text(f"{CABECALHO}\n{LINHA}\n;\n", encoding="utf-8")
    with pytest.raises(RecoveryBlocked) as exc:
        run(2217, fonte, "LEMANSCUP", dry_run=True, publication_url=URL,
            publication_sha256=declarado)
    assert "nao e a publicacao declarada" in str(exc.value)


def test_sem_declaracao_nenhuma_nao_se_inventa_url(fonte, monkeypatch):
    """
    No publication declared means `source_url` stays null.

    The alternative — deriving a plausible URL from the catalogue — would
    fill the column for rows nobody verified, which is the failure mode this
    whole gate exists to avoid.
    """
    chamadas = {}

    def falso_connect(**kwargs):
        chamadas["conectou"] = True
        raise RuntimeError("nao se conecta neste teste")

    monkeypatch.setitem(sys.modules, "psycopg2",
                        type("m", (), {"connect": staticmethod(falso_connect)}))
    for nome, valor in (("RECOVERY_DB_HOST", "127.0.0.1"),
                        ("RECOVERY_DB_PORT", "5491"),
                        ("RECOVERY_DB_NAME", "openwec"),
                        ("RECOVERY_DB_USER", "openwec")):
        monkeypatch.setenv(nome, valor)

    # Sem --publication-url o caminho segue ate a conexao: nao ha recusa de
    # proveniencia, porque nao ha proveniencia sendo afirmada.
    with pytest.raises(RuntimeError):
        run(2217, fonte, "LEMANSCUP", dry_run=True)
    assert chamadas.get("conectou") is True


# ── The URL reaches the row ──────────────────────────────────

def test_insert_result_grava_a_url_recebida():
    cur = CursorFalso([[(77,)]])
    linha = dict(zip(CABECALHO.split(";"), LINHA.split(";")))
    assert insert_result(cur, 2217, 366080, linha, URL) == 77

    sql, params = cur.executados[0]
    assert "source_url" in sql
    assert URL in params


def test_insert_result_sem_url_grava_nulo():
    cur = CursorFalso([[(77,)]])
    linha = dict(zip(CABECALHO.split(";"), LINHA.split(";")))
    insert_result(cur, 2217, 366080, linha)
    _, params = cur.executados[0]
    assert params[-1] is None


def test_a_recuperacao_repassa_a_url_para_a_linha():
    """
    Asserted over the module's own source, like the batch-mode absence.

    There is one call to `insert_result` on this path, and the recovery is
    only as good as that call carrying the URL; a refactor that drops the
    argument would leave every gate above still passing.
    """
    fonte_py = Path(rec.__file__).read_text(encoding="utf-8")
    chamadas = re.findall(r"insert_result\(([^)]*)\)", fonte_py)
    assert chamadas, "o call site sumiu"
    for argumentos in chamadas:
        assert "source_url" in argumentos


def test_recover_session_aceita_a_url():
    import inspect
    assinatura = inspect.signature(rec.recover_session)
    assert "source_url" in assinatura.parameters
    assert assinatura.parameters["source_url"].default is None

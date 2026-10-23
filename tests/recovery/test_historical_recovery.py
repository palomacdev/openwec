"""
The gates on the historical path, and the fact that it knows only one session.

This path writes to a real database, so what is tested here is mostly what it
refuses. The integrated exercise against a disposable restore is in
`docs/data-integrity/historical-recovery-procedure.md`; these are the parts
that can be pinned without one.

No database: the cursor answers from a queue and records every statement.
"""
from __future__ import annotations

import hashlib
import io
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import database.recovery.historical_recovery as hist  # noqa: E402
from database.recovery.historical_recovery import (  # noqa: E402
    ALLOWED_SESSION,
    BASELINE,
    SOURCE_SHA256,
    HistoricalBlocked,
    backup_evidence,
    cluster_identifier,
    confirm,
    external_references,
    post_invariants,
    preflight,
    session_identity,
    source_evidence,
)

from database.recovery.backup_attestation import ESSENTIAL_TABLES  # noqa: E402

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402
from tests.recovery.test_backup_attestation import (  # noqa: E402
    AGORA,
    CONTAGENS,
    atestado as com_evidencia,
)

CLUSTER = "7699952836394844194"
CLUSTER_ORIGEM = "7642299396037144610"


# ── One session, and no way to name another ──────────────────

def test_a_sessao_permitida_e_uma_so():
    assert ALLOWED_SESSION == 2226


def test_outra_sessao_e_recusada_antes_de_qualquer_consulta():
    cur = CursorFalso()
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, 1035, [], CLUSTER)
    assert "apenas a sessao 2226" in str(exc.value)
    assert cur.executados == []


def test_nao_ha_modo_de_lote():
    """
    The absence is asserted over the module's own source.

    A batch flag would be the easiest thing to add and the hardest to notice,
    so it is pinned here rather than left to review.
    """
    fonte = Path(hist.__file__).read_text(encoding="utf-8")
    for proibido in ('"--all"', "'--all'", '"--sessions"', '"--batch"',
                     '"--from-shortlist"'):
        assert proibido not in fonte


# ── The backup ───────────────────────────────────────────────

def test_sem_backup_nao_se_escreve(tmp_path):
    with pytest.raises(HistoricalBlocked) as exc:
        backup_evidence(tmp_path / "nao-existe.dump")
    assert "nao encontrado" in str(exc.value)


def test_backup_vazio_e_recusado(tmp_path):
    vazio = tmp_path / "vazio.dump"
    vazio.write_bytes(b"")
    with pytest.raises(HistoricalBlocked) as exc:
        backup_evidence(vazio)
    assert "vazio" in str(exc.value)


def test_backup_que_nao_e_custom_e_recusado(tmp_path):
    """
    A plain SQL dump would pass an existence check and would not restore.

    The revert procedure assumes `pg_restore`, so the file has to be the
    format `pg_restore` reads.
    """
    sql = tmp_path / "dump.sql"
    sql.write_bytes(b"-- PostgreSQL database dump\nCREATE TABLE x();\n")
    with pytest.raises(HistoricalBlocked) as exc:
        backup_evidence(sql)
    assert "custom" in str(exc.value)


def test_o_backup_sem_evidencia_e_recusado(tmp_path):
    """
    A well-formed archive is not evidence.

    `PGDMP` and a hash say the file is an archive. They say nothing about
    which database it came from, when, or whether it restores.
    """
    dump = tmp_path / "openwec.dump"
    dump.write_bytes(b"PGDMP" + b"\x00" * 64)
    with pytest.raises(HistoricalBlocked) as exc:
        backup_evidence(dump)
    assert "sem evidencia utilizavel" in str(exc.value)


def test_o_backup_com_evidencia_entra_no_relatorio(tmp_path):
    dump = com_evidencia(tmp_path)
    prova = backup_evidence(dump, agora=AGORA)
    assert prova["sha256"] == hashlib.sha256(dump.read_bytes()).hexdigest()
    assert prova["cluster_de_origem"] == CLUSTER_ORIGEM
    assert prova["capturado_em"].startswith("2026-10-23T18:00")
    assert prova["restauracao"]["ok"] is True
    assert prova["contagens"]["laps"] == CONTAGENS["laps"]


def test_o_backup_velho_demais_e_recusado(tmp_path):
    dump = com_evidencia(tmp_path)
    with pytest.raises(HistoricalBlocked) as exc:
        backup_evidence(dump, agora=AGORA + timedelta(hours=7))
    assert "acima do limite" in str(exc.value)


def test_o_backup_de_outro_cluster_e_recusado_na_pre_validacao(tmp_path):
    """The check the file alone cannot make: is this a pre-image of the target?"""
    dump = com_evidencia(tmp_path)
    backup = backup_evidence(dump, agora=AGORA)
    cur = CursorFalso([[(CLUSTER,)], [(CLUSTER,)]])
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, ALLOWED_SESSION, [], CLUSTER, backup)
    assert "nao corresponde ao alvo" in str(exc.value)


def test_o_banco_que_mudou_desde_o_backup_e_recusado(tmp_path):
    dump = com_evidencia(tmp_path)
    backup = backup_evidence(dump, agora=AGORA)
    mudou = dict(CONTAGENS, drivers=CONTAGENS["drivers"] + 1)
    cur = CursorFalso(
        [[(CLUSTER_ORIGEM,)]] + [[(CLUSTER_ORIGEM,)]]
        + [[(mudou[t],)] for t in ESSENTIAL_TABLES]
        + [[(3108, 1554)]])
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, ALLOWED_SESSION, [], CLUSTER_ORIGEM, backup)
    assert "mudou desde o backup" in str(exc.value)


def test_sem_backup_a_pre_validacao_nao_compara(tmp_path):
    """`preflight` is callable read-only, for the preconditions report."""
    cur = CursorFalso([[(CLUSTER,)], []])
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, ALLOWED_SESSION, [], CLUSTER)
    assert "nao existe" in str(exc.value)


# ── The source ───────────────────────────────────────────────

def test_a_fonte_com_outro_conteudo_e_recusada(tmp_path):
    csv = tmp_path / "fonte.csv"
    csv.write_bytes(b"POSITION;NUMBER\n1;3\n")
    with pytest.raises(HistoricalBlocked) as exc:
        source_evidence(csv)
    assert "nao e a que foi ensaiada" in str(exc.value)


def test_o_hash_da_fonte_esta_fixado():
    assert len(SOURCE_SHA256) == 64
    assert SOURCE_SHA256 == ("034e73c53cea3fdc5f67f636eeade3dabb689437"
                             "2fc1409fd5839973f472e29c")


# ── The cluster ──────────────────────────────────────────────

def test_o_cluster_sem_identidade_e_recusado():
    with pytest.raises(HistoricalBlocked):
        cluster_identifier(CursorFalso([[(None,)]]))
    with pytest.raises(HistoricalBlocked):
        cluster_identifier(CursorFalso([[]]))


def test_o_cluster_e_lido_do_proprio_servidor():
    cur = CursorFalso([[(CLUSTER,)]])
    assert cluster_identifier(cur) == CLUSTER
    assert "pg_control_system" in cur.sql


def test_o_cluster_declarado_tem_de_bater():
    cur = CursorFalso([[(CLUSTER,)]])
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, ALLOWED_SESSION, [], "7642299396037144610")
    assert "--expect-cluster" in str(exc.value)


# ── The session's identity ───────────────────────────────────

def test_a_sessao_ausente_e_recusada():
    with pytest.raises(HistoricalBlocked) as exc:
        session_identity(CursorFalso([[]]), 2226)
    assert "nao existe" in str(exc.value)


def test_a_sessao_com_outra_identidade_e_recusada():
    cur = CursorFalso([
        [(CLUSTER,)],
        [("ELMS", "09_2014", "02_IMOLA", "201405181400_Race")],
    ])
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, ALLOWED_SESSION, [], CLUSTER)
    assert "nao" in str(exc.value) and "LEMANSCUP" in str(exc.value)


# ── The baseline ─────────────────────────────────────────────

IDENTIDADE = ("LEMANSCUP", "02_2018", "01_PAUL RICARD", "201804141605_Race")


def ate_o_estado(estado: tuple) -> CursorFalso:
    return CursorFalso([[(CLUSTER,)], [IDENTIDADE], [estado]])


def test_a_fonte_com_outro_numero_de_carros_e_recusada():
    cur = CursorFalso([[(CLUSTER,)], [IDENTIDADE]])
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, ALLOWED_SESSION, [{"NUMBER": "3"}], CLUSTER)
    assert "28" in str(exc.value)


def test_um_estado_fora_dos_dois_previstos_interrompe():
    """
    The run stops rather than adapt. Half-recovered is not a starting point.
    """
    rows = [{"NUMBER": str(n)} for n in range(1, 29)]
    cur = ate_o_estado((999, 999, 0, 1554, 28))
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, ALLOWED_SESSION, rows, CLUSTER)
    assert "nenhum dos dois previstos" in str(exc.value)


def test_o_baseline_contaminado_e_o_que_foi_medido():
    assert BASELINE == {"results": 3108, "cars_in_results": 3108,
                        "result_drivers": 0, "laps": 1554, "cars_in_laps": 28}


# ── External references to the cars that would go ────────────

def test_sem_carros_a_remover_nada_e_consultado():
    cur = CursorFalso()
    assert external_references(cur, 2226, []) == {
        "results_de_outras_sessoes": 0, "laps": 0,
        "analytics_car_session": 0, "analytics_stints": 0}
    assert cur.executados == []


def test_as_quatro_referencias_sao_contadas():
    cur = CursorFalso([[(0, 0, 3, 0)]])
    assert external_references(cur, 2226, [1, 2])["analytics_car_session"] == 3


# ── The confirmation no flag can give ────────────────────────

def pre(cluster: str = CLUSTER) -> dict:
    return {"cluster": cluster}


def test_sem_ninguem_respondendo_nao_se_escreve():
    with pytest.raises(HistoricalBlocked) as exc:
        confirm(pre(), 2226, io.StringIO(""), io.StringIO())
    assert "ausente" in str(exc.value)


def test_a_sessao_confirmada_tem_de_ser_a_sessao():
    with pytest.raises(HistoricalBlocked) as exc:
        confirm(pre(), 2226, io.StringIO(f"1035\n{CLUSTER}\n"), io.StringIO())
    assert "invalida" in str(exc.value)


def test_o_cluster_confirmado_tem_de_ser_o_cluster():
    with pytest.raises(HistoricalBlocked) as exc:
        confirm(pre(), 2226, io.StringIO("2226\n7642299396037144610\n"),
                io.StringIO())
    assert "invalida" in str(exc.value)


def test_as_duas_respostas_certas_passam():
    confirm(pre(), 2226, io.StringIO(f"2226\n{CLUSTER}\n"), io.StringIO())


def test_a_confirmacao_e_pedida_em_voz_alta():
    saida = io.StringIO()
    confirm(pre(), 2226, io.StringIO(f"2226\n{CLUSTER}\n"), saida)
    texto = saida.getvalue()
    assert "sessao" in texto and "cluster" in texto


# ── Invariants before the commit ─────────────────────────────

ANTES = {"outras_sessoes": {"digest": "abc", "quantas": 5424}}


def depois_de(estado: tuple, sem_resultado: int = 0, sem_posicao: int = 0,
              digest: str = "abc", quantas: int = 5424) -> CursorFalso:
    return CursorFalso([[estado], [(sem_resultado,)], [(sem_posicao,)],
                        [(digest, quantas)]])


def test_o_estado_esperado_nao_deixa_falha():
    assert post_invariants(depois_de((28, 28, 54, 1554, 28)), 2226, ANTES) == []


@pytest.mark.parametrize("estado,trecho", [
    ((27, 27, 54, 1554, 28), "results = 27"),
    ((28, 28, 54, 1553, 28), "laps = 1553"),
    ((28, 28, 54, 1554, 27), "cars_in_laps = 27"),
    ((28, 28, 0, 1554, 28), "nenhum piloto"),
])
def test_o_estado_errado_vira_falha(estado, trecho):
    falhas = post_invariants(depois_de(estado), 2226, ANTES)
    assert any(trecho in f for f in falhas)


def test_o_carro_com_voltas_sem_resultado_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), sem_resultado=1), 2226, ANTES)
    assert any("sem resultado" in f for f in falhas)


def test_classificado_sem_posicao_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), sem_posicao=2), 2226, ANTES)
    assert any("sem posicao" in f for f in falhas)


def test_outra_sessao_alterada_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), digest="outro"), 2226, ANTES)
    assert any("outra sessao" in f for f in falhas)


def test_sessao_a_mais_ou_a_menos_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), quantas=5425), 2226, ANTES)
    assert any("numero de sessoes" in f for f in falhas)


# ── The rehearsal guard is not weakened ──────────────────────

def test_o_recuperador_de_ensaio_continua_exigindo_as_cinco_portas():
    """
    This path is additional, not a replacement.

    `recover_classification` still refuses any target that is not a declared
    disposable copy; nothing here imports around that or turns it off.
    """
    import database.recovery.recover_classification as ensaio
    fonte = Path(hist.__file__).read_text(encoding="utf-8")

    # Nothing here reads the rehearsal's own variables, so nothing here can
    # satisfy its gates on its behalf.
    assert ensaio.DISPOSABLE_ENV not in fonte
    assert ensaio.REHEARSAL_ID_ENV not in fonte

    # And nothing here borrows its guard functions, in either direction.
    for nome in ("refusal", "assert_writable", "identity_refusal",
                 "target_config", "marker_sql"):
        assert not hasattr(hist, nome), f"{nome} nao deveria estar aqui"

    # The rehearsal guard still asks for all five.
    assert hasattr(ensaio, "refusal") and hasattr(ensaio, "identity_refusal")

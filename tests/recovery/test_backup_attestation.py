"""
What a backup has to prove, and every way the proof can fail.

A `PGDMP` header and a hash say the file is a well-formed archive. They do
not say which database it came from, when, or whether it restores — and a
recovery that trusts them is trusting a filename. These tests pin the three
answers and the refusals that go with them.

No database: the cursor answers from a queue.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from database.recovery.backup_attestation import (  # noqa: E402
    ESSENTIAL_TABLES,
    AttestationInvalid,
    assert_matches_target,
    attestation_path,
    capture,
    load,
    record_restore,
    sha256_of,
    validate,
)

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402

ORIGEM = "7642299396037144610"
COPIA = "7699952836394844194"
AGORA = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)

CONTAGENS = {"sessions": 5425, "cars": 671353, "results": 671353,
             "result_drivers": 469457, "laps": 2034223, "drivers": 2845,
             "teams": 1111}
SESSAO = {"session_id": 2226, "results": 3108, "laps": 1554}


def cursor_de(cluster: str, contagens: dict = None, sessao: dict = None):
    """A cursor that answers cluster_and_counts and then session_counts."""
    contagens = contagens or CONTAGENS
    sessao = sessao or SESSAO
    respostas = [[(cluster,)]]
    respostas += [[(contagens[t],)] for t in ESSENTIAL_TABLES]
    respostas += [[(sessao["results"], sessao["laps"])]]
    return CursorFalso(respostas)


def dump_valido(tmp_path: Path, nome: str = "openwec.dump") -> Path:
    caminho = tmp_path / nome
    caminho.write_bytes(b"PGDMP" + b"\x00" * 128)
    return caminho


def atestado(tmp_path: Path, **ajustes) -> Path:
    """A dump with a complete, valid attestation beside it."""
    dump = dump_valido(tmp_path)
    capture(cursor_de(ORIGEM), dump, 2226, agora=AGORA)
    record_restore(cursor_de(COPIA), dump, agora=AGORA)
    if ajustes:
        prova = load(dump)
        for chave, valor in ajustes.items():
            if valor is None:
                prova.pop(chave, None)
            else:
                prova[chave] = valor
        attestation_path(dump).write_text(
            json.dumps(prova, ensure_ascii=False), encoding="utf-8")
    return dump


# ── Capture ──────────────────────────────────────────────────

def test_a_captura_registra_as_tres_coisas_que_um_hash_nao_diz(tmp_path):
    dump = dump_valido(tmp_path)
    prova = capture(cursor_de(ORIGEM), dump, 2226, agora=AGORA)

    assert prova["cluster_de_origem"] == ORIGEM          # de onde
    assert prova["capturado_em"].startswith("2026-10-23T18:00")   # quando
    assert prova["contagens"] == CONTAGENS               # de que estado
    assert prova["sessao"] == SESSAO
    assert prova["sha256"] == sha256_of(dump)
    assert prova["restauracao"] is None                  # ainda nao provada


def test_a_captura_recusa_um_dump_ausente(tmp_path):
    with pytest.raises(AttestationInvalid):
        capture(cursor_de(ORIGEM), tmp_path / "nao-existe.dump", 2226)


def test_a_captura_recusa_um_dump_vazio(tmp_path):
    vazio = tmp_path / "vazio.dump"
    vazio.write_bytes(b"")
    with pytest.raises(AttestationInvalid):
        capture(cursor_de(ORIGEM), vazio, 2226)


# ── Restore verification ─────────────────────────────────────

def test_a_restauracao_verificada_bate_as_contagens(tmp_path):
    dump = dump_valido(tmp_path)
    capture(cursor_de(ORIGEM), dump, 2226, agora=AGORA)
    prova = record_restore(cursor_de(COPIA), dump, agora=AGORA)

    assert prova["restauracao"]["ok"] is True
    assert prova["restauracao"]["cluster_de_destino"] == COPIA
    assert prova["restauracao"]["divergencias"] == {}


def test_a_restauracao_que_perdeu_linhas_nao_e_ok(tmp_path):
    """
    The case this exists for.

    A restore that silently drops rows leaves a file that still hashes
    correctly and still says `PGDMP`. Only counting the other side catches it.
    """
    dump = dump_valido(tmp_path)
    capture(cursor_de(ORIGEM), dump, 2226, agora=AGORA)
    quebrado = dict(CONTAGENS, laps=0, results=0)
    prova = record_restore(cursor_de(COPIA, quebrado), dump, agora=AGORA)

    assert prova["restauracao"]["ok"] is False
    assert set(prova["restauracao"]["divergencias"]) == {"laps", "results"}
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA)
    assert "restauracao falhou" in str(exc.value)


def test_restaurar_no_proprio_cluster_de_origem_nao_prova_nada(tmp_path):
    dump = dump_valido(tmp_path)
    capture(cursor_de(ORIGEM), dump, 2226, agora=AGORA)
    with pytest.raises(AttestationInvalid) as exc:
        record_restore(cursor_de(ORIGEM), dump, agora=AGORA)
    assert "cluster de origem" in str(exc.value)


# ── Validation ───────────────────────────────────────────────

def test_a_evidencia_completa_passa(tmp_path):
    prova = validate(atestado(tmp_path), agora=AGORA)
    assert prova["idade_horas"] == 0.0


def test_sem_evidencia_nenhuma_nao_passa(tmp_path):
    dump = dump_valido(tmp_path)
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA)
    assert "nao tem evidencia" in str(exc.value)


def test_a_evidencia_de_outro_arquivo_nao_passa(tmp_path):
    """The binding that makes the rest of the attestation mean anything."""
    dump = atestado(tmp_path)
    dump.write_bytes(b"PGDMP" + b"\x01" * 128)
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA)
    assert "nao descreve este arquivo" in str(exc.value)


def test_a_evidencia_sem_restauracao_nao_passa(tmp_path):
    dump = dump_valido(tmp_path)
    capture(cursor_de(ORIGEM), dump, 2226, agora=AGORA)
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA)
    assert "nao foi verificada" in str(exc.value)


def test_a_evidencia_ilegivel_nao_passa(tmp_path):
    dump = atestado(tmp_path)
    attestation_path(dump).write_text("{nao e json", encoding="utf-8")
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA)
    assert "ilegivel" in str(exc.value)


@pytest.mark.parametrize("campo", ["cluster_de_origem", "capturado_em",
                                   "contagens", "sessao"])
def test_a_evidencia_incompleta_nao_passa(tmp_path, campo):
    dump = atestado(tmp_path, **{campo: None})
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA)
    assert campo in str(exc.value)


def test_a_evidencia_sem_uma_das_contagens_nao_passa(tmp_path):
    parcial = {t: v for t, v in CONTAGENS.items() if t != "laps"}
    dump = atestado(tmp_path, contagens=parcial)
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA)
    assert "laps" in str(exc.value)


def test_a_evidencia_de_outro_schema_nao_passa(tmp_path):
    dump = atestado(tmp_path, schema=99)
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA)
    assert "schema" in str(exc.value)


def test_o_backup_velho_nao_passa(tmp_path):
    dump = atestado(tmp_path)
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA + timedelta(hours=7))
    assert "acima do limite" in str(exc.value)


def test_a_janela_e_configuravel(tmp_path):
    dump = atestado(tmp_path)
    prova = validate(dump, agora=AGORA + timedelta(hours=7), max_age_hours=12)
    assert prova["idade_horas"] == 7.0


def test_o_backup_do_futuro_nao_passa(tmp_path):
    dump = atestado(tmp_path)
    with pytest.raises(AttestationInvalid) as exc:
        validate(dump, agora=AGORA - timedelta(hours=1))
    assert "futuro" in str(exc.value)


def test_um_relogio_levemente_adiantado_e_tolerado(tmp_path):
    dump = atestado(tmp_path)
    validate(dump, agora=AGORA - timedelta(seconds=30))


# ── Pre-image of the target ──────────────────────────────────

def test_as_contagens_que_batem_passam(tmp_path):
    prova = validate(atestado(tmp_path), agora=AGORA)
    assert_matches_target(prova, cursor_de(ORIGEM), 2226)


def test_um_backup_de_outro_cluster_nao_serve(tmp_path):
    prova = validate(atestado(tmp_path), agora=AGORA)
    with pytest.raises(AttestationInvalid) as exc:
        assert_matches_target(prova, cursor_de(COPIA), 2226)
    assert "o alvo e" in str(exc.value)


def test_um_banco_que_mudou_desde_o_backup_nao_serve(tmp_path):
    """
    A backup is a pre-image or it is nothing.

    If anything in the essential tables moved after the dump was taken, the
    file is no longer a way back from the write about to happen.
    """
    prova = validate(atestado(tmp_path), agora=AGORA)
    mudou = dict(CONTAGENS, drivers=CONTAGENS["drivers"] + 1)
    with pytest.raises(AttestationInvalid) as exc:
        assert_matches_target(prova, cursor_de(ORIGEM, mudou), 2226)
    assert "mudou desde o backup" in str(exc.value)


def test_a_sessao_que_mudou_desde_o_backup_nao_serve(tmp_path):
    prova = validate(atestado(tmp_path), agora=AGORA)
    outra = {"session_id": 2226, "results": 28, "laps": 1554}
    with pytest.raises(AttestationInvalid) as exc:
        assert_matches_target(prova, cursor_de(ORIGEM, sessao=outra), 2226)
    assert "a sessao mudou" in str(exc.value)


def test_api_key_requests_nao_entra_na_comparacao():
    """
    It moves on its own while the API serves.

    A backup is not stale because a request was logged after it was taken,
    and refusing one for that would be refusing every backup.
    """
    assert "api_key_requests" not in ESSENTIAL_TABLES

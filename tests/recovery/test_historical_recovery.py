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
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import database.recovery.historical_recovery as hist  # noqa: E402
from database.recovery.historical_recovery import (  # noqa: E402
    ALLOWED_SESSIONS,
    SESSOES,
    HistoricalBlocked,
    autorizada,
    backup_evidence,
    cluster_identifier,
    confirm,
    external_references,
    post_invariants,
    preflight,
    session_identity,
    source_evidence,
)

ALLOWED_SESSION = 2226
BASELINE = SESSOES[2226].baseline
SOURCE_SHA256 = SESSOES[2226].source_sha256

from database.recovery.backup_attestation import ESSENTIAL_TABLES  # noqa: E402

from tests.recovery.test_recovery_guard import CursorFalso  # noqa: E402
from tests.recovery.test_backup_attestation import (  # noqa: E402
    AGORA,
    CONTAGENS,
    atestado as com_evidencia,
)

CLUSTER = "7699952836394844194"
CLUSTER_ORIGEM = "7642299396037144610"


# ── A closed allowlist, and no way to name anything else ─────

def test_a_allowlist_e_fechada_e_escrita_uma_a_uma():
    assert ALLOWED_SESSIONS == frozenset({2226, 2217})
    assert set(SESSOES) == ALLOWED_SESSIONS
    for sid, plano in SESSOES.items():
        assert plano.session_id == sid


def test_outra_sessao_e_recusada_antes_de_qualquer_consulta():
    cur = CursorFalso()
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, 1035, [], CLUSTER)
    assert "nao esta previsto" in str(exc.value)
    assert cur.executados == []


@pytest.mark.parametrize("sessao", [0, 1, 1003, 1243, 2216, 2218, 2225, 2227,
                                    5425, -2217, 99999])
def test_qualquer_sessao_fora_da_allowlist_e_recusada(sessao):
    """
    Including the neighbours of the two that are in it.

    An off-by-one on the identifier is the mistake this guards: 2216 and
    2218 are real sessions, and neither was rehearsed.
    """
    with pytest.raises(HistoricalBlocked):
        autorizada(sessao)


def test_as_duas_sessoes_tem_chave_de_lock_propria():
    chaves = {plano.lock_key for plano in SESSOES.values()}
    assert len(chaves) == len(SESSOES)


def test_cada_sessao_tem_publicacao_e_hash_proprios():
    urls = {p.source_url for p in SESSOES.values()}
    hashes = {p.source_sha256 for p in SESSOES.values()}
    assert len(urls) == len(hashes) == len(SESSOES)
    for plano in SESSOES.values():
        assert len(plano.source_sha256) == 64
        assert plano.source_url.startswith("https://")


def test_nao_ha_modo_de_lote():
    """
    The absence is asserted over the module's own source.

    A batch flag would be the easiest thing to add and the hardest to notice,
    so it is pinned here rather than left to review.
    """
    fonte = Path(hist.__file__).read_text(encoding="utf-8")
    for proibido in ('"--all"', "'--all'", '"--sessions"', '"--batch"',
                     '"--from-shortlist"', '"--range"', '"--session-ids"'):
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

@pytest.mark.parametrize("sessao", sorted(ALLOWED_SESSIONS))
def test_a_fonte_com_outro_conteudo_e_recusada(tmp_path, sessao):
    csv = tmp_path / "fonte.csv"
    csv.write_bytes(b"POSITION;NUMBER\n1;3\n")
    with pytest.raises(HistoricalBlocked) as exc:
        source_evidence(csv, SESSOES[sessao])
    assert "nao e a que foi ensaiada" in str(exc.value)
    assert str(sessao) in str(exc.value)


def test_a_fonte_de_outra_sessao_autorizada_e_recusada(tmp_path):
    """
    The two allowlisted sessions do not accept each other's publication.

    Both are Le Mans Cup races whose files are named alike; the hash is what
    tells them apart, and it is per session, not per module.
    """
    csv = tmp_path / "fonte.csv"
    csv.write_bytes(b"qualquer coisa")
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()
    assert digest not in {p.source_sha256 for p in SESSOES.values()}
    for sessao in ALLOWED_SESSIONS:
        with pytest.raises(HistoricalBlocked):
            source_evidence(csv, SESSOES[sessao])


def test_a_url_so_sai_junto_do_hash_que_confere(tmp_path):
    """
    Provenance is returned by the same call that proved the bytes.

    There is no path that hands out the URL for a file nobody hashed: the
    plan's hash is checked first, and the URL rides back with it.
    """
    csv = tmp_path / "fonte.csv"
    csv.write_bytes(b"conteudo qualquer")
    digest = hashlib.sha256(csv.read_bytes()).hexdigest()

    combina = replace(SESSOES[2217], source_sha256=digest)
    prova = source_evidence(csv, combina)
    assert prova["sha256"] == digest
    assert prova["url"] == SESSOES[2217].source_url

    nao_combina = replace(SESSOES[2217], source_sha256="0" * 64)
    with pytest.raises(HistoricalBlocked):
        source_evidence(csv, nao_combina)


def test_o_hash_da_fonte_esta_fixado():
    assert len(SOURCE_SHA256) == 64
    assert SOURCE_SHA256 == ("034e73c53cea3fdc5f67f636eeade3dabb689437"
                             "2fc1409fd5839973f472e29c")
    assert SESSOES[2217].source_sha256 == (
        "b6ac6628e470fe5cb4138bea4f003a0e864f93fe996e4dd3bb67b5e92c08ead7")


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


def test_o_baseline_da_2217_e_o_medido_no_ensaio():
    assert SESSOES[2217].baseline == {
        "results": 2310, "cars_in_results": 2310, "result_drivers": 0,
        "laps": 1155, "cars_in_laps": 19}
    assert SESSOES[2217].already_recovered == {
        "results": 19, "cars_in_results": 19, "laps": 1155, "cars_in_laps": 19}


# ── Creating a driver takes two independent yeses ────────────

IDENTIDADE_2217 = ("LEMANSCUP", "01_2017", "06_AUTODROMO DO ALGARVE",
                   "201710220900_Race")


def ate_a_porta_dos_pilotos(identidade: tuple) -> CursorFalso:
    return CursorFalso([[(CLUSTER,)], [identidade]])


def test_a_sessao_que_cria_piloto_recusa_sem_a_flag():
    """
    The allowlist saying a session needs new rows is not the authorisation.

    `drivers` is global: an operator who did not type the flag did not
    agree to add to it, whatever this table declares.
    """
    rows = [{"NUMBER": str(n)} for n in range(1, 20)]
    cur = ate_a_porta_dos_pilotos(IDENTIDADE_2217)
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, 2217, rows, CLUSTER, allow_new_drivers=False)
    assert "--allow-new-drivers" in str(exc.value)
    assert "12" in str(exc.value)


def test_a_flag_na_sessao_que_nao_preve_piloto_e_recusada():
    """
    And the flag alone is not authorisation either.

    2226 was rehearsed four times and created none; a run that arrives
    carrying permission to create is a run whose inputs are not what was
    rehearsed, so it stops instead of using it.
    """
    rows = [{"NUMBER": str(n)} for n in range(1, 29)]
    cur = ate_a_porta_dos_pilotos(IDENTIDADE)
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, 2226, rows, CLUSTER, allow_new_drivers=True)
    assert "nao preve criar piloto" in str(exc.value)


def test_a_fonte_da_2217_tem_de_trazer_19_carros():
    cur = ate_a_porta_dos_pilotos(IDENTIDADE_2217)
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, 2217, [{"NUMBER": "3"}], CLUSTER,
                  allow_new_drivers=True)
    assert "19" in str(exc.value)


def test_a_2217_com_a_identidade_da_2226_e_recusada():
    cur = CursorFalso([[(CLUSTER,)], [IDENTIDADE]])
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, 2217, [], CLUSTER, allow_new_drivers=True)
    assert "01_2017" in str(exc.value)


NUMEROS_2217 = [str(n) for n in range(1, 20)]


def preflight_da_2217(estado: tuple) -> dict:
    """Every answer `preflight` asks for, for a nineteen-car session."""
    voltas = [(numero, [1000 + i]) for i, numero in enumerate(NUMEROS_2217)]
    cur = CursorFalso([
        [(CLUSTER,)],            # cluster_identifier
        [IDENTIDADE_2217],       # session_identity
        [estado],                # session_state
        voltas,                  # lap_cars
        [],                      # removable_cars — nenhum
        [("digest", 5424)],      # other_sessions_digest
        [(2835,)],               # drivers_total
        [(True,)],               # advisory lock
    ])
    rows = [{"NUMBER": n} for n in NUMEROS_2217]
    return preflight(cur, 2217, rows, CLUSTER, allow_new_drivers=True)


def test_a_primeira_execucao_preve_os_doze_pilotos():
    pre = preflight_da_2217((2310, 2310, 0, 1155, 19))
    assert pre["estado_inicial"] == "contaminada"
    assert pre["pilotos_novos_previstos"] == 12


def test_a_reexecucao_preve_zero_pilotos():
    """
    The number comes from where the run starts.

    An already recovered session has the twelve rows already; demanding
    twelve more would roll back a run that did exactly the right thing.
    """
    pre = preflight_da_2217((19, 19, 38, 1155, 19))
    assert pre["estado_inicial"] == "ja recuperada"
    assert pre["pilotos_novos_previstos"] == 0


# ── The backup has to be of the session being recovered ──────

def test_o_backup_capturado_para_outra_sessao_e_recusado(tmp_path):
    """
    An attestation taken for 2226 does not cover a run on 2217.

    The counts would mismatch anyway, but the message that names the session
    is the one an operator can act on at three in the morning.
    """
    dump = com_evidencia(tmp_path)
    backup = backup_evidence(dump, agora=AGORA)
    cur = CursorFalso([[(CLUSTER_ORIGEM,)]])
    with pytest.raises(HistoricalBlocked) as exc:
        preflight(cur, 2217, [], CLUSTER_ORIGEM, backup,
                  allow_new_drivers=True)
    assert "capturada para a sessao 2226" in str(exc.value)


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

ANTES = {"outras_sessoes": {"digest": "abc", "quantas": 5424},
         "drivers_antes": 2845, "pilotos_novos_previstos": 0}


def depois_de(estado: tuple, sem_resultado: int = 0, sem_posicao: int = 0,
              digest: str = "abc", quantas: int = 5424,
              sem_proveniencia: int = 0) -> CursorFalso:
    return CursorFalso([[estado], [(sem_resultado,)], [(sem_posicao,)],
                        [(sem_proveniencia, estado[0])],
                        [(digest, quantas)]])


def test_o_estado_esperado_nao_deixa_falha():
    assert post_invariants(
        depois_de((28, 28, 54, 1554, 28)), 2226, ANTES, 0) == []


@pytest.mark.parametrize("estado,trecho", [
    ((27, 27, 54, 1554, 28), "results = 27"),
    ((28, 28, 54, 1553, 28), "laps = 1553"),
    ((28, 28, 54, 1554, 27), "cars_in_laps = 27"),
    ((28, 28, 0, 1554, 28), "nenhum piloto"),
])
def test_o_estado_errado_vira_falha(estado, trecho):
    falhas = post_invariants(depois_de(estado), 2226, ANTES, 0)
    assert any(trecho in f for f in falhas)


def test_o_carro_com_voltas_sem_resultado_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), sem_resultado=1), 2226, ANTES, 0)
    assert any("sem resultado" in f for f in falhas)


def test_classificado_sem_posicao_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), sem_posicao=2), 2226, ANTES, 0)
    assert any("sem posicao" in f for f in falhas)


def test_outra_sessao_alterada_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), digest="outro"), 2226, ANTES, 0)
    assert any("outra sessao" in f for f in falhas)


def test_sessao_a_mais_ou_a_menos_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), quantas=5425), 2226, ANTES, 0)
    assert any("numero de sessoes" in f for f in falhas)


# ── Provenance is an invariant, not a side effect ────────────

def test_linha_sem_a_url_da_publicacao_e_falha():
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28), sem_proveniencia=3), 2226, ANTES, 0)
    assert any("source_url" in f for f in falhas)


def test_a_url_conferida_e_a_da_allowlist_nao_apenas_nao_nula():
    """
    The check compares against the declared URL, so a row carrying some
    other URL fails exactly as a null row does.
    """
    cur = depois_de((28, 28, 54, 1554, 28))
    post_invariants(cur, 2226, ANTES, 0)
    sql, params = next((s, p) for s, p in cur.executados if "source_url" in s)
    assert params[0] == SESSOES[2226].source_url
    assert "IS DISTINCT FROM" in sql


def test_a_proveniencia_da_2217_e_conferida_contra_a_url_dela():
    antes = {**ANTES, "pilotos_novos_previstos": 12, "drivers_antes": 2823}
    cur = depois_de((19, 19, 38, 1155, 19))
    assert post_invariants(cur, 2217, antes, 12) == []
    _, params = next((s, p) for s, p in cur.executados if "source_url" in s)
    assert params[0] == SESSOES[2217].source_url


# ── Exactly the declared number of drivers ───────────────────

@pytest.mark.parametrize("criados,trecho", [
    (11, "11 pilotos criados"),
    (13, "13 pilotos criados"),
    (0, "0 pilotos criados"),
])
def test_criar_numero_diferente_de_pilotos_e_falha(criados, trecho):
    antes = {**ANTES, "pilotos_novos_previstos": 12, "drivers_antes": 2823}
    falhas = post_invariants(
        depois_de((19, 19, 38, 1155, 19)), 2217, antes, criados)
    assert any(trecho in f for f in falhas)


def test_um_piloto_criado_onde_nenhum_era_previsto_e_falha():
    """2226 declares zero, so one is a refusal and not a rounding."""
    falhas = post_invariants(
        depois_de((28, 28, 54, 1554, 28)), 2226, ANTES, 1)
    assert any("1 pilotos criados, esperado 0" in f for f in falhas)


def test_a_reexecucao_espera_zero_pilotos_novos():
    """
    `MEASURED` on the clean copy: the second run over an already recovered
    session creates none, because the twelve are already there. Comparing
    it against the session's declared twelve rolled back a correct run —
    the number this invariant wants comes from where the run started.
    """
    antes = {**ANTES, "pilotos_novos_previstos": 0, "drivers_antes": 2835}
    assert post_invariants(
        depois_de((19, 19, 38, 1155, 19)), 2217, antes, 0) == []


def test_a_contagem_de_pilotos_nao_consulta_a_tabela_inteira():
    """
    The number comes from the write path, not from `count(*) FROM drivers`.

    `MEASURED` on a disposable copy: one unrelated `INSERT INTO drivers`
    from another connection, while the run was waiting for the operator,
    made the before-and-after difference read thirteen and rolled back a
    correct recovery. `drivers` is a global table; its size is not a
    measurement of what this transaction did.
    """
    antes = {**ANTES, "pilotos_novos_previstos": 12, "drivers_antes": 2823}
    cur = depois_de((19, 19, 38, 1155, 19))
    assert post_invariants(cur, 2217, antes, 12) == []
    assert "FROM drivers" not in cur.sql


# ── Nothing else writes while this runs ──────────────────────

def test_o_lock_cobre_as_tabelas_sem_chave_estrangeira():
    """
    `analytics_car_session` and `analytics_stints` carry a `car_id` with no
    foreign key behind it.

    `MEASURED` on a disposable copy: a row inserted into
    `analytics_car_session` by another connection, after `external_references`
    had checked and while the run waited for the operator, named a car the
    plan then removed. The run reported COMMIT and left the reference
    dangling — nothing complained, and the API's inner join simply drops the
    car from the response.
    """
    assert "analytics_car_session" in hist.LOCKED_TABLES
    assert "analytics_stints" in hist.LOCKED_TABLES


def test_o_lock_cobre_tudo_o_que_a_recuperacao_escreve_ou_depende():
    assert set(hist.LOCKED_TABLES) == {
        "results", "result_drivers", "cars", "drivers", "laps",
        "analytics_car_session", "analytics_stints"}


def test_o_lock_nao_bloqueia_leitores():
    """
    SHARE ROW EXCLUSIVE conflicts with ROW EXCLUSIVE, which every writer
    takes, and not with ACCESS SHARE, which readers take. The API keeps
    serving for the twenty seconds this runs.
    """
    assert hist.WRITE_BLOCKING_LOCK == "SHARE ROW EXCLUSIVE"


def test_o_lock_e_nowait():
    """
    A queued lock request blocks the readers arriving behind it, so waiting
    for a busy table would take the API down with it.
    """
    fonte = Path(hist.__file__).read_text(encoding="utf-8")
    assert "MODE NOWAIT" in fonte


def test_a_transacao_e_repeatable_read():
    """
    At READ COMMITTED every statement takes a new snapshot, so the state
    `preflight` validated would not be the state the write operates on.
    """
    fonte = Path(hist.__file__).read_text(encoding="utf-8")
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" in fonte


def test_o_executor_irmao_ja_fazia_as_duas_coisas():
    """
    This is not a new pattern: `dedup_execute`, in this same package and for
    the same kind of operation, already locked the tables and ran at
    REPEATABLE READ. The production path was the one that did not.
    """
    import database.recovery.dedup_execute as dedup
    irmao = Path(dedup.__file__).read_text(encoding="utf-8")
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" in irmao
    assert dedup.WRITE_BLOCKING_LOCK == hist.WRITE_BLOCKING_LOCK


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

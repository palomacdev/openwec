"""
Continuity of identity across a shared exclusive identifier.

An entry-list identifier found in both a discovery and an existing competition
is where the question starts, not where it ends. The source can move an
identifier from one list to another, and a bare intersection cannot tell that
apart from a list that simply grew or shrank. So identity is only carried
forward when something other than the identifiers corroborates it, and never
when anything contradicts it.

Runs against a disposable database only: it writes.

    DB_HOST=... DB_PORT=... DB_NAME=... DB_USER=... DB_PASSWORD=... \
        python -m pytest tests/schema/test_exclusive_continuity.py
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database"))

psycopg2 = pytest.importorskip("psycopg2")
from competition_matching import match_and_apply  # noqa: E402

DSN = " ".join((
    "host=" + os.environ.get("DB_HOST", "127.0.0.1"),
    "port=" + os.environ.get("DB_PORT", "5440"),
    "dbname=" + os.environ.get("DB_NAME", "p31cont"),
    "user=" + os.environ.get("DB_USER", "testuser"),
    "password=" + os.environ.get("DB_PASSWORD", ""),
))


@pytest.fixture(scope="module")
def cur(disposable_db):
    try:
        conn = psycopg2.connect(DSN, connect_timeout=5)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"sem banco em {db}: {exc}")
    conn.autocommit = True
    c = conn.cursor()
    c.execute("SELECT count(*) FROM information_schema.columns "
              "WHERE table_name='competition_anchors' AND column_name='state'")
    if c.fetchone()[0] != 1:
        pytest.skip("migration 009 nao aplicada")
    yield c
    conn.close()


def _season(cur):
    cur.execute("SELECT id FROM series WHERE key='WEC'")
    series = cur.fetchone()[0]
    cur.execute("INSERT INTO seasons (series_id, raw_id, year, label) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (series_id, raw_id) DO UPDATE SET year=EXCLUDED.year RETURNING id",
                (series, "continuity", 2026, "cont"))
    return cur.fetchone()[0]


@pytest.fixture
def event(cur):
    """A fresh event per test, so one scenario never sees another's anchors."""
    cur.execute("INSERT INTO events (season_id, raw_id, name) VALUES (%s,%s,%s) RETURNING id",
                (_season(cur), f"zz_{uuid.uuid4().hex[:10]}", "continuidade"))
    return cur.fetchone()[0]


def g(ids=(), sessions=(), triples=(), kind="resolved", display="x"):
    return {"ids": set(ids), "sessions": set(sessions), "triples": set(triples),
            "pairs": {(n, t) for n, t, _ in triples},
            "numbers": {n for n, _, _ in triples},
            "classes": {c for _, _, c in triples if c},
            "kind": kind, "display": display}


ALPHA = [("1", "ALPHA", "LMP2"), ("2", "BETA", "LMP2")]
OMEGA = [("9", "OMEGA", "GT3"), ("8", "SIGMA", "GT3")]


def untouched(cur, comp_id):
    cur.execute("SELECT identity_uid::text, evidence_fingerprint, evidence, "
                "(SELECT count(*) FROM competition_anchors a WHERE a.competition_id=%s) "
                "FROM competitions WHERE id=%s", (comp_id, comp_id))
    return cur.fetchone()


# ── reassignment ──────────────────────────────────────────────────────────

def test_reatribuicao_com_populacao_e_sessoes_diferentes(cur, event):
    """The hazard this rule exists for: an identifier moved to another list."""
    first = match_and_apply(cur, event, [g(["900", "901"], ["S1"], ALPHA, display="A")])
    comp_a, uid_a = first["created"][0][1], first["created"][0][2]
    before = untouched(cur, comp_a)

    second = match_and_apply(cur, event, [g(["900", "902"], ["S9"], OMEGA, display="B")])
    assert not second["reused"], "identidade de A nao pode ser herdada por B"
    assert not second["created"], "nada deve ser criado antes da decisao humana"
    assert len(second["review"]) == 1
    assert untouched(cur, comp_a) == before, \
        "uma correspondencia contraditoria nao pode tocar a competicao existente"
    cur.execute("SELECT rationale FROM competition_match_reviews WHERE event_id=%s", (event,))
    assert "contraditoria" in cur.fetchone()[0]


def test_reatribuicao_com_ids_em_subconjunto(cur, event):
    """Containment of the identifiers, on its own, must not confirm identity."""
    first = match_and_apply(cur, event, [g(["900", "901", "902"], ["S1"], ALPHA, display="A")])
    comp_a = first["created"][0][1]
    before = untouched(cur, comp_a)

    # A strict subset of the identifiers, but a different field and different
    # sessions: the subset relation is exactly what must not carry the day.
    second = match_and_apply(cur, event, [g(["900", "901"], ["S9"], OMEGA, display="B")])
    assert not second["reused"]
    assert len(second["review"]) == 1
    assert untouched(cur, comp_a) == before


def test_duas_competicoes_com_ids_parcialmente_sobrepostos(cur, event):
    first = match_and_apply(cur, event, [g(["910", "911"], ["S1"], ALPHA, display="A")])
    comp_a = first["created"][0][1]
    before = untouched(cur, comp_a)
    second = match_and_apply(cur, event, [g(["911", "912"], ["S7"], OMEGA, display="B")])
    assert not second["reused"] and len(second["review"]) == 1
    assert untouched(cur, comp_a) == before


# ── legitimate continuity ─────────────────────────────────────────────────

def test_reimportacao_identica(cur, event):
    first = match_and_apply(cur, event, [g(["920", "921"], ["S1"], ALPHA, display="A")])
    uid, fp = first["created"][0][2], first["created"][0][3]
    second = match_and_apply(cur, event, [g(["920", "921"], ["S1"], ALPHA, display="A")])
    assert second["reused"] and second["reused"][0][2] == uid
    assert second["reused"][0][3] == fp
    assert not second["review"]


def test_ganho_e_perda_legitimos_de_identificadores(cur, event):
    first = match_and_apply(cur, event, [g(["930", "931"], ["S1"], ALPHA, display="A")])
    uid = first["created"][0][2]
    grown = match_and_apply(cur, event,
                            [g(["930", "931", "932"], ["S1"], ALPHA, display="A")])
    assert grown["reused"] and grown["reused"][0][2] == uid
    shrunk = match_and_apply(cur, event, [g(["930"], ["S1"], ALPHA, display="A")])
    assert shrunk["reused"] and shrunk["reused"][0][2] == uid


def test_correcao_parcial_com_populacao_preservada(cur, event):
    """One identifier corrected, same field, same sessions: identity continues."""
    first = match_and_apply(cur, event, [g(["940", "941"], ["S1"], ALPHA, display="A")])
    uid = first["created"][0][2]
    second = match_and_apply(cur, event, [g(["940", "942"], ["S1"], ALPHA, display="A")])
    assert second["reused"] and second["reused"][0][2] == uid
    cur.execute("SELECT state FROM competition_anchors WHERE event_id=%s "
                "AND anchor_kind='ecm_entry_id' AND anchor_value='941'", (event,))
    assert cur.fetchone()[0] == "superseded"


def test_correcao_total_com_populacao_preservada_vai_para_revisao(cur, event):
    """
    Every identifier replaced. The field and the sessions still match, which is
    exactly why it cannot be decided here: a re-issued list for the same
    competition and a new competition with the same field look identical.
    """
    first = match_and_apply(cur, event, [g(["950", "951"], ["S1"], ALPHA, display="A")])
    comp_a = first["created"][0][1]
    before = untouched(cur, comp_a)
    second = match_and_apply(cur, event, [g(["960", "961"], ["S1"], ALPHA, display="A")])
    assert not second["reused"]
    assert not second["created"]
    assert len(second["review"]) == 1
    assert untouched(cur, comp_a) == before


def test_evolucao_legitima_das_sessoes(cur, event):
    first = match_and_apply(cur, event, [g(["970"], ["S1"], ALPHA, display="A")])
    uid = first["created"][0][2]
    second = match_and_apply(cur, event, [g(["970", "971"], ["S1", "S2", "S3"], ALPHA,
                                            display="A")])
    assert second["reused"] and second["reused"][0][2] == uid
    assert second["reused"][0][3] != first["created"][0][3], "fingerprint deve acompanhar"


# ── absent evidence is neither confirmation nor contradiction ─────────────

def test_populacao_ausente_nao_confirma_sozinha(cur, event):
    """
    No population on either side. The sessions still meet, which is a real
    signal, so identity continues — but on the sessions, not on the bare
    identifier intersection.
    """
    first = match_and_apply(cur, event, [g(["980"], ["S1"], (), display="A")])
    uid = first["created"][0][2]
    second = match_and_apply(cur, event, [g(["980", "981"], ["S1"], (), display="A")])
    assert second["reused"] and second["reused"][0][2] == uid


def test_sem_populacao_nem_sessao_em_comum_e_insuficiente(cur, event):
    """Nothing but the identifiers. Absence of evidence confirms nothing."""
    first = match_and_apply(cur, event, [g(["990"], ["S1"], (), display="A")])
    comp_a = first["created"][0][1]
    before = untouched(cur, comp_a)
    second = match_and_apply(cur, event, [g(["990", "991"], ["S9"], (), display="B")])
    assert not second["reused"], "identificador isolado nao confirma identidade historica"
    assert len(second["review"]) == 1
    assert untouched(cur, comp_a) == before


def test_populacao_incompleta_nao_e_contradicao(cur, event):
    """A smaller field is not a different field."""
    first = match_and_apply(cur, event, [g(["995"], ["S1"], ALPHA, display="A")])
    uid = first["created"][0][2]
    second = match_and_apply(cur, event, [g(["995"], ["S1"], ALPHA[:1], display="A")])
    assert second["reused"] and second["reused"][0][2] == uid


# ── conflicting claims ────────────────────────────────────────────────────

def test_duas_descobertas_reivindicando_a_mesma_identidade(cur, event):
    first = match_and_apply(cur, event, [g(["800", "801"], ["S1"], ALPHA, display="A")])
    comp_a = first["created"][0][1]
    before = untouched(cur, comp_a)
    second = match_and_apply(cur, event, [
        g(["800"], ["S1"], ALPHA[:1], display="A1"),
        g(["801"], ["S1"], ALPHA[1:], display="A2"),
    ])
    assert not second["reused"] and not second["created"]
    assert len(second["review"]) == 2
    assert untouched(cur, comp_a) == before


def test_classe_separa_o_mesmo_numero(cur, event):
    """Same car number, same team spelling, different class: not the same field."""
    first = match_and_apply(cur, event,
                            [g(["810"], ["S1"], [("7", "ACME", "LMP2")], display="A")])
    comp_a = first["created"][0][1]
    before = untouched(cur, comp_a)
    second = match_and_apply(cur, event,
                             [g(["810", "811"], ["S9"], [("7", "OTHER", "LMP2")],
                                display="B")])
    assert not second["reused"]
    assert untouched(cur, comp_a) == before

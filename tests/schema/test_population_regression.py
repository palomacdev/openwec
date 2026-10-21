"""
Regressions over a populated database.

Scope: the competitive model only — competitions, entries, their associations,
candidates, anchors and provenance. It asserts nothing about results, laps or
the analytics tables, and must never be run against a persistent database: it
writes to exercise the matcher.

    DB_HOST=... DB_PORT=... DB_NAME=... DB_USER=... DB_PASSWORD=... \
        python -m pytest tests/schema/test_population_regression.py

Connection comes from the same environment variables the rest of the suite
uses. The defaults point at a disposable database and nothing else: the fixture
refuses to run against a persistent one.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database"))

psycopg2 = pytest.importorskip("psycopg2")
from competition_matching import match_and_apply, label_for  # noqa: E402

DEFAULT_DSN = " ".join((
    "host=" + os.environ.get("DB_HOST", "127.0.0.1"),
    "port=" + os.environ.get("DB_PORT", "5440"),
    "dbname=" + os.environ.get("DB_NAME", "p31pop"),
    "user=" + os.environ.get("DB_USER", "testuser"),
    "password=" + os.environ.get("DB_PASSWORD", ""),
))


def pytest_addoption(parser):  # pragma: no cover - pytest hook
    parser.addoption("--dsn", action="store", default=DEFAULT_DSN)


@pytest.fixture(scope="module")
def cur(request, disposable_db):
    dsn = request.config.getoption("--dsn", default=DEFAULT_DSN)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    c = conn.cursor()
    # Competitions in real events only. The other suites in this directory write
    # scratch events named zz_* and would otherwise make an empty database look
    # populated.
    c.execute("""SELECT count(*) FROM competitions c JOIN events e ON e.id = c.event_id
                 WHERE e.raw_id NOT LIKE 'zz!_%' ESCAPE '!'""")
    if c.fetchone()[0] == 0:
        # An external prerequisite, like the driver import above: this suite
        # reads a populated database and has nothing to say about an empty one.
        conn.close()
        pytest.skip("banco nao populado; rode database/populate_competitions.py antes")
    yield c
    conn.close()


def one(cur, sql, *a):
    # Pass None rather than an empty tuple: with any parameter sequence psycopg2
    # runs %-interpolation over the statement, and a LIKE 'zz_%' would be read
    # as a placeholder.
    cur.execute(sql, a or None)
    row = cur.fetchone()
    return row[0] if row else None


def event_id(cur, series, season, event):
    return one(cur, """
        SELECT e.id FROM events e JOIN seasons se ON se.id = e.season_id
        JOIN series sr ON sr.id = se.series_id
        WHERE sr.key = %s AND se.raw_id = %s AND e.raw_id = %s""", series, season, event)


# ── the named events ──────────────────────────────────────────────────────

def test_monza_2018_sessoes_compartilhadas(cur):
    """Two championships ran the Morning and Afternoon sessions together."""
    ev = event_id(cur, "LEMANSCUP", "02_2018", "02_AUTODROMO NAZIONALE DI MONZA")
    assert ev, "evento ausente"
    assert one(cur, "SELECT count(*) FROM competitions WHERE event_id=%s", ev) == 3
    shared = one(cur, """
        SELECT count(*) FROM (
          SELECT sc.session_id FROM session_competitions sc
          JOIN sessions s ON s.id = sc.session_id
          WHERE s.event_id = %s AND sc.competition_id IS NOT NULL
          GROUP BY sc.session_id HAVING count(*) > 1) t""", ev)
    assert shared >= 2, "Morning e Afternoon deveriam pertencer a mais de uma competicao"


def test_monza_2018_entrada_sem_competicao(cur):
    """#065 Graff carries no identifier in a session that belongs to two lists."""
    ev = event_id(cur, "LEMANSCUP", "02_2018", "02_AUTODROMO NAZIONALE DI MONZA")
    assert one(cur, """SELECT count(*) FROM entries
                       WHERE event_id=%s AND competition_id IS NULL AND number='065'""", ev) == 1


def test_bahrein_2025_ids_divergentes(cur):
    """
    #51 is recorded as 105154 in some sessions and 105155 in others, and runs
    the rookie test under 105479.

    It therefore has one entry per competition and no more: two in the halves
    of the main field, which stay apart only because the merge hypothesis
    between them has not been applied, and one in the rookie test, which is a
    genuinely separate entry list. What must never happen is two entries for
    #51 inside a single competition — that would be the old duplication.
    """
    ev = event_id(cur, "WEC", "14_2025", "08_BAHRAIN INTERNATIONAL CIRCUIT")
    assert ev
    ids = one(cur, """
        SELECT count(DISTINCT si.source_entry_id) FROM entries en
        JOIN entry_source_ids si ON si.entry_id = en.id
        WHERE en.event_id = %s AND en.number = '51'""", ev)
    assert ids >= 2, "os dois identificadores deveriam estar registrados como proveniencia"

    cur.execute("""SELECT competition_id, count(*) FROM entries
                   WHERE event_id = %s AND number = '51' GROUP BY 1""", (ev,))
    per_competition = cur.fetchall()
    assert all(n == 1 for _, n in per_competition), \
        f"inscricao duplicada dentro de uma competicao: {per_competition}"
    assert len(per_competition) == 3, per_competition


def test_imola_2025_series_de_apoio_separadas(cur):
    """ELMS plus support championships, none merged into the main competition."""
    ev = event_id(cur, "ELMS", "20_2025", "03_IMOLA")
    assert ev
    assert one(cur, "SELECT count(*) FROM competitions WHERE event_id=%s", ev) >= 4
    assert one(cur, """SELECT count(*) FROM competitions
                       WHERE event_id=%s AND resolution='provisional'""", ev) >= 3


def test_algarve_2018_sem_fusao_das_provas_de_apoio(cur):
    ev = event_id(cur, "ELMS", "13_2018", "06_AUTODROMO DO ALGARVE")
    assert ev
    assert one(cur, "SELECT count(*) FROM competitions WHERE event_id=%s", ev) >= 4


@pytest.mark.parametrize("season,event", [("14_2025", "04_LE MANS"), ("15_2026", "03_LE MANS")])
def test_le_mans_test_day_separado(cur, season, event):
    ev = event_id(cur, "WEC", season, event)
    assert ev
    assert one(cur, """SELECT count(*) FROM competitions
                       WHERE event_id=%s AND resolution='resolved'""", ev) >= 2


def test_eventos_sem_identificador_viram_provisional(cur):
    """No identifier anywhere means provisional, never resolved."""
    bad = one(cur, """
        SELECT count(*) FROM competitions c WHERE c.resolution = 'resolved'
          AND NOT EXISTS (SELECT 1 FROM competition_anchors a
                          WHERE a.competition_id = c.id AND a.exclusivity = 'exclusive')""")
    assert bad == 0, "competicao resolved sem nenhum identificador de origem"


# ── invariants of the model ───────────────────────────────────────────────

def test_rejeicao_de_associacao_artificial(cur):
    """unresolved never carries a competition, and inferred always a candidate."""
    assert one(cur, """SELECT count(*) FROM session_competitions
                       WHERE status='unresolved' AND competition_id IS NOT NULL""") == 0
    assert one(cur, """SELECT count(*) FROM session_competitions
                       WHERE status='inferred' AND justified_by IS NULL""") == 0
    assert one(cur, """
        SELECT count(*) FROM session_competitions sc
        JOIN session_competition_candidates cd ON cd.id = sc.justified_by
        WHERE cd.evaluation <> 'eligible'""") == 0


def test_criterios_nulos_nao_sustentam_elegibilidade(cur):
    """eligible demands time or population; classes and numbers alone never do."""
    assert one(cur, """
        SELECT count(*) FROM session_competition_candidates
        WHERE evaluation = 'eligible'
          AND timestamp_ok IS DISTINCT FROM TRUE
          AND population_ok IS DISTINCT FROM TRUE""") == 0


def test_evidencia_compartilhada_insuficiente_nao_confirma(cur):
    """A competition with no exclusive anchor is never resolved."""
    assert one(cur, """
        SELECT count(*) FROM competitions c WHERE c.resolution <> 'provisional'
          AND NOT EXISTS (SELECT 1 FROM competition_anchors a
                          WHERE a.competition_id = c.id AND a.exclusivity = 'exclusive')""") == 0


def test_hipoteses_nunca_aplicadas(cur):
    """
    Every hypothesis the discovery raised is recorded and none is acted on.
    Scoped to the real events: the identity tests below build scratch events of
    their own and would otherwise inflate the count.
    """
    assert one(cur, """
        SELECT count(*) FROM competition_match_reviews r
        JOIN events e ON e.id = r.event_id
        WHERE r.kind = 'merge_hypothesis' AND e.raw_id NOT LIKE 'zz_%'""") == 77
    assert one(cur, """
        SELECT count(*) FROM competition_match_reviews
        WHERE kind = 'merge_hypothesis' AND resolution_note ILIKE '%fundid%'""") == 0


def test_proveniencia_preservada(cur):
    assert one(cur, "SELECT count(*) FROM entry_team_history") > 0
    assert one(cur, "SELECT count(*) FROM entry_source_ids") > 0
    orphan = one(cur, """
        SELECT count(*) FROM entry_team_history h
        WHERE NOT EXISTS (SELECT 1 FROM entries e WHERE e.id = h.entry_id)""")
    assert orphan == 0


def test_tabelas_historicas_intactas(cur, historical_baseline):
    """
    Nothing in this work writes to results, laps, result_drivers, cars or
    drivers. Asserting zero would be wrong - the CI fixtures seed them on
    purpose - so this compares against the counts taken when the suite started.
    """
    now = {t: one(cur, f"SELECT count(*) FROM {t}")
           for t in historical_baseline}
    assert now == historical_baseline, (
        f"tabelas historicas mudaram: {historical_baseline} -> {now}")


# ── identity under change ─────────────────────────────────────────────────

def _scratch_event(cur):
    cur.execute("SELECT id FROM seasons ORDER BY id LIMIT 1")
    season = cur.fetchone()[0]
    raw = f"zz_{uuid.uuid4().hex[:8]}"
    cur.execute("INSERT INTO events (season_id, raw_id, name) VALUES (%s,%s,%s) RETURNING id",
                (season, raw, "regressao"))
    return cur.fetchone()[0]


def _group(ids=(), sessions=(), pairs=(), kind="resolved", display="x"):
    return {"ids": set(ids), "sessions": set(sessions), "pairs": set(pairs),
            "numbers": {n for n, _ in pairs}, "classes": {"LMP2"},
            "kind": kind, "display": display}


def test_ancoras_obsoletas_nao_confirmam(cur):
    """
    An identifier the source corrected away must stop confirming matches. It is
    kept as history, and a discovery carrying only the old value goes to review
    instead of being recognised automatically.
    """
    ev = _scratch_event(cur)
    g = _group(ids=["900", "901"], sessions=["S1"], pairs=[("1", "ALPHA")])
    first = match_and_apply(cur, ev, [dict(g)])
    uid = first["created"][0][2]

    corrected = _group(ids=["900", "902"], sessions=["S1"], pairs=[("1", "ALPHA")])
    second = match_and_apply(cur, ev, [dict(corrected)])
    assert second["reused"] and second["reused"][0][2] == uid

    state = one(cur, """
        SELECT state FROM competition_anchors
        WHERE event_id=%s AND anchor_kind='ecm_entry_id' AND anchor_value='901'""", ev)
    assert state == "superseded", "o identificador substituido deveria virar historico"
    assert one(cur, """SELECT count(*) FROM competition_anchors
                       WHERE event_id=%s AND anchor_value='901'""", ev) == 1, \
        "a proveniencia da ancora antiga nao pode ser apagada"

    stale_only = _group(ids=["901"], sessions=["S9"], pairs=[("7", "ZETA")])
    third = match_and_apply(cur, ev, [dict(stale_only)])
    assert not third["reused"], "ancora obsoleta nao pode confirmar correspondencia"


def test_fingerprint_evolui_sem_mudar_identidade(cur):
    ev = _scratch_event(cur)
    g = _group(ids=["910"], sessions=["S1"], pairs=[("1", "ALPHA")])
    first = match_and_apply(cur, ev, [dict(g)])
    uid, fp0 = first["created"][0][2], first["created"][0][3]
    grown = _group(ids=["910", "911"], sessions=["S1", "S2"],
                   pairs=[("1", "ALPHA"), ("2", "BETA")])
    second = match_and_apply(cur, ev, [dict(grown)])
    assert second["reused"][0][2] == uid
    assert second["reused"][0][3] != fp0
    assert one(cur, "SELECT identity_uid::text FROM competitions WHERE event_id=%s", ev) == uid


def test_reimportacao_apos_correcao_de_dados(cur):
    """Correcting a team spelling must not create a second entry or identity."""
    ev = _scratch_event(cur)
    g = _group(ids=["920"], sessions=["S1"], pairs=[("5", "CORVETTE")])
    first = match_and_apply(cur, ev, [dict(g)])
    uid = first["created"][0][2]
    fixed = _group(ids=["920"], sessions=["S1"], pairs=[("5", "CORVETTEGM")])
    second = match_and_apply(cur, ev, [dict(fixed)])
    assert second["reused"] and second["reused"][0][2] == uid
    assert one(cur, "SELECT count(*) FROM competitions WHERE event_id=%s", ev) == 1


def test_decisao_humana_preservada(cur):
    """A resolved review survives the matcher raising the same thing again."""
    ev = _scratch_event(cur)
    cur.execute("""
        INSERT INTO competition_match_reviews
          (event_id, kind, discovery_key, evidence, rationale, resolved_at, resolved_by,
           resolution_note)
        VALUES (%s, 'merge_hypothesis', %s, 'e', 'r', NOW(), 'revisor', 'nao fundir')
        RETURNING id""", (ev, "f" * 64))
    rid = cur.fetchone()[0]
    cur.execute("""
        INSERT INTO competition_match_reviews
          (event_id, kind, discovery_key, candidate_ids, evidence, rationale)
        VALUES (%s, 'merge_hypothesis', %s, '{}', 'e2', 'r2')
        ON CONFLICT (event_id, kind, discovery_key)
        DO UPDATE SET rationale = EXCLUDED.rationale, detected_at = NOW()""",
                (ev, "f" * 64))
    cur.execute("SELECT resolved_by, resolution_note FROM competition_match_reviews WHERE id=%s",
                (rid,))
    assert cur.fetchone() == ("revisor", "nao fundir")


def test_label_nao_participa_do_matching(cur):
    """The label is a function of the identity and nothing reads it to match."""
    cur.execute("SELECT identity_uid, label FROM competitions LIMIT 200")
    for uid, label in cur.fetchall():
        assert label == label_for(uid)
    assert one(cur, "SELECT count(DISTINCT label) FROM competitions") == \
           one(cur, "SELECT count(*) FROM competitions")

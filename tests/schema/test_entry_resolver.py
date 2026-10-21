"""
Resolution of a classification row onto a competitive entry.

Two halves. The first exercises the pure logic with no database at all — it is
a function of its arguments, so it can be checked exhaustively and cheaply. The
second runs the read-only adapter against a populated disposable database.

Nothing here writes. The adapter opens read-only connections and the suite has
no INSERT, UPDATE or DELETE of its own.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "database"))
sys.path.insert(0, str(ROOT / "database" / "loader"))

from entry_resolution import (  # noqa: E402
    ClassificationRow, CompetitionView, EntryView, Outcome, SessionView, resolve,
)


# ── the pure logic ────────────────────────────────────────────────────────

def entry(key: str, number: str, car_class: str | None = "LMP2") -> EntryView:
    return EntryView(entry_key=key, number=number, car_class=car_class)


def competition(key: str, entries: list[EntryView],
                source_ids: dict[str, str | set[str]] | None = None) -> CompetitionView:
    """source_ids accepts one entry key or a set, since the data has both."""
    normalised = {
        sid: frozenset({keys} if isinstance(keys, str) else keys)
        for sid, keys in (source_ids or {}).items()
    }
    return CompetitionView(
        competition_key=key,
        entries={(e.number, e.car_class): e for e in entries},
        source_ids=normalised,
    )


def row(number="7", car_class="LMP2", team="ACME", source_id=None):
    return ClassificationRow(number=number, car_class=car_class,
                             team=team, source_id=source_id)


def session(*competitions) -> SessionView:
    return SessionView(session_key="s", competitions=tuple(competitions))


def test_identificador_da_fonte_resolve():
    c = competition("A", [entry("e1", "7")], source_ids={"900": "e1"})
    r = resolve(session(c), row(source_id="900"))
    assert r.outcome is Outcome.RESOLVED_BY_SOURCE_ID
    assert r.entry_key == "e1"
    assert r.resolved


def test_competicao_unica_resolve_sem_identificador():
    c = competition("A", [entry("e1", "7")])
    r = resolve(session(c), row())
    assert r.outcome is Outcome.RESOLVED_BY_SOLE_COMPETITION
    assert r.entry_key == "e1"


def test_sessao_sem_competicao_e_ausencia_de_associacao():
    r = resolve(session(), row())
    assert r.outcome is Outcome.NO_ASSOCIATION
    assert r.entry_key is None
    assert not r.resolved


def test_duas_competicoes_sem_identificador_e_multiplas_candidatas():
    """Monza 2018 #065 Graff: the row is real, its competition is not decided."""
    a = competition("A", [entry("e1", "065", "LMP3")])
    b = competition("B", [entry("e2", "065", "LMP3")])
    r = resolve(session(a, b), row(number="065", car_class="LMP3"))
    assert r.outcome is Outcome.MULTIPLE_CANDIDATES
    assert r.entry_key is None
    assert r.candidates == ("A", "B")


def test_duas_competicoes_com_identificador_resolve():
    """Sharing a session does not make the entry ambiguous when the source names it."""
    a = competition("A", [entry("e1", "51", "HYPERCAR")], source_ids={"105154": "e1"})
    b = competition("B", [entry("e2", "51", "HYPERCAR")], source_ids={"105155": "e2"})
    first = resolve(session(a, b), row(number="51", car_class="HYPERCAR", source_id="105154"))
    second = resolve(session(a, b), row(number="51", car_class="HYPERCAR", source_id="105155"))
    assert (first.entry_key, second.entry_key) == ("e1", "e2")
    assert first.outcome is second.outcome is Outcome.RESOLVED_BY_SOURCE_ID


def test_identificador_que_nomeia_outra_inscricao_e_contraditorio():
    c = competition("A", [entry("e1", "7"), entry("e2", "9")],
                    source_ids={"900": "e2"})
    r = resolve(session(c), row(number="7", source_id="900"))
    assert r.outcome is Outcome.CONTRADICTORY_EVIDENCE
    assert r.entry_key is None
    assert "#9" in r.evidence


def test_identificador_de_outra_sessao_nao_resolve_mas_nao_contradiz():
    """
    An identifier owned by no competition of this session says nothing about
    this session. The row falls through to the session's own competitions.
    """
    c = competition("A", [entry("e1", "7")], source_ids={"900": "e1"})
    r = resolve(session(c), row(number="7", source_id="999"))
    assert r.outcome is Outcome.RESOLVED_BY_SOLE_COMPETITION
    assert r.entry_key == "e1"


def test_competicao_unica_sem_a_inscricao():
    c = competition("A", [entry("e1", "7")])
    r = resolve(session(c), row(number="99"))
    assert r.outcome is Outcome.ENTRY_NOT_FOUND
    assert r.entry_key is None


def test_classe_diferente_nao_resolve_para_outra_inscricao():
    """
    Two entries differing by class are different entries. Guessing between them
    is exactly what this module refuses to do.
    """
    c = competition("A", [entry("e1", "7", "LMP2")])
    r = resolve(session(c), row(number="7", car_class="GT3"))
    assert r.outcome is Outcome.ENTRY_NOT_FOUND


def test_linha_sem_classe_aceita_inscricao_sem_classe():
    """Pre-2013 exports carry no CLASS column at all."""
    c = competition("A", [entry("e1", "7", None)])
    r = resolve(session(c), row(number="7", car_class=None))
    assert r.outcome is Outcome.RESOLVED_BY_SOLE_COMPETITION
    assert r.entry_key == "e1"


def test_linha_sem_classe_nao_cai_em_inscricao_com_classe():
    c = competition("A", [entry("e1", "7", "LMP2")])
    r = resolve(session(c), row(number="7", car_class=None))
    assert r.outcome is Outcome.ENTRY_NOT_FOUND


def test_identificador_ligado_a_duas_inscricoes_e_contraditorio():
    """
    34 identifiers in the corpus name two entries of one competition, from
    three causes: 15 by leading-zero spelling, 12 by genuinely different
    numbers, 7 by one number under two classes. Keeping one quietly would be a
    tie broken in silence, whichever the cause.
    """
    c = competition("A", [entry("e1", "3", "LMP3"), entry("e2", "03", "LMP3")],
                    source_ids={"29660": {"e1", "e2"}})
    r = resolve(session(c), row(number="3", car_class="LMP3", source_id="29660"))
    assert r.outcome is Outcome.CONTRADICTORY_EVIDENCE
    assert r.entry_key is None
    assert "2 inscricoes" in r.evidence


def test_numero_com_zero_a_esquerda_nao_e_normalizado_em_silencio():
    """
    #03 and #3 are stored as different entries. The resolver does not fold them
    together: masking the defect here would hide it from the report that has to
    surface it.
    """
    c = competition("A", [entry("e1", "3", "LMP3")])
    r = resolve(session(c), row(number="03", car_class="LMP3"))
    assert r.outcome is Outcome.ENTRY_NOT_FOUND


def test_identificador_em_duas_competicoes_e_contraditorio():
    a = competition("A", [entry("e1", "7")], source_ids={"900": "e1"})
    b = competition("B", [entry("e2", "7")], source_ids={"900": "e2"})
    r = resolve(session(a, b), row(source_id="900"))
    assert r.outcome is Outcome.CONTRADICTORY_EVIDENCE


def test_resolucao_e_determinista():
    a = competition("A", [entry("e1", "7")], source_ids={"900": "e1"})
    b = competition("B", [entry("e2", "7")])
    view, r = session(a, b), row(source_id="900")
    decisions = {resolve(view, r) for _ in range(50)}
    assert len(decisions) == 1


@pytest.mark.parametrize("outcome,expected", [
    (Outcome.RESOLVED_BY_SOURCE_ID, True),
    (Outcome.RESOLVED_BY_SOLE_COMPETITION, True),
    (Outcome.NO_ASSOCIATION, False),
    (Outcome.MULTIPLE_CANDIDATES, False),
    (Outcome.CONTRADICTORY_EVIDENCE, False),
    (Outcome.ENTRY_NOT_FOUND, False),
])
def test_apenas_dois_desfechos_resolvem(outcome, expected):
    assert outcome.resolved is expected


def test_logica_pura_nao_referencia_banco_nem_disco():
    """
    A static check, and only that.

    It reads the module's source and fails if the names a database or
    filesystem access would need appear in it. That catches the obvious way
    this module could stop being pure; it does not prove the absence of side
    effects, which a text search cannot establish. The tests above exercise
    the behaviour; this one guards the shape.
    """
    import entry_resolution
    source = Path(entry_resolution.__file__).read_text(encoding="utf-8")
    for forbidden in ("psycopg2", "cursor", "execute(", "open(", "Path("):
        assert forbidden not in source, f"logica pura referencia {forbidden}"


# ── the read-only adapter ─────────────────────────────────────────────────

DSN = " ".join((
    "host=" + os.environ.get("DB_HOST", "127.0.0.1"),
    "port=" + os.environ.get("DB_PORT", "5440"),
    "dbname=" + os.environ.get("DB_NAME", "p31pop3"),
    "user=" + os.environ.get("DB_USER", "testuser"),
    "password=" + os.environ.get("DB_PASSWORD", ""),
))


@pytest.fixture(scope="module")
def cur(disposable_db):
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        conn = psycopg2.connect(DSN, connect_timeout=5)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"sem banco: {exc}")
    conn.set_session(readonly=True, autocommit=True)
    c = conn.cursor()
    c.execute("""SELECT count(*) FROM competitions c JOIN events e ON e.id = c.event_id
                 WHERE e.raw_id NOT LIKE 'zz!_%' ESCAPE '!'""")
    if c.fetchone()[0] == 0:
        conn.close()
        pytest.skip("banco nao populado; rode database/populate_competitions.py antes")
    yield c
    conn.close()


def _event(cur, series, season, event):
    cur.execute("""
        SELECT e.id FROM events e
        JOIN seasons se ON se.id = e.season_id
        JOIN series sr ON sr.id = se.series_id
        WHERE sr.key::text = %s AND se.raw_id = %s AND e.raw_id = %s""",
                (series, season, event))
    found = cur.fetchone()
    return found[0] if found else None


def _session(cur, event_id, name):
    cur.execute("SELECT id FROM sessions WHERE event_id = %s AND name = %s",
                (event_id, name))
    found = cur.fetchone()
    return found[0] if found else None


def test_adaptador_carrega_monza_com_duas_competicoes(cur):
    from entry_resolution_db import load_session_view
    event_id = _event(cur, "LEMANSCUP", "02_2018", "02_AUTODROMO NAZIONALE DI MONZA")
    assert event_id
    session_id = _session(cur, event_id, "Morning Session")
    assert session_id
    view = load_session_view(cur, session_id)
    assert len(view.competitions) >= 2, "Morning Session roda mais de uma competicao"


def test_monza_065_graff_fica_sem_decisao(cur):
    """The entry with no identifier in a session that runs two competitions."""
    from entry_resolution_db import load_session_view
    event_id = _event(cur, "LEMANSCUP", "02_2018", "02_AUTODROMO NAZIONALE DI MONZA")
    session_id = _session(cur, event_id, "Morning Session")
    view = load_session_view(cur, session_id)
    decision = resolve(view, row(number="065", car_class="LMP3", team="Graff"))
    assert decision.outcome is Outcome.MULTIPLE_CANDIDATES
    assert decision.entry_key is None
    assert len(decision.candidates) >= 2


def test_bahrein_51_identificadores_divergentes(cur):
    """
    #51 is recorded as 105154 in some sessions and 105155 in others.

    105155 names a single entry and resolves. 105154 is attached to two
    entries of its competition — #50 and #51, genuinely different cars — so
    the resolver refuses it instead of choosing. That is the whole point of
    the outcome being named rather than a row being picked.
    """
    from entry_resolution_db import load_session_view
    event_id = _event(cur, "WEC", "14_2025", "08_BAHRAIN INTERNATIONAL CIRCUIT")
    assert event_id

    cur.execute("""
        SELECT DISTINCT si.source_entry_id
        FROM entry_source_ids si JOIN entries e ON e.id = si.entry_id
        WHERE e.event_id = %s AND e.number IN ('51', '051')
        ORDER BY 1""", (event_id,))
    identifiers = {r[0] for r in cur.fetchall()}
    assert {"105154", "105155"} <= identifiers, sorted(identifiers)

    seen = {}
    for session_id, _name in _sessions_of(cur, event_id):
        view = load_session_view(cur, session_id)
        for identifier in ("105154", "105155"):
            decision = resolve(view, row(number="51", car_class="HYPERCAR",
                                         source_id=identifier))
            if decision.outcome in (Outcome.RESOLVED_BY_SOURCE_ID,
                                    Outcome.CONTRADICTORY_EVIDENCE):
                seen.setdefault(identifier, decision)

    assert seen["105155"].outcome is Outcome.RESOLVED_BY_SOURCE_ID
    assert seen["105155"].entry_key is not None
    assert seen["105154"].outcome is Outcome.CONTRADICTORY_EVIDENCE
    assert seen["105154"].entry_key is None, "nada deve ser escolhido em silencio"
    assert "inscricoes da competicao" in seen["105154"].evidence


def _sessions_of(cur, event_id):
    from entry_resolution_db import load_event_sessions
    return load_event_sessions(cur, event_id)


def test_sessao_sem_associacao_nao_resolve(cur):
    """Le Mans hourly snapshots: no competition, so no entry."""
    from entry_resolution_db import load_session_view
    cur.execute("""
        SELECT sc.session_id FROM session_competitions sc
        JOIN sessions s ON s.id = sc.session_id
        JOIN events e ON e.id = s.event_id
        WHERE sc.status = 'unresolved' AND e.raw_id NOT LIKE 'zz!_%' ESCAPE '!'
        ORDER BY sc.session_id LIMIT 1""")
    found = cur.fetchone()
    if not found:
        pytest.skip("nenhuma sessao sem associacao neste banco")
    view = load_session_view(cur, found[0])
    assert view.competitions == ()
    decision = resolve(view, row(number="7", car_class="LMP1"))
    assert decision.outcome is Outcome.NO_ASSOCIATION


def test_adaptador_e_somente_leitura(cur):
    """
    The connection this suite uses refuses writes.

    That is a property of the connection, demonstrated at runtime. It says
    nothing about how the adapter would behave on a writable connection; what
    keeps it read-only there is that its statements are all SELECT, which the
    static check below guards and review confirms.
    """
    psycopg2 = pytest.importorskip("psycopg2")
    with pytest.raises(psycopg2.Error):
        cur.execute("CREATE TEMP TABLE prova_escrita (x INT)")


def test_adaptador_nao_usa_limit_um():
    """
    Multiplicity must become an outcome, never a row quietly chosen. A LIMIT 1
    in the adapter would hide exactly the cases this work exists to surface.

    This parses the module and inspects the SQL string literals it contains.
    It cannot see a query built at runtime, so it is a guard against the
    change being made carelessly, not a proof that no such query exists.
    """
    import ast
    path = ROOT / "database" / "loader" / "entry_resolution_db.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # Only string literals that are actual SQL, never docstrings or comments:
    # the prose in this module says "no LIMIT 1 anywhere", and a plain text
    # search would match its own explanation.
    docstrings = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))}
    literals = [n.value.lower() for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in docstrings]
    for sql in literals:
        assert "limit 1" not in sql, sql
        assert "min(id)" not in sql.replace(" ", ""), sql
    assert any("select" in sql for sql in literals), "nenhuma consulta inspecionada"

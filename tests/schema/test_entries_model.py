"""
OpenWEC — Competitive entry model (migrations 006 and 008)

SCOPE: schema and index behaviour only.

These tests exercise the tables, the three partial unique indexes and the
ON CONFLICT targets directly. They do NOT exercise the loader: nothing here
imports database.loader, and the loader still writes to cars/results exactly as
before. Loader integration against the new model lands in a later PR.

The loader's write guard must never be exercised against a persistent database.
Overriding it runs a real import; test it only against a throwaway instance.

Proves that competitions/entries behave the way the identity audit requires:

  * re-importing the same classification does not create extra entries
  * entries that the source proves are different stay apart
  * entries the source only spells differently are not split
  * entries nothing can separate are kept apart AND marked, never merged

Run:
    pytest tests/schema/test_entries_model.py -v

Requirements:
    pip install pytest psycopg2-binary
    A database with database/schema.sql and migrations/006 applied.

Connection comes from the same environment variables the API uses
(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD).

Every test runs inside a transaction that is rolled back.
"""
import os

import pytest

psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 not installed")
import psycopg2.errors  # noqa: E402


DB = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "5433")),
    "dbname":   os.environ.get("DB_NAME", "openwec"),
    "user":     os.environ.get("DB_USER", "openwec"),
    "password": os.environ.get("DB_PASSWORD", "openwec"),
}


@pytest.fixture
def conn():
    try:
        c = psycopg2.connect(connect_timeout=5, **DB)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"No database reachable at {DB['host']}:{DB['port']} — {exc}")
    c.autocommit = False
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def cur(conn):
    with conn.cursor() as c:
        yield c


# Explicit ids in a range nothing else uses, for the same reason the
# bootstrap suite does it: independence from sequence state.
_ID = 910_001


def _event(cur):
    """Minimal series → season → event chain. Rolled back with the transaction."""
    cur.execute("SELECT id FROM series WHERE key = 'WEC'")
    series_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO seasons (id, series_id, raw_id, year, label) "
        "VALUES (%s, %s, 'entries-test', 1901, 'entries test')",
        (_ID, series_id),
    )
    cur.execute(
        "INSERT INTO events (id, season_id, raw_id, name, round) "
        "VALUES (%s, %s, 'entries-test', 'Entries Test Event', 1)",
        (_ID, _ID),
    )
    return _ID


def _competition(cur, event_id, label, cid):
    cur.execute(
        "INSERT INTO competitions (id, event_id, label) VALUES (%s, %s, %s) RETURNING id",
        (cid, event_id, label),
    )
    return cur.fetchone()[0]


def _team(cur, name, tid):
    """
    Upsert rather than insert: teams.name is unique globally, so a test must not
    assume the database is empty. tid is accepted for call-site readability but
    the row's own id is what is returned.
    """
    cur.execute(
        "INSERT INTO teams (name) VALUES (%s) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name,),
    )
    return cur.fetchone()[0]


def _upsert_entry(cur, competition_id, number, car_class, team_id, status="inferred"):
    """
    The write the loader will eventually perform. Conflict target is the
    partial index for the regime this row belongs to.
    """
    determined = "AND competition_id IS NOT NULL"
    if status == "ambiguous":
        target = "(competition_id, number, car_class, team_id)"
        where = f"WHERE identity_status = 'ambiguous' {determined}"
    elif car_class is not None:
        target = "(competition_id, number, car_class)"
        where = ("WHERE identity_status <> 'ambiguous' AND car_class IS NOT NULL "
                 f"{determined}")
    else:
        target = "(competition_id, number, team_id)"
        where = ("WHERE identity_status <> 'ambiguous' AND car_class IS NULL "
                 f"{determined}")

    # event_id comes from the competition rather than the call site: 008 made it
    # NOT NULL and tied the pair together with a composite foreign key, so an
    # entry can no longer be written without the event its competition is in.
    cur.execute(
        f"""
        INSERT INTO entries (competition_id, event_id, number, car_class, team_id,
                             identity_status)
        SELECT %s, c.event_id, %s, %s, %s, %s FROM competitions c WHERE c.id = %s
        ON CONFLICT {target} {where}
            DO UPDATE SET team_id = EXCLUDED.team_id
        RETURNING id
        """,
        (competition_id, number, car_class, team_id, status, competition_id),
    )
    return cur.fetchone()[0]


# ── Schema shape ──────────────────────────────────────────────────────────

def test_migration_objects_exist(cur):
    """006 must be applied for anything below to mean something."""
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name IN "
        "('competitions','entries','entry_team_history','entry_source_ids')"
    )
    found = {r[0] for r in cur.fetchall()}
    assert found == {"competitions", "entries", "entry_team_history", "entry_source_ids"}, found


def test_six_uniqueness_regimes_exist(cur):
    """
    One index per regime. 006 had three; 008 split each in two, because the
    scope of an identity is the competition when one is known and the event
    when none is. Keeping competition_id in the key for undetermined entries
    would make every NULL competition in the database one scope, so #065 LMP3
    at Monza would collide with #065 LMP3 at Spa.
    """
    cur.execute(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'entries' "
        "AND (indexname LIKE 'entries_ident%' OR indexname LIKE 'entries_undet%')"
    )
    names = {r[0] for r in cur.fetchall()}
    assert names == {
        "entries_ident_classed",
        "entries_ident_unclassed",
        "entries_ident_ambiguous",
        "entries_undet_classed",
        "entries_undet_unclassed",
        "entries_undet_ambiguous",
    }, names


def test_entry_is_scoped_to_its_event(cur):
    """008: a competition assigned to an entry must belong to the entry's event."""
    ev_a = _event(cur)
    # _event pins a fixed id, so the second event is built alongside it.
    cur.execute(
        "INSERT INTO events (id, season_id, raw_id, name, round) "
        "VALUES (%s, %s, 'entries-test-b', 'Entries Test Event B', 2) RETURNING id",
        (_ID + 1, _ID),
    )
    ev_b = cur.fetchone()[0]
    comp_b = _competition(cur, ev_b, "outro-evento", _ID + 50)
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        cur.execute(
            "INSERT INTO entries (competition_id, event_id, number, car_class, "
            "identity_status) VALUES (%s, %s, '7', 'LMP2', 'resolved')",
            (comp_b, ev_a),
        )


def test_undetermined_entry_needs_no_competition(cur):
    """
    008: competition_id NULL means the entry belongs to the event and its
    competition has not been determined. Monza 2018 #065 Graff is the case.
    Re-importing it must not add a second row.
    """
    ev = _event(cur)
    cur.execute(
        "INSERT INTO entries (competition_id, event_id, number, car_class, "
        "identity_status) VALUES (NULL, %s, '065', 'LMP3', 'inferred') RETURNING id",
        (ev,),
    )
    assert cur.fetchone()[0]
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(
            "INSERT INTO entries (competition_id, event_id, number, car_class, "
            "identity_status) VALUES (NULL, %s, '065', 'LMP3', 'inferred')",
            (ev,),
        )


def test_results_and_laps_untouched_by_this_migration(cur):
    """PR 1 is additive. Nothing may start pointing at entries yet."""
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name IN ('results','laps') AND column_name = 'entry_id'"
    )
    assert cur.fetchall() == [], "results/laps must not reference entries in PR 1"

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'results' AND column_name = 'car_id'"
    )
    assert cur.fetchone() is not None, "results.car_id must still exist"


# ── Idempotency ───────────────────────────────────────────────────────────

def test_reimport_does_not_create_extra_entries(cur):
    """The regression this model exists for: three loads, one entry."""
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    team = _team(cur, "Toyota Racing", _ID)

    ids = [_upsert_entry(cur, comp, "7", "HYPERCAR", team) for _ in range(3)]

    assert len(set(ids)) == 1, f"re-import created {len(set(ids))} entries: {ids}"
    cur.execute("SELECT count(*) FROM entries WHERE competition_id = %s", (comp,))
    assert cur.fetchone()[0] == 1


def test_reimport_is_idempotent_without_car_class(cur):
    """Pre-2013 exports carry no CLASS; the unclassed regime must still hold."""
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    team = _team(cur, "Tomcat", _ID)

    ids = [_upsert_entry(cur, comp, "88", None, team) for _ in range(3)]

    assert len(set(ids)) == 1, ids


def test_ambiguous_reimport_does_not_accumulate(cur):
    """Ambiguity must be preserved, not multiplied."""
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    a = _team(cur, "Algarve Pro Racing", _ID)

    ids = [_upsert_entry(cur, comp, "25", "LMP2", a, status="ambiguous") for _ in range(3)]

    assert len(set(ids)) == 1, ids


# ── Entries the source proves are different ───────────────────────────────

def test_monza_2018_two_competitions_keep_separate_entries(cur):
    """
    Le Mans Cup, Monza 2018: #3 LMP3 is DKR Engineering in one entry list and
    United Autosports in another. Different cars, different drivers. Grouping by
    event alone would merge them; grouping by competition does not.
    """
    ev = _event(cur)
    lmp3 = _competition(cur, ev, "lemans-cup", _ID)
    other = _competition(cur, ev, "support", _ID + 1)
    dkr = _team(cur, "DKR Engineering", _ID)
    united = _team(cur, "United Autosports", _ID + 1)

    a = _upsert_entry(cur, lmp3, "3", "LMP3", dkr, status="resolved")
    b = _upsert_entry(cur, other, "3", "LMP3", united, status="resolved")

    assert a != b, "entries proven distinct were merged"
    # Scoped to this event: the suite also runs against a populated database,
    # where #3 exists in plenty of real competitions.
    cur.execute("SELECT count(*) FROM entries WHERE number = '3' AND event_id = %s", (ev,))
    assert cur.fetchone()[0] == 2


def test_same_number_different_class_stays_separate(cur):
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    t = _team(cur, "Wolfhound", _ID)

    a = _upsert_entry(cur, comp, "25", "LMP2", t)
    b = _upsert_entry(cur, comp, "25", "J", t)

    assert a != b


def test_same_number_different_event_stays_separate(cur):
    """A number reused next season is a different entry."""
    ev = _event(cur)
    cur.execute("SELECT id FROM series WHERE key = 'WEC'")
    series_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO seasons (id, series_id, raw_id, year, label) "
        "VALUES (%s, %s, 'entries-test-2', 1902, 'entries test 2')",
        (_ID + 1, series_id),
    )
    cur.execute(
        "INSERT INTO events (id, season_id, raw_id, name, round) "
        "VALUES (%s, %s, 'entries-test-2', 'Next Season', 1)",
        (_ID + 1, _ID + 1),
    )
    t = _team(cur, "Toyota Racing", _ID)
    a = _upsert_entry(cur, _competition(cur, ev, "main", _ID), "7", "HYPERCAR", t)
    b = _upsert_entry(cur, _competition(cur, _ID + 1, "main", _ID + 1), "7", "HYPERCAR", t)

    assert a != b


def test_le_mans_test_day_is_a_separate_competition(cur):
    """
    Le Mans 2026 issues one entry list for the Test Day (107457–107518) and
    another for the race weekend (107123–107184). Two competitions, and the
    source ids are recorded as evidence rather than used as identity.
    """
    ev = _event(cur)
    weekend = _competition(cur, ev, "main", _ID)
    testday = _competition(cur, ev, "test-day", _ID + 1)
    t = _team(cur, "Toyota Racing", _ID)

    a = _upsert_entry(cur, weekend, "7", "HYPERCAR", t, status="resolved")
    b = _upsert_entry(cur, testday, "7", "HYPERCAR", t, status="resolved")
    assert a != b

    for entry_id, src in ((a, "107133"), (b, "107467")):
        cur.execute(
            "INSERT INTO entry_source_ids (entry_id, source_system, source_entry_id) "
            "VALUES (%s, 'ecm', %s)",
            (entry_id, src),
        )
    cur.execute("SELECT count(*) FROM entry_source_ids WHERE entry_id IN (%s, %s)", (a, b))
    assert cur.fetchone()[0] == 2


# ── Entries the source only spells differently ────────────────────────────

def test_team_name_variant_does_not_split_an_entry(cur):
    """
    "Corvette Racing" and "Corvette Racing - GM" are the same entry. team_id is
    an attribute, so the variant updates the row instead of creating one.
    """
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    v1 = _team(cur, "Corvette Racing", _ID)
    v2 = _team(cur, "Corvette Racing - GM", _ID + 1)

    a = _upsert_entry(cur, comp, "63", "LMGTE PRO", v1)
    b = _upsert_entry(cur, comp, "63", "LMGTE PRO", v2)

    assert a == b, "a spelling variant split the entry"
    cur.execute("SELECT team_id FROM entries WHERE id = %s", (a,))
    assert cur.fetchone()[0] == v2, "latest observed team should win"


def test_team_history_preserves_every_spelling(cur):
    """Decision 7: no silent overwrite. The previous spelling stays on record."""
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    v1 = _team(cur, "Iron Dames", _ID)
    v2 = _team(cur, "Iron Lynx", _ID + 1)
    entry = _upsert_entry(cur, comp, "85", "LMGT3", v1)

    for t in (v1, v2):
        cur.execute(
            "INSERT INTO entry_team_history (entry_id, team_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (entry, t),
        )
    _upsert_entry(cur, comp, "85", "LMGT3", v2)

    cur.execute("SELECT count(*) FROM entry_team_history WHERE entry_id = %s", (entry,))
    assert cur.fetchone()[0] == 2, "earlier team spelling was lost"


# ── Entries nothing can separate ──────────────────────────────────────────

def test_ambiguous_entries_coexist_and_are_marked(cur):
    """
    Imola 2025 #25 LMP2: Algarve Pro in most sessions, M Racing in Qualifying,
    no source identifier on either. Nothing in the data decides it, so both are
    kept, marked, and left for review. No merge.
    """
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    algarve = _team(cur, "Algarve Pro Racing", _ID)
    mracing = _team(cur, "M Racing", _ID + 1)

    a = _upsert_entry(cur, comp, "25", "LMP2", algarve, status="ambiguous")
    b = _upsert_entry(cur, comp, "25", "LMP2", mracing, status="ambiguous")
    cur.execute(
        "UPDATE entries SET review_note = %s WHERE id IN (%s, %s)",
        ("no source entry id; drivers disjoint", a, b),
    )

    assert a != b, "ambiguous entries were merged"
    # Scoped to this competition: the suite also runs against a populated
    # database, where real ambiguous entries exist and a global count would
    # pick them up.
    cur.execute(
        "SELECT count(*) FROM entries WHERE competition_id = %s "
        "AND identity_status = 'ambiguous' AND review_note IS NOT NULL",
        (comp,),
    )
    assert cur.fetchone()[0] == 2


def test_ambiguous_does_not_collide_with_settled_entry(cur):
    """The partial indexes are disjoint: a settled row and an ambiguous row
    with the same natural key must be able to coexist."""
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    t1 = _team(cur, "Team One", _ID)
    t2 = _team(cur, "Team Two", _ID + 1)

    a = _upsert_entry(cur, comp, "11", "LMP3", t1, status="resolved")
    b = _upsert_entry(cur, comp, "11", "LMP3", t2, status="ambiguous")

    assert a != b


def test_settled_duplicate_is_rejected_by_the_index(cur):
    """A straight INSERT that bypasses ON CONFLICT must still be refused."""
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    t = _team(cur, "Toyota Racing", _ID)
    _upsert_entry(cur, comp, "7", "HYPERCAR", t, status="resolved")

    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(
            "INSERT INTO entries (competition_id, event_id, number, car_class, team_id, identity_status) "
            "SELECT %s, c.event_id, '7', 'HYPERCAR', %s, 'resolved' "
            "FROM competitions c WHERE c.id = %s",
            (comp, t, comp),
        )


def test_competition_label_is_unique_per_event(cur):
    ev = _event(cur)
    _competition(cur, ev, "main", _ID)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(
            "INSERT INTO competitions (event_id, label) VALUES (%s, 'main')", (ev,)
        )


# ── Index regimes under stress ────────────────────────────────────────────
#
# Each case below was run against the database before being written down; the
# unclassed one failed and the index was corrected.

def test_unclassed_regime_protects_null_team(cur):
    """
    team_id is nullable. Without NULLS NOT DISTINCT a NULL team compares
    unequal to itself and the unclassed regime protects nothing, so a re-import
    silently duplicates. This failed before the index was fixed.
    """
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)

    cur.execute(
        "INSERT INTO entries (competition_id, event_id, number, car_class, team_id, identity_status) "
        "SELECT %s, c.event_id, '9', NULL, NULL, 'inferred' "
        "FROM competitions c WHERE c.id = %s",
        (comp, comp),
    )
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(
            "INSERT INTO entries (competition_id, event_id, number, car_class, team_id, identity_status) "
            "SELECT %s, c.event_id, '9', NULL, NULL, 'inferred' "
            "FROM competitions c WHERE c.id = %s",
            (comp, comp),
        )


def test_two_ambiguous_rows_with_identical_attributes_cannot_coexist(cur):
    """
    Documented limitation. When number, class AND team all match there is
    nothing left in the model to tell two entries apart, so the ambiguous index
    rejects the second. Keeping re-import idempotent is worth more than
    representing a case the data cannot describe.
    """
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    t = _team(cur, "Rev Team", _ID)

    _upsert_entry(cur, comp, "5", "LMP2", t, status="ambiguous")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute(
            "INSERT INTO entries (competition_id, event_id, number, car_class, team_id, identity_status) "
            "SELECT %s, c.event_id, '5', 'LMP2', %s, 'ambiguous' "
            "FROM competitions c WHERE c.id = %s",
            (comp, t, comp),
        )


def test_resolving_both_sides_of_an_ambiguity_is_refused(cur):
    """
    Promoting two ambiguous rows to 'resolved' would assert they are the same
    identity. The classed index refuses the second UPDATE, so a reviewer has to
    merge or re-key rather than flip a flag.
    """
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    a = _team(cur, "Team A", _ID)
    b = _team(cur, "Team B", _ID + 1)

    one = _upsert_entry(cur, comp, "7", "HYPERCAR", a, status="ambiguous")
    two = _upsert_entry(cur, comp, "7", "HYPERCAR", b, status="ambiguous")

    cur.execute("UPDATE entries SET identity_status = 'resolved' WHERE id = %s", (one,))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        cur.execute("UPDATE entries SET identity_status = 'resolved' WHERE id = %s", (two,))


def test_demoting_to_ambiguous_frees_the_classed_regime(cur):
    """The regimes are disjoint, so the transition back must work."""
    ev = _event(cur)
    comp = _competition(cur, ev, "main", _ID)
    a = _team(cur, "Team A", _ID)
    b = _team(cur, "Team B", _ID + 1)

    one = _upsert_entry(cur, comp, "7", "HYPERCAR", a, status="resolved")
    cur.execute("UPDATE entries SET identity_status = 'ambiguous' WHERE id = %s", (one,))
    two = _upsert_entry(cur, comp, "7", "HYPERCAR", b, status="resolved")

    assert one != two

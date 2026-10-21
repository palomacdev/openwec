"""
Read-only adapter between the database and the pure resolver.

Every statement here is a SELECT. Nothing in this module writes, and nothing
decides: it loads whole sets and hands them to entry_resolution.resolve, which
is where the decisions live. That split is deliberate — the decision logic is
testable without a database, and the database access has no judgement in it to
hide.

There is no LIMIT 1 anywhere. Where several rows could match, all of them are
loaded and the multiplicity becomes an outcome the resolver names.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from entry_resolution import (  # noqa: E402
    ClassificationRow, CompetitionView, EntryView, SessionView, resolve,
)

__all__ = ["load_session_view", "load_event_sessions", "resolve",
           "ClassificationRow", "SessionView"]


def _entry_key(entry_id: int) -> str:
    """A readable, stable handle for an entry inside one report run."""
    return f"entry:{entry_id}"


def _competition_key(identity_uid: str) -> str:
    """
    Competitions are named by their persistent identity, never by a row id.

    A report keyed on row ids would change meaning between databases; keyed on
    identity_uid it means the same thing wherever it is read.
    """
    return f"comp:{identity_uid}"


def load_session_view(cur, session_id: int) -> SessionView:
    """
    Everything the resolver needs about one session.

    Only competitions the session is actually associated with are loaded, and
    only through associations that name a competition: an 'unresolved' row
    means the association is pending, so it contributes no candidate.
    """
    cur.execute(
        """
        SELECT c.id, c.identity_uid::text
        FROM session_competitions sc
        JOIN competitions c ON c.id = sc.competition_id
        WHERE sc.session_id = %s AND sc.competition_id IS NOT NULL
        ORDER BY c.identity_uid
        """,
        (session_id,),
    )
    competitions = cur.fetchall()
    if not competitions:
        return SessionView(session_key=f"session:{session_id}", competitions=())

    comp_ids = [row[0] for row in competitions]

    cur.execute(
        """
        SELECT competition_id, id, number, car_class
        FROM entries
        WHERE competition_id = ANY(%s)
        ORDER BY competition_id, number, car_class NULLS FIRST, id
        """,
        (comp_ids,),
    )
    entries_by_comp: dict[int, dict[tuple[str, str | None], EntryView]] = {
        cid: {} for cid in comp_ids
    }
    entry_key_by_id: dict[int, str] = {}
    for competition_id, entry_id, number, car_class in cur.fetchall():
        key = _entry_key(entry_id)
        entry_key_by_id[entry_id] = key
        entries_by_comp[competition_id][(number, car_class)] = EntryView(
            entry_key=key, number=number, car_class=car_class)

    cur.execute(
        """
        SELECT e.competition_id, si.source_entry_id, si.entry_id
        FROM entry_source_ids si
        JOIN entries e ON e.id = si.entry_id
        WHERE e.competition_id = ANY(%s)
        ORDER BY e.competition_id, si.source_entry_id, si.entry_id
        """,
        (comp_ids,),
    )
    # Every entry an identifier names is kept. 34 identifiers in the corpus
    # name more than one entry of a competition, and not for one reason: 15 are
    # the same number spelled #03 and #3, 12 are different numbers outright,
    # and 7 are one number under two classes. The resolver reports the
    # contradiction; collapsing the set here would be a decision taken where no
    # decision belongs.
    accumulating: dict[int, dict[str, set[str]]] = {cid: {} for cid in comp_ids}
    for competition_id, source_entry_id, entry_id in cur.fetchall():
        accumulating[competition_id].setdefault(source_entry_id, set()).add(
            entry_key_by_id[entry_id])
    sources_by_comp = {
        cid: {sid: frozenset(keys) for sid, keys in sorted(by_source.items())}
        for cid, by_source in accumulating.items()
    }

    views = tuple(
        CompetitionView(
            competition_key=_competition_key(identity_uid),
            entries=entries_by_comp[competition_id],
            source_ids=sources_by_comp[competition_id],
        )
        for competition_id, identity_uid in competitions
    )
    return SessionView(session_key=f"session:{session_id}", competitions=views)


def load_event_sessions(cur, event_id: int) -> list[tuple[int, str]]:
    """(session_id, name) of one event, in a stable order."""
    cur.execute(
        "SELECT id, name FROM sessions WHERE event_id = %s ORDER BY name, id",
        (event_id,),
    )
    return [(row[0], row[1]) for row in cur.fetchall()]


def load_events(cur) -> list[tuple[int, str]]:
    """(event_id, qualified raw key) for every event, in a stable order."""
    cur.execute(
        """
        SELECT e.id, sr.key::text || '/' || se.raw_id || '/' || e.raw_id
        FROM events e
        JOIN seasons se ON se.id = e.season_id
        JOIN series sr ON sr.id = se.series_id
        ORDER BY 2, e.id
        """
    )
    return [(row[0], row[1]) for row in cur.fetchall()]

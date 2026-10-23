"""
Which session row, and which car row, a source file is talking about.

Both loaders used to answer this with `LIMIT 1` and no `ORDER BY`, against
queries that genuinely match several rows. That is not a tie broken badly —
it is a tie not noticed. Measured on the development corpus:

  sessions  630 of the 2.732 classification files the loader selects match
            more than one session row, because `sessions` is unique by
            (event_id, raw_id) while the lookup compared `name`. The raw/
            tree names a session folder "Practice 1"; the catalogue holds
            five "Practice 1" rows for Watkins Glen 2016, one per
            championship, each with its own raw_id and its own timestamp.

  cars      1.235 (session, number) pairs hold more than one car identity,
            and 687 (session, identity) pairs already have laps spread over
            several car_id. A number is not a car.

This module answers both questions without ever picking one of several
candidates. The decisions are pure functions over candidate lists; the
database functions below only load the candidates and name the outcome.

What counts as proof
--------------------
A session is resolved when exactly one row can match, either because there is
only one, or because the raw/ path carries the championship folder and exactly
one candidate's source_url is in that championship. The championship is the
discriminator the catalogue itself uses, so this is the published identity,
not a heuristic.

A car is resolved when every candidate row for that number in that session
carries the same eight attributes `upsert_car` writes — that is, when they are
the same car under the loader's own identity rule, duplicated by past runs.
The lowest id is then returned: a canonical representative among rows proven
identical, stable across runs, not a choice between different cars.

Where the source of laps states a team, class or group, those narrow a number
that several identities answer to. Four of the eight attributes are simply not
in a lap file — it carries no VEHICLE, TIRES or IMSA identifier — so narrowing
can confirm which identity a row belongs to but can never reconstruct one.

What counts as a contradiction
-----------------------------
Being the only candidate is not proof. Where the source states an attribute
and the stored row states a different one, the two describe different things
and the association is refused, not taken because nothing else was on offer.

Three cases stay apart, because they are three different facts:

  absent      the source states nothing — no obstacle to resolving
  compatible  both state a value and the values agree — resolution allowed
  conflicting both state a value and the values differ — refused and named

A stored NULL is absence, not disagreement: it blocks nothing, and it proves
nothing either, so it can never narrow a choice between candidates.

Comparison folds case and punctuation and nothing else; see `comparable`.
That is also why a spelling cannot break a tie: having declared "Idec Sport"
and "IDEC Sport" the same rendering of one name, the loader cannot then use
that same difference to pick between two cars. Measured: 1.058 of the 1.235
(session, number) pairs with competing identities differ only in team
spelling, and they are now refused rather than decided by punctuation.

What stays unresolved
---------------------
145 of the 2.732 classification files, all ELMS and WEC: those series' raw/
tree has no championship level, so two sessions of different championships
collapse onto one folder name and nothing in the file says which is which.

1.058 (session, number) pairs whose competing identities differ only in how
the team name is written, plus those whose identities agree on everything a
lap file could state. They are refused.

Nothing here falls back. A multiplicity is an outcome, never a quiet choice,
and neither is a contradiction.
"""
from __future__ import annotations

import enum
import re
import weakref
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote


class SessionOutcome(enum.Enum):
    SOLE_CANDIDATE = "sole_candidate"
    BY_CHAMPIONSHIP = "by_championship"
    NOT_FOUND = "not_found"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    CONTRADICTED_BY_CHAMPIONSHIP = "contradicted_by_championship"

    @property
    def resolved(self) -> bool:
        return self in (SessionOutcome.SOLE_CANDIDATE,
                        SessionOutcome.BY_CHAMPIONSHIP)


class CarOutcome(enum.Enum):
    SOLE_IDENTITY = "sole_identity"
    BY_SOURCE_ATTRIBUTES = "by_source_attributes"
    NO_CLASSIFICATION = "no_classification"
    NOT_IN_CLASSIFICATION = "not_in_classification"
    MULTIPLE_IDENTITIES = "multiple_identities"
    CONTRADICTED_BY_SOURCE = "contradicted_by_source"

    @property
    def resolved(self) -> bool:
        return self in (CarOutcome.SOLE_IDENTITY,
                        CarOutcome.BY_SOURCE_ATTRIBUTES)


class DriverOutcome(enum.Enum):
    EXACT_NAME = "exact_name"
    BY_NORMALIZED_NAME = "by_normalized_name"
    NO_NAME = "no_name"
    NOT_FOUND = "not_found"
    MULTIPLE_CANDIDATES = "multiple_candidates"

    @property
    def resolved(self) -> bool:
        return self in (DriverOutcome.EXACT_NAME,
                        DriverOutcome.BY_NORMALIZED_NAME)


@dataclass(frozen=True)
class SessionCandidate:
    session_id: int
    raw_id: str
    source_url: str | None


@dataclass(frozen=True)
class DriverCandidate:
    driver_id: int
    first_name: str | None
    last_name: str | None


@dataclass(frozen=True)
class CarCandidate:
    car_id: int
    identity: str              # the eight attributes upsert_car writes, as text
    team_name: str | None
    car_class: str | None
    car_group: str | None


# ── Pure decisions ────────────────────────────────────────────

# .../Results/<season>/<event>/<championship>/<session>/<file>
_CHAMPIONSHIP_IN_URL = re.compile(r"/Results/[^/]+/[^/]+/([^/]+)/", re.IGNORECASE)


def championship_of(source_url: str | None) -> str | None:
    """The championship segment of a catalogue URL, percent-decoding undone."""
    match = _CHAMPIONSHIP_IN_URL.search(source_url or "")
    return unquote(match.group(1)) if match else None


def in_championship(source_url: str | None, championship_raw: str) -> bool:
    """
    Whether a candidate belongs to the championship this folder names.

    Prefix, not equality: the corpus holds the folder as the filesystem could
    store it, and long names arrive truncated — "01_IMSA WeatherTech SportsCar
    Championsh" for "01_IMSA WeatherTech SportsCar Championship". A truncated
    folder name still identifies the championship it was cut from, and no two
    championships of one event in this corpus share a prefix.
    """
    champ = championship_of(source_url)
    return champ is not None and champ.startswith(championship_raw)


def choose_session(
    candidates: list[SessionCandidate],
    championship_raw: str | None = None,
) -> tuple[int | None, SessionOutcome]:
    """
    Being the only candidate is not proof when the path says otherwise.

    Measured on the corpus: 8 files under 01_IMSA WeatherTech SportsCar
    Championsh match exactly one session row, and that row belongs to the
    Pilot Challenge or the Continental Tire Challenge. The WeatherTech row
    is in the catalogue and was never created in the database, so accepting
    the sole candidate files WeatherTech data under another championship.
    """
    if not candidates:
        return None, SessionOutcome.NOT_FOUND

    if championship_raw:
        matching = [c for c in candidates
                    if in_championship(c.source_url, championship_raw)]
        if len(matching) == 1:
            return matching[0].session_id, SessionOutcome.BY_CHAMPIONSHIP
        if len(matching) > 1:
            return None, SessionOutcome.MULTIPLE_CANDIDATES

        # Nothing matched. That is a contradiction only where every candidate
        # states a championship: one that states none disagrees with nothing.
        if all(championship_of(c.source_url) is not None for c in candidates):
            return None, SessionOutcome.CONTRADICTED_BY_CHAMPIONSHIP

    if len(candidates) == 1:
        return candidates[0].session_id, SessionOutcome.SOLE_CANDIDATE
    return None, SessionOutcome.MULTIPLE_CANDIDATES


def _stated(value: str | None) -> str | None:
    """An attribute the source did not state is not an attribute to match on."""
    value = (value or "").strip()
    return value or None


def comparable(value: str) -> str:
    """
    The form two renderings of one name are compared in.

    Case and punctuation only. Measured over the 3.650 pairs where a lap file
    and a classification disagree on an attribute: folding case and
    punctuation reconciles 3.630 of them — "IDEC Sport" against "Idec Sport",
    the same publisher writing one team two ways — and leaves 20. Folding
    accents as well reconciles no further pair, so it is not done: an accent
    is a letter, and dropping it would widen the rule for nothing.

    Nothing else is folded. No team or class alias is built in: two names
    that differ in their letters stay different names.
    """
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _conflicts(stated: str, stored: str | None) -> bool:
    """
    A conflict takes two statements. A stored NULL is the absence of one.
    """
    return stored is not None and comparable(stated) != comparable(stored)


def _confirms(stated: str, stored: str | None) -> bool:
    """
    A stored NULL does not disagree, and it does not agree either: it cannot
    tell two candidates apart, so it never narrows.
    """
    return stored is not None and comparable(stated) == comparable(stored)


def choose_car(
    candidates: list[CarCandidate],
    team_name: str | None = None,
    car_class: str | None = None,
    car_group: str | None = None,
) -> tuple[int | None, CarOutcome]:
    """
    Decide among the cars classified under one number in one session.

    The caller tells NO_CLASSIFICATION from NOT_IN_CLASSIFICATION, because
    only the database knows whether the session has a classification at all.
    """
    if not candidates:
        return None, CarOutcome.NOT_IN_CLASSIFICATION

    wanted = {
        "team_name": _stated(team_name),
        "car_class": _stated(car_class),
        "car_group": _stated(car_group),
    }
    stated = {k: v for k, v in wanted.items() if v is not None}

    identities = {c.identity for c in candidates}
    if len(identities) == 1:
        # Candidates of one identity carry one set of attributes, so any of
        # them answers for all. Being the only car under this number does not
        # make it this row's car when the row describes a different one.
        only = candidates[0]
        if any(_conflicts(v, getattr(only, k)) for k, v in stated.items()):
            return None, CarOutcome.CONTRADICTED_BY_SOURCE
        return min(c.car_id for c in candidates), CarOutcome.SOLE_IDENTITY

    if stated:
        narrowed = [c for c in candidates
                    if all(_confirms(v, getattr(c, k)) for k, v in stated.items())]
        narrowed_identities = {c.identity for c in narrowed}
        if len(narrowed_identities) == 1:
            return min(c.car_id for c in narrowed), CarOutcome.BY_SOURCE_ATTRIBUTES
        if not narrowed and all(
            any(_conflicts(v, getattr(c, k)) for k, v in stated.items())
            for c in candidates
        ):
            # Every candidate states something this row disagrees with. The
            # row is not one of them, which is not the same as not knowing
            # which one it is.
            return None, CarOutcome.CONTRADICTED_BY_SOURCE

    return None, CarOutcome.MULTIPLE_IDENTITIES


# ── Database access: loads candidates, decides nothing ────────

_SESSION_CANDIDATES = """
    SELECT s.id, s.raw_id, s.source_url
    FROM sessions s
    JOIN events e   ON e.id  = s.event_id
    JOIN seasons se ON se.id = e.season_id
    JOIN series sr  ON sr.id = se.series_id
    WHERE sr.key::text = %s
      AND se.raw_id    = %s
      AND e.raw_id     = %s
      AND s.name       = %s
    ORDER BY s.id
"""

_CAR_CANDIDATES = """
    SELECT c.id,
           (c.number, c.team_id, c.vehicle, c.car_class, c.car_group,
            c.tires, c.imsa_car_id, c.imsa_team_id)::text,
           t.name, c.car_class, c.car_group
    FROM results r
    JOIN cars c      ON c.id = r.car_id
    LEFT JOIN teams t ON t.id = c.team_id
    WHERE r.session_id = %s AND c.number = %s
    ORDER BY c.id
"""


def resolve_session_id(
    cur,
    series_key: str,
    season_raw: str,
    event_raw: str,
    session_name: str,
    championship_raw: str | None = None,
) -> tuple[int | None, SessionOutcome]:
    cur.execute(_SESSION_CANDIDATES,
                (series_key, season_raw, event_raw, session_name))
    candidates = [SessionCandidate(*row) for row in cur.fetchall()]
    return choose_session(candidates, championship_raw)


def full_name_key(first_name: str | None, last_name: str | None) -> str:
    """
    The form two renderings of one driver's name are compared in.

    First and last are joined before folding, because the boundary between
    them is an artefact of the parser, not of the source:
    `split_wec_driver_name` cuts at the last space, so "Giedo VAN DER GARDE"
    becomes ("Giedo VAN DER", "GARDE") while another path produced
    ("Giedo", "Van Der Garde"). Comparing the two fields separately would
    call one driver two people.

    Folding is `comparable`: case and punctuation, nothing else. "Nic" does
    not become "Nicolas" here, and no nickname table exists.
    """
    return comparable((first_name or "") + (last_name or ""))


def choose_driver(
    candidates: list[DriverCandidate],
    first_name: str | None,
    last_name: str | None,
) -> tuple[int | None, DriverOutcome]:
    """
    Which driver row a source name refers to.

    The exact spelling wins when it exists — `drivers` is unique on
    (first_name, last_name), so at most one row can carry it, and taking it
    preserves exactly what the loader did before.

    Otherwise the normalised full name decides, and only when **one** row
    carries it. Several rows carrying it is the shape of the duplication
    measured in this table — 1.079 names held by 2.219 rows — and picking
    one of them would be picking by id, which decides nothing.
    """
    if not _stated(first_name) and not _stated(last_name):
        return None, DriverOutcome.NO_NAME

    exact = [c for c in candidates
             if (c.first_name or "") == (first_name or "")
             and (c.last_name or "") == (last_name or "")]
    if exact:
        return min(c.driver_id for c in exact), DriverOutcome.EXACT_NAME

    chave = full_name_key(first_name, last_name)
    iguais = [c for c in candidates
              if full_name_key(c.first_name, c.last_name) == chave]
    if len(iguais) == 1:
        return iguais[0].driver_id, DriverOutcome.BY_NORMALIZED_NAME
    if len(iguais) > 1:
        return None, DriverOutcome.MULTIPLE_CANDIDATES
    return None, DriverOutcome.NOT_FOUND


# The whole table is 2.845 rows, and the normalised form cannot be indexed
# without a migration, so it is folded instead of consulted per lap: the
# fallback would otherwise scan the table for each of the 432.174 laps that
# have no exact match.
#
# One fold per connection, held weakly. That is what bounds its life:
#
#   between databases   a connection cannot change database, so an index
#                       can never answer for a different one
#   between runs        a run opens its own connection and reads the table
#                       again; `forget_driver_index` covers a run that
#                       reuses one, and the loaders call it themselves
#   between connections two connections never share a fold, so one does not
#                       serve rows another has since written
#   within a run        `remember_driver` keeps rows this connection just
#                       wrote visible to its own later lookups
#
# The entry disappears when the connection is collected, so nothing has to
# be invalidated by hand for the common case.
_DRIVER_INDEX: "weakref.WeakKeyDictionary[Any, dict[str, list[DriverCandidate]]]" = (
    weakref.WeakKeyDictionary()
)


def forget_driver_index(cur=None) -> None:
    """
    Drop the fold for this cursor's connection, or for all of them.

    A loader calls this once when its run begins, so a second run on a
    connection that is still open reads the table again instead of trusting
    what another connection may have changed meanwhile.
    """
    if cur is None:
        _DRIVER_INDEX.clear()
    else:
        _DRIVER_INDEX.pop(cur.connection, None)


def remember_driver(cur, candidate: DriverCandidate) -> None:
    """Keep a row this connection has just written visible to its lookups."""
    indice = _DRIVER_INDEX.get(cur.connection)
    if indice is None:
        return
    indice.setdefault(
        full_name_key(candidate.first_name, candidate.last_name), []
    ).append(candidate)


def driver_index(cur) -> dict[str, list[DriverCandidate]]:
    conexao = cur.connection
    indice = _DRIVER_INDEX.get(conexao)
    if indice is None:
        cur.execute("SELECT id, first_name, last_name FROM drivers")
        indice = {}
        for row in cur.fetchall():
            c = DriverCandidate(*row)
            indice.setdefault(full_name_key(c.first_name, c.last_name), []).append(c)
        _DRIVER_INDEX[conexao] = indice
    return indice


def resolve_driver_id(
    cur,
    first_name: str | None,
    last_name: str | None,
) -> tuple[int | None, DriverOutcome]:
    if not _stated(first_name) and not _stated(last_name):
        return None, DriverOutcome.NO_NAME
    candidatos = driver_index(cur).get(full_name_key(first_name, last_name), [])
    return choose_driver(candidatos, first_name, last_name)


def resolve_car_id(
    cur,
    session_id: int,
    number: str,
    team_name: str | None = None,
    car_class: str | None = None,
    car_group: str | None = None,
) -> tuple[int | None, CarOutcome]:
    cur.execute(_CAR_CANDIDATES, (session_id, number))
    candidates = [CarCandidate(*row) for row in cur.fetchall()]

    car_id, outcome = choose_car(candidates, team_name, car_class, car_group)

    if outcome is CarOutcome.NOT_IN_CLASSIFICATION:
        # A session with no classification at all is a different fact from a
        # car missing out of one, and the two call for different responses.
        cur.execute("SELECT EXISTS (SELECT 1 FROM results WHERE session_id = %s)",
                    (session_id,))
        if not cur.fetchone()[0]:
            return None, CarOutcome.NO_CLASSIFICATION

    return car_id, outcome

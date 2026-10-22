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

What stays unresolved
---------------------
145 of the 2.732 classification files, all ELMS and WEC: those series' raw/
tree has no championship level, so two sessions of different championships
collapse onto one folder name and nothing in the file says which is which.

98 (session, number) pairs whose competing identities agree on team spelling,
class and group — everything a lap file could state. They are refused.

Nothing here falls back. A multiplicity is an outcome, never a quiet choice.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from urllib.parse import unquote


class SessionOutcome(enum.Enum):
    SOLE_CANDIDATE = "sole_candidate"
    BY_CHAMPIONSHIP = "by_championship"
    NOT_FOUND = "not_found"
    MULTIPLE_CANDIDATES = "multiple_candidates"

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

    @property
    def resolved(self) -> bool:
        return self in (CarOutcome.SOLE_IDENTITY,
                        CarOutcome.BY_SOURCE_ATTRIBUTES)


@dataclass(frozen=True)
class SessionCandidate:
    session_id: int
    raw_id: str
    source_url: str | None


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
    if not candidates:
        return None, SessionOutcome.NOT_FOUND
    if len(candidates) == 1:
        return candidates[0].session_id, SessionOutcome.SOLE_CANDIDATE

    if championship_raw:
        narrowed = [c for c in candidates
                    if in_championship(c.source_url, championship_raw)]
        if len(narrowed) == 1:
            return narrowed[0].session_id, SessionOutcome.BY_CHAMPIONSHIP

    return None, SessionOutcome.MULTIPLE_CANDIDATES


def _stated(value: str | None) -> str | None:
    """An attribute the source did not state is not an attribute to match on."""
    value = (value or "").strip()
    return value or None


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

    identities = {c.identity for c in candidates}
    if len(identities) == 1:
        return min(c.car_id for c in candidates), CarOutcome.SOLE_IDENTITY

    wanted = {
        "team_name": _stated(team_name),
        "car_class": _stated(car_class),
        "car_group": _stated(car_group),
    }
    stated = {k: v for k, v in wanted.items() if v is not None}
    if stated:
        narrowed = [c for c in candidates
                    if all(getattr(c, k) == v for k, v in stated.items())]
        if narrowed and len({c.identity for c in narrowed}) == 1:
            return min(c.car_id for c in narrowed), CarOutcome.BY_SOURCE_ATTRIBUTES

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

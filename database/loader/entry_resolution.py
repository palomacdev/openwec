"""
Deciding which competitive entry a classification row belongs to.

Pure: no database, no filesystem, no clock, no randomness. Everything it needs
arrives as arguments, and the same arguments always produce the same decision.
The database adapter lives in entry_resolution_db.py.

Why a row needs deciding at all
-------------------------------
A classification row says "car #51, LMGT3, Ferrari AF Corse, position 3". None
of that names an entry. Within one session `number` is unique — measured, zero
collisions across 3,240 sessions — but a session is not the scope of an entry:
Monza 2018 runs two championships in the same Morning session, and the same
number in two competitions of one event is two different entries.

So `(session_id, number)` is not an identity. The legacy data shows what
happens when it is treated as one: at Imola 2025 the session row named
"Race 1" holds #115 as GPA RACING over 12 laps and as DC RACING over 8 laps —
two entries, one key.

The outcomes
------------
Four things must stay distinguishable, because they call for different
responses and only one of them is a defect:

  RESOLVED_BY_SOURCE_ID      the source printed an entry-list identifier and it
                             belongs to a competition this session runs
  RESOLVED_BY_SOLE_COMPETITION
                             the session runs exactly one competition, so the
                             entry is the one with this number and class in it
  NO_ASSOCIATION             the session belongs to no competition at all —
                             absence of evidence, not a conflict
  MULTIPLE_CANDIDATES        the session runs several competitions and nothing
                             in the row says which — the row is real, its
                             competition is not determined
  SOURCE_ID_OUT_OF_SCOPE     the row carries an identifier owned by a
                             competition of this event that this session does
                             not run — the row probably belongs elsewhere
  CONTRADICTORY_EVIDENCE     the identifier names an entry this row does not
                             describe, or names several at once
  ENTRY_NOT_FOUND            the competition is determined but holds no entry
                             with this number and class

Nothing here falls back to picking one of several candidates. A multiplicity is
an outcome, never a tie broken quietly.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Mapping


class Outcome(enum.Enum):
    RESOLVED_BY_SOURCE_ID = "resolved_by_source_id"
    RESOLVED_BY_SOLE_COMPETITION = "resolved_by_sole_competition"
    NO_ASSOCIATION = "no_association"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    SOURCE_ID_OUT_OF_SCOPE = "source_id_out_of_scope"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    ENTRY_NOT_FOUND = "entry_not_found"

    @property
    def resolved(self) -> bool:
        return self in (Outcome.RESOLVED_BY_SOURCE_ID,
                        Outcome.RESOLVED_BY_SOLE_COMPETITION)


@dataclass(frozen=True)
class EntryView:
    """One competitive entry, as the resolver needs to see it."""
    entry_key: str          # stable identity of the entry (not a row id)
    number: str
    car_class: str | None


@dataclass(frozen=True)
class CompetitionView:
    """
    One competition a session runs.

    `entries` is keyed by (number, car_class) because that is the uniqueness
    regime migration 008 enforces for a determined competition with a class.

    `source_ids` maps an entry-list identifier to the SET of entries carrying
    it. A set, not a single entry, because the data has 34 identifiers attached
    to more than one entry of the same competition, from three distinct causes:

        15  the same car number written #03 and #3
        12  genuinely different numbers — at Bahrain 2025 the identifier
            105154 is attached to both #50 and #51
         7  the same number recorded under two classes

    Those are not one problem, and none of them is this module's to fix.
    Keeping only one entry would be a tie broken quietly, which is what this
    module exists to refuse.
    """
    competition_key: str
    entries: Mapping[tuple[str, str | None], EntryView]
    source_ids: Mapping[str, frozenset[str]]    # source identifier -> {entry_key}


@dataclass(frozen=True)
class SessionView:
    """
    `foreign_source_ids` maps an identifier known ELSEWHERE in this event to
    the competitions that own it. Without it the resolver cannot tell an
    identifier belonging to a competition this session does not run from one
    the event has never seen, and would treat both as absent.
    """
    session_key: str
    competitions: tuple[CompetitionView, ...]
    foreign_source_ids: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class ClassificationRow:
    number: str
    car_class: str | None
    team: str
    source_id: str | None


@dataclass(frozen=True)
class Resolution:
    outcome: Outcome
    entry_key: str | None
    evidence: str
    candidates: tuple[str, ...] = ()     # competition keys considered

    @property
    def resolved(self) -> bool:
        return self.outcome.resolved


def _entry_of(competition: CompetitionView, row: ClassificationRow) -> EntryView | None:
    """
    The entry with this number and class.

    Falls back to the class-less key only when the row itself carries no class,
    which is the pre-2013 regime. It never searches across classes: two entries
    differing by class are different entries, and guessing between them is the
    behaviour this module exists to refuse.
    """
    key = (row.number, row.car_class or None)
    found = competition.entries.get(key)
    if found is not None:
        return found
    if row.car_class:
        return None
    return competition.entries.get((row.number, None))


def resolve(session: SessionView, row: ClassificationRow) -> Resolution:
    """Decide which entry a classification row belongs to, or why it cannot."""
    competitions = session.competitions
    keys = tuple(c.competition_key for c in competitions)
    unrecognised: str | None = None

    # 1. An identifier the source itself printed, when it names an entry of a
    #    competition this session runs. Nothing corroborates better.
    if row.source_id:
        owners = [c for c in competitions if row.source_id in c.source_ids]
        if len(owners) == 1:
            competition = owners[0]
            named = competition.source_ids[row.source_id]
            if len(named) > 1:
                return Resolution(
                    Outcome.CONTRADICTORY_EVIDENCE, None,
                    f"identificador {row.source_id} esta ligado a {len(named)} "
                    f"inscricoes da competicao {competition.competition_key}; "
                    f"a fonte nao distingue qual delas esta nesta linha",
                    keys)
            entry_key = next(iter(named))
            stated = competition.entries.get((row.number, row.car_class or None))
            if stated is not None and stated.entry_key == entry_key:
                return Resolution(
                    Outcome.RESOLVED_BY_SOURCE_ID, entry_key,
                    f"identificador {row.source_id} pertence a competicao "
                    f"{competition.competition_key}, e a inscricao confere em "
                    f"numero e classe",
                    keys)
            # The identifier names an entry, but not the one this row describes.
            named = next((e for e in competition.entries.values()
                          if e.entry_key == entry_key), None)
            described = (f"#{named.number} {named.car_class or 'sem classe'}"
                         if named else "inscricao desconhecida")
            return Resolution(
                Outcome.CONTRADICTORY_EVIDENCE, None,
                f"identificador {row.source_id} nomeia {described}, mas a linha "
                f"descreve #{row.number} {row.car_class or 'sem classe'}",
                keys)
        if len(owners) > 1:
            return Resolution(
                Outcome.CONTRADICTORY_EVIDENCE, None,
                f"identificador {row.source_id} pertence a "
                f"{len(owners)} competicoes desta sessao",
                keys)
        # Owned by no competition this session runs. Two very different
        # situations, and collapsing them would hide the dangerous one.
        elsewhere = session.foreign_source_ids.get(row.source_id)
        if elsewhere:
            return Resolution(
                Outcome.SOURCE_ID_OUT_OF_SCOPE, None,
                f"identificador {row.source_id} pertence a "
                f"{', '.join(sorted(elsewhere))}, que esta sessao nao roda",
                keys)
        # Known nowhere in the event: no evidence either way. The row falls
        # through to the session's own competitions, and the evidence says so.
        unrecognised = row.source_id

    # 2. No identifier that places the row. The session's competitions decide.
    if not competitions:
        return Resolution(
            Outcome.NO_ASSOCIATION, None,
            "a sessao nao esta associada a nenhuma competicao", keys)

    if len(competitions) > 1:
        return Resolution(
            Outcome.MULTIPLE_CANDIDATES, None,
            f"a sessao roda {len(competitions)} competicoes e a linha nao traz "
            f"identificador que a situe",
            keys)

    competition = competitions[0]
    entry = _entry_of(competition, row)
    if entry is None:
        return Resolution(
            Outcome.ENTRY_NOT_FOUND, None,
            f"competicao {competition.competition_key} nao tem inscricao "
            f"#{row.number} {row.car_class or 'sem classe'}",
            keys)
    note = (f"; identificador {unrecognised} nao e conhecido neste evento"
            if unrecognised else "")
    return Resolution(
        Outcome.RESOLVED_BY_SOLE_COMPETITION, entry.entry_key,
        f"sessao roda somente {competition.competition_key}; inscricao "
        f"#{entry.number} {entry.car_class or 'sem classe'}{note}",
        keys)

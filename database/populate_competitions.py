#!/usr/bin/env python3
"""
Populate competitions, entries and their provenance from the raw exports.

Writes only the tables introduced by migrations 006-009, plus the structural
rows they depend on (series/seasons/events/sessions/teams). It never touches
results, laps, result_drivers or the analytics tables, and never runs the
legacy loader.

It refuses to start unless the target database passes an explicit environment
check: the point of this script is to be run against a disposable database, and
a mistake here is not recoverable by re-running it.

Usage:
    python database/populate_competitions.py --dsn "..." --raw raw [--confirm-disposable]
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from competition_discovery import analyse, iter_events, norm_team  # noqa: E402
from competition_matching import match_and_apply, record_merge_hypothesis  # noqa: E402

PROTECTED_DATABASES = {"openwec", "ensaio", "openwec_prod", "postgres"}
PROTECTED_PORTS = {"5432", "5433", "5544"}

SERIES_KEY = {"wec": "WEC", "elms": "ELMS", "alms": "ALMS",
              "lemanscup": "LEMANSCUP", "imsa": "IMSA"}

SESSION_TYPE = (
    ("hyperpole", "Hyperpole"), ("qualif", "Qualifying"), ("warm", "WarmUp"),
    ("practice", "Practice"), ("test", "Test"), ("prologue", "Prologue"),
    ("race", "Race"), ("hour", "Race"), ("session", "Other"),
)


def session_type_of(name: str) -> str:
    low = name.lower()
    for needle, value in SESSION_TYPE:
        if needle in low:
            return value
    return "Other"


def guard(cur, dsn: str, confirmed: bool) -> dict:
    """Refuse to write anywhere that has not been shown to be disposable."""
    # The port must come from the DSN, not from inet_server_port(): inside a
    # container the server always reports 5432, so the server-side value cannot
    # tell the disposable instance from the development one.
    dsn_parts = dict(p.split("=", 1) for p in dsn.split() if "=" in p)
    port = dsn_parts.get("port", "?")
    cur.execute("SELECT current_database(), inet_server_addr()::text, current_user, version()")
    db, host, user, version = cur.fetchone()
    info = {"database": db, "host": dsn_parts.get("host", host or "local"),
            "port_dsn": port, "user": user, "server": version.split(",")[0]}
    print("Ambiente alvo:")
    for k, v in info.items():
        print(f"  {k:9s}: {v}")

    problems = []
    if db in PROTECTED_DATABASES:
        problems.append(f"database '{db}' esta na lista protegida")
    if port in PROTECTED_PORTS:
        problems.append(f"porta {port} do DSN esta na lista protegida")
    cur.execute("SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('competition_anchors','competition_match_reviews')")
    if cur.fetchone()[0] != 2:
        problems.append("migrations 009 nao aplicadas neste banco")
    cur.execute("SELECT count(*) FROM results")
    n_results = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM laps")
    n_laps = cur.fetchone()[0]
    if n_results or n_laps:
        problems.append(f"banco contem dados historicos: {n_results} results, {n_laps} laps")
    if not confirmed:
        problems.append("--confirm-disposable nao foi informado")

    if problems:
        print("\nRECUSADO. Este banco nao foi demonstrado descartavel:")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(2)
    print("  verificado: banco descartavel, sem dados historicos\n")
    return info


class Populator:
    def __init__(self, cur):
        self.cur = cur
        self.teams: dict[str, int] = {}
        self.stats = collections.Counter()
        self.diffs: list[str] = []

    # ── structural rows ───────────────────────────────────────────────────
    def event_row(self, series: str, season: str, event: str) -> int:
        c = self.cur
        c.execute("SELECT id FROM series WHERE key = %s", (SERIES_KEY[series],))
        row = c.fetchone()
        if not row:
            raise SystemExit(f"serie desconhecida no schema: {series}")
        series_id = row[0]
        year = int(season.split("_", 1)[1][:4])
        c.execute("INSERT INTO seasons (series_id, raw_id, year, label) VALUES (%s,%s,%s,%s) "
                  "ON CONFLICT (series_id, raw_id) DO UPDATE SET year = EXCLUDED.year "
                  "RETURNING id", (series_id, season, year, season.split("_", 1)[1]))
        season_id = c.fetchone()[0]
        c.execute("INSERT INTO events (season_id, raw_id, name) VALUES (%s,%s,%s) "
                  "ON CONFLICT (season_id, raw_id) DO UPDATE SET name = EXCLUDED.name "
                  "RETURNING id", (season_id, event, event.split("_", 1)[-1][:100]))
        return c.fetchone()[0]

    def session_row(self, event_id: int, name: str) -> int:
        self.cur.execute(
            "INSERT INTO sessions (event_id, raw_id, name, session_type) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (event_id, raw_id) DO UPDATE SET name = EXCLUDED.name RETURNING id",
            (event_id, name[:80], name[:80], session_type_of(name)))
        return self.cur.fetchone()[0]

    def team_row(self, name: str) -> int | None:
        if not name:
            return None
        if name in self.teams:
            return self.teams[name]
        self.cur.execute("INSERT INTO teams (name) VALUES (%s) "
                         "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                         (name[:120],))
        self.teams[name] = self.cur.fetchone()[0]
        return self.teams[name]

    # ── the competitive model ─────────────────────────────────────────────
    def populate_event(self, key: str, ev_path: Path):
        a = analyse(ev_path)
        if not a:
            return
        series, season, event = key.split("/")
        event_id = self.event_row(series, season, event)
        sess_id = {name: self.session_row(event_id, name) for name in a["sess"]}
        self.stats["eventos"] += 1
        self.stats["sessoes"] += len(a["sess"])

        groups = [{"ids": c["ids"], "sessions": c["sessions"], "numbers": c["numbers"],
                   "pairs": c["pairs"], "classes": c["classes"], "kind": c["kind"],
                   "display": f"{event} [{c['kind']}]",
                   "evidence": self._evidence(c)} for c in a["comps"]]
        report = match_and_apply(self.cur, event_id, [dict(g) for g in groups])

        comp_id: dict[int, int] = {}
        for idx, cid, _uid, _fp in report["created"]:
            comp_id[idx] = cid
            self.stats["competicoes_criadas"] += 1
        for idx, cid, _uid, _fp in report["reused"]:
            comp_id[idx] = cid
            self.stats["competicoes_reusadas"] += 1
        for idx, _hit, _why in report["review"]:
            self.stats["correspondencias_ambiguas"] += 1
            self.diffs.append(f"{key}: grupo {idx} sem competicao persistida (revisao)")
        for i, g in enumerate(groups):
            self.stats[f"descobertas_{g['kind']}"] += 1

        for small, big, ca, cb in a["hyp"]:
            record_merge_hypothesis(
                self.cur, event_id, f"grupo{small}", f"grupo{big}",
                f"classes {list(ca)} contidas em {list(cb)}; sessoes do menor contidas "
                f"nas do maior; nenhuma classe propria. Nao aplicada.")
            self.stats["hipoteses"] += 1

        self._associations(a, comp_id, sess_id)
        self._entries(a, comp_id, sess_id, event_id, key)

    @staticmethod
    def _evidence(c) -> str:
        if c["ids"]:
            ids = sorted(c["ids"])
            return (f"{len(ids)} identificadores de lista, {ids[0]}..{ids[-1]}; "
                    f"{len(c['sessions'])} sessoes")
        return (f"sem identificador; agrupada por populacao de inscricoes compartilhada "
                f"em {len(c['sessions'])} sessoes")

    def _associations(self, a, comp_id, sess_id):
        cand_id: dict[tuple[str, int], int] = {}
        for name, k, evaluation, crit in a["cands"]:
            if k not in comp_id:
                continue
            t_ok, c_ok, n_ok, no_conf, pop_ok = crit
            self.cur.execute(
                "INSERT INTO session_competition_candidates "
                "(session_id, competition_id, evaluation, timestamp_ok, classes_ok, "
                " numbers_ok, no_id_conflict, population_ok, rationale, provenance) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (session_id, competition_id) DO UPDATE SET "
                "  evaluation = EXCLUDED.evaluation, timestamp_ok = EXCLUDED.timestamp_ok, "
                "  classes_ok = EXCLUDED.classes_ok, numbers_ok = EXCLUDED.numbers_ok, "
                "  no_id_conflict = EXCLUDED.no_id_conflict, "
                "  population_ok = EXCLUDED.population_ok, rationale = EXCLUDED.rationale "
                "RETURNING id",
                (sess_id[name], comp_id[k], evaluation, t_ok, c_ok, n_ok, no_conf, pop_ok,
                 self._rationale(evaluation, crit),
                 "classificacao e weather dos CSVs brutos"))
            cand_id[(name, k)] = self.cur.fetchone()[0]
            self.stats[f"candidatos_{evaluation}"] += 1

        for name, links in a["assoc"].items():
            for k, status, just in links:
                if k not in comp_id:
                    continue
                jid = cand_id.get((name, just)) if just is not None else None
                self.cur.execute(
                    "INSERT INTO session_competitions "
                    "(session_id, competition_id, status, evidence, justified_by, "
                    " justified_by_evaluation) VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (session_id, competition_id) DO UPDATE SET "
                    "  status = EXCLUDED.status, evidence = EXCLUDED.evidence",
                    (sess_id[name], comp_id[k], status,
                     "identificadores da propria sessao" if status == "resolved"
                     else "candidato unico elegivel",
                     jid, "eligible" if jid else None))
                self.stats[f"associacoes_{status}"] += 1

        for name in a["unresolved"]:
            self.cur.execute(
                "INSERT INTO session_competitions (session_id, competition_id, status, evidence) "
                "VALUES (%s, NULL, 'unresolved', %s) "
                "ON CONFLICT (session_id, competition_id) DO UPDATE SET evidence = EXCLUDED.evidence",
                (sess_id[name], "nenhum candidato elegivel unico; preservada sem atribuicao"))
            self.stats["associacoes_unresolved"] += 1

    @staticmethod
    def _rationale(evaluation, crit) -> str:
        names = ("timestamp", "classes", "numeros", "sem_conflito_id", "populacao")
        parts = [f"{n}={'T' if v is True else 'F' if v is False else 'NULL'}"
                 for n, v in zip(names, crit)]
        return f"{evaluation}: " + ", ".join(parts)

    def _entries(self, a, comp_id, sess_id, event_id, key):
        """
        One row per competitive entry, keyed exactly as the indexes of 008 are.
        An entry whose competition could not be determined is stored against the
        event with competition_id NULL — never against an invented competition.
        """
        placements: dict[tuple, dict] = {}
        for name, d in a["sess"].items():
            here = [k for k, _, _ in a["assoc"][name] if k in comp_id]
            for r in d["rows"]:
                if r["sid"] and r["sid"] in a["owner"] and a["owner"][r["sid"]] in comp_id:
                    k = a["owner"][r["sid"]]
                elif len(here) == 1:
                    k = here[0]
                else:
                    k = None
                tail = (r["number"], r["cls"]) if r["cls"] else (r["number"], r["tnorm"])
                slot = placements.setdefault(
                    (k,) + tail,
                    {"comp": k, "number": r["number"], "cls": r["cls"] or None,
                     "teams": {}, "sources": set(), "sessions": set()})
                slot["teams"].setdefault(r["team"], set()).add(name)
                slot["sessions"].add(name)
                if r["sid"]:
                    slot["sources"].add((r["sid"], name))

        determined_tails = {k[1:] for k in placements if k[0] is not None}
        for pk, slot in placements.items():
            if pk[0] is None and pk[1:] in determined_tails:
                continue    # already placed through another session
            spellings = {norm_team(t) for t in slot["teams"] if t}
            status = "ambiguous" if len(spellings) > 1 else (
                "resolved" if slot["sources"] else "inferred")
            team_name = sorted(slot["teams"], key=lambda t: (-len(slot["teams"][t]), t))[0] \
                if slot["teams"] else ""
            team_id = self.team_row(team_name)
            cid = comp_id.get(slot["comp"]) if slot["comp"] is not None else None

            entry_id = self._upsert_entry(cid, event_id, slot, team_id, status)
            if entry_id is None:
                self.diffs.append(f"{key}: inscricao #{slot['number']} {slot['cls']} nao persistida")
                continue
            self.stats["inscricoes_determinadas" if cid else "inscricoes_indeterminadas"] += 1
            self.stats[f"inscricoes_{status}"] += 1

            for team, seen_in in slot["teams"].items():
                tid = self.team_row(team)
                if tid is None:
                    continue
                for sname in seen_in:
                    self.cur.execute(
                        "INSERT INTO entry_team_history (entry_id, team_id, session_id) "
                        "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        (entry_id, tid, sess_id[sname]))
                    self.stats["proveniencia_equipe"] += 1
            for sid_value, sname in slot["sources"]:
                self.cur.execute(
                    "INSERT INTO entry_source_ids "
                    "(entry_id, source_system, source_entry_id, session_id) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (entry_id, "imsa" if not sid_value.isdigit() else "ecm",
                     sid_value, sess_id[sname]))
                self.stats["proveniencia_identificador"] += 1

    def _upsert_entry(self, cid, event_id, slot, team_id, status):
        """Idempotent against whichever of the six uniqueness regimes applies."""
        c = self.cur
        if cid is not None:
            where = ("competition_id = %s AND number = %s AND car_class IS NOT DISTINCT FROM %s"
                     if slot["cls"] else
                     "competition_id = %s AND number = %s AND car_class IS NULL "
                     "AND team_id IS NOT DISTINCT FROM %s")
            args = (cid, slot["number"], slot["cls"] if slot["cls"] else team_id)
        else:
            where = ("event_id = %s AND competition_id IS NULL AND number = %s "
                     "AND car_class IS NOT DISTINCT FROM %s" if slot["cls"] else
                     "event_id = %s AND competition_id IS NULL AND number = %s "
                     "AND car_class IS NULL AND team_id IS NOT DISTINCT FROM %s")
            args = (event_id, slot["number"], slot["cls"] if slot["cls"] else team_id)
        c.execute(f"SELECT id, identity_status FROM entries WHERE {where}", args)
        row = c.fetchone()
        if row:
            # Never silently downgrade a reviewed status.
            c.execute("UPDATE entries SET team_id = COALESCE(%s, team_id) WHERE id = %s",
                      (team_id, row[0]))
            return row[0]
        c.execute(
            "INSERT INTO entries (competition_id, event_id, number, car_class, team_id, "
            " identity_status, review_note) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (cid, event_id, slot["number"], slot["cls"], team_id, status,
             "varias grafias de equipe na mesma competicao" if status == "ambiguous" else None))
        return c.fetchone()[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--raw", default="raw", type=Path)
    ap.add_argument("--confirm-disposable", action="store_true")
    ap.add_argument("--limit", type=int, help="process only the first N events")
    args = ap.parse_args(argv)

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    cur = conn.cursor()
    guard(cur, args.dsn, args.confirm_disposable)

    p = Populator(cur)
    n = 0
    for key, ev in iter_events(args.raw):
        p.populate_event(key, ev)
        n += 1
        if n % 50 == 0:
            conn.commit()
            print(f"  {n} eventos...")
        if args.limit and n >= args.limit:
            break
    conn.commit()

    print("\nResumo da populacao:")
    for k in sorted(p.stats):
        print(f"  {k:34s}: {p.stats[k]}")
    if p.diffs:
        print(f"\nDivergencias descoberta -> persistido: {len(p.diffs)}")
        for d in p.diffs[:15]:
            print(f"  {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

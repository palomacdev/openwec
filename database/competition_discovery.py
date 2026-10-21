"""
Competition discovery from the raw classification exports.

Read-only: reads raw/, writes nothing and touches no database. Given an event
directory it returns the entry lists the source supports, the sessions
associated with each, the candidate evaluations behind any inferred
association, and the merge hypotheses it refuses to act on.

No threshold on identifier distance is used anywhere. Lists come from the
structure of which sessions each identifier appears in; identifier values are
only ever compared for equality. Replacing every identifier with an opaque
token leaves the result unchanged.
"""
from __future__ import annotations
import collections, csv, io, os, re, unicodedata
from pathlib import Path

# Review switch: when set, approved merge hypotheses are applied instead of
# only being recorded. Off by default — a hypothesis is not a decision.
APPLY_HYPOTHESES = os.environ.get("OPENWEC_APPLY_MERGE_HYPOTHESES") == "1"

SERIES = ("wec", "elms", "alms", "lemanscup", "imsa")
ID_COLS = ("ECM Participant Id", "IMSA_EventEntryId", "IMSAEventEntryId")


def read_csv(p: Path):
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            t = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []
    return [{(k or "").strip().lstrip("﻿"): (v or "").strip()
             for k, v in r.items() if k}
            for r in csv.DictReader(io.StringIO(t), delimiter=";")]


def norm_team(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    s = re.sub(r"\b(TEAM|RACING|MOTORSPORTS?|COMPETITION|SPORT)\b", "", s)
    return re.sub(r"[^A-Z0-9]", "", s)


def src_id(r: dict) -> str:
    for c in ID_COLS:
        v = r.get(c, "").strip()
        if v:
            return v
    return ""


def sess_name(p: Path) -> str:
    return p.parent.parent.name if p.parent.name.lower() == "classification" else p.parent.name


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb

    def groups(self, keys):
        g = collections.defaultdict(list)
        for k in keys:
            g[self.find(k)].append(k)
        return list(g.values())


def scan_event(ev: Path):
    """session -> dict(rows, ids, classes, numbers, triples, window)"""
    sess = {}
    for p in ev.rglob("*"):
        if p.suffix.upper() != ".CSV":
            continue
        low = str(p).lower()
        name = sess_name(p)
        if "classification" in low:
            rows = []
            for r in read_csv(p):
                n = r.get("NUMBER", "").strip()
                if not n:
                    continue
                rows.append({"number": n,
                             "cls": r.get("CLASS", "").strip().upper(),
                             "team": r.get("TEAM", "").strip(),
                             "tnorm": norm_team(r.get("TEAM", "")),
                             "sid": src_id(r)})
            if rows:
                sess.setdefault(name, {"rows": [], "window": None})["rows"].extend(rows)
        elif "weather" in low:
            secs = [int(v) for r in read_csv(p)
                    for v in [r.get("TIME_UTC_SECONDS", "").strip()] if v.isdigit()]
            if secs:
                d = sess.setdefault(name, {"rows": [], "window": None})
                w = (min(secs), max(secs))
                d["window"] = w if d["window"] is None else (min(d["window"][0], w[0]),
                                                             max(d["window"][1], w[1]))
    out = {}
    for name, d in sess.items():
        if not d["rows"]:
            continue
        out[name] = {
            "rows": d["rows"],
            "window": d["window"],
            "ids": {r["sid"] for r in d["rows"] if r["sid"]},
            "classes": {r["cls"] for r in d["rows"] if r["cls"]},
            "numbers": {r["number"] for r in d["rows"]},
            "triples": {(r["number"], r["tnorm"], r["cls"]) for r in d["rows"]},
            "pairs": {(r["number"], r["tnorm"]) for r in d["rows"]},
        }
    return out


def discover_lists(sess):
    """
    A: atoms = identifiers grouped by the exact set of sessions they appear in.
       Purely structural; no numeric distance between identifiers is consulted.
    B: two atoms merge only on positive evidence that a session which omitted one
       of them did so because of a class restriction, not because it is a
       different list.
    """
    sig = collections.defaultdict(set)
    for name, d in sess.items():
        for i in d["ids"]:
            sig[i].add(name)
    if not sig:
        return [], []

    grouped = collections.defaultdict(set)
    for i, s in sig.items():
        grouped[frozenset(s)].add(i)
    atoms = list(grouped.items())

    acls = []
    for s, ids in atoms:
        c = set()
        for name in s:
            for r in sess[name]["rows"]:
                if r["sid"] in ids and r["cls"]:
                    c.add(r["cls"])
        acls.append(c)

    def excluded_by_class(session_name, other_ids, other_cls):
        """Did this session omit the other atom because of a class restriction?"""
        scls = sess[session_name]["classes"]
        if not scls or not other_cls:
            return False
        return not (scls & other_cls) and not (sess[session_name]["ids"] & other_ids)

    def confined_within(small, big):
        """
        One atom never observed outside the other's sessions, bringing no class
        the other does not already field. This is what partial participation
        inside a single entry list looks like — and also what a second
        championship confined to shared sessions looks like. It is recorded as
        a correspondence hypothesis for review, never applied.
        """
        return atoms[small][0] < atoms[big][0] and acls[small] and acls[small] <= acls[big]

    uf = UF()
    for k in range(len(atoms)):
        uf.find(k)
    hypotheses = []
    for a in range(len(atoms)):
        for b in range(a + 1, len(atoms)):
            sa, ia = atoms[a]
            sb, ib = atoms[b]
            if not (sa & sb):
                continue
            if all(excluded_by_class(n, ib, acls[b]) for n in sa - sb) and \
               all(excluded_by_class(n, ia, acls[a]) for n in sb - sa):
                # Every session that omitted one atom was restricted to classes
                # that atom does not field. The omission is explained; merge.
                uf.union(a, b)
            elif confined_within(a, b):
                hypotheses.append((a, b, sorted(acls[a]), sorted(acls[b])))
                if APPLY_HYPOTHESES:
                    uf.union(a, b)
            elif confined_within(b, a):
                hypotheses.append((b, a, sorted(acls[b]), sorted(acls[a])))
                if APPLY_HYPOTHESES:
                    uf.union(a, b)

    lists = []
    atom_of_list = {}
    for grp in uf.groups(range(len(atoms))):
        for k in grp:
            atom_of_list[k] = len(lists)
        ids = set().union(*(atoms[k][1] for k in grp))
        names = set().union(*(atoms[k][0] for k in grp))
        numbers, classes = set(), set()
        for name in names:
            for r in sess[name]["rows"]:
                if r["sid"] in ids:
                    numbers.add(r["number"])
                    if r["cls"]:
                        classes.add(r["cls"])
        wins = [sess[n]["window"] for n in names if sess[n]["window"]]
        lists.append({"ids": ids, "sessions": names, "numbers": numbers,
                      "classes": classes,
                      "window": (min(w[0] for w in wins), max(w[1] for w in wins))
                                if wins else None,
                      "pairs": {(r["number"], r["tnorm"]) for name in names
                                for r in sess[name]["rows"] if r["sid"] in ids},
                      "triples": {(r["number"], r["tnorm"], r["cls"]) for name in names
                                  for r in sess[name]["rows"] if r["sid"] in ids}})
    # Ordered by evidence volume and session names only. Identifier values are
    # never compared, so relabelling every identifier cannot change the result.
    order = sorted(range(len(lists)),
                   key=lambda k: (-len(lists[k]["ids"]), -len(lists[k]["sessions"]),
                                  sorted(lists[k]["sessions"])))
    pos = {old: new for new, old in enumerate(order)}
    lists = [lists[k] for k in order]
    # One hypothesis per pair of lists, carrying the union of the class evidence
    # that raised it. Two atoms of the same list pair are the same hypothesis.
    merged = {}
    for a, b, ca, cb in hypotheses:
        la, lb = atom_of_list[a], atom_of_list[b]
        if la == lb:
            continue
        key = (pos[la], pos[lb])
        acc = merged.setdefault(key, (set(), set()))
        acc[0].update(ca)
        acc[1].update(cb)
    hyp = sorted((k[0], k[1], tuple(sorted(v[0])), tuple(sorted(v[1])))
                 for k, v in merged.items())
    return lists, hyp


def evaluate(sd, comp, others):
    """Four criteria. None means no evidence, and is never read as TRUE."""
    sc, cc = sd["classes"], comp["classes"]
    if not sc or not cc:
        classes_ok = None
    elif sc <= cc:
        classes_ok = True
    elif not (sc & cc):
        classes_ok = False
    else:
        classes_ok = None

    sn, cn = sd["numbers"], comp["numbers"]
    if not sn or not cn:
        numbers_ok = None
    elif sn <= cn:
        numbers_ok = True
    elif not (sn & cn):
        numbers_ok = False
    else:
        numbers_ok = None

    w, cw = sd["window"], comp["window"]
    if not w or not cw:
        timestamp_ok = None
    elif cw[0] <= w[0] <= cw[1]:
        timestamp_ok = True
    elif any(o["window"] and o["window"][0] <= w[0] <= o["window"][1] for o in others):
        timestamp_ok = False
    else:
        timestamp_ok = None

    other_ids = set().union(*[o["ids"] for o in others]) if others else set()
    if not sd["ids"]:
        no_id_conflict = None
    elif sd["ids"] <= comp["ids"]:
        no_id_conflict = True
    elif sd["ids"] & other_ids:
        no_id_conflict = False
    else:
        no_id_conflict = None

    sp, cp = sd["pairs"], comp["pairs"]
    if not sp or not cp:
        population_ok = None
    elif sp <= cp:
        population_ok = True
    elif not (sp & cp):
        population_ok = False
    else:
        population_ok = None

    crit = (timestamp_ok, classes_ok, numbers_ok, no_id_conflict, population_ok)
    # Objective levels. Class and number agreement alone never confirms a
    # competition: they say the fields look alike, not that they are the same
    # field. Eligibility needs the session to sit inside the competition's time
    # window, or its entries to sit inside the competition's own entry
    # population, teams included.
    if any(c is False for c in crit):
        ev = "rejected"
    elif timestamp_ok is True or population_ok is True:
        ev = "eligible"
    elif sum(1 for c in crit if c is True) >= 2:
        ev = "compatible"
    else:
        ev = "indeterminate"
    return ev, crit


def analyse(ev_path: Path):
    sess = scan_event(ev_path)
    if not sess:
        return None
    lists, hyp = discover_lists(sess)
    owner = {i: k for k, L in enumerate(lists) for i in L["ids"]}
    comps = [{"kind": "resolved", "ids": L["ids"], "classes": L["classes"],
              "numbers": L["numbers"], "window": L["window"], "pairs": L["pairs"],
              "triples": L["triples"], "sessions": set(L["sessions"])} for L in lists]

    assoc = collections.defaultdict(list)
    for name, d in sess.items():
        for k in sorted({owner[i] for i in d["ids"] if i in owner}):
            assoc[name].append((k, "resolved", None))

    cands = []
    contested = set()
    for name, d in sess.items():
        if assoc[name]:
            continue
        elig = []
        for k, c in enumerate(comps):
            e, crit = evaluate(d, c, [o for j, o in enumerate(comps) if j != k])
            cands.append((name, k, e, crit))
            if e == "eligible":
                elig.append(k)
        if len(elig) == 1:
            assoc[name].append((elig[0], "inferred", elig[0]))
        elif len(elig) > 1:
            # Plausibly part of a competition already known, but the evidence
            # does not say which. It stays unresolved: inventing a provisional
            # competition here would assert a grouping the source contradicts.
            contested.add(name)

    # A session only forms a provisional competition when every existing
    # competition positively rejects it. Merely failing to prove membership is
    # not evidence of a separate championship, so those sessions stay
    # unresolved rather than being given a competition of their own.
    verdicts = collections.defaultdict(set)
    for name, k, e, _ in cands:
        verdicts[name].add(e)
    left = [n for n in sess
            if not assoc[n] and n not in contested
            and (not comps or verdicts[n] == {"rejected"})]
    uf = UF()
    for n in left:
        uf.find(n)
    for i in range(len(left)):
        for j in range(i + 1, len(left)):
            if sess[left[i]]["triples"] & sess[left[j]]["triples"]:
                uf.union(left[i], left[j])
    prov = []
    for grp in uf.groups(left):
        if len(grp) < 2:
            continue
        k = len(comps)
        comps.append({"kind": "provisional", "ids": set(),
                      "classes": set().union(*(sess[n]["classes"] for n in grp)),
                      "numbers": set().union(*(sess[n]["numbers"] for n in grp)),
                      "pairs": set().union(*(sess[n]["pairs"] for n in grp)),
                      "triples": set().union(*(sess[n]["triples"] for n in grp)),
                      "window": None, "sessions": set(grp)})
        prov.append((k, sorted(grp)))
        for n in grp:
            assoc[n].append((k, "resolved", None))

    # ── Entry placement ───────────────────────────────────────────────────
    # Keyed exactly as the unique indexes of migration 008 are: the team takes
    # part only where no class is available to tell entries apart. An entry is
    # undetermined only when NO observation anywhere in the event placed it —
    # a car identified in Free Practice does not become undetermined because
    # the race-hour snapshots carry no identifier.
    def key(r, comp):
        tail = (r["number"], r["cls"]) if r["cls"] else (r["number"], r["tnorm"])
        return (comp,) + tail

    placed = set()
    seen_undetermined = {}
    for name, d in sess.items():
        comps_here = [k for k, _, _ in assoc[name]]
        for r in d["rows"]:
            if r["sid"] and r["sid"] in owner:
                placed.add(key(r, owner[r["sid"]]))
            elif len(comps_here) == 1:
                placed.add(key(r, comps_here[0]))
            else:
                seen_undetermined.setdefault(
                    key(r, None), (name, r["number"], r["cls"], r["team"]))

    determined_tails = {k[1:] for k in placed}
    undetermined = [v for k, v in seen_undetermined.items()
                    if k[1:] not in determined_tails]
    placed = len(placed)
    return {"sess": sess, "comps": comps, "assoc": assoc, "cands": cands,
            "lists": lists, "owner": owner, "prov": prov, "hyp": hyp,
            "unresolved": sorted(n for n in sess if not assoc[n]),
            "placed": placed, "undetermined": undetermined}


def iter_events(raw: Path, want: str | None = None):
    for series in SERIES:
        sd = raw / series
        if not sd.is_dir():
            continue
        for season in sorted(d for d in sd.iterdir() if d.is_dir()):
            for ev in sorted(d for d in season.iterdir() if d.is_dir()):
                key = f"{series}/{season.name}/{ev.name}"
                if want and want.lower() not in key.lower():
                    continue
                yield key, ev

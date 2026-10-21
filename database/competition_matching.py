"""
Idempotent matching of a discovery against the competitions already stored.

Identity is never recomputed. A discovery is matched through its anchors, and
only a one-to-one correspondence is acted on; anything else is written to
competition_match_reviews and left alone.
"""
from __future__ import annotations
import collections
import hashlib
import uuid


def fingerprint(group) -> str:
    """Deterministic over the evidence observed, and free to change with it."""
    payload = "|".join((
        ",".join(sorted(group["sessions"])),
        ",".join(sorted(group["numbers"])),
        ",".join(sorted(group["classes"])),
        ",".join(sorted(group["anchors"])),
    ))
    return hashlib.sha256(payload.encode()).hexdigest()


def label_for(uid) -> str:
    """
    Technical label. Injective in the identity, because it carries the whole
    uid: truncating to 12 characters was not a function identity could rely on,
    and a collision there would have forced a second competition to take a
    label that was not its own. Never read by the matcher.
    """
    return "c" + str(uid)


def discovery_key(group) -> str:
    """Stable name for a discovery, used to deduplicate review rows."""
    return hashlib.sha256(("|".join(sorted(group["anchors"]))).encode()).hexdigest()


def anchors_of(group):
    """
    (kind, value, exclusivity) triples.

    Entry-list identifiers are exclusive: an entry belongs to one list. Sessions
    are shared: Monza 2018 runs two championships in the same session, so a
    session name cannot own a competition. A competition with identifiers keeps
    its sessions as corroborating evidence rather than discarding them.
    """
    out = []
    if group["ids"]:
        kind = "imsa_entry_id" if group.get("source") == "imsa" else "ecm_entry_id"
        out += [(kind, v, "exclusive") for v in sorted(group["ids"])]
    out += [("session", s, "shared") for s in sorted(group["sessions"])]
    # number # team # class — the class takes part where the source has one, so
    # that two championships sharing a car number are told apart by the class
    # as well as the team.
    triples = group.get("triples")
    if triples is None:
        triples = {(n, t, "") for n, t in group.get("pairs", ())}
    out += [("entry", f"{n}#{t}#{c}", "shared") for n, t, c in sorted(triples)]
    return out


def match_and_apply(cur, event_id, groups, *, now_label="import"):
    """
    groups: list of dicts with ids, sessions, numbers, classes, kind.
    Returns a report dict. Writes competitions, anchors and reviews.
    """
    for g in groups:
        trip = anchors_of(g)
        g["anchors"] = [f"{k}:{v}" for k, v, _ in trip]
        g["exclusive"] = {f"{k}:{v}" for k, v, e in trip if e == "exclusive"}
        g["shared"] = {f"{k}:{v}" for k, v, e in trip if e == "shared"}

    # Only CURRENT anchors confirm. A superseded one — an identifier the source
    # has since corrected away — may still nominate a candidate, but that route
    # ends in review. Obsolete evidence never confirms on its own.
    cur.execute(
        "SELECT anchor_kind, anchor_value, exclusivity, competition_id "
        "FROM competition_anchors WHERE event_id = %s AND state = 'current'", (event_id,))
    excl_owner = {}
    comp_sessions = collections.defaultdict(set)   # competition_id -> {session anchor}
    comp_entries = collections.defaultdict(set)    # competition_id -> {entry anchor}
    held_by = collections.defaultdict(set)         # shared anchor -> {competition_id}
    for k, v, ex, c in cur.fetchall():
        a = f"{k}:{v}"
        if ex == "exclusive":
            excl_owner[a] = c
            continue
        held_by[a].add(c)
        (comp_sessions if k == "session" else comp_entries)[c].add(a)

    def _by_number(entries):
        """entry anchors -> {(number, class): {team}}"""
        out = collections.defaultdict(set)
        for a in entries:
            parts = a[len("entry:"):].split("#")
            number, team = parts[0], parts[1] if len(parts) > 1 else ""
            car_class = parts[2] if len(parts) > 2 else ""
            out[(number, car_class)].add(team)
        return out

    def population_agrees(g_entries, c_entries):
        """
        Continuity of the entry population.

        Session overlap says two things ran at the same place; it does not say
        they are the same competition. A support series confined to Free
        Practice 1 has a session set contained in the main competition's, and
        containment alone would match it. Its entry population would not.

        TRUE   the fields meet on (number, class) and at least one of those
               shared entries carries the same team
        FALSE  the fields are disjoint on (number, class) — a different field
        None   one side has no population, or the fields meet but every shared
               number is fielded by a differently spelled team. That shape is a
               rename as often as a replacement, and this model has held from
               the start that a team name is an attribute of an entry and never
               its identity, so it decides nothing here.
        """
        if not g_entries or not c_entries:
            return None
        g_map, c_map = _by_number(g_entries), _by_number(c_entries)
        shared = set(g_map) & set(c_map)
        if not shared:
            return False
        if any(g_map[key] & c_map[key] for key in shared):
            return True
        return None

    def sessions_agree(g_sessions, c_sessions):
        """TRUE when the session sets meet, FALSE when disjoint, None when unknown."""
        if not g_sessions or not c_sessions:
            return None
        if g_sessions & c_sessions:
            return True
        return False

    # An exclusive anchor identifies on its own. A shared one never does: it
    # nominates candidates, which then have to pass BOTH the containment test
    # on sessions and independent corroboration from the entry population.
    def shared_candidates(g):
        g_sess = {a for a in g["shared"] if a.startswith("session:")}
        g_ent = {a for a in g["shared"] if a.startswith("entry:")}
        cand = {c for a in g["shared"] for c in held_by.get(a, ())}
        qualified, refuted = [], []
        for c in sorted(cand):
            contained = comp_sessions[c] <= g_sess or g_sess <= comp_sessions[c]
            agrees = population_agrees(g_ent, comp_entries[c])
            if agrees is False:
                refuted.append(c)
            elif contained and agrees is True:
                qualified.append(c)
        return cand, qualified, refuted

    comp_exclusive = collections.defaultdict(set)
    for anchor, c in excl_owner.items():
        comp_exclusive[c].add(anchor)

    def continuity(g, c):
        """
        Whether a shared exclusive identifier is enough to go on being the same
        competition.

        Five things are weighed separately, and an identifier intersection is
        only the first of them. On its own it never confirms historical
        identity: the source can reassign an identifier from one entry list to
        another, and a bare intersection cannot tell that from a list that
        simply grew or shrank.

        Returns (verdict, evidence) with verdict in
        'corroborada' | 'contraditoria' | 'insuficiente'.
        """
        g_sess = {a for a in g["shared"] if a.startswith("session:")}
        g_ent = {a for a in g["shared"] if a.startswith("entry:")}

        kept = g["exclusive"] & comp_exclusive[c]
        dropped = comp_exclusive[c] - g["exclusive"]
        added = g["exclusive"] - comp_exclusive[c]
        contained = not dropped or not added

        population = population_agrees(g_ent, comp_entries[c])
        sessions = sessions_agree(g_sess, comp_sessions[c])
        reassignment = bool(dropped) and bool(added)

        evidence = {
            "identificadores_mantidos": len(kept),
            "identificadores_perdidos": len(dropped),
            "identificadores_novos": len(added),
            "contencao": contained,
            "populacao": population,
            "sessoes": sessions,
            "sinal_de_reatribuicao": reassignment,
        }

        # A contradiction anywhere stops the match, whatever else agrees.
        if population is False or sessions is False:
            return "contraditoria", evidence

        # Corroboration has to come from something other than the identifiers.
        # Absence of evidence is not corroboration, so None does not count.
        if population is True or sessions is True:
            return "corroborada", evidence

        return "insuficiente", evidence

    def describe(ev):
        def tf(v):
            return "sim" if v is True else "nao" if v is False else "sem evidencia"
        return (f"identificadores mantidos={ev['identificadores_mantidos']}, "
                f"perdidos={ev['identificadores_perdidos']}, "
                f"novos={ev['identificadores_novos']}; "
                f"contencao={tf(ev['contencao'])}; "
                f"populacao={tf(ev['populacao'])}; "
                f"sessoes={tf(ev['sessoes'])}; "
                f"reatribuicao={tf(ev['sinal_de_reatribuicao'])}")

    reaches, how, verdicts = [], [], []
    for g in groups:
        if g["exclusive"]:
            hit = sorted({excl_owner[a] for a in g["exclusive"] if a in excl_owner})
            if hit:
                judged = {c: continuity(g, c) for c in hit}
                good = [c for c in hit if judged[c][0] == "corroborada"]
                if good:
                    g["_evidence"] = {c: judged[c][1] for c in good}
                    reaches.append(good)
                    how.append("exclusiva")
                else:
                    g["_evidence"] = {c: judged[c][1] for c in hit}
                    g["_why"] = "; ".join(
                        f"competicao {c}: {judged[c][0]} ({describe(judged[c][1])})"
                        for c in hit)
                    reaches.append(hit)
                    how.append("exclusiva-sem-continuidade")
                verdicts.append(judged)
                continue
            verdicts.append({})
            # Identifiers that match nothing. Either the source issued a new
            # entry list for the same competition, or this really is a new
            # competition. Shared evidence cannot tell those apart, so when it
            # points at an existing competition the decision is not taken here.
            _, qualified, _ = shared_candidates(g)
            reaches.append(qualified)
            how.append("exclusiva-nova" if qualified else "exclusiva")
            continue
        g_sess = {a for a in g["shared"] if a.startswith("session:")}
        g_ent = {a for a in g["shared"] if a.startswith("entry:")}
        cand = {c for a in g["shared"] for c in held_by.get(a, ())}
        qualified, refuted = [], []
        for c in sorted(cand):
            contained = comp_sessions[c] <= g_sess or g_sess <= comp_sessions[c]
            agrees = population_agrees(g_ent, comp_entries[c])
            if agrees is False:
                refuted.append(c)
            elif contained and agrees is True:
                qualified.append(c)
        if qualified:
            reaches.append(qualified)
            how.append("compartilhada")
        elif cand and len(refuted) == len(cand):
            # Every candidate is positively a different field. Sharing a
            # session with them is not a reason to withhold a new competition.
            reaches.append([])
            how.append("compartilhada-refutada")
        elif cand:
            reaches.append(sorted(cand))
            how.append("compartilhada-insuficiente")
        else:
            reaches.append([])
            how.append("compartilhada")

    claimed = collections.defaultdict(list)
    for i, comps in enumerate(reaches):
        for c in comps:
            claimed[c].append(i)

    report = {"reused": [], "created": [], "review": []}

    for i, g in enumerate(groups):
        hit = reaches[i]
        if how[i] == "exclusiva-sem-continuidade":
            _review(cur, event_id, g, hit, "ambiguous_match",
                    "identificador exclusivo em comum, mas sem continuidade corroborada. "
                    "Uma intersecao de identificadores nao distingue uma lista que mudou de "
                    "uma reatribuicao do identificador para outra competicao. "
                    + g.get("_why", ""))
            report["review"].append((i, hit, "continuidade nao corroborada"))
            continue
        if how[i] == "exclusiva-nova":
            _review(cur, event_id, g, hit, "ambiguous_match",
                    "identificadores inteiramente novos, mas sessoes e populacao coincidem com "
                    "competicao existente: lista reemitida para a mesma competicao ou competicao "
                    "nova com o mesmo campo — a evidencia nao distingue")
            report["review"].append((i, hit, "identificadores substituidos"))
            continue
        if how[i] == "compartilhada-insuficiente":
            _review(cur, event_id, g, hit, "ambiguous_match",
                    "apenas evidencia compartilhada: as sessoes coincidem mas a populacao de "
                    "inscricoes nao corrobora nem refuta. Compartilhar sessao nao identifica "
                    "competicao, e conter um conjunto de sessoes no outro tambem nao")
            report["review"].append((i, hit, "corroboracao insuficiente"))
            continue
        if len(hit) > 1:
            _review(cur, event_id, g, hit, "ambiguous_match",
                    f"a descoberta alcanca {len(hit)} competicoes existentes")
            report["review"].append((i, hit, "alcanca varias"))
            continue
        if len(hit) == 1 and len(claimed[hit[0]]) > 1:
            _review(cur, event_id, g, hit, "ambiguous_match",
                    f"{len(claimed[hit[0]])} descobertas alcancam a competicao {hit[0]}")
            report["review"].append((i, hit, "varias descobertas, uma competicao"))
            continue

        fp = fingerprint(g)
        if len(hit) == 1:
            comp_id = hit[0]
            # A corroborated reuse records what corroborated it, so the decision
            # can be read back later rather than inferred from the outcome.
            note = g.get("evidence")
            ev = (g.get("_evidence") or {}).get(comp_id)
            if ev:
                note = ((note + " | ") if note else "") + \
                       "continuidade corroborada: " + describe(ev)
            cur.execute(
                "UPDATE competitions SET evidence_fingerprint = %s, display_name = %s, "
                "resolution = %s, evidence = %s, last_seen_at = NOW() WHERE id = %s "
                "RETURNING identity_uid", (fp, g["display"], g["kind"], note, comp_id))
            uid = str(cur.fetchone()[0])
            report["reused"].append((i, comp_id, uid, fp))
        else:
            # The label is derived from the identity, never from the evidence.
            # Deriving it from the anchors collided the moment two provisional
            # competitions shared the same single session anchor, and it would
            # also have changed the label whenever a session was added. Being a
            # function of a unique uid, it cannot collide and cannot drift.
            uid = uuid.uuid4()
            cur.execute(
                "INSERT INTO competitions (event_id, identity_uid, label, resolution, "
                "evidence, evidence_fingerprint, display_name, last_seen_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, NOW()) RETURNING id",
                (event_id, str(uid), label_for(uid), g["kind"],
                 g.get("evidence"), fp, g["display"]))
            comp_id = cur.fetchone()[0]
            report["created"].append((i, comp_id, str(uid), fp))

        seen = []
        for kind, value, ex in anchors_of(g):
            seen.append(f"{kind}:{value}")
            cur.execute(
                "INSERT INTO competition_anchors "
                "(competition_id, event_id, anchor_kind, anchor_value, exclusivity) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (competition_id, anchor_kind, anchor_value) "
                "DO UPDATE SET last_seen_at = NOW(), state = 'current', superseded_at = NULL",
                (comp_id, event_id, kind, value, ex))
        # Anything this competition used to carry and no longer does becomes
        # history. The row is kept — provenance is not deleted — but it stops
        # being able to confirm a match.
        cur.execute(
            "UPDATE competition_anchors SET state = 'superseded', superseded_at = NOW() "
            "WHERE competition_id = %s AND state = 'current' "
            "  AND (anchor_kind || ':' || anchor_value) <> ALL(%s::text[])",
            (comp_id, seen))
    return report


def _review(cur, event_id, group, candidate_ids, kind, rationale):
    cur.execute(
        "INSERT INTO competition_match_reviews "
        "(event_id, kind, discovery_key, candidate_ids, evidence, rationale) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (event_id, kind, discovery_key) DO UPDATE "
        "SET candidate_ids = EXCLUDED.candidate_ids, rationale = EXCLUDED.rationale, "
        "    detected_at = NOW()",
        (event_id, kind, discovery_key(group), candidate_ids,
         "ancoras: " + ", ".join(group["anchors"][:8]), rationale))


def record_merge_hypothesis(cur, event_id, a_key, b_key, rationale):
    cur.execute(
        "INSERT INTO competition_match_reviews "
        "(event_id, kind, discovery_key, candidate_ids, evidence, rationale) "
        "VALUES (%s, 'merge_hypothesis', %s, '{}', %s, %s) "
        "ON CONFLICT (event_id, kind, discovery_key) DO UPDATE "
        "SET rationale = EXCLUDED.rationale, detected_at = NOW()",
        (event_id, hashlib.sha256((a_key + "||" + b_key).encode()).hexdigest(),
         f"{a_key} contido em {b_key}", rationale))

"""
OpenWEC — Phase 4: Team Normalization and Deduplication

Fixes team name inconsistencies across seasons and series:
  - Capitalisation: "AF CORSE" → "AF Corse"
  - Duplicates: multiple entries for same team → merge into one

Strategy:
  - clean_key = lowercase + alphanumeric only (used for matching)
  - canonical name = picked by scoring (Title Case > ALL CAPS > other)
  - all car references updated to canonical team_id
  - duplicate team entries deleted

Usage — as a module, not as a file. The script imports `database.db`, which
resolves only when the repository root is on the path, and running the file
directly puts its own directory there instead:

    python -m database.enrichment.normalize_teams --dry-run
    python -m database.enrichment.normalize_teams

Exit codes:
    0  nothing failed, including when there was nothing to merge
    1  it ran and at least one merge failed; the summary names which
    3  every merge went through and a resource refused to close afterwards.
       Separate from 1 on purpose: the data is in the state the merges left
       it, and a caller that only cares whether the rows are right can tell
       that apart from a merge that did not happen

The output is ASCII on purpose. The decorations this file used to print — an
arrow, a tick, a cross — have no cp1252 encoding, which is what a Windows
console uses by default, and printing one raised UnicodeEncodeError before a
single merge had been attempted.
"""

import argparse
import re
import sys
import psycopg2
import psycopg2.extras
from collections import defaultdict


from database.db import DB_CONFIG
from database.loader.load_classification import (
    CAR_COLUMNS, try_lock_car_identity,
)


class FusaoRecusada(Exception):
    """A fusao criaria identidade concorrente dentro de uma sessao."""


# As colunas da identidade, com `team_id` ja remapeado para o canonico. E a
# identidade que o carro **passaria a ter**, que e a que pode colidir.
_IDENTIDADE_POS_FUSAO = ", ".join(
    "%(canon)s::int" if coluna == "team_id" else f"c.{coluna}"
    for coluna in CAR_COLUMNS
)

# Toda sessao que aponta para um carro da equipe duplicada, por resultado ou
# por volta. E o mesmo criterio de pertencimento que `car_session_scope` usa
# no carregador e no guarda de identidade.
AFETADOS = f"""
    SELECT DISTINCT s.session_id, {_IDENTIDADE_POS_FUSAO}
      FROM cars c
      JOIN LATERAL (
            SELECT r.session_id FROM results r WHERE r.car_id = c.id
             UNION
            SELECT l.session_id FROM laps    l WHERE l.car_id = c.id
           ) s ON TRUE
     WHERE c.team_id = %(dup)s
"""

# Uma colisao interna: dois carros distintos da mesma sessao que, **depois**
# da fusao, carregariam a mesma identidade — e que antes dela nao carregavam,
# porque apontavam para equipes diferentes.
#
# A ultima condicao e o que separa o que a fusao cria do que ja existia. Dois
# carros que ja estavam os dois na equipe duplicada ja colidiam antes; a
# fusao nao e responsavel por eles e nao e aqui que isso se resolve.
COLISOES = f"""
    WITH envolvidos AS (
        SELECT s.session_id, c.id AS car_id, c.team_id AS equipe_antes,
               ({_IDENTIDADE_POS_FUSAO}) AS depois
          FROM cars c
          JOIN LATERAL (
                SELECT r.session_id FROM results r WHERE r.car_id = c.id
                 UNION
                SELECT l.session_id FROM laps    l WHERE l.car_id = c.id
               ) s ON TRUE
         WHERE c.team_id IN (%(dup)s, %(canon)s)
    )
    SELECT session_id, depois::text,
           array_agg(DISTINCT car_id ORDER BY car_id) AS carros
      FROM envolvidos
     GROUP BY session_id, depois
    HAVING count(DISTINCT car_id) > 1
       AND count(DISTINCT equipe_antes) > 1
     ORDER BY session_id
"""


def identidades_afetadas(cur, dup_id: int, canonical_id: int) -> list[tuple]:
    """
    As (sessao, identidade) que a fusao alteraria, em ordem determinista.

    A ordem e determinista para que duas fusoes simultaneas nao esperem uma
    pela outra em circulo. Ela **nao** resolve o circulo com a ingestao, que
    resolve as identidades na ordem do arquivo e nao tem como ordena-las:
    contra ela, o que protege e nao esperar (`try_lock_car_identity`).
    """
    cur.execute(AFETADOS, {"dup": dup_id, "canon": canonical_id})
    return sorted((linha[0], tuple(linha[1:])) for linha in cur.fetchall())


def colisoes_internas(cur, dup_id: int, canonical_id: int) -> list[tuple]:
    """Os grupos que a fusao juntaria dentro de uma sessao. Vazio e o normal."""
    cur.execute(COLISOES, {"dup": dup_id, "canon": canonical_id})
    return [(linha[0], linha[2]) for linha in cur.fetchall()]


def toma_a_equipe(cur, dup_id: int) -> None:
    """
    A linha da equipe que vai ser apagada, sem esperar por ela.

    `cars.team_id` referencia `teams(id)`, entao toda ingestao que insere um
    carro segura um KEY SHARE na linha da equipe ate comitar — e o DELETE
    desta fusao precisa do exclusivo. Esperar por ele fecharia o segundo
    circulo: a ingestao ja tem o KEY SHARE e vai pedir um lock consultivo que
    a fusao segura. `MEASURED`: nessa ordem o PostgreSQL aborta a
    **ingestao**, nao a fusao, que e o pior dos dois resultados.

    Entao a fusao nao espera por nada. Ou toma tudo de imediato, ou nao
    acontece.
    """
    try:
        cur.execute("SELECT 1 FROM teams WHERE id = %s FOR UPDATE NOWAIT",
                    (dup_id,))
    except psycopg2.errors.LockNotAvailable as exc:
        raise FusaoRecusada(
            f"a equipe {dup_id} esta em uso por outra transacao; a fusao nao "
            f"espera pela linha, para nao formar espera circular com a "
            f"ingestao") from exc


def _primeira_linha(erro: Exception) -> str:
    """
    One line of a message that psycopg2 writes over several.

    A foreign key violation arrives as the error, then DETAIL, then sometimes
    a hint. The whole text is printed where the failure happens; the summary
    at the end needs the part that identifies it, on one line each.
    """
    texto = " ".join(str(erro).split())
    return texto or erro.__class__.__name__


def clean_key(name: str) -> str:
    """Strips non-alphanumeric and lowercases for matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def name_score(name: str) -> int:
    """
    Scores a name for canonicality. Higher = better.
    Title Case with spaces > mixed case > ALL CAPS > all lower
    """
    if not name:
        return 0
    score = 0
    # Has at least one space (not a single word)
    if " " in name:
        score += 10
    # Not all uppercase
    if name != name.upper():
        score += 5
    # Not all lowercase
    if name != name.lower():
        score += 3
    # Starts with uppercase
    if name[0].isupper():
        score += 2
    # Title-case-ish (most words capitalized)
    words = name.split()
    cap_words = sum(1 for w in words if w and w[0].isupper())
    if words and cap_words / len(words) > 0.6:
        score += 4
    return score


def pick_canonical(names: list[str]) -> str:
    """Picks the best name from a group of duplicates."""
    return max(names, key=name_score)


def find_duplicate_groups(cur) -> list[dict]:
    """Returns groups of team IDs/names that share the same clean_key."""
    cur.execute("SELECT id, name FROM teams ORDER BY id")
    rows = cur.fetchall()

    groups_by_key: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        key = clean_key(row["name"])
        groups_by_key[key].append({"id": row["id"], "name": row["name"]})

    duplicates = []
    for key, group in groups_by_key.items():
        if len(group) > 1:
            canonical_name = pick_canonical([g["name"] for g in group])
            canonical_id   = next(g["id"] for g in group if g["name"] == canonical_name)
            # If tie, pick lowest id
            if sum(1 for g in group if g["name"] == canonical_name) > 1:
                canonical_id = min(g["id"] for g in group if g["name"] == canonical_name)

            duplicates.append({
                "clean_key":      key,
                "canonical_id":   canonical_id,
                "canonical_name": canonical_name,
                "duplicates":     [g for g in group if g["id"] != canonical_id],
            })

    return sorted(duplicates, key=lambda x: x["canonical_name"])


def run(dry_run: bool = False) -> int:
    """
    Merges teams whose names differ only in spelling. Returns an exit code.

    Each merge is its own transaction, as it always was: the UPDATE that
    repoints `cars` and the DELETE that removes the duplicate commit together
    or not at all, and a failure rolls that pair back without reaching the
    merges already committed. What changes here is only that the failure
    reaches the caller — the run used to end with `Errors: 1` and exit 0.

    Closing the cursors and the connection is reported too, under its own
    code, because a connection that has already dropped says so there and
    nowhere else. It never displaces a merge or rollback failure: those keep
    the exit code, and the closing trouble is printed under them.

    `cars` stays the only table repointed. `entries` and `entry_team_history`
    reference `teams` as well, so a team either of them holds cannot be
    deleted and that merge fails on the foreign key. That is the current
    policy and not an oversight to settle here: what a team merge means for a
    competitive entry is the entry model's question. The failure is reported
    with its reason instead of being counted and forgotten.
    """
    print("=" * 60)
    print("OpenWEC - Phase 4: Team Normalization")
    print(f"  Dry run: {dry_run}")
    print("=" * 60)

    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT COUNT(*) AS n FROM teams")
    total_before = cur.fetchone()["n"]
    print(f"\n  Teams before: {total_before}")

    groups = find_duplicate_groups(cur)
    total_to_delete = sum(len(g["duplicates"]) for g in groups)

    print(f"  Duplicate groups: {len(groups)}")
    print(f"  Teams to merge:   {total_to_delete}")
    print(f"  Teams after:      {total_before - total_to_delete}")

    print(f"\n[GROUPS] (showing first 20)")
    for g in groups[:20]:
        print(f"\n  canonical: [{g['canonical_id']:5d}] '{g['canonical_name']}'")
        for d in g["duplicates"]:
            print(f"    merge <- [{d['id']:5d}] '{d['name']}'")

    if dry_run:
        print(f"\n[DRY RUN] No changes written.")
        cur.close()
        conn.close()
        return 0

    print(f"\n[APPLY] Merging {total_to_delete} duplicate teams...")

    write_cur = conn.cursor()
    merged = 0
    falhas: list[tuple[int, int, str]] = []
    interrompido = False

    for g in groups:
        if interrompido:
            break
        canonical_id = g["canonical_id"]
        for dup in g["duplicates"]:
            dup_id = dup["id"]
            try:
                # R1 — coordenacao com `upsert_car`, dentro desta transacao.
                #
                # Fundir equipes **muda a identidade** de carros ja gravados:
                # `team_id` e uma das oito colunas de CAR_COLUMNS. Sem isto, a
                # fusao escapava das duas protecoes que a ingestao tem — o
                # lock por (sessao, identidade) e o guarda de identidade — e
                # podia deixar dois carros de uma sessao indistinguiveis, que
                # e exatamente o que o guarda recusa criar.
                #
                # Os locks vem primeiro: uma ingestao que chegue depois espera
                # aqui, e a checagem abaixo passa a ver um estado estavel.
                #
                # Mas a fusao nao espera por eles. Ela toma varios numa
                # transacao so, e a ingestao toma os seus na ordem do arquivo:
                # duas ordens que nao se conhecem sao uma espera circular, e o
                # PostgreSQL ja a demonstrou com `deadlock detected`. Entao,
                # se um lock nao esta livre, a fusao desiste.
                for sessao, identidade in identidades_afetadas(
                        write_cur, dup_id, canonical_id):
                    if not try_lock_car_identity(write_cur, sessao, identidade):
                        raise FusaoRecusada(
                            f"a identidade do carro {identidade[0]} na sessao "
                            f"{sessao} esta em uso por outra transacao; a "
                            f"fusao nao espera, para nao formar espera "
                            f"circular com a ingestao")

                colisoes = colisoes_internas(write_cur, dup_id, canonical_id)
                if colisoes:
                    # Nao se escolhe um dos carros, nem pelo menor id: a
                    # fusao e que nao acontece. Qual dos dois e a inscricao
                    # publicada nao se decide aqui.
                    detalhe = "; ".join(
                        f"sessao {s}: carros {list(ids)}" for s, ids in colisoes[:5])
                    raise FusaoRecusada(
                        f"a fusao daria a dois carros da mesma sessao uma "
                        f"identidade so — {detalhe}")

                # O ultimo lock que falta, tambem sem esperar: a linha da
                # equipe que o DELETE abaixo vai apagar.
                toma_a_equipe(write_cur, dup_id)

                # Update all cars pointing to the duplicate
                write_cur.execute("""
                    UPDATE cars SET team_id = %s WHERE team_id = %s
                """, (canonical_id, dup_id))

                # Delete the duplicate team
                write_cur.execute("DELETE FROM teams WHERE id = %s", (dup_id,))
                conn.commit()

                merged += 1

            except Exception as falha:
                # The original error first and whole, before anything else can
                # go wrong: a rollback that fails must not be what the reader
                # is left holding.
                #
                # Uma recusa nao e um defeito, e e rotulada a parte para que
                # quem le o relatorio nao a leia como um erro de banco. As
                # duas contam como fusao nao aplicada, que e o que importa
                # para o codigo de saida.
                rotulo = ("REFUSED" if isinstance(falha, FusaoRecusada)
                          else "ERROR")
                print(f"  [{rotulo}] merge {dup_id} -> {canonical_id}: {falha}")
                motivo = _primeira_linha(falha)
                try:
                    conn.rollback()
                except Exception as falha_do_rollback:
                    # The connection cannot be trusted any more, and every
                    # merge left would fail the same way. Stop, and say why.
                    razao_do_rollback = _primeira_linha(falha_do_rollback)
                    falhas.append((dup_id, canonical_id,
                                   f"{motivo} | o rollback tambem falhou: "
                                   f"{razao_do_rollback}"))
                    print(f"  [ABORT] o rollback falhou ({razao_do_rollback}); "
                          f"as fusoes restantes nao serao tentadas")
                    interrompido = True
                    break
                falhas.append((dup_id, canonical_id, motivo))

    # Every resource is closed even when an earlier one refused to, and a
    # refusal is recorded rather than swallowed: closing is where a connection
    # that has already gone announces itself, and that announcement was the
    # one piece of evidence this loop used to throw away. It cannot change
    # what was committed, and it is not allowed to displace the merge or
    # rollback failures reported below it either.
    problemas_ao_fechar: list[str] = []
    for nome, fechavel in (("write_cur", write_cur), ("cur", cur),
                           ("conn", conn)):
        try:
            fechavel.close()
        except Exception as falha_ao_fechar:          # noqa: BLE001
            problemas_ao_fechar.append(
                f"{nome}: {_primeira_linha(falha_ao_fechar)}")

    print(f"\n  Merged:  {merged}")
    print(f"  Deleted: {merged}")
    print(f"  Errors:  {len(falhas)}")
    print(f"  Teams remaining: {total_before - merged}")

    if falhas:
        print(f"\n[FAILURES] {len(falhas)} merge(s) nao foram aplicados")
        for de, para, motivo in falhas:
            print(f"  team {de} -> {para}: {motivo}")
        if interrompido:
            print("  (a execucao parou antes de tentar as fusoes restantes)")

    if problemas_ao_fechar:
        print(f"\n[CLOSE] {len(problemas_ao_fechar)} recurso(s) nao fecharam "
              f"sem erro")
        for linha in problemas_ao_fechar:
            print(f"  {linha}")
        print("  o que foi comitado continua comitado; nenhuma fusao depende "
              "deste fechamento")

    # A merge that failed is the graver fact and keeps the code, so a bad
    # shutdown can never stand in front of it.
    if falhas:
        return 1
    if problemas_ao_fechar:
        return 3
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Team Normalization")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run))
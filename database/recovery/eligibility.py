"""
OpenWEC — which contaminated sessions the existing recoverer would accept.

51 sessions hold a lap log where their classification should be. One of them,
2226, was recovered in PR #53. This answers the operational question that
comes next: of the 50 still pending, how many would the code we already have
take, and what stops the rest.

Nothing here decides eligibility on its own. The verdict comes from running
`plan_recovery` and `assert_complete_match` — the same two functions the
recoverer runs before it writes — and reporting what they say. A session is
`ELIGIBLE` when both pass, which is a stronger statement than two counts
matching: the numbers have to be the same numbers, each one carried by a
single `car_id`.

Read only. No statement here writes, and the connection is opened with
`default_transaction_read_only`, so one that tried would fail rather than
succeed quietly.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "loader"))

from load_classification import (  # noqa: E402
    as_int,
    field,
    get_final_classification,
    is_classification_file,
    read_csv,
)
from database.recovery.recover_classification import (  # noqa: E402
    Plan,
    RecoveryBlocked,
    assert_complete_match,
    plan_recovery,
)

ELIGIBLE = "ELIGIBLE"
BLOCKED = "BLOCKED"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

CATALOGS = {
    "ELMS":      Path("catalog/elms/catalog.json"),
    "LEMANSCUP": Path("catalog/lemanscup/catalog.json"),
    "IMSA":      Path("catalog/imsa/catalog.json"),
    "ALMS":      Path("catalog/alms/catalog.json"),
    "WEC":       Path("catalog/wec/wec_catalog.json"),
}

USER_AGENT = "openwec-eligibility-scan/1.0"
MAX_BYTES = 4_000_000
TIMEOUT = 30
PAUSE = 1.5


# ── The verdict, taken from the recoverer's own rules ────────

@dataclass
class Eligibility:
    session_id: int
    series: str
    season: str
    event: str
    session_name: str
    source_file: str | None = None
    source_url: str | None = None
    cars_in_source: int | None = None
    cars_with_laps: int | None = None
    duplicated_in_source: list[str] = dataclass_field(default_factory=list)
    numbers_multi_car: list[str] = dataclass_field(default_factory=list)
    source_without_laps: list[str] = dataclass_field(default_factory=list)
    laps_without_source: list[str] = dataclass_field(default_factory=list)
    laps_count_divergences: int | None = None
    verdict: str = SOURCE_UNAVAILABLE
    reason: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def verdict_for(cur, session_id: int, rows: list[dict] | None
                ) -> tuple[str, str | None, Plan | None]:
    """
    What the recoverer would answer for this session, and why.

    The two calls are the recoverer's own, in the order it makes them. A
    session that passes both is one `recover_session` would write; a session
    that fails either is one it would refuse, with the same message.
    """
    if not rows:
        return SOURCE_UNAVAILABLE, "sem fonte utilizavel", None

    try:
        plan = plan_recovery(cur, session_id, rows)
    except RecoveryBlocked as exc:
        return BLOCKED, str(exc), None

    try:
        assert_complete_match(plan)
    except RecoveryBlocked as exc:
        return BLOCKED, str(exc), plan

    return ELIGIBLE, None, plan


def laps_divergences(rows: list[dict], por_numero: dict[str, int]) -> int:
    """
    How many matched cars the source credits with a different lap count.

    Not a gate, and not part of the recoverer's rules: a measurement that
    separates a source describing the end of the race from one describing an
    hour mark along the way. Monza 2018 is the case that showed why — its
    `Hour 1` file disagrees with the lap log on 24 of 27 cars, its `Hour 2`
    file on none.
    """
    divergentes = 0
    for row in rows:
        numero = row.get("NUMBER", "").strip()
        se_banco = por_numero.get(numero)
        if se_banco is None:
            continue
        da_fonte = as_int(field(row, "LAPS"))
        if da_fonte is not None and da_fonte != se_banco:
            divergentes += 1
    return divergentes


def reason_group(linha: Eligibility) -> str:
    """
    The family a refusal belongs to, for counting.

    A session with no laps at all is kept apart from one whose numbers merely
    disagree. Both come back from `assert_complete_match` as an incomplete
    match, but they are not the same problem: one has a classification and
    nothing to attach it to, the other has two sides that do not line up.
    """
    if linha.verdict == ELIGIBLE:
        return "—"
    if not linha.cars_with_laps:
        return "sem voltas no banco"

    reason = linha.reason or ""
    if "mais de um car_id" in reason:
        return "numero em mais de um car_id"
    if "mais de uma vez" in reason:
        return "numero repetido na fonte"
    if "sem NUMBER" in reason:
        return "linha da fonte sem NUMBER"
    if "pareamento incompleto" in reason:
        return "pareamento incompleto"
    if "fonte" in reason:
        return "sem fonte utilizavel"
    return "outro"


# ── Finding the published source for a session ───────────────

def catalog_files(catalogo: dict, season: str, event: str, session: str
                  ) -> list[dict]:
    """The catalogue's file list for one session, or an empty list."""
    try:
        sessoes = (catalogo[season]["events"][event]["championships"])
    except KeyError:
        return []
    for campeonato in sessoes.values():
        entrada = campeonato.get("sessions", {}).get(session)
        if entrada:
            return entrada.get("files", []) or []
    return []


def choose_source(arquivos: list[dict], area: Path) -> tuple[str, str] | None:
    """
    Which of the catalogue's files the loader would read, and its URL.

    The choice is not made here: the names are laid out as the publisher
    spells them — the hour folder is part of `filename` — and
    `get_final_classification` picks among them exactly as it does on disk.
    Reusing it is the point; a second selection rule would be a second thing
    to keep true.
    """
    candidatos = {}
    for arquivo in arquivos:
        nome = (arquivo.get("filename") or "").strip()
        url = (arquivo.get("url") or "").strip()
        if not nome or not url:
            continue
        destino = area / nome
        if not is_classification_file(Path(destino.name)):
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"")
        candidatos[destino.resolve()] = (nome, url)

    if not candidatos:
        return None

    escolhido = get_final_classification(area)
    if escolhido is None:
        return None
    return candidatos.get(escolhido.resolve())


def fetch_rows(url: str, area: Path) -> list[dict] | None:
    """
    Reads one published CSV into a scratch file outside `raw/`.

    `read_csv` takes a path and handles the encodings this corpus uses, so
    the bytes land in a temporary file rather than being decoded twice by two
    different rules.
    """
    requisicao = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(requisicao, timeout=TIMEOUT) as resposta:
            dados = resposta.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if len(dados) > MAX_BYTES:
        return None

    temporario = area / "source.csv"
    temporario.write_bytes(dados)
    try:
        return read_csv(temporario)
    finally:
        temporario.unlink(missing_ok=True)


# ── The scan ─────────────────────────────────────────────────

CONTAMINATED_SQL = """
WITH tup AS (
  SELECT c.id, (c.number,c.team_id,c.vehicle,c.car_class,
                c.car_group,c.tires,c.imsa_car_id,c.imsa_team_id)::text AS t
  FROM cars c),
g AS (
  SELECT r.session_id, tup.t, count(*) AS n
  FROM results r JOIN tup ON tup.id = r.car_id GROUP BY 1,2),
m AS (SELECT session_id, max(n) AS maior FROM g GROUP BY 1)
SELECT s.id, sr.key::text, se.raw_id, e.raw_id, s.raw_id
FROM m
JOIN sessions s ON s.id = m.session_id
JOIN events  e  ON e.id = s.event_id
JOIN seasons se ON se.id = e.season_id
JOIN series  sr ON sr.id = se.series_id
WHERE m.maior >= %s
ORDER BY sr.key, se.raw_id, e.raw_id, s.raw_id
"""

# The two populations are disjoint by a wide margin: outside the list the
# largest (session, car tuple) group is 7, inside it the smallest is 26.
# PR #43 §3.1 measured both, and a second, independent walk of `raw/`
# selected the same set.
CONTAMINATION_FLOOR = 26


def contaminated_sessions(cur, floor: int = CONTAMINATION_FLOOR) -> list[tuple]:
    cur.execute(CONTAMINATED_SQL, (floor,))
    return cur.fetchall()


def scan(cur, area: Path, limite: int | None = None,
         somente: set[int] | None = None) -> list[Eligibility]:
    catalogos: dict[str, dict] = {}
    resultados: list[Eligibility] = []

    alvos = contaminated_sessions(cur)
    if somente is not None:
        alvos = [a for a in alvos if a[0] in somente]
    if limite:
        alvos = alvos[:limite]

    for indice, (sid, serie, season, event, session_raw) in enumerate(alvos):
        linha = Eligibility(sid, serie, season, event, session_raw)

        caminho = CATALOGS.get(serie)
        if caminho and serie not in catalogos and caminho.exists():
            catalogos[serie] = json.loads(caminho.read_text(encoding="utf-8"))

        arquivos = catalog_files(catalogos.get(serie, {}), season, event,
                                 session_raw)
        pasta = area / str(sid)
        pasta.mkdir(parents=True, exist_ok=True)
        escolha = choose_source(arquivos, pasta)

        rows = None
        if escolha:
            linha.source_file, linha.source_url = escolha
            if indice:
                time.sleep(PAUSE)
            rows = fetch_rows(linha.source_url, pasta)
            if rows is None:
                linha.reason = "a fonte nao pode ser lida"

        if rows:
            linha.cars_in_source = len(rows)
            numeros = [r.get("NUMBER", "").strip() for r in rows]
            linha.duplicated_in_source = sorted(
                {n for n in numeros if n and numeros.count(n) > 1})

        cur.execute(
            "SELECT c.number, count(DISTINCT l.car_id), count(*) "
            "FROM laps l JOIN cars c ON c.id = l.car_id "
            "WHERE l.session_id = %s GROUP BY c.number", (sid,))
        medido = {n: (carros, voltas) for n, carros, voltas in cur.fetchall()}
        linha.cars_with_laps = sum(carros for carros, _ in medido.values())
        linha.numbers_multi_car = sorted(
            n for n, (carros, _) in medido.items() if carros > 1)
        if rows:
            linha.laps_count_divergences = laps_divergences(
                rows, {n: voltas for n, (_, voltas) in medido.items()})

        veredito, motivo, plano = verdict_for(cur, sid, rows)
        linha.verdict = veredito
        linha.reason = motivo or linha.reason
        if plano is not None:
            linha.source_without_laps = sorted(plano.source_without_laps)
            linha.laps_without_source = sorted(plano.laps_without_source)

        resultados.append(linha)

    return resultados


# ── CLI ──────────────────────────────────────────────────────

def read_only_connection(cfg: dict):
    """A connection that cannot write, whatever is asked of it."""
    import psycopg2
    return psycopg2.connect(
        connect_timeout=5,
        options="-c default_transaction_read_only=on",
        **cfg,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5433)
    ap.add_argument("--dbname", default="openwec")
    ap.add_argument("--user", default="openwec")
    ap.add_argument("--password", default="")
    ap.add_argument("--area", type=Path, required=True,
                    help="scratch directory, outside raw/")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    cfg = {"host": args.host, "port": args.port, "dbname": args.dbname,
           "user": args.user, "password": args.password}
    conn = read_only_connection(cfg)
    try:
        with conn.cursor() as cur:
            linhas = scan(cur, args.area, args.limit,
                          set(args.only) if args.only else None)
    finally:
        conn.rollback()
        conn.close()

    for linha in linhas:
        print(f"{linha.session_id:>5} {linha.series:<10} {linha.season:<8} "
              f"{linha.event[:28]:<28} {linha.verdict:<19} "
              f"fonte={linha.cars_in_source} voltas={linha.cars_with_laps} "
              f"[{reason_group(linha)}] {linha.reason or ''}")

    contagem: dict[str, int] = {}
    for linha in linhas:
        chave = linha.verdict if linha.verdict == ELIGIBLE else reason_group(linha)
        contagem[chave] = contagem.get(chave, 0) + 1
    print()
    for chave, quantas in sorted(contagem.items(), key=lambda kv: -kv[1]):
        print(f"  {quantas:>3}  {chave}")

    if args.json:
        args.json.write_text(
            json.dumps([linha.as_dict() for linha in linhas],
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

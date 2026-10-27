"""
Sandbox for the loader suites that write.

Two gates stand between a test and a real table.

The first is the declaration the schema suites already use:
OPENWEC_TEST_DB_IS_DISPOSABLE. It is necessary and not sufficient — an
environment variable can be set by mistake, and a name is not a property of
danger: the CI service container is called `openwec` on port 5433, exactly
like the development database.

The second is the shape of what is already there. A database carrying the
historical corpus is not a fixture database whatever it is called, and these
suites refuse it. The CI fixtures seed single-digit rows; the development
instance holds hundreds of thousands.

Past both gates, nothing is written to `public` anyway. Each module gets its
own schema, created empty, used through `search_path`, and dropped whole at
the end. There is no delete to get wrong, because there is nothing to keep.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

DISPOSABLE_ENV = "OPENWEC_TEST_DB_IS_DISPOSABLE"

DB = {
    "host":     os.environ.get("DB_HOST", "127.0.0.1"),
    "port":     int(os.environ.get("DB_PORT", "5433")),
    "dbname":   os.environ.get("DB_NAME", "openwec"),
    "user":     os.environ.get("DB_USER", "openwec"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

# The fixtures in tests/fixtures.sql seed a handful of rows. Anything far above
# that is a database with history, and history is not ours to write next to.
# The development instance sits five orders of magnitude above this line.
FIXTURE_CEILING = {"results": 1_000, "laps": 1_000, "cars": 1_000}

# Tables the sandbox mirrors, in creation order. `LIKE` copies columns,
# defaults, checks and indexes, so the sandbox follows the real schema instead
# of restating it; foreign keys are deliberately not copied, which is what
# keeps a sandbox row from referring to a public one.
#
# series, seasons and events are here to be READ, not written by the loader:
# resolving a session joins all three, and a table missing from the sandbox
# would resolve through search_path to the one in public — that is, the
# suite would read the real corpus.
# ingest_log is written by the loader and read by nothing else here; it is
# mirrored so the provenance suite can exercise the real UNIQUE (source_url)
# instead of restating it.
SANDBOX_TABLES = ("series", "seasons", "events", "teams", "drivers",
                  "sessions", "cars", "results", "ingest_log")


def declared_disposable() -> bool:
    return os.environ.get(DISPOSABLE_ENV, "").strip().lower() in ("1", "true", "yes")


def refusal(counts: dict[str, int]) -> str | None:
    """
    Why this database must not be written to, or None if it may be.

    Pure, so the rule can be tested against the real numbers of the
    development instance without connecting to it.
    """
    heavy = [f"{t}={n}" for t, n in sorted(counts.items())
             if n > FIXTURE_CEILING.get(t, 0)]
    if heavy:
        return (
            "o destino carrega acervo historico (" + ", ".join(heavy) + "), "
            "acima do que as fixtures semeiam; nao e um banco de fixtures "
            f"por mais que {DISPOSABLE_ENV} diga o contrario"
        )
    return None


def historical_counts(cur) -> dict[str, int]:
    counts = {}
    for table in FIXTURE_CEILING:
        cur.execute(f"SELECT count(*) FROM public.{table}")
        counts[table] = cur.fetchone()[0]
    return counts


def connect_to_sandbox(sandbox: dict):
    """A connection whose unqualified writes land in the sandbox schema."""
    import psycopg2

    conn = psycopg2.connect(connect_timeout=5, **sandbox["db"])

    # Outside a transaction on purpose. SET is transactional in PostgreSQL, so
    # a path set inside one goes back to the default on ROLLBACK — and a suite
    # that rolls back to simulate a failed load would carry on writing to
    # public. In autocommit the statement reaches the session itself.
    conn.autocommit = True
    with conn.cursor() as cur:
        # public stays on the path for the enum types the loader casts to; it
        # comes second, so every table the loader names resolves to the sandbox.
        cur.execute(f'SET search_path TO "{sandbox["schema"]}", public')
    conn.autocommit = False
    return conn


RAW = Path(__file__).resolve().parents[2] / "raw"

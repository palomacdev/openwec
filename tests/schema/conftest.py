"""
Shared guard for the schema suites that write.

Nothing intrinsic tells a disposable database from a persistent one. A name is
not a property of danger — the CI service container is called `openwec` on port
5433, exactly like the development database — and neither is a row count, since
the CI fixtures seed cars, results and result_drivers themselves.

So it is declared, not guessed. A suite that writes depends on the
`disposable_db` fixture below, which skips unless
OPENWEC_TEST_DB_IS_DISPOSABLE is set. Skipping is the right default: pointing
pytest at an unknown database should be quiet and harmless, never a write and
never a red build.
"""
from __future__ import annotations

import os

import pytest

DISPOSABLE_ENV = "OPENWEC_TEST_DB_IS_DISPOSABLE"

HISTORICAL_TABLES = ("results", "laps", "result_drivers", "cars", "drivers")


@pytest.fixture(scope="module")
def disposable_db():
    """Skip the whole module unless the target database was declared disposable."""
    if os.environ.get(DISPOSABLE_ENV, "").strip().lower() not in ("1", "true", "yes"):
        pytest.skip(
            f"{DISPOSABLE_ENV} nao definido: esta suite escreve no banco e so "
            "roda onde ele foi declarado descartavel"
        )


def historical_counts(cur) -> dict[str, int]:
    counts = {}
    for table in HISTORICAL_TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


@pytest.fixture(scope="module")
def historical_baseline(cur):
    """
    Row counts of the tables this work must never touch, taken once.

    Asserting zero would be wrong: the CI fixtures seed these tables on
    purpose. What matters is that nothing here changes them.
    """
    return historical_counts(cur)

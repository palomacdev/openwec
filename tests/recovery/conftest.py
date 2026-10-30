"""
Fixtures for the recovery suites that need a database.

The sandbox is the loader's, imported rather than rebuilt: it is the routine
that creates a schema and drops it whole, and a second copy of it would be a
second chance to drop the wrong one. The guard tested here —
`identity_twin` — reads `cars`, `results` and `laps`, which the sandbox
mirrors.
"""
from __future__ import annotations

from tests.loader.sandbox import (  # noqa: F401
    DB,
    connect_to_sandbox,
    sandbox,
)

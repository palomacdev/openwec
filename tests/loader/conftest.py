"""
Fixtures for the loader suites.

The rules themselves live in sandbox.py, which is an ordinary module: pytest
does not let a conftest be imported by name, and the guard has to be testable.
The `sandbox` fixture lives there too, since R2-B — `tests/recovery` needs the
same one to exercise the identity guard against a real database, and two
copies of a routine that creates and drops schemas would be two chances to
drop the wrong thing.
"""
from __future__ import annotations

from tests.loader.sandbox import (  # noqa: F401
    DB,
    DISPOSABLE_ENV,
    SANDBOX_TABLES,
    connect_to_sandbox,
    declared_disposable,
    historical_counts,
    refusal,
    sandbox,
)

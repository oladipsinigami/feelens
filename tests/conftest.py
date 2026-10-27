"""Shared fixtures.

The engine's own data files are live: the FX snapshot gets refreshed whenever
ECB publishes a new rate. Tests must not read them, or every legitimate refresh
breaks the suite and the temptation is to edit the expected numbers until they
pass — which quietly destroys the test's meaning.

So tests read a frozen copy in `fixtures/`. Refresh the live snapshot freely;
update the fixture deliberately, in its own commit, with the numbers recomputed.
"""

from pathlib import Path

import pytest

from app.engine import load_fees, load_rates

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fees():
    return load_fees(FIXTURES / "fees.json")


@pytest.fixture(scope="session")
def rates():
    return load_rates(FIXTURES / "fx_snapshot.json")

"""Shared fixtures: one small synthetic dataset reused by every test."""

import pandas as pd
import pytest

from petlr.generate import generate

END_MONTH = "2026-09"


@pytest.fixture(scope="session")
def raw_and_planted():
    # Smaller than production (80 groups) so the suite stays fast.
    return generate(n_groups=80, n_months=24, end_month=END_MONTH, seed=7)


@pytest.fixture(scope="session")
def raw(raw_and_planted):
    return {k: v.copy() for k, v in raw_and_planted[0].items()}


@pytest.fixture(scope="session")
def planted(raw_and_planted):
    return raw_and_planted[1]


@pytest.fixture(scope="session")
def cleaned(raw_and_planted):
    from petlr.pipeline import clean

    tables = {k: v.copy() for k, v in raw_and_planted[0].items()}
    return clean(tables, as_of=pd.Timestamp("2026-09-30"))

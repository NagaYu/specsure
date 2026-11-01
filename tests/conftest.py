import pytest

from src.snapshot import FIXTURE_SEED, Catalog


@pytest.fixture(scope="session")
def mini():
    return Catalog.from_seed(FIXTURE_SEED)


@pytest.fixture(scope="session")
def bench():
    return Catalog.from_seed()

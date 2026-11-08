from collections.abc import Callable

import pytest
from lru_cache import IMPLEMENTATIONS
from lru_cache_driver import LruCacheDriver, Read, View

from pytest_quint_connect import HistoryContext


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--lru-implementation",
        choices=sorted(IMPLEMENTATIONS),
        default="list",
        help="which cache the contract checks (stale-read has a deliberate bug)",
    )


class CacheDriver(LruCacheDriver):
    """Applies model actions to a real cache and reads its state back. No cache logic lives here.

    LruCacheDriver is generated from lru.qnt (lru_cache_driver.py): one typed method per
    action, and observe() typed as the model's View.
    """

    def __init__(self, cache_type: type) -> None:
        self.cache_type = cache_type
        self.cache = cache_type(2)
        self.last_read: Read = {"tag": "NoRead"}

    def init(self) -> None:
        self.cache = self.cache_type(2)
        self.last_read = {"tag": "NoRead"}

    def store(self, key: str, value: int) -> None:
        self.cache.put(key, value)
        self.last_read = {"tag": "NoRead"}

    def fetch(self, key: str) -> None:
        value = self.cache.get(key)
        self.last_read = {"tag": "Miss"} if value is None else {"tag": "Hit", "value": value}

    def inspect(self) -> None:
        pass

    def observe(self) -> View:
        return {
            "entries": [{"key": key, "value": value} for key, value in self.cache.items()],
            "lastRead": self.last_read,
        }


@pytest.fixture
def lru_driver(request: pytest.FixtureRequest) -> Callable[[HistoryContext], CacheDriver]:
    chosen = IMPLEMENTATIONS[request.config.getoption("--lru-implementation")]

    def start(context: HistoryContext) -> CacheDriver:
        # The manifest's mutant runs the buggy cache; the contract must notice.
        return CacheDriver(IMPLEMENTATIONS[context.mutant] if context.mutant else chosen)

    return start


@pytest.fixture
def ordered_driver() -> Callable[[HistoryContext], CacheDriver]:
    return lambda context: CacheDriver(IMPLEMENTATIONS[context.mutant or "ordered"])

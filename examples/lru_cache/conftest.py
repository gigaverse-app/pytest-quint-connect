from collections.abc import Callable
from typing import Any

import pytest
from lru_cache import IMPLEMENTATIONS

from pytest_quint_connect import HistoryContext, Step


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--lru-implementation",
        choices=sorted(IMPLEMENTATIONS),
        default="list",
        help="which cache the contract checks (stale-read has a deliberate bug)",
    )


class CacheDriver:
    """Applies model steps to a real cache and reads its state back. No cache logic lives here."""

    def __init__(self, cache_type: type) -> None:
        self.cache_type = cache_type
        self.cache = cache_type(2)
        self.last_read: dict[str, Any] = {"tag": "NoRead"}

    def step(self, step: Step) -> None:
        if step.action == "init":
            self.cache = self.cache_type(2)
            self.last_read = {"tag": "NoRead"}
        elif step.action == "store":
            self.cache.put(step.args["key"], step.args["value"])
            self.last_read = {"tag": "NoRead"}
        elif step.action == "fetch":
            value = self.cache.get(step.args["key"])
            self.last_read = {"tag": "Miss"} if value is None else {"tag": "Hit", "value": value}
        elif step.action != "inspect":
            raise ValueError(f"unknown action {step.action}")

    def observe(self) -> dict[str, Any]:
        entries = [{"key": key, "value": value} for key, value in self.cache.items()]
        return {"entries": entries, "lastRead": self.last_read}


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

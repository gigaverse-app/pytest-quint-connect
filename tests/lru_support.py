"""Load the LRU example's real cache and driver for engine tests that do not run Quint."""

import importlib.util
import sys
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "lru_cache"
MANIFEST = EXAMPLE / "quint-connect.json"
DATA = Path(__file__).parent / "data"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if str(EXAMPLE) not in sys.path:
    sys.path.insert(0, str(EXAMPLE))
lru_cache = _load("lru_cache", EXAMPLE / "lru_cache.py")
example_conftest = _load("lru_example_conftest", EXAMPLE / "conftest.py")
CacheDriver = example_conftest.CacheDriver
IMPLEMENTATIONS = lru_cache.IMPLEMENTATIONS

# LRU cache example

A two-entry least-recently-used cache, checked against [lru.qnt](lru.qnt):

| Implementation | Result |
| --- | --- |
| `LRUCache` (list) | passes all 17 cases |
| `OrderedLRUCache` (`OrderedDict` refactor) | passes the same 17 cases on the same histories |
| `StaleReadLRUCache` (reads don't refresh recency) | fails; also the `stale-read` mutant every run must catch |

```bash
pip install pytest-quint-connect  # plus Node.js 22+
pytest --quint-require-complete        # 34 passed: both implementations
pytest --lru-implementation stale-read # fails with a field-level diff and a replay command
```

[quint-connect.json](quint-connect.json) declares the contract,
[conftest.py](conftest.py) the driver, and [test_lru_contract.py](test_lru_contract.py)
the two contract tests.

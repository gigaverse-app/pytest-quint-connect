from pytest_quint_connect import quint_contract

# The cache chosen with --lru-implementation (default: the list-based original).
test_lru_cache = quint_contract("quint-connect.json", driver="lru_driver")

# The OrderedDict refactor, checked against the same generated histories.
test_ordered_refactor = quint_contract("quint-connect.json", driver="ordered_driver")

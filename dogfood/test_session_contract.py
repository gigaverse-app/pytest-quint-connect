"""pytest-quint-connect, checked by pytest-quint-connect.

Quint generates histories of pytest sessions from session.qnt; each step runs a real
pytest session of the LRU example with the installed plugin, and the receipt and exit
code it leaves must match the model. Four mutants break the plugin's own completeness
and receipt logic; each must be caught.
"""

from pytest_quint_connect import quint_contract

test_session_receipts = quint_contract("quint-connect.json", driver="session_driver")

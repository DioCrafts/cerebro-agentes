"""Fixtures of the conversation-memory tests. The in-memory Hindsight is the global ``hindsight`` fixture
(``tests/conftest.py``); here only the accounts API environment."""

# The accounts API fixtures (``env``: AUTH_MODE=required over one private database file).
from tests.auth.conftest import env, fast_scrypt  # noqa: F401

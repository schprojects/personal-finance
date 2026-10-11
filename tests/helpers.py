"""Shared test helpers."""
from backend import FinanceBackend


def session_token(path, user, password):
    """Issue a dashboard session token and close the connection used for it."""
    with FinanceBackend(path=path) as b:
        return b.issue_session(user, password)

"""Where the sqlite database lives. Overridable via FINANCE_DB (tests, dist)."""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def db_path():
    p = os.environ.get("FINANCE_DB")
    return p or os.path.join(BASE, "var", "networth.db")
"""Where the sqlite database lives.

Resolution order: the FINANCE_DB environment variable, then FINANCE_DB in an
optional `.env` file next to the code (gitignored; a relative path is relative
to the project folder), then var/networth.db. Containers set FINANCE_DB in
compose, so `.env` only selects the database for host runs.
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dotenv_value(name, env_file=None):
    """`name` from the .env file (KEY=VALUE lines, # comments), else None."""
    try:
        with open(env_file or os.path.join(BASE, ".env")) as f:
            for line in f:
                key, sep, val = line.strip().partition("=")
                if sep and key.strip() == name and not key.lstrip().startswith("#"):
                    return val.strip().strip("\"'") or None
    except OSError:
        pass
    return None


def db_path():
    p = os.environ.get("FINANCE_DB") or _dotenv_value("FINANCE_DB")
    if p:
        return p if os.path.isabs(p) else os.path.join(BASE, p)
    return os.path.join(BASE, "var", "networth.db")

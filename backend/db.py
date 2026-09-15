"""Small sqlite connection helpers. Rows come back as plain dicts."""
import os
import sqlite3


def connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def rows(con, query, params=()):
    return [dict(r) for r in con.execute(query, params)]


def one(con, query, params=()):
    r = con.execute(query, params).fetchone()
    return dict(r) if r is not None else None


def ensure_dir(path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
"""SQLite schema, versioned via PRAGMA user_version.

Migrations are append-only: a released migration is never edited; a schema
change adds a new entry to MIGRATIONS.
"""
import sqlite3


def migrate(conn):
    """Apply pending migrations; returns the final schema version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for v in range(version, len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[v])
        conn.execute(f"PRAGMA user_version = {v + 1}")
    conn.commit()
    return len(MIGRATIONS)


MIGRATIONS = [
    # v1 — families/users (seeded from members.yaml), categories, entries,
    # investments, auth_tokens, settings. Dates are ISO YYYY-MM-DD; timestamps
    # UTC ISO; amounts real ₹.
    """
    CREATE TABLE families (
        id   TEXT PRIMARY KEY,
        name TEXT NOT NULL
    );
    CREATE TABLE users (
        id             TEXT PRIMARY KEY,
        username       TEXT NOT NULL UNIQUE,
        family_id      TEXT NOT NULL REFERENCES families(id),
        data_dir       TEXT NOT NULL,
        display_name   TEXT NOT NULL DEFAULT '',
        password_salt  BLOB,
        password_hash  BLOB,
        created_at     TEXT NOT NULL
    );
    CREATE TABLE categories (
        id    TEXT PRIMARY KEY,
        label TEXT NOT NULL UNIQUE,
        kind  TEXT NOT NULL
    );
    CREATE TABLE entries (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         TEXT NOT NULL REFERENCES users(id),
        category_id     TEXT NOT NULL REFERENCES categories(id),
        kind            TEXT NOT NULL,
        fy              TEXT NOT NULL,
        account         TEXT NOT NULL DEFAULT '',
        date            TEXT NOT NULL,
        mode            TEXT NOT NULL DEFAULT '',
        particulars     TEXT NOT NULL,
        deposit         REAL NOT NULL DEFAULT 0,
        withdrawal      REAL NOT NULL DEFAULT 0,
        balance         REAL,
        manual_override INTEGER NOT NULL DEFAULT 0,
        nkey            TEXT NOT NULL,
        file            TEXT NOT NULL DEFAULT '',
        edited_at       TEXT,
        UNIQUE (user_id, nkey)
    );
    CREATE INDEX idx_entries_fy          ON entries(fy);
    CREATE INDEX idx_entries_category_id ON entries(category_id);
    CREATE INDEX idx_entries_user_id     ON entries(user_id);
    CREATE TABLE investments (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id        TEXT NOT NULL REFERENCES users(id),
        type           TEXT NOT NULL,
        label          TEXT NOT NULL,
        folio          TEXT NOT NULL DEFAULT '',
        units          REAL,
        purchase_date  TEXT,
        purchase_nav   REAL,
        latest_date    TEXT,
        latest_nav     REAL,
        value_at_cost  REAL,
        value_latest   REAL,
        edited_at      TEXT,
        UNIQUE (user_id, type, label, folio)
    );
    CREATE TABLE auth_tokens (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL REFERENCES users(id),
        token_hash  TEXT NOT NULL UNIQUE,
        issued_at   TEXT NOT NULL,
        expires_at  TEXT NOT NULL,
        revoked     INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    INSERT INTO settings (key, value) VALUES
        ('fy_start_month', '4'),
        ('session_ttl_seconds', '600');
    """,
    # v2 — freeze rows edited before the manual-override flag: dashboard edits stamped
    # edited_at without setting manual_override, leaving them re-importable.
    # Idempotent: a second run touches nothing.
    """
    UPDATE entries SET manual_override = 1
     WHERE edited_at IS NOT NULL AND manual_override = 0;
    """,
]
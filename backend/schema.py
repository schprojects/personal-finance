"""SQLite schema, versioned via PRAGMA user_version.

Migrations are append-only: a released migration is never edited; a schema
change adds a new entry to MIGRATIONS.
"""
import sqlite3


def migrate(conn):
    """Apply pending migrations; returns the final schema version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for v in range(version, len(MIGRATIONS)):
        if v == 8:  # v9 drops auto_rules: keep any stored rules in the csv first
            _export_db_rules(conn)
        conn.executescript(MIGRATIONS[v])
        conn.execute(f"PRAGMA user_version = {v + 1}")
    conn.commit()
    return len(MIGRATIONS)


def _export_db_rules(conn, path=None):
    """rules used to live in the db. Append any that exist to
    auto-rules.csv (skipping lines already there) before the table is dropped.
    Raises, aborting the migration, if the file cannot be written."""
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                       " AND name='auto_rules'").fetchone()
    rows = conn.execute("SELECT kind, category, pattern FROM auto_rules"
                        " ORDER BY rule_id").fetchall() if has else []
    if not rows:
        return
    from finance.rules import RULES_FILE
    path = path or RULES_FILE
    try:
        with open(path, encoding="utf-8-sig") as f:
            have = {ln.strip() for ln in f}
    except FileNotFoundError:
        have = set()
    new = [f"{k},{c},{p}" for k, c, p in rows if f"{k},{c},{p}" not in have]
    if new:
        with open(path, "a", encoding="utf-8") as f:
            f.write("# carried over from the database\n" + "\n".join(new) + "\n")


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
    # v3 — category/kind cleanup. Merge misspelt / case-duplicate /
    # artefact categories into their canonical label, and turn credit-only
    # `transfer` rows into plain `income` (default category). Idempotent.
    """
    INSERT OR IGNORE INTO categories (id, label, kind) VALUES
        ('Loan & EMI', 'Loan & EMI', 'expense'),
        ('Bank & DP charges', 'Bank & DP charges', 'expense'),
        ('Shopping & goods', 'Shopping & goods', 'expense'),
        ('', '', 'income');
    UPDATE entries SET category_id = 'Loan & EMI'        WHERE category_id = 'Loan & EMO';
    UPDATE entries SET category_id = 'Bank & DP charges' WHERE category_id = 'Bank & DP Charges';
    UPDATE entries SET category_id = 'Shopping & goods'  WHERE category_id = 'nan';
    DELETE FROM categories WHERE id IN ('Loan & EMO', 'Bank & DP Charges', 'nan')
        AND NOT EXISTS (SELECT 1 FROM entries WHERE category_id = categories.id);
    UPDATE entries SET kind = 'income', category_id = ''
     WHERE kind = 'transfer' AND deposit > 0 AND withdrawal = 0;
    """,
    # v4 — category labels contain no whitespace ("Loan & EMI" ->
    # "Loan_&_EMI"). categories.id == label, so re-point entries then drop the
    # old rows. Idempotent: a second run finds no label with a space.
    """
    INSERT OR IGNORE INTO categories (id, label, kind)
        SELECT REPLACE(TRIM(id), ' ', '_'), REPLACE(TRIM(label), ' ', '_'), kind
          FROM categories WHERE id LIKE '% %';
    UPDATE entries SET category_id = REPLACE(TRIM(category_id), ' ', '_')
     WHERE category_id LIKE '% %';
    DELETE FROM categories WHERE id LIKE '% %'
        AND NOT EXISTS (SELECT 1 FROM entries WHERE category_id = categories.id);
    """,
    # v5 — no "&" in category labels ("Loan_&_EMI" -> "Loan_EMI").
    # Same shape as v4; idempotent (a second run finds no label with "&").
    """
    INSERT OR IGNORE INTO categories (id, label, kind)
        SELECT REPLACE(REPLACE(id, '_&_', '_'), '&', ''),
               REPLACE(REPLACE(label, '_&_', '_'), '&', ''), kind
          FROM categories WHERE id LIKE '%&%';
    UPDATE entries SET category_id = REPLACE(REPLACE(category_id, '_&_', '_'), '&', '')
     WHERE category_id LIKE '%&%';
    DELETE FROM categories WHERE id LIKE '%&%'
        AND NOT EXISTS (SELECT 1 FROM entries WHERE category_id = categories.id);
    """,
    # v6 — user-creatable kinds, auto-rules stored in the db,
    # and the spec's initial categories (backend-specs.md) seeded. Idempotent.
    """
    CREATE TABLE IF NOT EXISTS kinds (
        name TEXT PRIMARY KEY
    );
    INSERT OR IGNORE INTO kinds (name) VALUES
        ('expense'), ('income'), ('investment'), ('transfer'),
        ('reimbursed'), ('refund'), ('credit'), ('review');
    CREATE TABLE IF NOT EXISTS auto_rules (
        rule_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        match_column TEXT NOT NULL,
        pattern      TEXT NOT NULL,
        kind         TEXT NOT NULL,
        category     TEXT NOT NULL DEFAULT '',
        UNIQUE (match_column, pattern)
    );
    INSERT OR IGNORE INTO categories (id, label, kind) VALUES
        ('Bank_DP_charges','Bank_DP_charges','expense'),
        ('Bills_aggregators','Bills_aggregators','expense'),
        ('Cash_withdrawals','Cash_withdrawals','expense'),
        ('Dining_out','Dining_out','expense'),
        ('Donations_gifts','Donations_gifts','expense'),
        ('Family_support','Family_support','expense'),
        ('Fitness_Sports','Fitness_Sports','expense'),
        ('Friends','Friends','expense'),
        ('Groceries','Groceries','expense'),
        ('Healthcare_pharmacy','Healthcare_pharmacy','expense'),
        ('Income_tax','Income_tax','expense'),
        ('Insurance','Insurance','expense'),
        ('Internet_phone','Internet_phone','expense'),
        ('Kids_school','Kids_school','expense'),
        ('Learning_upskilling','Learning_upskilling','expense'),
        ('Legal_professional','Legal_professional','expense'),
        ('Loan_EMI','Loan_EMI','expense'),
        ('Misc_small','Misc_small','expense'),
        ('Shopping_goods','Shopping_goods','expense'),
        ('Subscriptions_digital','Subscriptions_digital','expense'),
        ('Transport_fuel','Transport_fuel','expense'),
        ('Travel_leisure','Travel_leisure','expense'),
        ('Utilities','Utilities','expense'),
        ('Vehicles','Vehicles','expense'),
        ('UNKNOWN','UNKNOWN','expense');
    """,
    # v7 — the v6 seed briefly used the typo `Mis_small`; the spec label is
    # `Misc_small`. Databases already at v6 get the right row and lose the
    # unused typo row (entries are re-pointed first, so nothing is orphaned).
    # Idempotent.
    """
    INSERT OR IGNORE INTO categories (id, label, kind)
        VALUES ('Misc_small', 'Misc_small', 'expense');
    UPDATE entries SET category_id = 'Misc_small' WHERE category_id = 'Mis_small';
    DELETE FROM categories WHERE id = 'Mis_small';
    """,
    # v8 — a debited transfer is spend under the default category
    # `Transfer`. Re-point existing debit transfers that had no real category.
    # Idempotent.
    """
    INSERT OR IGNORE INTO categories (id, label, kind)
        VALUES ('Transfer', 'Transfer', 'transfer');
    UPDATE entries SET category_id = 'Transfer'
     WHERE kind = 'transfer' AND withdrawal > 0
       AND category_id IN ('', 'UNKNOWN');
    """,
    # v9 — auto-rules live in auto-rules.csv, not the db. Rows were
    # exported by _export_db_rules before this runs. Idempotent.
    """
    DROP TABLE IF EXISTS auto_rules;
    """,
]
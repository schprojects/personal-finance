"""FinanceBackend — the system of record. Every read/write goes through here.

Families/users/families come from members.yaml (seeded by the importer); the
dashboard and CLI authenticate against the same database. Rule: an entry may be
changed only by its owner; anyone in the family may read everything.
"""
import datetime as dt
import hashlib
import re

from . import auth, db, schema
from .paths import db_path

UNKNOWN = "UNKNOWN"
FAMILY_ID = "f1"

# Misspelt / case-duplicate / artefact labels -> canonical label.
# Keys are already in canonical form (see canon_category).
CATEGORY_ALIASES = {"Loan_EMO": "Loan_EMI",
                    "Bank_DP_Charges": "Bank_DP_charges",
                    "nan": "Shopping_goods"}


def canon_category(label):
    """Canonical category label: trimmed; every run of
    whitespace and/or `&` becomes a single `_`; known misspellings folded.
    Blank stays blank."""
    label = re.sub(r"[\s&_]*[\s&][\s&_]*", "_", (label or "").strip())
    return CATEGORY_ALIASES.get(label.strip("_"), label.strip("_"))


TRANSFER = "Transfer"


def default_category(kind):
    """The category an entry takes when none is given: income has only the
    default (blank) category, a transfer is `Transfer`, the rest are UNKNOWN."""
    return "" if kind == "income" else TRANSFER if kind == "transfer" else UNKNOWN


def settle_kind(kind, category, deposit, withdrawal):
    """One semantic for credited transfers: a credit-only
    `transfer` is simply `income` (default category). Income always lands on
    the default category. A debited transfer is spend and, without a
    real category, takes the default `Transfer`."""
    if kind == "transfer" and (deposit or 0) > 0 and not (withdrawal or 0):
        kind = "income"
    if kind == "income":
        category = ""
    elif kind == "transfer" and category in ("", UNKNOWN, None):
        category = TRANSFER
    return kind, category


def nkey(account, date, particulars, deposit, withdrawal):
    raw = f"{account}|{date}|{(particulars or '').strip().upper()}|{deposit}|{withdrawal}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FinanceBackend:
    def __init__(self, path=None):
        self.path = path or db_path()
        db.ensure_dir(self.path)
        self.con = db.connect(self.path)
        schema.migrate(self.con)

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- users / family ----------------------------------------------------
    def seed_members(self, members):
        """Create the family + a user per members.yaml row. Idempotent."""
        self.con.execute(
            "INSERT OR IGNORE INTO families (id, name) VALUES (?, ?)",
            (FAMILY_ID, "family"))
        for m in members:
            self.con.execute(
                "INSERT OR IGNORE INTO users"
                " (id, username, family_id, data_dir, display_name, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (m["name"], m["name"], FAMILY_ID, m["data_dir"],
                 m["name"], auth.now_utc().isoformat()))
        self.con.commit()

    def users(self):
        return db.rows(
            self.con,
            "SELECT id, username, display_name, family_id,"
            " (password_hash IS NOT NULL) AS has_password"
            " FROM users ORDER BY username")

    def data_dir(self, user_id):
        """The member's data folder (from members.yaml), '' if unknown."""
        r = db.one(self.con, "SELECT data_dir FROM users WHERE id = ?", (user_id,))
        return r["data_dir"] if r else ""

    def family_user_ids(self, user_id):
        u = db.one(self.con, "SELECT family_id FROM users WHERE id = ?", (user_id,))
        if not u:
            return []
        return [r["id"] for r in db.rows(
            self.con,
            "SELECT id FROM users WHERE family_id = ? ORDER BY username",
            (u["family_id"],))]

    # -- passwords / sessions ---------------------------------------------
    def set_password(self, username, new_password, current_password=None):
        """Bootstrap (no password yet) needs no current; later changes do, and
        revoke every live session for that user."""
        user = db.one(self.con, "SELECT * FROM users WHERE username = ?", (username,))
        if not user:
            raise ValueError(f"no such user: {username}")
        if user["password_salt"]:
            if current_password is None:
                raise ValueError("user already has a password; supply the current one")
            if not auth.verify_password(
                    current_password, bytes(user["password_salt"]),
                    bytes(user["password_hash"])):
                raise ValueError("current password does not match")
        salt, digest = auth.hash_password(new_password)
        self.con.execute(
            "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
            (salt, digest, user["id"]))
        self.con.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user["id"],))
        self.con.commit()

    def check_password(self, username, password):
        """CLI gate: verify a password without creating a session row."""
        user = db.one(self.con, "SELECT * FROM users WHERE username = ?", (username,))
        if not user or not user["password_salt"]:
            return False
        return auth.verify_password(password, bytes(user["password_salt"]),
                                    bytes(user["password_hash"]))

    def issue_session(self, username, password, ttl=None):
        user = db.one(self.con,
                      "SELECT * FROM users WHERE username = ?", (username,))
        if not user or not user["password_salt"]:
            return None
        if not auth.verify_password(password, bytes(user["password_salt"]),
                                    bytes(user["password_hash"])):
            return None
        ttl = int(ttl or self.get_setting("session_ttl_seconds", 600))
        raw, digest = auth.new_token()
        now = auth.now_utc()
        self.con.execute(
            "INSERT INTO auth_tokens (user_id, token_hash, issued_at, expires_at, revoked)"
            " VALUES (?, ?, ?, ?, 0)",
            (user["id"], digest, now.isoformat(),
             (now + dt.timedelta(seconds=ttl)).isoformat()))
        self.con.commit()
        return raw

    def user_for_session(self, raw_token):
        if not raw_token:
            return None
        row = db.one(self.con,
                     "SELECT * FROM auth_tokens WHERE token_hash = ? AND revoked = 0",
                     (auth.token_digest(raw_token),))
        if not row:
            return None
        if dt.datetime.fromisoformat(row["expires_at"]) < auth.now_utc():
            return None
        return db.one(self.con, "SELECT * FROM users WHERE id = ?", (row["user_id"],))

    def revoke_session(self, raw_token):
        if not raw_token:
            return
        self.con.execute(
            "UPDATE auth_tokens SET revoked = 1 WHERE token_hash = ?",
            (auth.token_digest(raw_token),))
        self.con.commit()

    # -- settings ----------------------------------------------------------
    def get_setting(self, key, default=None):
        r = db.one(self.con, "SELECT value FROM settings WHERE key = ?", (key,))
        return r["value"] if r else default

    def set_setting(self, key, value):
        self.con.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.con.commit()

    def fy_of(self, date_s, start_month=None):
        """FY label from an ISO date, e.g. '2025-04-01' -> '2025-26'."""
        start = start_month or int(self.get_setting("fy_start_month", "4"))
        d = dt.date.fromisoformat(date_s)
        yy = d.year if d.month >= start else d.year - 1
        return f"{yy}-{str(yy + 1)[-2:]}"

    # -- categories --------------------------------------------------------
    def categories(self):
        return db.rows(self.con,
                       "SELECT * FROM categories ORDER BY kind, label")

    def get_or_create_category(self, label, kind):
        label = canon_category(label)
        if not label and kind in ("expense", "review"):
            label = UNKNOWN
        self.con.execute(
            "INSERT OR IGNORE INTO categories (id, label, kind) VALUES (?, ?, ?)",
            (label, label, kind))
        return label

    def create_category(self, label, kind):
        """Dashboard create: blank labels rejected; returns 'created'|'exists'."""
        label = canon_category(label)
        if not label:
            raise ValueError("category label must not be blank")
        existing = db.one(self.con,
                          "SELECT id FROM categories WHERE label = ?", (label,))
        if existing:
            return "exists"
        self.con.execute(
            "INSERT INTO categories (id, label, kind) VALUES (?, ?, ?)",
            (label, label, kind))
        self.con.commit()
        return "created"

    # -- kinds --------------------------------------------------
    def kinds(self):
        return [r["name"] for r in db.rows(
            self.con, "SELECT name FROM kinds ORDER BY rowid")]

    def create_kind(self, name):
        """Dashboard create: lower-case single token; 'created'|'exists'."""
        name = re.sub(r"\s+", "_", (name or "").strip().lower())
        if not name:
            raise ValueError("kind must not be blank")
        if name in self.kinds():
            return "exists"
        self.con.execute("INSERT INTO kinds (name) VALUES (?)", (name,))
        self.con.commit()
        return "created"

    # -- entries -----------------------------------------------------------
    def upsert_bank_entry(self, user_id, *, account, date, particulars,
                          deposit, withdrawal, balance, kind, category, file=""):
        """Importer path: create / update / skip by natural key. manual_override
        rows are never touched. Returns one of the four actions."""
        dep, wd = float(deposit or 0), float(withdrawal or 0)
        key = nkey(account, date, particulars, dep, wd)
        kind, category = settle_kind(kind, category, dep, wd)
        category_id = self.get_or_create_category(category, kind)
        existing = db.one(self.con,
                          "SELECT * FROM entries WHERE user_id = ? AND nkey = ?",
                          (user_id, key))
        if existing is None:
            self.con.execute(
                "INSERT INTO entries (user_id, category_id, kind, fy, account, date,"
                " particulars, deposit, withdrawal, balance, manual_override,"
                " nkey, file, edited_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL)",
                (user_id, category_id, kind, self.fy_of(date), account, date,
                 particulars, dep, wd, balance, key, file))
            self.con.commit()
            return "created"
        if existing["manual_override"]:
            return "manual_override"
        if existing["kind"] == kind and existing["category_id"] == category_id:
            return "skipped"
        self.con.execute(
            "UPDATE entries SET category_id = ?, kind = ?, file = ?"
            " WHERE user_id = ? AND nkey = ?",
            (category_id, kind, file, user_id, key))
        self.con.commit()
        return "updated"

    def has_entry(self, user_id, *, account, date, particulars, deposit, withdrawal):
        """True when the natural key is already stored for this user."""
        key = nkey(account, date, particulars, float(deposit or 0),
                   float(withdrawal or 0))
        return db.one(self.con, "SELECT 1 FROM entries WHERE user_id = ? AND nkey = ?",
                      (user_id, key)) is not None

    def entries(self, *, user_ids=None, kinds=None, category=None, fy=None,
                date_from=None, date_to=None, account=None, q=None,
                limit=None):
        sql = ("SELECT e.*, c.label AS category FROM entries e"
               " JOIN categories c ON c.id = e.category_id")
        conds, params = [], []
        if user_ids:
            user_ids = list(user_ids)
            conds.append(f"e.user_id IN ({','.join('?' * len(user_ids))})")
            params += user_ids
        if kinds:
            kinds = list(kinds)
            conds.append(f"e.kind IN ({','.join('?' * len(kinds))})")
            params += kinds
        if category:
            conds.append("c.label = ?"); params.append(category)
        if fy:
            fys = [fy] if isinstance(fy, str) else list(fy)
            conds.append(f"e.fy IN ({','.join('?' * len(fys))})")
            params += fys
        if account:
            conds.append("e.account = ?"); params.append(account)
        if q:
            conds.append("e.particulars LIKE ?"); params.append(f"%{q}%")
        if date_from:
            conds.append("e.date >= ?"); params.append(date_from)
        if date_to:
            conds.append("e.date <= ?"); params.append(date_to)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY e.date, e.id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return db.rows(self.con, sql, params)

    def update_entry(self, entry_id, user_id, kind=None, category=None,
                     commit=True):
        """Dashboard edit: set a row's kind and/or category (owner only)."""
        row = db.one(self.con, "SELECT * FROM entries WHERE id = ?", (entry_id,))
        if not row:
            raise ValueError("entry not found")
        if row["user_id"] != user_id:
            raise ValueError("not the owner of this entry")
        if kind is not None and kind not in self.kinds():
            raise ValueError(f"unsupported kind: {kind}")
        category = (category or "").strip()
        if kind is None and not category:
            raise ValueError("provide a kind and/or category")
        if kind is not None:
            kind, category = settle_kind(kind, category, row["deposit"],
                                         row["withdrawal"])
        effective = kind if kind is not None else row["kind"]
        new_category_id = None  # None = leave the category as it is
        if effective == "income":
            # An income entry only ever carries the default category, whatever
            # the edit asked for (backend-specs.md).
            new_category_id = self.get_or_create_category("", "income")
        elif category:
            new_category_id = self.get_or_create_category(category, effective)
        elif not row["category_id"]:
            # income -> expense/refund/... without a category: the kind's default
            new_category_id = self.get_or_create_category(
                default_category(effective), effective)
        # Dashboard edits are authoritative: manual_override=1 freezes the row
        # against future re-imports (upsert_bank_entry returns manual_override).
        sets, params = ["edited_at = ?", "manual_override = 1"], \
            [auth.now_utc().isoformat()]
        if kind is not None:
            sets.append("kind = ?"); params.append(kind)
        if new_category_id is not None:
            sets.append("category_id = ?"); params.append(new_category_id)
        params.append(entry_id)
        self.con.execute(
            f"UPDATE entries SET {', '.join(sets)} WHERE id = ?", params)
        if commit:
            self.con.commit()

    def bulk_update_entries(self, user_id, changes):
        """Explicit batched save: `changes` maps entry id ->
        (kind or None, category or None). All-or-nothing: any invalid row rolls
        the whole batch back. Returns the number of rows changed."""
        try:
            for entry_id, (kind, category) in changes.items():
                self.update_entry(entry_id, user_id, kind=kind or None,
                                  category=category, commit=False)
        except Exception:
            self.con.rollback()
            raise
        self.con.commit()
        return len(changes)

    def classify_unknown(self, entry_id, user_id, kind, category):
        """Dashboard edit: give an UNKNOWN row a real kind + category."""
        self.update_entry(entry_id, user_id, kind=kind, category=category)

    # -- investments -------------------------------------------------------
    def upsert_investment(self, user_id, *, type_, label, folio="", units=None,
                          purchase_date=None, purchase_nav=None, latest_date=None,
                          latest_nav=None, value_at_cost=None, value_latest=None):
        existing = db.one(
            self.con,
            "SELECT * FROM investments WHERE user_id = ? AND type = ? AND label = ? AND folio = ?",
            (user_id, type_, label, folio))
        if existing is None:
            self.con.execute(
                "INSERT INTO investments (user_id, type, label, folio, units,"
                " purchase_date, purchase_nav, latest_date, latest_nav,"
                " value_at_cost, value_latest)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, type_, label, folio, units, purchase_date, purchase_nav,
                 latest_date, latest_nav, value_at_cost, value_latest))
            self.con.commit()
            return "created"
        fields = {"units": units, "purchase_date": purchase_date,
                  "purchase_nav": purchase_nav, "latest_date": latest_date,
                  "latest_nav": latest_nav, "value_at_cost": value_at_cost,
                  "value_latest": value_latest}
        changes = {k: v for k, v in fields.items()
                   if v is not None and existing[k] != v}
        if not changes:
            return "skipped"
        sets = ", ".join(f"{k} = ?" for k in changes)
        self.con.execute(
            f"UPDATE investments SET {sets} WHERE id = ?",
            [*changes.values(), existing["id"]])
        self.con.commit()
        return "updated"

    def investments(self, *, user_ids=None, type_=None):
        sql = "SELECT * FROM investments"
        conds, params = [], []
        if user_ids:
            user_ids = list(user_ids)
            conds.append(f"user_id IN ({','.join('?' * len(user_ids))})")
            params += user_ids
        if type_:
            conds.append("type = ?"); params.append(type_)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        return db.rows(self.con, sql + " ORDER BY user_id, type, label", params)

    INVESTMENT_EDITABLE = ("units", "purchase_date", "purchase_nav",
                           "latest_date", "latest_nav", "value_at_cost",
                           "value_latest")

    def update_investment_fields(self, investment_id, user_id, **fields):
        """Dashboard edit: write any non-identity column (owner only). None
        clears a field; unknown fields are rejected."""
        unknown = set(fields) - set(self.INVESTMENT_EDITABLE)
        if unknown:
            raise ValueError(f"cannot edit: {sorted(unknown)}")
        row = db.one(self.con, "SELECT * FROM investments WHERE id = ?",
                     (investment_id,))
        if not row:
            raise ValueError("investment not found")
        if row["user_id"] != user_id:
            raise ValueError("not the owner of this investment")
        if not fields:
            raise ValueError("nothing to update")
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.con.execute(
            f"UPDATE investments SET {sets}, edited_at = ? WHERE id = ?",
            [*fields.values(), auth.now_utc().isoformat(), investment_id])
        self.con.commit()

    def update_investment_latest(self, investment_id, user_id, value_latest,
                                 latest_date=None):
        self.update_investment_fields(
            investment_id, user_id, value_latest=value_latest,
            latest_date=latest_date)
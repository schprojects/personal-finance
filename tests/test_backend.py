"""Unit tests for backend/ (schema, auth, entries, investments)."""
import os
import tempfile
import time
import unittest
from unittest import mock

from backend.auth import new_token
from backend.model import FinanceBackend
from backend.paths import db_path
from backend.schema import MIGRATIONS


_OPENED = []


def make_backend(d):
    b = FinanceBackend(path=os.path.join(d, "networth.db"))
    _OPENED.append(b)
    return b


def tearDownModule():
    """Close every backend a test opened via make_backend (idempotent)."""
    while _OPENED:
        try:
            _OPENED.pop().close()
        except Exception:
            pass


class TestSchema(unittest.TestCase):
    def test_migrates_and_seeds(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_backend(d)
            self.assertEqual(
                b.con.execute("PRAGMA user_version").fetchone()[0],
                len(MIGRATIONS))
            tables = {r["name"] for r in b.con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"families", "users", "categories", "entries",
                             "investments", "auth_tokens", "settings"} <= tables)
            self.assertEqual(b.get_setting("session_ttl_seconds"), "600")
            self.assertEqual(b.get_setting("fy_start_month"), "4")
            b2 = make_backend(d)  # idempotent re-open
            self.assertEqual(
                b2.con.execute("PRAGMA user_version").fetchone()[0],
                len(MIGRATIONS))
            b.close()

    def test_v2_backfills_manual_override_for_pre_fix_edits(self):
        """Rows edited before the manual-override flag had edited_at set but manual_override
        left at 0. v2 migration freezes them so re-imports cannot clobber.
        Untouched rows (edited_at IS NULL) keep manual_override = 0."""
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "networth.db")
            con = sqlite3.connect(path)
            con.executescript(MIGRATIONS[0])
            con.execute("PRAGMA user_version = 1")
            con.execute(
                "INSERT INTO families (id, name) VALUES ('f1', 'f')")
            con.execute(
                "INSERT INTO users (id, username, family_id, data_dir,"
                " created_at) VALUES ('a', 'a', 'f1', 'data/a',"
                " '2026-01-01T00:00:00')")
            con.execute(
                "INSERT INTO categories (id, label, kind)"
                " VALUES ('c', 'c', 'expense')")
            con.execute(
                "INSERT INTO entries (user_id, category_id, kind, fy, date,"
                " particulars, nkey, edited_at, manual_override)"
                " VALUES ('a','c','expense','2026-27','2026-04-01','p',"
                " 'k-edited', '2026-09-01T00:00:00', 0)")
            con.execute(
                "INSERT INTO entries (user_id, category_id, kind, fy, date,"
                " particulars, nkey, edited_at, manual_override)"
                " VALUES ('a','c','expense','2026-27','2026-04-02','q',"
                " 'k-untouched', NULL, 0)")
            con.commit()
            con.close()
            b = make_backend(d)  # opens -> migrate() runs v2
            self.assertEqual(
                b.con.execute("PRAGMA user_version").fetchone()[0],
                len(MIGRATIONS))
            rows = {r["date"]: r for r in b.entries(user_ids=["a"])}
            self.assertEqual(rows["2026-04-01"]["manual_override"], 1)
            self.assertEqual(rows["2026-04-02"]["manual_override"], 0)
            b.close()

    def test_db_path_defaults_to_var(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("backend.paths.BASE", d), \
                mock.patch.dict(os.environ):
            os.environ.pop("FINANCE_DB", None)
            self.assertTrue(db_path().endswith("var/networth.db"))


class TestMembersAndFy(unittest.TestCase):
    def test_seed_and_fy(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_backend(d)
            b.seed_members([{"name": "alex", "data_dir": "data/alex"},
                            {"name": "blair", "data_dir": "data/blair"}])
            users = b.users()
            self.assertEqual({u["username"] for u in users},
                             {"alex", "blair"})
            self.assertEqual(b.family_user_ids("alex"),
                             ["alex", "blair"])
            self.assertEqual(b.fy_of("2025-04-01"), "2025-26")
            self.assertEqual(b.fy_of("2025-03-31"), "2024-25")
            b.set_setting("fy_start_month", "1")
            self.assertEqual(b.fy_of("2025-03-01"), "2025-26")
            b.close()


class TestPasswords(unittest.TestCase):
    def test_bootstrap_and_change(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_backend(d)
            b.seed_members([{"name": "a", "data_dir": "data/a"}])
            b.set_password("a", "first")          # bootstrap, no current
            with self.assertRaises(ValueError):
                b.set_password("a", "x")          # needs current now
            with self.assertRaises(ValueError):
                b.set_password("a", "x", current_password="wrong")
            b.set_password("a", "second", current_password="first")
            self.assertTrue(b.issue_session("a", "second"))
            b.close()

    def test_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_backend(d)
            b.seed_members([{"name": "a", "data_dir": "data/a"}])
            b.set_password("a", "pw")
            self.assertIsNone(b.issue_session("a", "nope"))
            self.assertIsNone(b.issue_session("nobody", "pw"))
            raw = b.issue_session("a", "pw")
            self.assertIsNotNone(raw)
            user = b.user_for_session(raw)
            self.assertEqual(user["username"], "a")
            b.revoke_session(raw)
            self.assertIsNone(b.user_for_session(raw))
            self.assertIsNone(b.user_for_session("garbage"))
            b.close()

    def test_session_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            b = make_backend(d)
            b.seed_members([{"name": "a", "data_dir": "data/a"}])
            b.set_password("a", "pw")
            raw = b.issue_session("a", "pw", ttl=1)
            self.assertIsNotNone(b.user_for_session(raw))
            time.sleep(1.2)
            self.assertIsNone(b.user_for_session(raw))
            b.close()


class TestEntries(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.b = make_backend(self.d.name)
        self.b.seed_members([{"name": "a", "data_dir": "data/a"},
                             {"name": "b", "data_dir": "data/b"}])

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def test_create_update_skip(self):
        kw = dict(account="A", date="2025-04-01", particulars="UPI/someone@ok/x", deposit=0, withdrawal=100,
                  balance=900, file="t.csv")
        self.assertEqual(self.b.upsert_bank_entry(
            "a", kind="expense", category="Groceries", **kw), "created")
        self.assertEqual(self.b.upsert_bank_entry(
            "a", kind="expense", category="Groceries", **kw), "skipped")
        self.assertEqual(self.b.upsert_bank_entry(
            "a", kind="expense", category="Transport_fuel", **kw), "updated")
        row = self.b.entries(user_ids=["a"], q="someone")[0]
        self.assertEqual(row["category"], "Transport_fuel")
        self.assertEqual(row["fy"], "2025-26")

    def test_manual_override_never_touched(self):
        kw = dict(account="A", date="2025-04-02", particulars="P",
                  deposit=0, withdrawal=50, balance=100, file="t.csv")
        self.b.upsert_bank_entry("a", kind="expense", category="UNKNOWN", **kw)
        rid = self.b.entries(user_ids=["a"], category="UNKNOWN")[0]["id"]
        self.b.con.execute("UPDATE entries SET manual_override=1 WHERE id=?", (rid,))
        self.b.con.commit()
        self.assertEqual(self.b.upsert_bank_entry(
            "a", kind="expense", category="Groceries", **kw), "manual_override")
        self.assertEqual(
            self.b.entries(user_ids=["a"])[0]["category"], "UNKNOWN")

    def test_classify_unknown(self):
        kw = dict(account="A", date="2025-05-01", particulars="W",
                  deposit=0, withdrawal=10, balance=50, file="t.csv")
        self.b.upsert_bank_entry("a", kind="review", category="UNKNOWN", **kw)
        rid = self.b.entries(user_ids=["a"])[0]["id"]
        self.b.classify_unknown(rid, "a", "expense", "Groceries")
        self.assertEqual(self.b.entries(user_ids=["a"])[0]["category"], "Groceries")
        with self.assertRaises(ValueError):
            self.b.classify_unknown(rid, "b", "expense", "X")  # not owner
        with self.assertRaises(ValueError):
            self.b.classify_unknown(999, "a", "expense", "X")

    def test_update_entry_partial_and_owner(self):
        kw = dict(account="A", date="2025-05-02", particulars="W",
                  deposit=0, withdrawal=10, balance=50, file="t.csv")
        self.b.upsert_bank_entry("a", kind="expense", category="UNKNOWN", **kw)
        rid = self.b.entries(user_ids=["a"])[0]["id"]
        self.b.update_entry(rid, "a", category="Groceries")       # keep kind
        row = self.b.entries(user_ids=["a"])[0]
        self.assertEqual((row["kind"], row["category"]),
                         ("expense", "Groceries"))
        self.b.update_entry(rid, "a", kind="review")              # keep category
        row = self.b.entries(user_ids=["a"])[0]
        self.assertEqual((row["kind"], row["category"]),
                         ("review", "Groceries"))
        self.assertIsNotNone(row["edited_at"])
        self.b.update_entry(rid, "a", kind="expense", category="UNKNOWN")
        with self.assertRaises(ValueError):
            self.b.update_entry(rid, "b", category="X")           # not owner
        with self.assertRaises(ValueError):
            self.b.update_entry(999, "a", category="X")
        with self.assertRaises(ValueError):
            self.b.update_entry(rid, "a")                         # nothing given
        with self.assertRaises(ValueError):
            self.b.update_entry(rid, "a", kind="bogus")
        row = self.b.entries(user_ids=["a"])[0]
        self.assertEqual((row["kind"], row["category"]),
                         ("expense", "UNKNOWN"))
        # dashboard edits freeze the row against later re-imports
        self.assertEqual(row["manual_override"], 1)
        self.assertEqual(self.b.upsert_bank_entry(
            "a", kind="expense", category="Groceries", **kw), "manual_override")

    def test_entry_filters(self):
        rows = [
            dict(account="A", date="2025-04-03", particulars="UPI/x",
                 deposit=0, withdrawal=10, balance=90, kind="expense",
                 category="Groceries"),
            dict(account="B", date="2025-04-04", particulars="ACH/INC",
                 deposit=5000, withdrawal=0, balance=5090, kind="income",
                 category="Salary"),
        ]
        for r in rows:
            self.b.upsert_bank_entry("a", file="t.csv", **(r | {"balance": r["balance"]}))
        self.assertEqual(len(self.b.entries(user_ids=["a"])), 2)
        self.assertEqual(len(self.b.entries(user_ids=["a"], kinds=["income"])), 1)
        self.assertEqual(len(self.b.entries(user_ids=["a"], fy="2025-26")), 2)
        self.assertEqual(
            self.b.entries(user_ids=["a"], date_from="2025-04-04")[0]["particulars"],
            "ACH/INC")
        self.assertEqual(
            self.b.entries(user_ids=["a"], q="ach")[0]["kind"], "income")


class TestInvestments(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.b = make_backend(self.d.name)
        self.b.seed_members([{"name": "a", "data_dir": "data/a"}])

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def test_upsert_and_latest_update(self):
        kw = dict(type_="MF", label="Axis Large Cap", folio="F1",
                  units=100, value_at_cost=1000, value_latest=1200)
        self.assertEqual(self.b.upsert_investment("a", **kw), "created")
        self.assertEqual(self.b.upsert_investment("a", **kw), "skipped")
        self.assertEqual(self.b.upsert_investment(
            "a", type_="MF", label="Axis Large Cap", folio="F1",
            value_latest=1300), "updated")
        inv = self.b.investments(user_ids=["a"])[0]
        self.assertEqual(inv["value_latest"], 1300)
        self.b.update_investment_latest(inv["id"], "a", 1400, latest_date="2026-01-01")
        self.assertEqual(self.b.investments(user_ids=["a"])[0]["value_latest"], 1400)
        with self.assertRaises(ValueError):
            self.b.update_investment_latest(inv["id"], "other", 1)  # not owner
        self.assertEqual(len(self.b.investments(user_ids=["a"], type_="FD")), 0)

    def test_update_investment_fields_whitelist_and_owner(self):
        self.b.upsert_investment(
            "a", type_="MF", label="Axis", folio="F1", units=100,
            purchase_date="2023-04-01", purchase_nav=10.0, latest_date="2024-04-01",
            latest_nav=11.0, value_at_cost=1000, value_latest=1100)
        rid = self.b.investments(user_ids=["a"])[0]["id"]
        with self.assertRaises(ValueError):
            self.b.update_investment_fields(rid, "a", type="cash")   # identity
        with self.assertRaises(ValueError):
            self.b.update_investment_fields(rid, "a")                # nothing
        with self.assertRaises(ValueError):
            self.b.update_investment_fields(rid, "other", units=1)   # not owner
        with self.assertRaises(ValueError):
            self.b.update_investment_fields(999, "a", units=1)
        self.b.update_investment_fields(rid, "a", units=90,
                                        purchase_nav=10.5, latest_nav=None)
        inv = self.b.investments(user_ids=["a"])[0]
        self.assertEqual(inv["units"], 90)
        self.assertEqual(inv["purchase_nav"], 10.5)
        self.assertIsNone(inv["latest_nav"])                          # cleared
        self.assertIsNotNone(inv["edited_at"])


class TestCategories(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.b = make_backend(self.d.name)
        self.b.seed_members([{"name": "a", "data_dir": "data/a"}])

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def test_create_category(self):
        self.assertEqual(self.b.create_category("Travel", "expense"), "created")
        self.assertEqual(self.b.create_category("Travel", "income"), "exists")
        self.assertEqual(
            [c for c in self.b.categories() if c["label"] == "Travel"][0]["kind"],
            "expense")
        with self.assertRaises(ValueError):
            self.b.create_category("   ", "expense")
        # importer path still creates implicitly
        self.assertEqual(self.b.get_or_create_category("Pet care", "expense"),
                         "Pet_care")
        self.assertIn("Pet_care", {c["label"] for c in self.b.categories()})


if __name__ == "__main__":
    unittest.main()

class TestCleanupSemantics(unittest.TestCase):
    """category aliases, credited transfer == income, migration v3."""

    def setUp(self):
        import tempfile
        self.d = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.d.name, "n.db")
        self.b = FinanceBackend(path=self.path)
        self.b.seed_members([{"name": "a", "data_dir": "data/a"}])
        self.kw = dict(account="A", balance=0, file="f")

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def _row(self, particulars):
        return self.b.con.execute(
            "SELECT kind, category_id FROM entries WHERE particulars = ?",
            (particulars,)).fetchone()

    def test_credited_transfer_becomes_income(self):
        self.b.upsert_bank_entry("a", date="2024-05-01", particulars="T1",
                                 deposit=500, withdrawal=0, kind="transfer",
                                 category="UNKNOWN", **self.kw)
        self.assertEqual(tuple(self._row("T1")), ("income", ""))

    def test_debit_transfer_stays_transfer(self):
        self.b.upsert_bank_entry("a", date="2024-05-01", particulars="T2",
                                 deposit=0, withdrawal=500, kind="transfer",
                                 category="", **self.kw)
        self.assertEqual(self._row("T2")["kind"], "transfer")

    def test_dashboard_edit_credit_to_transfer_is_income(self):
        self.b.upsert_bank_entry("a", date="2024-05-01", particulars="T3",
                                 deposit=500, withdrawal=0, kind="expense",
                                 category="Misc_small", **self.kw)
        eid = self.b.con.execute("SELECT id FROM entries").fetchone()[0]
        self.b.update_entry(eid, "a", kind="transfer")
        self.assertEqual(tuple(self._row("T3")), ("income", ""))

    def test_category_aliases_merge(self):
        for i, lbl in enumerate(("Loan & EMO", "Bank & DP Charges", "nan")):
            self.b.upsert_bank_entry("a", date="2024-05-0%d" % (i + 1),
                                     particulars=f"A{i}", deposit=0,
                                     withdrawal=10, kind="expense",
                                     category=lbl, **self.kw)
        labels = {r["label"] for r in self.b.categories()}
        self.assertTrue({"Loan_EMI", "Bank_DP_charges",
                         "Shopping_goods"} <= labels)
        self.assertFalse(labels & {"Loan & EMO", "Bank & DP Charges", "nan"})

    def test_migration_v3_cleans_legacy_rows(self):
        import sqlite3
        con = sqlite3.connect(self.path)
        con.execute("PRAGMA user_version = 2")
        con.executescript("""
        INSERT OR IGNORE INTO categories VALUES ('Loan & EMO','Loan & EMO','expense'),
          ('nan','nan','refund'),('UNKNOWN','UNKNOWN','review');
        INSERT INTO entries (user_id,category_id,kind,fy,date,particulars,
          deposit,withdrawal,nkey) VALUES
          ('a','Loan & EMO','expense','2024-25','2024-05-01','L',0,5,'k1'),
          ('a','nan','refund','2024-25','2024-05-02','R',9,0,'k2'),
          ('a','UNKNOWN','transfer','2024-25','2024-05-03','CT',7,0,'k3'),
          ('a','UNKNOWN','transfer','2024-25','2024-05-04','DT',0,7,'k4');""")
        con.commit(); con.close()
        self.b.close()
        self.b = FinanceBackend(path=self.path)
        got = {r["particulars"]: (r["kind"], r["category_id"])
               for r in self.b.con.execute("SELECT * FROM entries")}
        self.assertEqual(got["L"], ("expense", "Loan_EMI"))
        self.assertEqual(got["R"], ("refund", "Shopping_goods"))
        self.assertEqual(got["CT"], ("income", ""))
        self.assertEqual(got["DT"], ("transfer", "Transfer"))  # v8
        self.assertNotIn("nan", {r["id"] for r in self.b.categories()})
        self.b.close()
        self.b = FinanceBackend(path=self.path)  # idempotent re-run


class TestCategoryUnderscores(unittest.TestCase):
    """category labels never contain whitespace."""

    def test_canon_category(self):
        from backend.model import canon_category
        self.assertEqual(canon_category("  Home &  maintenance "), "Home_maintenance")
        self.assertEqual(canon_category("Groceries"), "Groceries")
        self.assertEqual(canon_category("Loan & EMO"), "Loan_EMI")
        self.assertEqual(canon_category(""), "")
        self.assertEqual(canon_category(None), "")

    def test_all_write_paths_store_underscores(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            b = FinanceBackend(path=os.path.join(d, "n.db"))
            b.seed_members([{"name": "a", "data_dir": "data/a"}])
            b.upsert_bank_entry("a", account="A", date="2024-05-01", particulars="P", deposit=0, withdrawal=5,
                                balance=0, kind="expense",
                                category="Dining out", file="f")
            self.assertEqual(b.create_category("Pet care", "expense"), "created")
            self.assertEqual(b.create_category("Pet  care", "expense"), "exists")
            rid = b.entries()[0]["id"]
            b.update_entry(rid, "a", category="Kids & school")
            labels = {c["label"] for c in b.categories()}
            self.assertTrue({"Pet_care", "Kids_school"} <= labels)
            self.assertFalse([l for l in labels if " " in l])
            b.close()

    def test_migration_v4_renames_legacy_rows_idempotently(self):
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.db")
            b = FinanceBackend(path=path)
            b.seed_members([{"name": "a", "data_dir": "data/a"}])
            b.close()
            con = sqlite3.connect(path)
            con.execute("PRAGMA user_version = 3")
            con.executescript("""
            INSERT OR IGNORE INTO categories VALUES ('Dining out','Dining out','expense'),
                                          ('Groceries','Groceries','expense');
            INSERT INTO entries (user_id,category_id,kind,fy,date,particulars,
              deposit,withdrawal,nkey) VALUES
              ('a','Dining out','expense','2024-25','2024-05-01','D',0,5,'k1'),
              ('a','Groceries','expense','2024-25','2024-05-02','G',0,5,'k2');""")
            con.commit(); con.close()
            for _ in range(2):  # second open proves idempotence
                b = FinanceBackend(path=path)
                got = {r["particulars"]: r["category_id"] for r in
                       b.con.execute("SELECT * FROM entries")}
                self.assertEqual(got, {"D": "Dining_out", "G": "Groceries"})
                self.assertFalse([c for c in b.categories() if " " in c["label"]])
                b.close()


class TestCategoryNoAmpersand(unittest.TestCase):
    """no '&' either; labels are [A-Za-z0-9_] words joined by '_'."""

    def test_canon_drops_ampersand(self):
        from backend.model import canon_category
        for raw, want in [("Loan & EMI", "Loan_EMI"), ("Loan_&_EMI", "Loan_EMI"),
                          ("Fitness&Sports", "Fitness_Sports"),
                          ("Home &  maintenance", "Home_maintenance"),
                          ("Bank & DP Charges", "Bank_DP_charges"),
                          ("Pet_care", "Pet_care"), ("a & & b", "a_b")]:
            self.assertEqual(canon_category(raw), want, raw)

    def test_migration_v5_renames_legacy_rows_idempotently(self):
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.db")
            b = FinanceBackend(path=path)
            b.seed_members([{"name": "a", "data_dir": "data/a"}])
            b.close()
            con = sqlite3.connect(path)
            con.execute("PRAGMA user_version = 4")
            con.executescript("""
            INSERT OR IGNORE INTO categories VALUES ('Loan_&_EMI','Loan_&_EMI','expense'),
                                          ('Dining_out','Dining_out','expense');
            INSERT INTO entries (user_id,category_id,kind,fy,date,particulars,
              deposit,withdrawal,nkey) VALUES
              ('a','Loan_&_EMI','expense','2024-25','2024-05-01','L',0,5,'k1'),
              ('a','Dining_out','expense','2024-25','2024-05-02','D',0,5,'k2');""")
            con.commit(); con.close()
            for _ in range(2):
                b = FinanceBackend(path=path)
                got = {r["particulars"]: r["category_id"] for r in
                       b.con.execute("SELECT * FROM entries")}
                self.assertEqual(got, {"L": "Loan_EMI", "D": "Dining_out"})
                self.assertFalse([c for c in b.categories() if "&" in c["label"]])
                b.close()

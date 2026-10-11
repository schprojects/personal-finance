"""Unified app: auto-rules, SQL/text filter, staged import,
export suffixing, creatable kinds, and the Import / Export views."""
import os
import tempfile
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

from backend import FinanceBackend
from helpers import session_token
from finance import exporting, importing, query, rules
from finance.rules import parse_rules

from test_app import APP, USER_A, make_db

CARRIER = ("DATE,MODE,PARTICULARS,DEPOSIT,WITHDRAWAL,BALANCE,KIND,CATEGORY,ACCOUNT\n"
           "01-04-2024,UPI,SWIGGY ORDER 1,0,200,0,,,A1\n"
           "02-04-2024,UPI,SWIGGY ORDER 2,0,300,0,expense,Dining_out,A1\n"
           "03-04-2024,NEFT,SALARY ACME,5000,0,0,,,A1\n"
           "04-04-2024,UPI,MYSTERY PAYEE,0,10,0,,,A1\n")
RULES = parse_rules(["expense,Groceries,swiggy"])[0]


class TestRules(unittest.TestCase):
    """auto-rules.csv syntax; every README example is checked here."""
    KINDS = {"expense", "income", "investment", "transfer", "refund", "credit",
             "review", "reimbursed"}

    def one(self, line, **row):
        rs, warn = parse_rules([line], self.KINDS)
        self.assertEqual(warn, [], line)
        row.setdefault("deposit", 0)
        row.setdefault("withdrawal", 0)
        return rules.suggest(rs, row)

    def test_plain_text_is_case_insensitive_substring(self):
        r = self.one("expense,Groceries,SM MART", particulars="upi/sm mart@okpay", withdrawal=5)
        self.assertEqual((r.kind, r.category), ("expense", "Groceries"))
        self.assertIsNone(self.one("expense,Groceries,SM MART", particulars="zomato", withdrawal=5))

    def test_alternatives(self):
        line = "expense,Dining out,SWIGGY|ZOMATO|DOMINOS"
        for p in ("a swiggy", "ZOMATO 1", "dominos"):
            self.assertEqual(self.one(line, particulars=p, withdrawal=9).category, "Dining_out")
        self.assertIsNone(self.one(line, particulars="kfc", withdrawal=9))

    def test_direction(self):
        self.assertEqual(self.one("income,,ACH/ @dir:dep", particulars="ACH/X", deposit=5).kind, "income")
        self.assertIsNone(self.one("income,,ACH/ @dir:dep", particulars="ACH/X", withdrawal=5))
        self.assertIsNotNone(self.one("expense,Cash withdrawals,ATM @dir:wd", particulars="ATM", withdrawal=5))
        self.assertIsNone(self.one("expense,Cash withdrawals,ATM @dir:wd", particulars="ATM", deposit=5))

    def test_amount_operators(self):
        for op, amt, hit in (("<=", 10000, True), ("<=", 10001, False),
                             (">", 10000, False), (">", 10001, True),
                             (">=", 10000, True), ("<", 10000, False),
                             ("=", 10000, True), ("=", 9999, False)):
            r = self.one(f"expense,Loan & EMI,CHELU @amt{op}10000", particulars="chelu", withdrawal=amt)
            self.assertEqual(r is not None, hit, (op, amt))

    def test_qualifiers_combine(self):
        line = "expense,Shopping,AMAZON @dir:wd @amt>=500"
        self.assertIsNotNone(self.one(line, particulars="amazon", withdrawal=500))
        self.assertIsNone(self.one(line, particulars="amazon", withdrawal=499))
        self.assertIsNone(self.one(line, particulars="amazon", deposit=900))

    def test_legacy_mode_qualifier_is_more_text(self):
        r = self.one("expense,Bank & DP charges,@mode:BANK CHARGES", particulars="x BANK CHARGES y", withdrawal=1)
        self.assertEqual(r.category, "Bank_DP_charges")

    def test_any_kind_and_debited_transfer(self):
        self.assertEqual(self.one("investment,,ZERODHA|GROWW @dir:wd", particulars="groww", withdrawal=1).kind, "investment")
        self.assertEqual(self.one("transfer,,TO FAMILY @dir:wd", particulars="to family", withdrawal=1).kind, "transfer")

    def test_comments_blank_lines_and_quoted_header_are_ignored(self):
        rs, warn = parse_rules(['"# auto-rules.csv - header"', "# KIND,CATEGORY,EXPRESSION",
                                "", "expense,Groceries,MART"], self.KINDS)
        self.assertEqual((len(rs), warn), (1, []))

    def test_bad_lines_are_skipped_and_reported(self):
        rs, warn = parse_rules(["expense,Groceries", "bogus,Groceries,X",
                                "expense,Groceries,X @amt~5", "expense,Groceries,X @dir:up",
                                "expense,Groceries,@amt>5", "expense,Groceries,OK"], self.KINDS)
        self.assertEqual([r.texts for r in rs], [("OK",)])
        self.assertEqual(len(warn), 5)
        self.assertTrue(warn[0].startswith("line 1:"))

    def test_first_match_wins_and_loader(self):
        rs, _ = parse_rules(["expense,Dining_out,swiggy", "expense,Other,swig"])
        self.assertEqual(rules.suggest(rs, {"particulars": "UPI SWIGGY", "withdrawal": 1}).category, "Dining_out")
        self.assertEqual(rules.load_rules("/nonexistent/auto-rules.csv"), ([], []))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "auto-rules.csv")
            with open(p, "w") as f:
                f.write("expense,Groceries,MART\n")
            self.assertEqual(len(rules.load_rules(p)[0]), 1)

    def test_apply_rules_never_overrides_a_value(self):
        out = rules.apply_rules(RULES, [
            {"particulars": "swiggy", "kind": "", "category": "", "withdrawal": 1},
            {"particulars": "swiggy", "kind": "expense", "category": "Fixed", "withdrawal": 1}])
        self.assertEqual((out[0]["category"], out[0]["rule"]), ("Groceries", True))
        self.assertEqual((out[1]["category"], out[1]["rule"]), ("Fixed", False))

    def test_the_real_format_example_file_parses_cleanly(self):
        rs, warn = rules.load_rules(os.path.join(os.path.dirname(rules.__file__), "..", "auto-rules.example.csv"), self.KINDS)
        self.assertEqual(warn, [])
        self.assertGreater(len(rs), 5)


class TestQuery(unittest.TestCase):
    ROWS = [{"id": 1, "kind": "review", "category": "UNKNOWN", "particulars": "UPI SWIGGY"},
            {"id": 2, "kind": "expense", "category": "Groceries", "particulars": "MART"},
            {"id": 3, "kind": "income", "category": "", "particulars": "SALARY"}]

    def test_where_variants(self):
        for q in ("where category = 'Groceries'",
                  "select * where category = 'Groceries'",
                  "SELECT * FROM t WHERE category = 'Groceries'"):
            self.assertEqual([r["id"] for r in query.run_query(self.ROWS, q)], [2])

    def test_is_empty_shorthand(self):
        q = "where kind is empty and category is empty and particulars like '%swiggy%'"
        self.assertEqual([r["id"] for r in query.run_query(self.ROWS, q)], [1])

    def test_rejects_non_select_and_bad_sql(self):
        for q in ("delete from t", "where 1; drop table t", "", "where nope = 1",
                  "select * from t"):
            with self.assertRaises(ValueError):
                query.run_query(self.ROWS, q)

    def test_text_and_sql_are_exclusive(self):
        with self.assertRaises(ValueError):
            query.filter_rows(self.ROWS, text="x", sql="where 1=1")
        self.assertEqual(len(query.filter_rows(self.ROWS, text="mart")), 1)
        self.assertEqual(len(query.filter_rows(self.ROWS)), 3)


class TestImporting(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.csv = os.path.join(self.d.name, "s.csv")
        with open(self.csv, "w") as f:
            f.write(CARRIER)
        self.db = os.path.join(self.d.name, "n.db")
        make_db(self.db)
        self.b = FinanceBackend(path=self.db)

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def test_precedence_csv_then_rule_then_default(self):
        raw, failed, _ = importing.stage_statement(self.csv)
        self.assertEqual(failed, [])
        got = {r["particulars"]: (r["kind"], r["category"], r["rule"])
               for r in importing.resolve(raw, RULES)}
        self.assertEqual(got["SWIGGY ORDER 1"], ("expense", "Groceries", True))   # rule
        self.assertEqual(got["SWIGGY ORDER 2"], ("expense", "Dining_out", False))  # csv wins
        self.assertEqual(got["SALARY ACME"], ("income", "", False))
        self.assertEqual(got["MYSTERY PAYEE"], ("expense", "UNKNOWN", False))

    def test_partial_csv_classification_is_preserved_and_completed(self):
        base = {"account": "A", "date": "2024-04-01", "deposit": 0,
                "withdrawal": 10, "balance": 90, "file": "s.csv",
                "particulars": "SWIGGY PARTIAL"}
        rows = [dict(base, kind="expense", category=""),
                dict(base, particulars="CATEGORY ONLY", kind="",
                     category="Dining_out"),
                dict(base, particulars="INCOME CATEGORY", deposit=10,
                     withdrawal=0, kind="income", category="Groceries")]
        resolved = importing.resolve(rows, RULES)
        self.assertEqual([(r["kind"], r["category"], r["rule"])
                          for r in resolved],
                         [("expense", "UNKNOWN", False),
                          ("expense", "Dining_out", False),
                          ("income", "", False)])

    def test_rules_off_leaves_defaults(self):
        raw, _, _ = importing.stage_statement(self.csv)
        got = {r["particulars"]: r["category"] for r in importing.resolve(raw, [])}
        self.assertEqual(got["SWIGGY ORDER 1"], "UNKNOWN")

    def test_staging_writes_nothing_commit_writes(self):
        before = len(self.b.entries(user_ids=["a"]))
        raw, _, _ = importing.stage_statement(self.csv)
        final = importing.resolve(raw, RULES)
        self.assertEqual(len(self.b.entries(user_ids=["a"])), before)
        counts = importing.commit_rows(self.b, "a", final)
        self.assertEqual(counts["created"], 4)
        self.assertEqual(len(self.b.entries(user_ids=["a"])), before + 4)


class TestExporting(unittest.TestCase):
    def test_unique_path_adds_numeric_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.csv")
            rows = [{"user_id": "a", "account": "A", "date": "2024-04-01",
                     "particulars": "P", "deposit": 0.0,
                     "withdrawal": 5.0, "balance": 1.0, "kind": "expense",
                     "category": "G", "fy": "2024-25"}]
            first, n = exporting.export_to_path(rows, p)
            second, _ = exporting.export_to_path(rows, p)
            third, _ = exporting.export_to_path(rows, p)
            self.assertEqual((first, n), (p, 1))
            self.assertEqual(second, os.path.join(d, "x-1.csv"))
            self.assertEqual(third, os.path.join(d, "x-2.csv"))
            with open(first) as f:
                self.assertIn("01-04-2024", f.read())

    def test_default_path(self):
        self.assertEqual(exporting.default_export_path(["b", "a"], ["2024-25"], "o"),
                         os.path.join("o", "export-a+b-2024-25.csv"))


class TestBackendAdditions(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.b = FinanceBackend(path=os.path.join(self.d.name, "n.db"))

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def test_spec_categories_seeded(self):
        labels = {c["label"] for c in self.b.categories()}
        self.assertEqual(len({"Groceries", "Loan_EMI", "UNKNOWN", "Vehicles",
                              "Utilities", "Income_tax"} - labels), 0)
        self.assertGreaterEqual(len(labels), 25)

    def test_create_kind_then_use_it(self):
        self.assertEqual(self.b.create_kind("Gift Card"), "created")
        self.assertEqual(self.b.create_kind("gift_card"), "exists")
        self.assertIn("gift_card", self.b.kinds())
        with self.assertRaises(ValueError):
            self.b.create_kind("  ")

    def test_entries_accepts_multiple_years(self):
        self.b.seed_members([{"name": "a", "data_dir": "d"}])
        for day, fy in (("2024-05-01", "2024-25"), ("2025-05-01", "2025-26"),
                        ("2026-05-01", "2026-27")):
            self.b.upsert_bank_entry("a", account="A", date=day, particulars=day, deposit=0, withdrawal=1,
                                     balance=0, kind="expense", category="Groceries")
        self.assertEqual(len(self.b.entries(fy=["2024-25", "2026-27"])), 2)
        self.assertEqual(len(self.b.entries(fy="2025-26")), 1)


class TestImportAndExportViews(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)
        self.csv = os.path.join(self.d.name, "s.csv")
        with open(self.csv, "w") as f:
            f.write(CARRIER)
        self.rules_file = os.path.join(self.d.name, "auto-rules.csv")
        with open(self.rules_file, "w") as f:
            f.write("expense,Groceries,swiggy\n")
        p = mock.patch("finance.rules.RULES_FILE", self.rules_file)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.d.cleanup()

    def _at(self, page):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER_A)
        at.session_state["token"] = session_token(self.db, "a", "pw")
        at.run()
        at.radio(key="page").set_value(page)
        at.run()
        return at

    def test_import_preview_then_save_goes_to_transactions(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Import")
            self.assertFalse(at.exception)
            at.text_input(key="imp_path_—").set_value(self.csv)
            at.run()
            next(b for b in at.button if b.label == "Load file").click().run()
            self.assertFalse(at.exception)
            # preview in memory: nothing written yet
            b = FinanceBackend(path=self.db)
            n0 = len(b.entries(user_ids=["a"]))
            b.close()
            matched = next(m for m in at.metric if m.label.startswith("Matched"))
            self.assertEqual(matched.value, "1")
            at.checkbox(key="imp_rules").uncheck().run()
            matched = next(m for m in at.metric if m.label.startswith("Matched"))
            self.assertEqual(matched.value, "0")
            at.checkbox(key="imp_rules").check().run()
            next(b for b in at.button if b.label.startswith("Save import")).click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.radio(key="page").value, "Transactions")
            b = FinanceBackend(path=self.db)
            self.assertEqual(len(b.entries(user_ids=["a"])), n0 + 4)
            b.close()

    def test_export_writes_file_and_never_overwrites(self):
        out = os.path.join(self.d.name, "out.csv")
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Export")
            self.assertFalse(at.exception)
            for _ in range(2):
                at.text_input(key=[t.key for t in at.text_input
                                   if t.key.startswith("exp_path")][0]).set_value(out)
                at.run()
                at.button(key="exp_go").click().run()
                self.assertFalse(at.exception)
        self.assertTrue(os.path.exists(out))
        self.assertTrue(os.path.exists(os.path.join(self.d.name, "out-1.csv")))

    def test_home_links_navigate(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = AppTest.from_file(APP)
            at.session_state["user"] = dict(USER_A)
            at.session_state["token"] = session_token(self.db, "a", "pw")
            at.run()
            self.assertEqual(at.radio(key="page").value, "Home")
            at.button(key="home_Export").click().run()
            self.assertEqual(at.radio(key="page").value, "Export")


def _spend(fy, cat, wd, kind="expense", dep=0):
    return {"member": "a", "kind": kind, "wd": wd, "dep": dep, "fy": fy, "category": cat}


class TestMultiYearCategoryAggregation(unittest.TestCase):
    """Categories are totalled across the selected years (Top category / pie)."""

    def rows(self):
        # Loan_EMI's largest single year (100) beats any single Groceries year,
        # but Groceries (60+70+80 = 210) wins once the years are combined.
        return [_spend("2022-23", "Loan_EMI", 100), _spend("2022-23", "Groceries", 60),
                _spend("2023-24", "Loan_EMI", 20), _spend("2023-24", "Groceries", 70),
                _spend("2024-25", "Groceries", 80), _spend("2024-25", "Dining_out", 5)]

    def test_same_category_is_summed_over_years(self):
        from finance import spend
        self.assertEqual(spend.by_category(self.rows()),
                         {"Loan_EMI": 120.0, "Groceries": 210.0, "Dining_out": 5.0})
        self.assertEqual(sum(spend.by_category(self.rows()).values()),
                         spend.total(self.rows()))

    def test_top_category_over_years_is_the_largest_total(self):
        from finance import spend
        by_bucket = max(spend.by_fy_category(self.rows()).items(), key=lambda kv: kv[1])
        self.assertEqual(by_bucket[0][1], "Loan_EMI")        # the old, wrong answer
        self.assertEqual(spend.top_categories(self.rows(), 1), [("Groceries", 210.0)])

    def test_top_categories_have_no_duplicate_labels(self):
        from finance import spend
        names = [c for c, _ in spend.top_categories(self.rows())]
        self.assertEqual(len(names), len(set(names)))

    def test_top_eight_ranks_after_consolidation(self):
        from finance import spend
        # 10 categories over 2 years; split halves so no single bucket ranks right
        rows = []
        for i in range(10):
            rows += [_spend("2023-24", f"C{i}", 10 * (i + 1)),
                     _spend("2024-25", f"C{i}", 10 * (i + 1))]
        # Big_once (150) beats every single-year bucket (max 100) but not C9..C7 combined
        rows.append(_spend("2023-24", "Big_once", 150))
        top = spend.top_categories(rows)
        self.assertEqual(len(top), 8)
        self.assertEqual([c for c, _ in top],
                         ["C9", "C8", "C7", "Big_once", "C6", "C5", "C4", "C3"])
        self.assertEqual(top[0][1], 200.0)
        # the three dropped categories are the smallest consolidated totals
        self.assertEqual(sorted(spend.by_category(rows).values())[:3], [20.0, 40.0, 60.0])

    def test_magnitude_ranking_uses_net_after_credit_on_expense(self):
        from finance import spend
        rows = [_spend("2023-24", "A", 100), _spend("2024-25", "A", 0, dep=30),
                _spend("2024-25", "B", 90)]
        self.assertEqual(spend.top_categories(rows), [("B", 90.0), ("A", 70.0)])

    def test_overview_top_category_and_pie_consolidate_years(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"FINANCE_DB": os.path.join(d, "n.db")}):
            db = os.path.join(d, "n.db")
            with FinanceBackend(path=db) as b:
                b.seed_members([{"name": "a", "data_dir": "d"}])
                b.set_password("a", "pw")
                for date, cat, wd in (("2022-05-01", "Loan_EMI", 100), ("2022-06-01", "Groceries", 60),
                                      ("2023-05-01", "Loan_EMI", 20), ("2023-06-01", "Groceries", 70),
                                      ("2024-05-01", "Groceries", 80)):
                    b.upsert_bank_entry("a", account="A", date=date, particulars=cat + date,
                                        deposit=0, withdrawal=wd, balance=0,
                                        kind="expense", category=cat)
            at = AppTest.from_file(APP)
            at.session_state["user"] = dict(USER_A)
            at.session_state["token"] = session_token(db, "a", "pw")
            at.run()
            at.radio(key="page").set_value("Overview").run()
            self.assertFalse(at.exception)
            top = [m for m in at.metric if m.label == "Top category"][0]
            self.assertEqual((top.value, top.delta), ("Groceries", "₹210.00"))
            import plotly.io as pio
            figs = [pio.from_json(e.proto.spec) for e in at.get("plotly_chart")]
            pie = [t for fig in figs for t in fig.data if t.type == "pie"][0]
            self.assertEqual(sorted(pie.labels), ["Groceries", "Loan_EMI"])
            import base64
            import numpy as np
            vals = pie.values          # plotly 7 may ship arrays as {dtype, bdata}
            if isinstance(vals, dict):
                vals = np.frombuffer(base64.b64decode(vals["bdata"]), dtype=vals["dtype"])
            labels = pie.labels
            if isinstance(labels, dict):
                labels = list(labels.values())
            self.assertEqual({k: float(v) for k, v in zip(labels, vals)},
                             {"Groceries": 210.0, "Loan_EMI": 120.0})


class TestConnectionOwnership(unittest.TestCase):
    """Backends are closeable and context-managed; the CLI closes its own."""

    def test_context_manager_closes_the_connection(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            with FinanceBackend(path=os.path.join(d, "n.db")) as b:
                b.con.execute("SELECT 1")
            with self.assertRaises(sqlite3.ProgrammingError):
                b.con.execute("SELECT 1")

    def test_cli_main_closes_its_backend(self):
        import impex
        opened = []
        real = impex.FinanceBackend

        class Spy(real):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                opened.append(self)

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"FINANCE_DB": os.path.join(d, "n.db")}), \
                mock.patch("impex.FinanceBackend", Spy), \
                mock.patch("impex.load_members", return_value=[{"name": "a", "data_dir": "d"}]):
            impex.main(["backup", "--dest", os.path.join(d, "bk")])
            self.assertEqual(len(opened), 1)
            import sqlite3
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].con.execute("SELECT 1")

    def test_db_path_resolution_order(self):
        from backend import paths
        with tempfile.TemporaryDirectory() as d:
            env = os.path.join(d, ".env")
            with open(env, "w") as f:
                f.write("# c\nFINANCE_DB=real/networth.db\n")
            self.assertEqual(paths._dotenv_value("FINANCE_DB", env), "real/networth.db")
            with mock.patch.object(paths, "BASE", d), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("FINANCE_DB", None)
                self.assertEqual(paths.db_path(), os.path.join(d, "real", "networth.db"))
                os.environ["FINANCE_DB"] = "/x/y.db"          # env var wins
                self.assertEqual(paths.db_path(), "/x/y.db")
            os.remove(env)
            with mock.patch.object(paths, "BASE", d), mock.patch.dict(os.environ, {}):
                os.environ.pop("FINANCE_DB", None)
                self.assertEqual(paths.db_path(), os.path.join(d, "var", "networth.db"))


class TestRulesFileOnly(unittest.TestCase):
    """rules come from auto-rules.csv only; the db table is gone."""

    def test_v9_drops_table_and_carries_stored_rules_into_the_csv(self):
        import sqlite3
        from backend import schema
        with tempfile.TemporaryDirectory() as d:
            path, rules_csv = os.path.join(d, "n.db"), os.path.join(d, "auto-rules.csv")
            FinanceBackend(path=path).close()
            con = sqlite3.connect(path)
            con.execute("PRAGMA user_version = 8")
            con.executescript("""CREATE TABLE auto_rules (rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_column TEXT, pattern TEXT, kind TEXT, category TEXT);
                INSERT INTO auto_rules (match_column,pattern,kind,category)
                VALUES ('particulars','swiggy','expense','Dining_out'),
                       ('particulars','mart','expense','Groceries');""")
            con.commit()
            with open(rules_csv, "w") as f:
                f.write("expense,Groceries,mart\n")           # already present: not duplicated
            with mock.patch("finance.rules.RULES_FILE", rules_csv):
                schema.migrate(con)
                schema.migrate(con)                              # idempotent
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            con.close()
            self.assertNotIn("auto_rules", tables)
            with open(rules_csv) as f:
                text = f.read()
            self.assertEqual(text.count("expense,Groceries,mart"), 1)
            self.assertIn("expense,Dining_out,swiggy", text)

    def test_rules_file_that_is_a_directory_means_no_rules(self):
        """Docker makes a folder for a missing bind-mounted file."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(rules.load_rules(d), ([], []))

    def test_missing_members_file_gives_a_clear_message_and_example_is_valid(self):
        import yaml
        from finance import common
        with tempfile.TemporaryDirectory() as d, mock.patch.object(common, "BASE", d):
            with self.assertRaises(SystemExit) as cm:
                common.load_members()
            self.assertIn("members.example.yaml", str(cm.exception))
            os.mkdir(os.path.join(d, "members.yaml"))      # Docker's folder
            with self.assertRaises(SystemExit):
                common.load_members()
        ex = os.path.join(os.path.dirname(common.__file__), "..", "members.example.yaml")
        with open(ex) as f:
            doc = yaml.safe_load(f)
        self.assertEqual([m["name"] for m in doc["members"]], ["alex", "blair"])

    def test_backend_has_no_db_rule_api(self):
        self.assertFalse(hasattr(FinanceBackend, "auto_rules"))
        self.assertFalse(hasattr(FinanceBackend, "save_rules"))

    def test_import_screen_reports_missing_file_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"FINANCE_DB": os.path.join(d, "n.db")}):
            db = os.path.join(d, "n.db")
            make_db(db)
            csv_path = os.path.join(d, "s.csv")
            with open(csv_path, "w") as f:
                f.write(CARRIER)
            rf = os.path.join(d, "auto-rules.csv")
            with mock.patch("finance.rules.RULES_FILE", rf):
                def open_import():
                    at = AppTest.from_file(APP)
                    at.session_state["user"] = dict(USER_A)
                    at.session_state["token"] = session_token(db, "a", "pw")
                    at.run()
                    at.radio(key="page").set_value("Import").run()
                    at.text_input(key="imp_path_—").set_value(csv_path).run()
                    next(b for b in at.button if b.label == "Load file").click().run()
                    self.assertFalse(at.exception)
                    return at
                at = open_import()
                self.assertTrue(any("auto-rules.csv" in i.value for i in at.info))
                with open(rf, "w") as f:
                    f.write("expense,Groceries,swiggy\nbogus,X,Y\n")
                at = open_import()
                self.assertTrue(any("1 rule line(s) skipped" in w.value for w in at.warning))
                matched = next(m for m in at.metric if m.label.startswith("Matched"))
                self.assertEqual(matched.value, "1")


if __name__ == "__main__":
    unittest.main()


ICICI = ("\n"
         "Statement of Transactions in SavingsNumber: XXXXXXXX3404\n"
         "\n"
         "DATE,MODE,PARTICULARS,DEPOSITS,WITHDRAWALS,BALANCE\n"
         "01-04-2024,,B/F,0,0,1000\n"
         "02-04-2024,DEBIT CARD,SHOP ONE,0,100,900\n"
         "03-04-2024,NET BANKING,SALARY,250,0,1150\n")


class TestModeRemoved(unittest.TestCase):
    """Payment mode is not stored, exported or shown."""

    def test_statement_with_mode_column_still_imports_and_mode_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.csv")
            with open(p, "w") as f:
                f.write(ICICI)
            rows, failed, _ = importing.stage_statement(p)
            self.assertEqual(failed, [])
            self.assertEqual([r["particulars"] for r in rows], ["SHOP ONE", "SALARY"])
            self.assertTrue(all("mode" not in r for r in rows))

    def test_fresh_database_has_no_mode_column(self):
        with tempfile.TemporaryDirectory() as d:
            b = FinanceBackend(path=os.path.join(d, "n.db"))
            cols = {r[1] for r in b.con.execute("PRAGMA table_info(entries)")}
            b.close()
        self.assertNotIn("mode", cols)

    def test_older_dev_database_with_mode_column_still_works(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.db")
            b = FinanceBackend(path=path)
            b.seed_members([{"name": "a", "data_dir": "d"}])
            b.close()
            con = sqlite3.connect(path)
            con.execute("ALTER TABLE entries ADD COLUMN mode TEXT NOT NULL DEFAULT ''")
            con.commit()
            con.close()
            b = FinanceBackend(path=path)
            self.assertEqual(b.upsert_bank_entry(
                "a", account="A", date="2024-05-01", particulars="P", deposit=0,
                withdrawal=1, balance=0, kind="expense", category="Groceries"),
                "created")
            self.assertEqual(len(b.entries(user_ids=["a"])), 1)
            b.close()

    def test_export_has_no_mode_header(self):
        self.assertNotIn("MODE", exporting.EXPORT_COLS)

    def test_overview_has_no_modes_filter(self):
        d = tempfile.TemporaryDirectory()
        db = os.path.join(d.name, "networth.db")
        make_db(db)
        with mock.patch.dict(os.environ, {"FINANCE_DB": db}):
            at = AppTest.from_file(APP)
            at.session_state["user"] = dict(USER_A)
            at.session_state["token"] = session_token(db, "a", "pw")
            at.run()
            at.radio(key="page").set_value("Overview").run()
            self.assertFalse(at.exception)
            self.assertNotIn("Modes", [m.label for m in at.multiselect])
        d.cleanup()


class TestBaselineExplanation(unittest.TestCase):
    """Daily-spend baseline: exactly four categories, listed with amounts."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)
        b = FinanceBackend(path=self.db)
        for day, cat, amt in (("2024-04-10", "Loan_EMI", 700),
                              ("2024-04-11", "Vehicles", 300),
                              ("2024-04-12", "Misc_small", 40)):
            b.upsert_bank_entry("a", account="A", date=day, particulars=f"X {cat}",
                                deposit=0, withdrawal=amt, balance=0,
                                kind="expense", category=cat)
        b.close()

    def tearDown(self):
        self.d.cleanup()

    def _overview(self):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER_A)
        at.session_state["token"] = session_token(self.db, "a", "pw")
        at.run()
        at.radio(key="page").set_value("Overview").run()
        self.assertFalse(at.exception)
        return at

    def _table(self, at, first_col):
        return next(t.value for t in at.dataframe
                    if first_col in t.value.columns)

    def test_baseline_set_is_exactly_four_and_excludes_small_misc(self):
        from finance.common import BASELINE_EXCLUDED
        self.assertEqual(BASELINE_EXCLUDED,
                         {"Income_tax", "Vehicles", "Loan_EMI", "Bank_DP_charges"})

    def test_off_lists_categories_as_would_be_excluded(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._overview()
            t = self._table(at, "Status")
            self.assertEqual(set(t["Status"]), {"would be excluded"})
            by = dict(zip(t["Category"], t["Net spend"]))
            self.assertEqual(by["Loan_EMI"], "₹700.00")
            self.assertEqual(by["Vehicles"], "₹300.00")
            self.assertEqual(by["Total"], "₹1,000.00")
            self.assertNotIn("Misc_small", by)

    def test_on_excludes_exactly_the_listed_total(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._overview()
            before = next(m.value for m in at.metric if m.label == "Net spend")
            at.checkbox(key="baseline").check().run()
            self.assertFalse(at.exception)
            after = next(m.value for m in at.metric if m.label == "Net spend")
            t = self._table(at, "Status")
            self.assertEqual(set(t["Status"]), {"excluded"})
            rem = self._table(at, "Filter / option")
            removed = dict(zip(rem["Filter / option"], rem["Net spend removed"]))
            self.assertEqual(removed["Daily-spend baseline"], "₹1,000.00")
            num = lambda s: float(s.replace("₹", "").replace(",", ""))  # noqa: E731
            self.assertAlmostEqual(num(before) - num(after), 1000.0)


class TestMiscSmallSpelling(unittest.TestCase):
    def test_seeded_label_matches_spec_and_v7_repairs_a_v6_database(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.db")
            b = FinanceBackend(path=path)
            labels = {c["label"] for c in b.categories()}
            b.seed_members([{"name": "a", "data_dir": "d"}])
            b.close()
            self.assertIn("Misc_small", labels)
            self.assertNotIn("Mis_small", labels)
            # a database that was already at v6 with the typo row in use
            con = sqlite3.connect(path)
            con.execute("PRAGMA user_version = 6")
            con.execute("INSERT INTO categories VALUES ('Mis_small','Mis_small','expense')")
            con.execute(
                "INSERT INTO entries (user_id,category_id,kind,fy,date,particulars,"
                "deposit,withdrawal,nkey) VALUES "
                "('a','Mis_small','expense','2024-25','2024-05-01','P',0,5,'k')")
            con.commit()
            con.close()
            for _ in range(2):  # second open proves idempotence
                b = FinanceBackend(path=path)
                row = b.con.execute("SELECT category_id FROM entries").fetchone()
                gone = b.con.execute(
                    "SELECT COUNT(*) FROM categories WHERE id='Mis_small'").fetchone()[0]
                b.close()
                self.assertEqual((row[0], gone), ("Misc_small", 0))


class TestVisualDesign(unittest.TestCase):
    """theme file, helpers and the redesigned Home / Categories."""

    def _at(self, db, page=None, scope=None):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER_A)
        at.session_state["token"] = session_token(db, "a", "pw")
        at.run()
        if page:
            at.radio(key="page").set_value(page).run()
        if scope:
            at.radio(key="scope").set_value(scope).run()
        self.assertFalse(at.exception)
        return at

    def test_theme_file_defines_light_and_dark(self):
        import tomllib
        with open(os.path.join(os.path.dirname(APP), ".streamlit", "config.toml"), "rb") as f:
            cfg = tomllib.load(f)
        for mode in ("light", "dark"):
            for key in ("primaryColor", "backgroundColor", "textColor"):
                self.assertIn(key, cfg["theme"][mode])
        self.assertGreaterEqual(len(cfg["theme"]["chartCategoricalColors"]), 8)
        self.assertFalse(cfg["browser"]["gatherUsageStats"])

    def test_packaging_includes_theme_and_helpers(self):
        root = os.path.dirname(APP)
        for rel in ("Dockerfile", os.path.join("scripts", "make_dist.py")):
            with open(os.path.join(root, rel)) as f:
                text = f.read()
            self.assertIn(".streamlit", text, rel)
            self.assertIn("ui_theme.py", text, rel)

    def test_style_fig_sets_shared_layout(self):
        import plotly.graph_objects as go
        import ui_theme
        fig = ui_theme.style_fig(go.Figure(), 300)
        self.assertEqual(fig.layout.height, 300)
        self.assertEqual(fig.layout.legend.orientation, "h")
        self.assertEqual(fig.layout.hovermode, "x unified")
        self.assertEqual(len(fig.layout.colorway), 8)
        self.assertIsNone(fig.layout.title.text)  # never the string "undefined"

    def test_home_has_snapshot_and_six_section_cards(self):
        d = tempfile.TemporaryDirectory()
        db = os.path.join(d.name, "n.db")
        make_db(db)
        with mock.patch.dict(os.environ, {"FINANCE_DB": db}):
            at = self._at(db)
            self.assertEqual([m.label for m in at.metric],
                             ["Net spend", "Savings rate", "Unclassified", "Net worth"])
            cards = [b.key for b in at.button if b.key and b.key.startswith("home_")
                     and b.key not in ("home_review", "home_start")]
            self.assertEqual(sorted(cards), sorted(
                f"home_{p}" for p in ("Overview", "Categories", "Transactions",
                                      "Holdings", "Import", "Export")))
            # the UNKNOWN row offers a one-click review
            at.button(key="home_review").click().run()
            self.assertEqual(at.radio(key="page").value, "Transactions")
            self.assertEqual(at.text_input(key="tx_sql").value, "where category is empty")
        d.cleanup()

    def test_empty_database_home_shows_onboarding_card(self):
        d = tempfile.TemporaryDirectory()
        db = os.path.join(d.name, "n.db")
        b = FinanceBackend(path=db)
        b.seed_members([{"name": "a", "data_dir": "d"}])
        b.set_password("a", "pw")
        b.close()
        with mock.patch.dict(os.environ, {"FINANCE_DB": db}):
            at = self._at(db)
            self.assertEqual(len(at.metric), 0)
            at.button(key="home_start").click().run()
            self.assertEqual(at.radio(key="page").value, "Import")
        d.cleanup()

    def test_category_trend_chart_has_data(self):
        d = tempfile.TemporaryDirectory()
        db = os.path.join(d.name, "n.db")
        make_db(db)
        with mock.patch.dict(os.environ, {"FINANCE_DB": db}):
            at = self._at(db, "Categories", "Me")
            charts = at.get("plotly_chart")
            self.assertGreaterEqual(len(charts), 1)
            self.assertIn('"x":', charts[0].proto.spec)  # a real series, not an empty axis
        d.cleanup()


class TestSpecViolationFixes(unittest.TestCase):
    """the six spec violations found in review."""

    # 1 -- incoming vs net spend --------------------------------------------
    def test_incoming_and_net_spend_definitions(self):
        from finance import spend
        mk = lambda k, dep=0, wd=0: {"kind": k, "dep": dep, "wd": wd, "fy": "x",  # noqa: E731
                                     "category": "C", "member": "m"}
        rows = [mk("income", dep=100), mk("credit", dep=10), mk("refund", dep=20),
                mk("transfer", dep=30), mk("expense", dep=5),   # credited expense
                mk("investment", dep=7), mk("transfer", wd=40), mk("income", wd=1)]
        self.assertEqual([r["dep"] for r in spend.incoming(rows)], [100, 10, 20, 30])
        self.assertEqual(spend.incoming_total(rows), 160)
        # refunds are not netted against spend; a credited expense is; a debited
        # transfer is spend and a credited transfer is never netted
        self.assertEqual(spend.total(rows + [mk("expense", wd=50)]), 50 - 5 + 40)

    # 2 -- MF purchase history -----------------------------------------------
    def test_mf_sheet_purchase_columns_are_parsed(self):
        from finance.holdings import parse_mf_sheet
        header = ["Scheme Name", "Folio No", "Cost Value", "Unit Balance", "NAV Date",
                  "NAV", "Market Value", "Purchase Date", "Purchase NAV"]
        schemes, _ = parse_mf_sheet(header, [["Axis", "1", "1000", "100", "01-04-2024",
                                              "11", "1100", "01-04-2023", "10"]])
        self.assertEqual((schemes[0]["purchase_date"], schemes[0]["purchase_nav"],
                          schemes[0]["nav"]), ("01-04-2023", 10.0, 11.0))

    # 3 -- XIRR with partial history ------------------------------------------
    def test_investment_xirr_is_none_for_partial_history_and_never_raises(self):
        from finance.holdings import investment_xirr
        full = dict(purchase_date="2023-04-01", latest_date="2024-04-01",
                    value_at_cost=1000, value_latest=1100)
        self.assertAlmostEqual(investment_xirr(full), .1, places=2)
        for patch in ({"value_at_cost": None}, {"value_latest": None},
                      {"purchase_date": None}, {"latest_date": ""},
                      {"purchase_date": "not-a-date"}, {"value_at_cost": 0},
                      {"purchase_date": "2024-04-01"}):  # same day
            self.assertIsNone(investment_xirr({**full, **patch}), patch)

    def test_holdings_page_renders_with_partial_history(self):
        d = tempfile.TemporaryDirectory()
        db = os.path.join(d.name, "n.db")
        make_db(db)
        b = FinanceBackend(path=db)
        b.upsert_investment("a", type_="MF", label="Partial", folio="9",
                            purchase_date="2023-04-01", latest_date="2024-04-01",
                            value_latest=500)  # cost missing: used to crash XIRR
        b.close()
        with mock.patch.dict(os.environ, {"FINANCE_DB": db}):
            at = AppTest.from_file(APP)
            at.session_state["user"] = dict(USER_A)
            at.session_state["token"] = session_token(db, "a", "pw")
            at.run()
            for scope in ("Family", "Me"):
                at.radio(key="page").set_value("Holdings").run()
                at.radio(key="scope").set_value(scope).run()
                self.assertFalse(at.exception, scope)
        d.cleanup()

    # 4 -- income keeps the default category --------------------------------
    def test_income_category_edits_are_ignored_and_kind_change_defaults(self):
        d = tempfile.TemporaryDirectory()
        b = FinanceBackend(path=os.path.join(d.name, "n.db"))
        b.seed_members([{"name": "a", "data_dir": "d"}])
        for part, dep, wd, kind in (("SAL", 100, 0, "income"), ("SHOP", 0, 5, "expense")):
            b.upsert_bank_entry("a", account="A", date="2024-05-01", particulars=part,
                                deposit=dep, withdrawal=wd, balance=0, kind=kind,
                                category="" if kind == "income" else "Groceries")
        inc, exp = (b.entries(user_ids=["a"], kinds=[k])[0]["id"] for k in ("income", "expense"))
        b.update_entry(inc, "a", category="Dining_out")           # category-only edit
        self.assertEqual(b.entries(user_ids=["a"], kinds=["income"])[0]["category"], "")
        b.bulk_update_entries("a", {inc: (None, "Dining_out"), exp: (None, "Dining_out")})
        rows = {r["kind"]: r["category"] for r in b.entries(user_ids=["a"])}
        self.assertEqual(rows, {"income": "", "expense": "Dining_out"})
        b.update_entry(inc, "a", kind="expense")                   # income -> expense
        changed = next(r for r in b.entries(user_ids=["a"]) if r["id"] == inc)
        self.assertEqual((changed["kind"], changed["category"]), ("expense", "UNKNOWN"))
        b.close()
        d.cleanup()

    def test_staging_drops_category_for_income_rows(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pf", APP)
        pf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pf)
        base = {1: {"kind": "income", "category": "—"}, 2: {"kind": "expense", "category": "G"}}
        staged = {}
        pf.stage_bulk(staged, [1, 2], base, category="Dining_out")
        self.assertEqual(staged, {2: {"category": "Dining_out"}})
        staged = {}
        pf.merge_cell_edits(staged, set(), [1, 2], {0: {"Category": "Dining_out"}}, base)
        self.assertEqual(staged, {})

    # 6 -- create kind -------------------------------------------------------
    def test_create_kind_completes_without_a_runtime_error(self):
        d = tempfile.TemporaryDirectory()
        db = os.path.join(d.name, "n.db")
        make_db(db)
        with mock.patch.dict(os.environ, {"FINANCE_DB": db}):
            at = AppTest.from_file(APP)
            at.session_state["user"] = dict(USER_A)
            at.session_state["token"] = session_token(db, "a", "pw")
            at.run()
            at.radio(key="page").set_value("Categories").run()
            at.radio(key="scope").set_value("Me").run()
            for attempt in (1, 2):
                at.text_input(key="kindname").set_value("gift card")
                next(b for b in at.button if b.label == "Create kind").click().run()
                self.assertFalse(at.exception, f"attempt {attempt}")
            b = FinanceBackend(path=db)
            self.assertIn("gift_card", b.kinds())
            b.close()
        d.cleanup()

    def test_no_bare_conditional_expression_statements(self):
        """Streamlit 'magic' re-parses a bare `a() if c else b()` statement and
        raises SyntaxError at run time (the create-kind crash)."""
        import ast
        root = os.path.dirname(APP)
        for name in ("personal-finance.py", "ui_theme.py"):
            with open(os.path.join(root, name)) as f:
                tree = ast.parse(f.read())
            bad = [n.lineno for n in ast.walk(tree)
                   if isinstance(n, ast.Expr) and isinstance(n.value, ast.IfExp)]
            self.assertEqual(bad, [], f"{name}: bare conditional expression at {bad}")


class TestDebitedTransferIsSpend(unittest.TestCase):
    """a debited transfer is spend under the default category Transfer."""

    def _rows(self):
        mk = lambda m, k, wd=0, dep=0, c="C": {"member": m, "kind": k, "wd": wd,  # noqa: E731
                                               "dep": dep, "fy": "x", "category": c}
        return [mk("a", "expense", 100), mk("a", "transfer", wd=40, c="Transfer"),
                mk("b", "transfer", dep=30, c="Transfer"), mk("b", "expense", dep=5)]

    def test_spend_arithmetic_and_breakdowns_agree(self):
        from finance import spend
        rows = self._rows()
        self.assertEqual(spend.total(rows), 100 + 40 - 5)       # credit side not netted
        self.assertEqual(sum(v["net"] for v in spend.by_member(rows).values()),
                         spend.total(rows))
        self.assertEqual(sum(spend.by_fy_category(rows).values()), spend.total(rows))
        self.assertEqual(spend.by_member(rows)["a"],
                         {"debits": 140.0, "credits": 0.0, "net": 140.0, "rows": 2})
        self.assertEqual(spend.incoming_total(rows), 30)

    def test_schema_v8_repoints_debit_transfers_idempotently(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "n.db")
            b = FinanceBackend(path=path)
            b.seed_members([{"name": "a", "data_dir": "d"}])
            b.close()
            con = sqlite3.connect(path)
            con.execute("PRAGMA user_version = 7")
            con.executescript("""
            INSERT OR IGNORE INTO categories VALUES ('', '', 'income');
            INSERT INTO entries (user_id,category_id,kind,fy,date,particulars,deposit,
              withdrawal,nkey) VALUES
              ('a','','transfer','2024-25','2024-05-01','P1',0,10,'k1'),
              ('a','UNKNOWN','transfer','2024-25','2024-05-02','P2',0,20,'k2'),
              ('a','Groceries','transfer','2024-25','2024-05-03','P3',0,30,'k3'),
              ('a','UNKNOWN','expense','2024-25','2024-05-04','P4',0,40,'k4');""")
            con.commit()
            con.close()
            for _ in range(2):  # second open proves idempotence
                b = FinanceBackend(path=path)
                got = {r["particulars"]: r["category"] for r in b.entries(user_ids=["a"])}
                b.close()
                self.assertEqual(got, {"P1": "Transfer", "P2": "Transfer",
                                       "P3": "Groceries", "P4": "UNKNOWN"})

    def test_default_category_on_every_write_path(self):
        with tempfile.TemporaryDirectory() as d:
            b = FinanceBackend(path=os.path.join(d, "n.db"))
            b.seed_members([{"name": "a", "data_dir": "d"}])
            up = lambda part, dep, wd, kind, cat: b.upsert_bank_entry(  # noqa: E731
                "a", account="A", date="2024-05-01", particulars=part, deposit=dep,
                withdrawal=wd, balance=0, kind=kind, category=cat)
            up("T1", 0, 10, "transfer", "UNKNOWN")      # import with no real category
            up("T2", 0, 10, "transfer", "")
            up("S", 100, 0, "income", "")
            rows = {r["particulars"]: r for r in b.entries(user_ids=["a"])}
            self.assertEqual((rows["T1"]["category"], rows["T2"]["category"]),
                             ("Transfer", "Transfer"))
            b.update_entry(rows["S"]["id"], "a", kind="expense")   # income -> expense
            b.update_entry(rows["T1"]["id"], "a", category="Dining_out")  # explicit wins
            got = {r["particulars"]: r["category"] for r in b.entries(user_ids=["a"])}
            self.assertEqual(got["S"], "UNKNOWN")
            self.assertEqual(got["T1"], "Dining_out")
            b.update_entry(rows["T2"]["id"], "a", kind="expense")
            b.update_entry(rows["T2"]["id"], "a", kind="transfer")
            self.assertEqual({r["particulars"]: r["category"]
                              for r in b.entries(user_ids=["a"])}["T2"], "Transfer")
            b.close()

    def test_import_preview_defaults_transfer_and_settles_credited_transfer(self):
        raw = [dict(account="A", date="2024-05-01", particulars="PARK", deposit=0.0,
                    withdrawal=50.0, balance=0, kind="transfer", category="", file="f"),
               dict(account="A", date="2024-05-02", particulars="BACK", deposit=50.0,
                    withdrawal=0.0, balance=0, kind="transfer", category="", file="f")]
        got = {r["particulars"]: (r["kind"], r["category"]) for r in importing.resolve(raw)}
        self.assertEqual(got["PARK"], ("transfer", "Transfer"))
        self.assertEqual(got["BACK"], ("income", ""))      # credit-only transfer = income

    def test_home_snapshot_counts_the_transfer(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("pf2", APP)
        pf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pf)
        rows = [{"id": 1, "kind": "expense", "wd": 100, "dep": 0, "fy": "2025-26",
                 "category": "G", "member": "a"},
                {"id": 2, "kind": "transfer", "wd": 400, "dep": 0, "fy": "2025-26",
                 "category": "Transfer", "member": "a"}]
        self.assertEqual(pf.home_snapshot(rows, [])[1], 500)

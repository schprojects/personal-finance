"""Unit tests for impex.py (importer/exporter CLI) and llm.py."""
import contextlib
import csv
import io
import os
import tempfile
import unittest
from unittest import mock

import impex
from backend import FinanceBackend
from finance.llm import parse_response
from finance.rules import RuleSet

MEMBERS = [{"name": "a", "data_dir": "data/a"}, {"name": "b", "data_dir": "data/b"}]


def make_statement(path, rows, closing_multiplier=1.0):
    """rows = [(date, mode, particulars, dep, wd, bal)]; balance column the
    fixture must reconcile: last row bal == 1000 + sum(dep) - sum(wd)."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([])
        w.writerow(["Statement of Transactions in SavingsNumber: XXXXXXXX3404"])
        w.writerow([])
        w.writerow(["DATE", "MODE", "PARTICULARS", "DEPOSITS", "WITHDRAWALS", "BALANCE"])
        for r in rows:
            w.writerow(list(r))


def make_carrier_file(path, rows):
    """Exporter-style CSV where rows already carry KIND/CATEGORY."""
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["MEMBER", "ACCOUNT", "DATE", "MODE", "PARTICULARS",
                    "DEPOSIT", "WITHDRAWAL", "BALANCE", "KIND", "CATEGORY", "FY"])
        for r in rows:
            w.writerow(list(r))


RULES_CSV = (
    "expense,Groceries,STARBAZAAR\n"
    "expense,Transport & fuel,MOONSOON @amt<=10000\n"
    "income,,ACH/INCOME\n"
)


class TestImportBankStatements(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.backend = FinanceBackend(path=os.path.join(self.d.name, "db.sqlite"))
        self.backend.seed_members(MEMBERS)
        data = os.path.join(self.d.name, "data")
        self.a_dir = os.path.join(data, "a", "bank-statements", "2024-25")
        os.makedirs(self.a_dir, exist_ok=True)
        self.rules = RuleSet.parse(RULES_CSV)
        self.ctx = contextlib.ExitStack()
        self.ctx.enter_context(contextlib.chdir(self.d.name))
        self.ctx.enter_context(mock.patch("impex.load_members", return_value=MEMBERS))

    def tearDown(self):
        self.ctx.close()
        self.backend.close()
        self.d.cleanup()

    def test_imports_classifies_and_is_idempotent(self):
        make_statement(os.path.join(self.a_dir, "s.csv"), [
            ["01-04-2024", "", "B/F", 0, 0, 1000.00],
            ["02-04-2024", "", "UPI/STARBAZAAR MART@okpay", 0, 100, 900.00],
            ["03-04-2024", "", "ACH/INCOME", 250, 0, 1150.00],
            ["04-04-2024", "", "RANDOM-UNKNOWN-PAYEE", 0, 777, 373.00],
        ])
        c1 = impex.import_bank_statements(self.backend, "a", self.rules)
        self.assertEqual(c1["created"], 3)
        rows = self.backend.entries(user_ids=["a"])
        cats = {(r["kind"], r["category"]) for r in rows}
        self.assertIn(("expense", "Groceries"), cats)
        self.assertIn(("income", ""), cats)
        # RANDOM-UNKNOWN-PAYEE hits the default fallback: debit -> expense/UNKNOWN
        self.assertIn(("expense", "UNKNOWN"), cats)
        self.assertEqual([r["fy"] for r in rows], ["2024-25"] * 3)
        c2 = impex.import_bank_statements(self.backend, "a", self.rules)
        self.assertEqual(c2["skipped"], 3)

    def test_reconcile_fail_writes_failed_csv_and_dedups(self):
        # opening 1000, one 100 withdrawal, but closing says 1000 -> FAIL
        make_statement(os.path.join(self.a_dir, "bad.csv"), [
            ["01-04-2024", "", "B/F", 0, 0, 1000.00],
            ["02-04-2024", "", "UPI/starbazaar@ok/Payment", 0, 100, 1000.00],
        ])
        c = impex.import_bank_statements(self.backend, "a", self.rules)
        self.assertEqual(c["created"], 0)
        f = os.path.join("output", "2024-25", "failed-1.csv")
        self.assertTrue(os.path.exists(f))
        with open(f, newline="") as fh:
            got = list(csv.DictReader(fh))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["PARTICULARS"], "UPI/starbazaar@ok/Payment")
        impex.import_bank_statements(self.backend, "a", self.rules)
        self.assertFalse(os.path.exists(
            os.path.join("output", "2024-25", "failed-2.csv")))

    def test_carrier_csv_honors_specified_kind_category(self):
        # exporter-round-trip file: specified values win (no rules/LLM/credit
        # rewrite), income keeps only the default category, absent values infer.
        make_carrier_file(os.path.join(self.a_dir, "carrier.csv"), [
            ["a", "A", "01-04-2024", "", "FOO-PAYEE", 0, 100, 900,
             "expense", "Groceries", "2024-25"],
            ["a", "A", "02-04-2024", "", "BAR-REFUND", 250, 0, 1150,
             "expense", "Refund", "2024-25"],
            ["a", "A", "03-04-2024", "", "UPI/STARBAZAAR MART@okpay", 0, 100, 1050,
             "", "", "2024-25"],
            ["a", "A", "04-04-2024", "", "SALARY-ACH", 500, 0, 1550,
             "income", "Salary", "2024-25"],
        ])
        c = impex.import_bank_statements(self.backend, "a", self.rules)
        self.assertEqual(c["created"], 4)
        rows = self.backend.entries(user_ids=["a"])
        by_part = {r["particulars"]: r for r in rows}
        # specified values used verbatim, even for a deposit (no credit rewrite)
        self.assertEqual((by_part["FOO-PAYEE"]["kind"],
                          by_part["FOO-PAYEE"]["category"]), ("expense", "Groceries"))
        self.assertEqual((by_part["BAR-REFUND"]["kind"],
                          by_part["BAR-REFUND"]["category"]), ("expense", "Refund"))
        # not specified -> normal inference (rule match on STARBAZAAR)
        self.assertEqual((by_part["UPI/STARBAZAAR MART@okpay"]["kind"],
                          by_part["UPI/STARBAZAAR MART@okpay"]["category"]),
                         ("expense", "Groceries"))
        # income has ONLY the default category
        self.assertEqual((by_part["SALARY-ACH"]["kind"],
                          by_part["SALARY-ACH"]["category"]), ("income", ""))

    def test_carrier_csv_dd_mm_yyyy_dates(self):
        # hand-authored carrier file with dd/mm/yyyy dates (like mamta_FY17-18)
        make_carrier_file(os.path.join(self.a_dir, "m.csv"), [
            ["a", "?", "19/01/2017", "", "PKT/BUY STUFF", 0, 500, 28798.44,
             "expense", "Misc & small", "2016-17"],
            ["a", "?", "01/02/2017", "", "ACH/WIPRO", 10, 0, 28808.44,
             "income", "", "2016-17"],
        ])
        c = impex.import_bank_statements(self.backend, "a", self.rules)
        self.assertEqual(c["created"], 2)
        rows = self.backend.entries(user_ids=["a"])
        by_part = {r["particulars"]: r for r in rows}
        self.assertEqual(by_part["PKT/BUY STUFF"]["date"], "2017-01-19")
        self.assertEqual(by_part["PKT/BUY STUFF"]["fy"], "2016-17")
        self.assertEqual(by_part["PKT/BUY STUFF"]["category"], "Misc & small")
        self.assertEqual((by_part["ACH/WIPRO"]["kind"],
                          by_part["ACH/WIPRO"]["category"]), ("income", ""))

    def test_bad_match_guard_and_credit(self):
        # @amt qualifier + deposits-become-credit
        make_statement(os.path.join(self.a_dir, "s.csv"), [
            ["01-04-2024", "", "B/F", 0, 0, 1000.00],
            ["02-04-2024", "", "UPI/moonsoontaxi@ok/x", 0, 500, 500.00],
            ["03-04-2024", "", "UPI/moonsoontaxi@ok/y", 0, 20000, -19500.00],
        ])
        impex.import_bank_statements(self.backend, "a", self.rules)
        rows = self.backend.entries(user_ids=["a"])
        by_part = {r["particulars"]: r for r in rows}
        self.assertEqual(by_part["UPI/moonsoontaxi@ok/x"]["category"], "Transport & fuel")
        self.assertEqual(by_part["UPI/moonsoontaxi@ok/y"]["category"], "UNKNOWN")

    def test_investments_import(self):
        inv = os.path.join(self.d.name, "data", "a", "investments")
        os.makedirs(inv, exist_ok=True)
        with open(os.path.join(inv, "mfs.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Scheme Name", "Cost Value (INR)", "Unit Balance",
                        "NAV Date", "NAV (INR)", "Market Value (INR)"])
            w.writerow(["Axis Cap", "1000", "10", "11-Sep-2026", "120", "1200"])
        with open(os.path.join(inv, "others.csv"), "w", newline="") as f:
            f.write("Fixed deposits,500000.0\nPF,2000000.0\nTo-receive-anil,100000.0\n")
        c = impex.import_investments(self.backend, "a")
        self.assertEqual(c, {"created": 4, "updated": 0, "skipped": 0})
        holdings = self.backend.investments(user_ids=["a"])
        types = {r["type"] for r in holdings}
        self.assertEqual(types, {"MF", "FD", "EPF", "loan-receivable"})


class TestExport(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.backend = FinanceBackend(path=os.path.join(self.d.name, "db.sqlite"))
        self.backend.seed_members(MEMBERS)
        for owner in ("a", "b"):
            self.backend.upsert_bank_entry(
                owner, account="A", date="2024-04-01", mode="", particulars=f"{owner}-pay",
                deposit=0, withdrawal=100, balance=900, kind="expense",
                category="Groceries", file="s.csv")

    def tearDown(self):
        self.backend.close()
        self.d.cleanup()

    def test_export_scope_and_format(self):
        out = io.StringIO()
        ns = lambda **kw: type("A", (), kw)()  # noqa: E731
        n = impex.export_entries(self.backend, "a", ns(
            user=None, type="bank", fy=None, kind=None, category=None,
            account=None, q=None), out)
        self.assertEqual(n, 2)  # family scope includes b
        out = io.StringIO()
        n = impex.export_entries(self.backend, "a", ns(
            user="a", type="bank", fy=None, kind=None, category=None,
            account=None, q=None), out)
        self.assertEqual(n, 1)
        first = out.getvalue().splitlines()[1].split(",")
        self.assertEqual(first, ["a", "A", "01-04-2024", "", "a-pay",
                                 "0.00", "100.00", "900.00", "expense",
                                 "Groceries", "2024-25"])


class TestBackupRestore(unittest.TestCase):
    def test_backup_then_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            b1 = FinanceBackend(path=os.path.join(d, "db.sqlite"))
            b1.seed_members([{"name": "a", "data_dir": "data/a"}])
            b1.upsert_bank_entry("a", account="A", date="2024-04-01", mode="",
                                 particulars="P", deposit=0, withdrawal=1,
                                 balance=9, kind="expense", category="X",
                                 file="f")
            dest = os.path.join(d, "backups")
            target = impex.backup(b1, dest)
            b1.close()
            impex.restore(target)
            b2 = FinanceBackend(path=os.path.join(d, "db.sqlite"))
            self.assertEqual(len(b2.entries(user_ids=["a"])), 1)
            with self.assertRaises(SystemExit):
                impex.restore(os.path.join(d, "not-a-db.txt"))


class TestLlmParseAndAppend(unittest.TestCase):
    def test_parse_response_filters_unknown_categories(self):
        text = "0\tGroceries\n1\tNOT-KNOWN\njunk\n2\tTransport & fuel\n"
        got = parse_response(text, ["Groceries", "Transport & fuel"])
        self.assertEqual(got, {0: "Groceries", 2: "Transport & fuel"})

    def test_append_auto_rules_dedups_by_keyword(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "auto-rules.csv")
            with open(path, "w") as f:
                f.write("expense,Groceries,STARBAZAAR\n")
            added = impex.append_auto_rules(path, {0: "Groceries", 1: "Fuel"},
                                            ["STARBAZAAR", "PETROL|DIESEL"])
            self.assertEqual(added, 1)
            with open(path) as f:
                text = f.read()
            self.assertIn("PETROL|DIESEL", text)
            self.assertEqual(text.count("STARBAZAAR"), 1)


class TestCliGate(unittest.TestCase):
    def test_cli_import_with_wrong_password_exits_without_changes(self):
        with tempfile.TemporaryDirectory() as d, contextlib.chdir(d), \
                mock.patch.dict("os.environ", {"FINANCE_DB": os.path.join(d, "db.sqlite")}), \
                mock.patch("impex.load_members", return_value=MEMBERS):
            b = FinanceBackend()
            b.seed_members(MEMBERS)
            b.set_password("a", "pw")
            b.close()
            with mock.patch("impex.sys.stdin", new=io.StringIO("WRONG\n")):
                with self.assertRaises(SystemExit):
                    impex.main(["import", "--user", "a", "--password-stdin"])
            b = FinanceBackend()
            self.assertEqual(b.entries(user_ids=["a"]), [])

    def test_cli_set_password_bootstrap_then_import(self):
        with tempfile.TemporaryDirectory() as d, contextlib.chdir(d), \
                mock.patch.dict("os.environ", {"FINANCE_DB": os.path.join(d, "db.sqlite")}), \
                mock.patch("impex.load_members", return_value=MEMBERS):
            with mock.patch("impex.sys.stdin", new=io.StringIO("pw\npw\n")):
                impex.main(["auth", "--user", "a", "--password-stdin"])
            os.makedirs(os.path.join(d, "data", "a", "bank-statements", "2024-25"))
            make_statement(
                os.path.join(d, "data", "a", "bank-statements", "2024-25", "s.csv"),
                [["01-04-2024", "", "B/F", 0, 0, 1000.00],
                 ["02-04-2024", "", "UPI/SM MART@okpay", 0, 100, 900.00]])
            with mock.patch("impex.sys.stdin", new=io.StringIO("pw\n")):
                impex.main(["import", "--user", "a", "--password-stdin"])
            b = FinanceBackend()
            rows = b.entries(user_ids=["a"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["category"], "Groceries")


if __name__ == "__main__":
    unittest.main()
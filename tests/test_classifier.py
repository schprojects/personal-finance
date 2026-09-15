"""Unit tests for classify.py (rule tagging, parser, reconciliation, outputs)."""
import contextlib
import csv
import io
import os
import tempfile
import unittest

import classify


FIXTURE_RULES = """\
# fixture: mirrors the sentiment a typical rules file expresses
expense,Bank & DP charges,@mode:BANK CHARGES
expense,Transport & fuel,MOONSOON @amt<=10000
expense,Loan & EMI,MOONSOON @amt>10000
income,,INT.PD
investment,,INVPLAT/MF-
expense,Family support,RELATIV
expense,Groceries,STARBAZAAR
"""


class TestRuleSet(unittest.TestCase):
    def setUp(self):
        self.rs = classify.RuleSet.parse(FIXTURE_RULES)

    def test_grocery_kw(self):
        self.assertEqual(self.rs.match("VPS/STARBAZAAR/20171231101939/0", ""),
                         ("expense", "Groceries"))

    def test_bank_charges_by_mode(self):
        self.assertEqual(self.rs.match("XX", "BANK CHARGES"),
                         ("expense", "Bank & DP charges"))

    def test_merchant_threshold(self):
        self.assertEqual(self.rs.match("UPI/moonsoontaxi@ok/...", "", 5000),
                         ("expense", "Transport & fuel"))
        self.assertEqual(self.rs.match("UPI/moonsoontaxi@ok/...", "", 15000),
                         ("expense", "Loan & EMI"))

    def test_investment_excluded(self):
        self.assertEqual(self.rs.match("INVPLAT/MF-123", "")[0], "investment")

    def test_unknown_goes_to_review(self):
        self.assertEqual(self.rs.match("RANDOM-UNKNOWN-PAYEE", ""), None)

    def test_family_support_before_generic(self):
        self.assertEqual(self.rs.match("UPI/RELATIV/...", ""),
                         ("expense", "Family support"))


class TestBucketLongTail(unittest.TestCase):
    def test_small_totals_downgraded_to_misc(self):
        rows = [
            {"part": "UPI/a@ok/1", "wd": 300.0, "kind": "review", "category": "REVIEW"},
            {"part": "UPI/a@ok/2", "wd": 300.0, "kind": "review", "category": "REVIEW"},
            {"part": "UPI/bigspender@ok/1", "wd": 2000.0, "kind": "review", "category": "REVIEW"},
        ]
        classify.bucket_long_tail(rows)
        by_part = {r["part"]: r for r in rows}
        self.assertEqual(by_part["UPI/a@ok/1"]["kind"], "expense")
        self.assertEqual(by_part["UPI/a@ok/1"]["category"], "Misc & small")
        self.assertEqual(by_part["UPI/bigspender@ok/1"]["kind"], "review")


def make_statement(path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([])
        w.writerow(["Statement of Transactions in SavingsNumber: XXXXXXXX3404"])
        w.writerow([])
        w.writerow(["DATE", "MODE", "PARTICULARS", "DEPOSITS", "WITHDRAWALS", "BALANCE"])
        w.writerow(["01-04-2024", "", "B/F", 0, 0, 1000.00])
        w.writerow(["02-04-2024", "", "UPI/starbazaar@ok/Payment", 0, 100, 900.00])
        w.writerow(["03-04-2024", "", "ACH/INCOME", 250, 0, 1150.00])
        w.writerow(["04-04-2024", "BANK CHARGES", "something", 0, 50, 1100.00])
        w.writerow(["05-04-2024", "", "RANDOM-UNKNOWN-PAYEE", 0, 777, 323.00])


class TestReconcile(unittest.TestCase):
    def test_parses_and_reconciles_tiny_statement(self):
        got = None
        with tempfile.TemporaryDirectory() as d:
            make_statement(os.path.join(d, "t.csv"))
            blocks = classify.parse_blocks(os.path.join(d, "t.csv"))
            self.assertEqual(len(blocks), 1)
            acct, txs = blocks[0]
            opening, ok, msg, txs = classify.reconcile(acct, txs)
            self.assertTrue(ok, msg)
            self.assertEqual(opening, 1000.00)
            self.assertEqual(txs[-1]["bal"], 323.00)


class TestRunOutputs(unittest.TestCase):
    def test_numbered_runs_and_consolidated_inventory(self):
        rows = [
            {"member": "a", "file": "t.csv", "account": "A",
             "date": "01-04-2024", "mode": "", "part": "P1",
             "dep": 0.0, "wd": 100.0, "bal": 900.0,
             "kind": "expense", "category": "Groceries", "fy": "2024-25"},
            {"member": "a", "file": "t.csv", "account": "A",
             "date": "02-04-2024", "mode": "", "part": "P2",
             "dep": 0.0, "wd": 40.0, "bal": 860.0,
             "kind": "review", "category": "REVIEW", "fy": "2024-25"},
            {"member": "a", "file": "t.csv", "account": "A",
             "date": "01-04-2025", "mode": "", "part": "P3",
             "dep": 0.0, "wd": 5.0, "bal": 855.0,
             "kind": "expense", "category": "Utilities", "fy": "2025-26"},
        ]
        with tempfile.TemporaryDirectory() as d:
            with contextlib.chdir(d):
                self.assertEqual(classify.next_run_number(), 1)
                by_fy = classify.write_run(1, rows)
                self.assertEqual(set(by_fy), {"2024-25", "2025-26"})
                self.assertTrue(os.path.exists("output/classified-1.csv"))
                self.assertTrue(os.path.exists("output/review-1.csv"))
                self.assertTrue(os.path.exists("repo/classified-main-1.csv"))
                self.assertTrue(os.path.exists("output/2024-25/classified-1.csv"))

                with open("output/review-1.csv", newline="") as f:
                    review = list(csv.DictReader(f))
                self.assertEqual([r["PARTICULARS"] for r in review], ["P2"])

                def rows_of(p):
                    with open(p, newline="") as f:
                        return list(csv.DictReader(f))

                main = rows_of("repo/classified-main-1.csv")
                slices = rows_of("output/2024-25/classified-1.csv") + \
                    rows_of("output/2025-26/classified-1.csv")
                self.assertEqual(len(main), len(rows))
                self.assertEqual(len(main), len(slices))

                self.assertEqual(classify.next_run_number(), 2)
                classify.write_run(2, rows)
                self.assertTrue(os.path.exists("output/classified-2.csv"))
                self.assertTrue(os.path.exists("repo/classified-main-2.csv"))
                self.assertEqual(classify.next_run_number(), 3)


if __name__ == "__main__":
    unittest.main()
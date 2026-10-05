"""Unit tests for classify.py (statement parser, reconciliation)."""
import csv
import os
import tempfile
import unittest

import finance.classify as classify


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


if __name__ == "__main__":
    unittest.main()

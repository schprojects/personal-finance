"""Unit tests for common.py (helpers shared by classifier and app)."""
import os
import unittest

from finance.common import fy_of, fy_of_str, inr, money, payee_key, load_members


class TestFy(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(fy_of_str("01-04-2024"), "2024-25")
        self.assertEqual(fy_of_str("31-03-2025"), "2024-25")
        self.assertEqual(fy_of_str("30-04-2025"), "2025-26")

    def test_fy_of_date(self):
        import datetime as dt
        self.assertEqual(fy_of(dt.date(2026, 9, 14)), "2026-27")


class TestMoneyInr(unittest.TestCase):
    def test_money_parses_commas_and_spaces(self):
        self.assertEqual(money("24,00,000.000"), 2400000.0)
        self.assertEqual(money(""), 0.0)

    def test_inr_format(self):
        self.assertEqual(inr(1478810), "₹14,78,810.00")
        self.assertEqual(inr(-500), "-₹500.00")


class TestPayeeKey(unittest.TestCase):
    def test_upi(self):
        self.assertEqual(payee_key("UPI/salmar@ok/..."),
                         "UPI:salmar")

    def test_bil_provider_token(self):
        self.assertEqual(payee_key("BIL/123456789012345/bescom_jun24/BESCOM_1"),
                         "BIL:bescom_jun24")
        self.assertEqual(payee_key("BIL/ONL/123456789012345/EXCO/7"),
                         "BIL:EXCO")

    def test_bank_transfer(self):
        self.assertTrue(payee_key("NEFT-HDFCN520250403...").startswith("BANK:"))


class TestMembers(unittest.TestCase):
    def test_loads_all_real_members(self):
        members = load_members()
        self.assertGreaterEqual(len(members), 1)
        for m in members:
            self.assertTrue(os.path.isdir(m["data_dir"]))


if __name__ == "__main__":
    unittest.main()
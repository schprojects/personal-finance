"""Unit tests for common.py (helpers shared by classifier and app)."""
import unittest

from common import fy_of, fy_of_str, inr, money, payee_key, load_members


class TestFy(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(fy_of_str("01-04-2017"), "2017-18")
        self.assertEqual(fy_of_str("31-03-2018"), "2017-18")
        self.assertEqual(fy_of_str("30-04-2018"), "2018-19")

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
        self.assertEqual(payee_key("UPI/srikeshavamart@ok/..."),
                         "UPI:srikeshavamart")

    def test_bil_provider_token(self):
        self.assertEqual(payee_key("BIL/[REDACTED]/bescom_mar18/BESCOM_LICI6195"),
                         "BIL:bescom_mar18")
        self.assertEqual(payee_key("BIL/ONL/[REDACTED]/TECHPROCES/[REDACTED]"),
                         "BIL:TECHPROCES")

    def test_bank_transfer(self):
        self.assertTrue(payee_key("NEFT-HDFCN520250403...").startswith("BANK:"))


class TestMembers(unittest.TestCase):
    def test_loads_[MEMBER]_and_[MEMBER](self):
        members = load_members()
        names = {m["name"] for m in members}
        self.assertEqual(names, {"[MEMBER]", "[MEMBER]"})
        self.assertTrue(all(m["data_dir"].endswith("data/[MEMBER]")
                            or m["data_dir"].endswith("data/[MEMBER]")
                            for m in members))


if __name__ == "__main__":
    unittest.main()
"""Unit tests for holdings.py (investment sheet parsing and return calc)."""
import os
import tempfile
import unittest

from holdings import (absolute_return_pct, household_value, is_mf_sheet,
                      parse_investments, parse_label_rows, parse_mf_sheet)

MF_HEADER = ["Folio No.", "ISIN", "Scheme Name", "Cost Value (INR)",
             "Unit Balance", "NAV Date", "NAV (INR)", "Market Value (INR)",
             "Registrar"]

MF_ROWS = [
    ["9101/0", "INF1", "Axis Large Cap Fund", "24,00,000.000", "57,149.034",
     "11-Sep-2026", "68.69", "39,25,567.15", "KF"],
    ["Total", "", "", "1,70,54,641.98", "", "", "", "2,84,18,555.13", ""],
]


class TestSheetDetection(unittest.TestCase):
    def test_mf_sheet_by_header(self):
        self.assertTrue(is_mf_sheet(MF_HEADER))

    def test_label_sheet_not_detected(self):
        self.assertFalse(is_mf_sheet(["", ""]))


class TestParseMfSheet(unittest.TestCase):
    def test_schemes_and_total(self):
        schemes, total = parse_mf_sheet(MF_HEADER, MF_ROWS)
        self.assertEqual(len(schemes), 1)
        s = schemes[0]
        self.assertEqual(s["scheme"], "Axis Large Cap Fund")
        self.assertEqual(s["units"], 57149.034)
        self.assertEqual(s["cost"], 2400000.0)
        self.assertEqual(s["market"], 3925567.15)
        # NAV value column, not NAV Date
        self.assertEqual(s["nav"], 68.69)
        self.assertEqual(s["nav_date"], "11-Sep-2026")
        self.assertAlmostEqual(s["ret_pct"], 63.57, places=2)
        self.assertEqual(total, 28418555.13)


class TestParseLabelRows(unittest.TestCase):
    def test_rows_and_total(self):
        data = [["Gold funds", "1500000"], ["Cash", "5400000"],
                ["Total", "6900000"], ["", "skip"]]
        assets, total = parse_label_rows(data)
        self.assertEqual(len(assets), 2)
        self.assertEqual(total, 6900000.0)


class TestAbsoluteReturn(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(absolute_return_pct(200, 250), 25.0)

    def test_not_computable(self):
        self.assertIsNone(absolute_return_pct(0, 250))
        self.assertIsNone(absolute_return_pct(None, 250))


class TestParseInvestments(unittest.TestCase):
    def test_both_sheet_shapes(self):
        members = [{"name": "x", "data_dir": ""}]
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "investments")
            os.makedirs(root)
            with open(os.path.join(root, "a-mfs.csv"), "w") as f:
                import csv
                w = csv.writer(f)
                w.writerow(MF_HEADER)
                w.writerows(MF_ROWS)
            with open(os.path.join(root, "b-rest.csv"), "w") as f:
                w = csv.writer(f)
                w.writerow(["GOLD", "1500000"])
                w.writerow(["PF", "10100000"])
            members[0]["data_dir"] = d
            inv = parse_investments(members)
            entry = inv["x"]
            self.assertEqual(len(entry["schemes"]), 1)
            self.assertEqual(len(entry["assets"]), 2)
            self.assertEqual(entry["total"], 28418555.13)
        self.assertGreater(household_value(inv), 0)


if __name__ == "__main__":
    unittest.main()
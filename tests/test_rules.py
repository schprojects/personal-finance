"""Unit tests for the external-rules layer: parsing, matching, suggestions."""
import os
import tempfile
import unittest

from rules import Rule, RuleSet, RuleSuggester, parse_line


class TestParseLine(unittest.TestCase):
    def test_simple_keyword(self):
        r = parse_line("expense,Groceries,[MERCHANT]")
        self.assertEqual((r.kind, r.category, r.keywords),
                         ("expense", "Groceries", ("[MERCHANT]",)))

    def test_any_of_pipe(self):
        r = parse_line("expense,Transport & fuel,[MERCHANT]|[MERCHANT] @amt<=10000")
        self.assertEqual(r.keywords, ("[MERCHANT]", "[MERCHANT]"))
        self.assertEqual(r.amt_max, 10000)
        self.assertIsNone(r.amt_min)

    def test_sentinel_space_preserved(self):
        r = parse_line("refund,,REF ")
        self.assertEqual(r.keywords, ("REF ",))  # must not match REFILL

    def test_mode_qualifier_at_start(self):
        r = parse_line("expense,Bank & DP charges,@mode:BANK CHARGES")
        self.assertEqual(r.mode, "BANK CHARGES")
        self.assertEqual(r.keywords, ())

    def test_kind_and_category_only(self):
        r = parse_line("income,,INT.PD")
        self.assertEqual((r.kind, r.category), ("income", ""))


class TestMatch(unittest.TestCase):
    def test_first_match_wins(self):
        rules = RuleSet.parse(
            "expense,A,AAA\n"      # first
            "expense,B,AAAA\n"     # also matches, but must lose
            "income,,ZZZ\n")
        self.assertEqual(rules.match("AAAA", ""), ("expense", "A"))

    def test_mode_filter(self):
        rs = RuleSet.parse("expense,Charges,@mode:BANK CHARGES\n")
        self.assertEqual(rs.match("anything", "BANK CHARGES"), ("expense", "Charges"))
        self.assertIsNone(rs.match("anything", "DEBIT CARD"))

    def test_amt_bounds(self):
        rs = RuleSet.parse("expense,Small,@amt<=1000\nexpense,Large,@amt>1000\n")
        self.assertEqual(rs.match("X", "", 999), ("expense", "Small"))
        self.assertEqual(rs.match("X", "", 1000), ("expense", "Small"))
        self.assertEqual(rs.match("X", "", 1001), ("expense", "Large"))

    def test_sentinel_space_not_within_word(self):
        # "REF " (trailing space) must not match REFILL
        self.assertFalse(Rule("refund", "", ("REF ",)).match("CYLINDER_REFILL", ""))
        self.assertTrue(Rule("refund", "", ("REF ",)).match("REF [MERCHANT]", ""))

    def test_no_keyword_no_qualifier_matches_everything(self):
        self.assertTrue(Rule("cash", "", ()).match("ANYTHING", ""))

    def test_empty_category_classifies_as_non_expense(self):
        self.assertEqual(Rule("investment", "", ("EBA/MF",)).match("EBA/MF-1", ""), True)


class TestSuggestions(unittest.TestCase):
    def test_by_payee_and_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "review-9.csv")
            with open(path, "w", newline="") as f:
                f.write("MEMBER,FILE,ACCOUNT,DATE,MODE,PARTICULARS,WITHDRAWAL,BALANCE\n"
                        "s,s.csv,A,01-04-2017,,UPI/payee1@ok/one,100,900\n"
                        "s,s.csv,A,02-04-2017,,UPI/payee1@ok/two,150,750\n"
                        "s,s.csv,A,03-04-2017,,UPI/payee2@ok/one,50,700\n")
            su = RuleSuggester(path)
            by = su.by_payee()
            self.assertEqual(by[0][0], "UPI:payee1")
            self.assertEqual(by[0][1][0:2], [2, 250.0])
            lines = su.suggested("CATEGORY")
            self.assertTrue(any("expense,CATEGORY" in l for l in lines))
            self.assertEqual(lines[0], "# {0}: 3 review rows".format(path))


if __name__ == "__main__":
    unittest.main()
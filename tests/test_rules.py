"""Unit tests for the external-rules layer: parsing, matching, suggestions."""
import os
import tempfile
import unittest

from rules import Rule, RuleSet, RuleSuggester, parse_line


CSV_RULES = (
    "# comment first row\n"
    "expense,Bank & DP charges,@mode:BANK CHARGES\n"
    "expense,Transport & fuel,MOONSOON|MOONSOONM @amt<=10000\n"
    'refund,,"REF "\n'
    "income,,INT.PD\n"
)


class TestParseLine(unittest.TestCase):
    def test_simple_keyword(self):
        r = parse_line("expense,Groceries,STARBAZAAR")
        self.assertEqual((r.kind, r.category, r.keywords),
                         ("expense", "Groceries", ("STARBAZAAR",)))

    def test_any_of_pipe(self):
        r = parse_line("expense,Transport & fuel,MOONSOON|MOONSOONM @amt<=10000")
        self.assertEqual(r.keywords, ("MOONSOON", "MOONSOONM"))
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
        self.assertTrue(Rule("refund", "", ("REF ",)).match("REF STARBAZAAR", ""))

    def test_dir_qualifier_dep_and_wd(self):
        rs = RuleSet.parse("income,,ACH/ @dir:dep\n"
                           "expense,,ACH/ @dir:wd\n")
        self.assertEqual(rs.match("ACH/ICICI SA", "", 0, dir_="dep"), ("income", ""))
        self.assertEqual(rs.match("ACH/P2PCO", "", 500, dir_="wd"), ("expense", ""))

    def test_dir_defaults_to_both(self):
        rs = RuleSet.parse("transfer,,PAY/\n")
        self.assertEqual(rs.match("PAY/PAYT/Self", "", 1.0, dir_="dep"), ("transfer", ""))
        self.assertEqual(rs.match("PAY/PAYT/Self", "", 1.0, dir_="wd"), ("transfer", ""))

    def test_dir_same_keyword_two_rules_directions(self):
        rs = RuleSet.parse("investment,,BROKCO @dir:wd\n"
                           "income,,BROKCO @dir:dep\n")
        self.assertEqual(rs.match("UPI/iclbrokco@yes/BUY", "", 5, dir_="wd"),
                         ("investment", ""))
        self.assertEqual(rs.match("redemption BROKCO", "", 0, dir_="dep"),
                         ("income", ""))

    def test_dir_parsed_from_line(self):
        self.assertEqual(parse_line("income,,X @dir:dep").dir_, "dep")
        self.assertEqual(parse_line("income,,X @dir:wd").dir_, "wd")
        self.assertIsNone(parse_line("income,,X").dir_)
        with self.assertRaises(ValueError):
            parse_line("income,,X @dir:sideways")

    def test_no_keyword_no_qualifier_matches_everything(self):
        self.assertTrue(Rule("cash", "", ()).match("ANYTHING", ""))

    def test_empty_category_classifies_as_non_expense(self):
        self.assertEqual(Rule("investment", "", ("INVPLAT/MF",)).match("INVPLAT/MF-1", ""), True)


class TestCsvRules(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.csv = os.path.join(self.d.name, "auto-rules.csv")

    def tearDown(self):
        self.d.cleanup()

    def write(self, text):
        with open(self.csv, "w", newline="") as f:
            f.write(text)

    def test_csv_parses_like_txt(self):
        self.write(CSV_RULES)
        txt = RuleSet.parse("".join(
            l + "\n" for l in CSV_RULES.splitlines() if l and not l.startswith("#")))
        csv_rs = RuleSet.from_csv(self.csv)
        self.assertEqual([r.kind for r in csv_rs.rules], [r.kind for r in txt.rules])
        self.assertEqual(csv_rs.match("UPI/moonsoontaxi@ok", "", 500),
                         ("expense", "Transport & fuel"))
        self.assertEqual(csv_rs.match("REF STARBAZAAR", ""), ("refund", ""))
        kw = [r.keywords for r in csv_rs.rules]
        self.assertIn(("REF ",), kw)  # sentinel survives csv round-trip

    def test_from_file_selects_parser(self):
        self.write(CSV_RULES)
        self.assertEqual(len(RuleSet.from_file(self.csv).rules), 4)
        # parse-path fallback (rules.txt is gitignored, so only when present)
        if os.path.exists("rules.txt"):
            self.assertGreater(len(RuleSet.from_file("rules.txt").rules), 100)

    def test_combined_manual_first(self):
        self.write(CSV_RULES)
        manual = os.path.join(self.d.name, "manual-rules.csv")
        with open(manual, "w", newline="") as f:
            f.write("expense,Specific,BANK CHARGES\n")  # wants to win
        rs = RuleSet.combined(manual, self.csv)
        self.assertEqual(rs.match("BANK CHARGES", ""),
                         ("expense", "Specific"))  # manual wins, no mode gate
        rs2 = RuleSet.combined(None, self.csv)
        self.assertEqual(rs2.match("x", "BANK CHARGES", 1_000_000),
                         ("expense", "Bank & DP charges"))


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
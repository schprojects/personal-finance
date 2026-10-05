"""Value-level dashboard tests (bug-fix-001 regression suite).

Uses a deterministic two-member, two-FY sample dataset whose overview figures
are exact and hand-checkable. Asserts exact dashboard strings per section and
scope, that Family view is read-only while Me can edit, and that the Overview
Incoming/Net-spend/Savings-rate metric cards are scoped to the selected FYs
(and no longer read zero because the default Kinds filter drops income rows).
"""
import os
import tempfile
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

from backend import FinanceBackend


def make_db(path):
    b = FinanceBackend(path=path)
    b.seed_members([{"name": "alice", "data_dir": "data/alice"},
                    {"name": "bob", "data_dir": "data/bob"}])
    b.set_password("alice", "pw")
    with b.con:
        m = dict(account="A", balance=0, file="s")
        b.upsert_bank_entry("alice", date="2024-04-05", mode="NEFT",
                            particulars="ACH/SALARY", deposit=100000,
                            withdrawal=0, kind="income", category="", **m)
        b.upsert_bank_entry("alice", date="2024-04-10", mode="UPI",
                            particulars="UPI/SM MART@okpay", deposit=0,
                            withdrawal=2000, kind="expense",
                            category="Groceries", **m)
        b.upsert_bank_entry("alice", date="2024-05-03", mode="POS",
                            particulars="POS/BIGBAZAR", deposit=0,
                            withdrawal=3000, kind="expense",
                            category="Clothing", **m)
        b.upsert_bank_entry("alice", date="2024-05-20", mode="UPI",
                            particulars="UPI/CASHBACK@okpay", deposit=500,
                            withdrawal=0, kind="refund",
                            category="Cashback", **m)
        b.upsert_bank_entry("alice", date="2024-06-01", mode="UPI",
                            particulars="MISC/UNKNOWN RETAILER", deposit=0,
                            withdrawal=1200, kind="review",
                            category="UNKNOWN", **m)
        b.upsert_bank_entry("alice", date="2024-06-15", mode="NEFT",
                            particulars="ACH/FREELANCE", deposit=50000,
                            withdrawal=0, kind="income", category="", **m)
        b.upsert_bank_entry("alice", date="2025-04-05", mode="NEFT",
                            particulars="ACH/SALARY", deposit=120000,
                            withdrawal=0, kind="income", category="", **m)
        b.upsert_bank_entry("alice", date="2025-04-12", mode="UPI",
                            particulars="UPI/SM MART@okpay", deposit=0,
                            withdrawal=2500, kind="expense",
                            category="Groceries", **m)
        b.upsert_bank_entry("alice", date="2025-04-30", mode="IMPS",
                            particulars="INTRA/TO FAMILY", deposit=1000,
                            withdrawal=0, kind="transfer", category="", **m)
        b.upsert_bank_entry("bob", date="2024-04-06", mode="NEFT",
                            particulars="ACH/SALARY", deposit=60000,
                            withdrawal=0, kind="income", category="", **m)
        b.upsert_bank_entry("bob", date="2024-04-12", mode="UPI",
                            particulars="UPI/SM MART@okpay", deposit=0,
                            withdrawal=1500, kind="expense",
                            category="Groceries", **m)
        b.upsert_bank_entry("bob", date="2025-04-10", mode="NEFT",
                            particulars="ACH/MATURITY", deposit=40000,
                            withdrawal=0, kind="income", category="", **m)
        b.upsert_investment("alice", type_="MF", label="Axis Cap Fund",
                            folio="1", units=100, purchase_date="2023-04-01",
                            purchase_nav=10.0, latest_date="2024-04-01",
                            latest_nav=11.0, value_at_cost=1000,
                            value_latest=1100)
        b.upsert_investment("alice", type_="cash", label="Cash in bank",
                            value_latest=5000)
        b.upsert_investment("bob", type_="MF", label="HDFC Flexi",
                            folio="2", units=200, purchase_date="2023-04-01",
                            purchase_nav=10.0, latest_date="2024-04-01",
                            latest_nav=11.0, value_at_cost=2000,
                            value_latest=2200)
    b.close()
    return path


USER = {"id": "alice", "username": "alice", "display_name": "alice",
        "family_id": "f1"}

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "personal-finance.py")

# Overview metric-card order (row 1: Net spend / Per year / Top category /
# Unreviewed; row 2 c2: Incoming / Net spend / Savings rate).
NET_SPEND, PER_YEAR, TOP_CAT, UNREVIEWED = 0, 1, 2, 3
INCOMING, NET_SPEND2, SAVINGS = 4, 5, 6

# Family scope, all FYs (default sidebar).
FAMILY = {"incoming": "₹3,70,000.00", "net_spend": "₹8,500.00",
          "per_year": "₹4,250.00", "top_cat": "Groceries",
          "top_cat_d": "₹3,500.00", "unreviewed": "₹1,200.00  (1 rows)",
          "savings": "98%", "savings_d": "₹3,61,500.00 saved"}
# Me (alice), all FYs.
ME = {"incoming": "₹2,70,000.00", "net_spend": "₹7,000.00",
      "per_year": "₹3,500.00", "top_cat": "Clothing",
      "top_cat_d": "₹3,000.00", "unreviewed": "₹1,200.00  (1 rows)",
      "savings": "97%", "savings_d": "₹2,63,000.00 saved"}
# Family scope, only FY 2025-26 selected.
FAMILY_2526 = {"incoming": "₹1,60,000.00", "net_spend": "₹2,500.00",
               "per_year": "₹2,500.00", "top_cat": "Groceries",
               "top_cat_d": "₹2,500.00", "unreviewed": "₹0.00  (0 rows)",
               "savings": "98%", "savings_d": "₹1,57,500.00 saved"}


class TestOverviewValues(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)

    def tearDown(self):
        self.d.cleanup()

    def _at(self, scope="Family"):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER)
        at.session_state["token"] = FinanceBackend(path=self.db).issue_session("alice", "pw")
        at.run()
        if scope != "Family":
            at.radio(key="scope").set_value("Me")
            at.run()
        return at

    def _metrics(self, at):
        return [m for m in at.metric]

    def test_overview_family_all_fy(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            self.assertFalse(at.exception)
            m = self._metrics(at)
            self.assertEqual([m[NET_SPEND].label, m[NET_SPEND].value],
                             ["Net spend", FAMILY["net_spend"]])
            self.assertEqual([m[PER_YEAR].label, m[PER_YEAR].value],
                             ["Per year", FAMILY["per_year"]])
            self.assertEqual([m[TOP_CAT].label, m[TOP_CAT].value,
                              m[TOP_CAT].delta],
                             ["Top category", FAMILY["top_cat"],
                              FAMILY["top_cat_d"]])
            self.assertEqual([m[UNREVIEWED].label, m[UNREVIEWED].value],
                             ["Unreviewed", FAMILY["unreviewed"]])
            self.assertEqual([m[INCOMING].label, m[INCOMING].value],
                             ["Incoming", FAMILY["incoming"]])
            # regression: incoming reads real income, not ₹0 (default kinds
            # filter must not drop income/credit rows)
            self.assertNotEqual(m[INCOMING].value, "₹0.00")
            self.assertEqual([m[SAVINGS].label, m[SAVINGS].value,
                              m[SAVINGS].delta],
                             ["Savings rate", FAMILY["savings"],
                              FAMILY["savings_d"]])

    def test_overview_me_all_fy(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Me")
            self.assertFalse(at.exception)
            m = self._metrics(at)
            self.assertEqual(m[INCOMING].value, ME["incoming"])
            self.assertEqual(m[NET_SPEND2].value, ME["net_spend"])
            self.assertEqual(m[SAVINGS].value, ME["savings"])

    def test_overview_scoped_to_fy(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            at.multiselect(key="fys").set_value(["2025-26"])
            at.run()
            self.assertFalse(at.exception)
            m = self._metrics(at)
            self.assertEqual(m[INCOMING].value, FAMILY_2526["incoming"])
            self.assertEqual(m[NET_SPEND2].value, FAMILY_2526["net_spend"])
            self.assertEqual(m[PER_YEAR].value, FAMILY_2526["per_year"])
            self.assertEqual(m[TOP_CAT].value, FAMILY_2526["top_cat"])
            self.assertEqual(m[SAVINGS].value, FAMILY_2526["savings"])
            self.assertEqual(m[UNREVIEWED].value, FAMILY_2526["unreviewed"])

    def test_overview_me_scoped_to_fy(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Me")
            at.multiselect(key="fys").set_value(["2025-26"])
            at.run()
            self.assertFalse(at.exception)
            m = self._metrics(at)
            self.assertEqual(m[INCOMING].value, "₹1,20,000.00")
            self.assertEqual(m[NET_SPEND2].value, "₹2,500.00")
            # (120000 - 2500) / 120000 = 97.9% -> 98%
            self.assertEqual(m[SAVINGS].value, "98%")


class TestTransactionsValues(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)

    def tearDown(self):
        self.d.cleanup()

    def _at(self, scope="Family"):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER)
        at.session_state["token"] = FinanceBackend(path=self.db).issue_session("alice", "pw")
        at.run()
        at.radio(key="page").set_value("Transactions")
        at.run()
        if scope != "Family":
            at.radio(key="scope").set_value("Me")
            at.run()
        return at

    def test_default_kinds_row_counts(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            self.assertEqual(at.caption[0].value, "6 rows")
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertEqual(at.caption[0].value, "5 rows")

    def test_with_income_rows_and_scope(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            at.multiselect(key="kinds").set_value(
                ["expense", "income", "refund", "review", "transfer"])
            at.run()
            self.assertEqual(at.caption[0].value, "12 rows")
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertEqual(at.caption[0].value, "9 rows")


class TestCategoriesValues(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)

    def tearDown(self):
        self.d.cleanup()

    def _at(self, scope="Family"):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER)
        at.session_state["token"] = FinanceBackend(path=self.db).issue_session("alice", "pw")
        at.run()
        at.radio(key="page").set_value("Categories")
        at.run()
        if scope != "Family":
            at.radio(key="scope").set_value("Me")
            at.run()
        return at

    def test_category_totals_and_share(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            at.selectbox(key="pickcat").set_value("Groceries")
            at.run()
            total = next(m for m in at.metric
                         if "Total" in m.label)
            share = next(m for m in at.metric
                         if "Share" in m.label)
            self.assertEqual(total.value, "₹6,000.00")
            self.assertEqual(share.value, "70.6%")
            at.radio(key="scope").set_value("Me")
            at.run()
            total = next(m for m in at.metric if "Total" in m.label)
            share = next(m for m in at.metric if "Share" in m.label)
            self.assertEqual(total.value, "₹4,500.00")
            self.assertEqual(share.value, "64.3%")

    def test_create_category_form_is_me_only(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            self.assertEqual([t.key for t in at.text_input
                              if t.key == "catname"], [])
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertEqual([t.key for t in at.text_input
                              if t.key == "catname"], ["catname"])

    def test_create_category_writes_db(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Me")
            at.text_input(key="catname").set_value("Travel")
            at.run()
            at.selectbox(key="catkind").set_value("expense")
            at.run()
            btn = [b for b in at.button if b.label == "Create category"]
            self.assertEqual(len(btn), 1)
            btn[0].click().run()
            self.assertFalse(at.exception)
            b = FinanceBackend(path=self.db)
            try:
                labels = {c["label"] for c in b.categories()}
                self.assertIn("Travel", labels)
                travel = [c for c in b.categories() if c["label"] == "Travel"][0]
                self.assertEqual(travel["kind"], "expense")
            finally:
                b.close()


class TestReviewReadOnly(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)

    def tearDown(self):
        self.d.cleanup()

    def _at(self, scope="Family"):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER)
        at.session_state["token"] = FinanceBackend(path=self.db).issue_session("alice", "pw")
        at.run()
        at.radio(key="page").set_value("Review")
        at.run()
        if scope != "Family":
            at.radio(key="scope").set_value("Me")
            at.run()
        return at

    def test_review_metrics_both_scopes(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            self.assertEqual(at.metric[0].value, "1")
            self.assertEqual(at.metric[1].value, "₹0.00")   # Credit
            self.assertEqual(at.metric[2].value, "₹1,200.00")  # Debit
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertEqual(at.metric[0].value, "1")
            self.assertEqual(at.metric[2].value, "₹1,200.00")

def test_family_view_is_read_only(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            self.assertTrue(any("read-only" in c.value
                                for c in at.caption))
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertFalse(any("read-only" in c.value
                                 for c in at.caption))


class TestHoldingsReadOnly(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)

    def tearDown(self):
        self.d.cleanup()

    def _at(self, scope="Family"):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER)
        at.session_state["token"] = FinanceBackend(path=self.db).issue_session("alice", "pw")
        at.run()
        at.radio(key="page").set_value("Holdings")
        at.run()
        if scope != "Family":
            at.radio(key="scope").set_value("Me")
            at.run()
        return at

    def test_net_worth_metrics(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            nw = at.metric[0]
            self.assertEqual(nw.value, "₹8,300.00")
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertEqual(at.metric[0].value, "₹6,100.00")

    def test_family_view_is_read_only(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at("Family")
            self.assertTrue(any("read-only" in c.value
                                for c in at.caption))
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertFalse(any("read-only" in c.value
                                 for c in at.caption))


class TestSampleEntries(unittest.TestCase):
    def test_fixture_entries_are_clean_and_scoped(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "networth.db")
            make_db(db)
            b = FinanceBackend(path=db)
            try:
                rows = b.entries(user_ids=["alice", "bob"])
                self.assertEqual(len(rows), 12)
                by_kind = {}
                for r in rows:
                    by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
                self.assertEqual(by_kind,
                                 {"income": 5, "expense": 4, "refund": 1,
                                  "review": 1, "transfer": 1})
                self.assertEqual({r["fy"] for r in rows},
                                 {"2024-25", "2025-26"})
                rev = [r for r in rows if r["kind"] == "review"][0]
                self.assertEqual(rev["category"], "UNKNOWN")
                # transfer deposit funds neither spending nor income
                funding = [r["kind"] for r in rows
                           if r["kind"] not in ("transfer", "investment",
                                                "refund", "reimbursed")
                           and r["deposit"] > 0]
                self.assertEqual(len(funding), 5)
            finally:
                b.close()


if __name__ == "__main__":
    unittest.main()
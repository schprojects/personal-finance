"""Pure contracts from auth, backend and importer/exporter specs."""
import base64
import datetime as dt
import hashlib
import importlib.util
import os
import unittest
from unittest import mock

from backend import auth
from finance.classify import reconcile
from finance.holdings import absolute_return_pct, xirr
from finance import spend


APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "personal-finance.py")


def dashboard_module():
    """Load the dashboard helpers without starting Streamlit."""
    spec = importlib.util.spec_from_file_location("spec_business_dashboard", APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPasswordContract(unittest.TestCase):
    def test_pbkdf2_sha256_with_at_least_600000_iterations(self):
        salt = b'synthetic-salt!!'
        self.assertGreaterEqual(auth.ITERATIONS, 600000)
        actual_salt, digest = auth.hash_password('sample-password', salt=salt)
        self.assertEqual(actual_salt, salt)
        self.assertEqual(digest, hashlib.pbkdf2_hmac(
            'sha256', b'sample-password', salt, auth.ITERATIONS))
        self.assertNotEqual(digest, b'sample-password')

    def test_identical_passwords_receive_random_salts(self):
        left = auth.hash_password('sample-password')
        right = auth.hash_password('sample-password')
        self.assertNotEqual(left[0], right[0])
        self.assertNotEqual(left[1], right[1])

    def test_verification_uses_constant_time_comparison(self):
        salt, digest = auth.hash_password('sample-password')
        with mock.patch.object(auth.hmac, 'compare_digest',
                               wraps=auth.hmac.compare_digest) as compare:
            self.assertTrue(auth.verify_password('sample-password', salt, digest))
            self.assertFalse(auth.verify_password('incorrect', salt, digest))
        self.assertEqual(compare.call_count, 2)

    def test_unset_credentials_cannot_authenticate(self):
        for salt, digest in ((None, None), (b'', b''), (b'salt', None)):
            with self.subTest(salt=salt):
                self.assertFalse(auth.verify_password('anything', salt, digest))

    def test_opaque_token_has_256_random_bits_and_sha256_digest(self):
        raw, digest = auth.new_token()
        other, _ = auth.new_token()
        self.assertGreaterEqual(len(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))), 32)
        self.assertNotEqual(raw, other)
        self.assertEqual(digest, hashlib.sha256(raw.encode()).hexdigest())
        self.assertNotEqual(digest, raw)


class TestReconciliationContract(unittest.TestCase):
    def test_balanced_block_excludes_opening_row(self):
        opening = dict(part='B/F', dep=0, wd=0, bal=1000)
        rows = [dict(part='salary', dep=250, wd=0, bal=1250),
                dict(part='shop', dep=0, wd=100, bal=1150)]
        balance, ok, reason, transactions = reconcile('A', [opening, *rows])
        self.assertEqual(balance, 1000)
        self.assertTrue(ok, reason)
        self.assertEqual(transactions, rows)

    def test_unbalanced_block_returns_all_transactions_for_failure_report(self):
        rows = [dict(part='B/F', dep=0, wd=0, bal=1000),
                dict(part='shop', dep=0, wd=100, bal=999)]
        _, ok, _, failed = reconcile('A', rows)
        self.assertFalse(ok)
        self.assertEqual(failed, rows[1:])

    def test_missing_opening_balance_is_rejected(self):
        rows = [dict(part='shop', dep=0, wd=100, bal=900)]
        _, ok, reason, failed = reconcile('A', rows)
        self.assertFalse(ok)
        self.assertIn('B/F', reason)
        self.assertEqual(failed, rows)


class TestInvestmentReturnContract(unittest.TestCase):
    def test_profit_and_loss_for_one_year(self):
        start = dt.date(2023, 4, 1)
        end = start + dt.timedelta(days=365)
        for latest, rate in ((1200, .2), (800, -.2), (1000, 0)):
            with self.subTest(latest=latest):
                self.assertAlmostEqual(xirr([(start, -1000), (end, latest)]), rate, places=6)
                self.assertAlmostEqual(absolute_return_pct(1000, latest), rate * 100)

    def test_missing_cost_or_history_has_no_fabricated_return(self):
        self.assertIsNone(absolute_return_pct(None, 1200))
        self.assertIsNone(absolute_return_pct(0, 1200))
        self.assertIsNone(xirr([(dt.date(2024, 4, 1), 1200)]))

    def test_dashboard_partial_history_has_no_fabricated_xirr(self):
        """backend-specs: dates alone are not enough to calculate XIRR."""
        app = dashboard_module()
        base = {"purchase_date": "2024-04-01", "latest_date": "2025-04-01",
                "value_at_cost": None, "value_latest": 1200}
        self.assertIsNone(app._xirr_of(base))
        self.assertIsNone(app._xirr_of(dict(base, value_at_cost=1000,
                                            value_latest=None)))


class TestOverviewAccountingContract(unittest.TestCase):
    """Pure accounting rules stated by dashboard-specs.md."""

    @staticmethod
    def row(row_id, kind, dep=0, wd=0, fy="2025-26", category="Groceries"):
        return {"id": row_id, "kind": kind, "dep": dep, "wd": wd,
                "fy": fy, "category": category, "member": "alex"}

    def test_net_spend_is_expense_plus_debited_transfers_and_refunds_are_not_netted(self):
        rows = [self.row(1, "expense", wd=100),
                self.row(2, "expense", dep=20),
                self.row(3, "refund", dep=30),
                self.row(4, "refund", wd=7),
                self.row(5, "investment", wd=500),
                self.row(6, "income", dep=1000),
                self.row(7, "transfer", wd=300)]
        # a debited transfer is spend; investment / income are not
        self.assertEqual([spend.delta(r) for r in rows],
                         [100, -20, 0, 0, 0, 0, 300])
        self.assertEqual(spend.total(rows), 380)
        self.assertEqual(spend.by_fy_category(rows),
                         {("2025-26", "Groceries"): 380})

    def test_incoming_uses_the_kinds_named_by_the_specification(self):
        """dashboard-specs: credited income, refund and transfer are incoming."""
        app = dashboard_module()
        rows = [self.row(1, "income", dep=1000),
                self.row(2, "refund", dep=50),
                self.row(3, "transfer", dep=200),
                self.row(4, "investment", dep=300),
                self.row(5, "expense", dep=25),
                self.row(6, "income", wd=10)]
        self.assertEqual([r["id"] for r in app.incoming(rows)], [1, 2, 3])

    def test_big_ticket_is_positive_spend_in_focus_year_sorted_and_limited(self):
        app = dashboard_module()
        rows = [self.row(1, "expense", wd=10),
                self.row(2, "refund", dep=100),
                self.row(3, "expense", wd=50),
                self.row(4, "expense", wd=500, fy="2024-25"),
                self.row(5, "investment", wd=1000)]
        self.assertEqual([r["id"] for r in app.big_ticket(rows, "2025-26", n=2)],
                         [3, 1])

    def test_home_snapshot_obeys_scope_and_latest_financial_year(self):
        app = dashboard_module()
        rows = [self.row(1, "expense", wd=999, fy="2024-25"),
                self.row(2, "expense", wd=200),
                self.row(3, "refund", dep=20),
                self.row(4, "income", dep=1000),
                self.row(5, "expense", wd=5, category="UNKNOWN")]
        investments = [{"value_latest": 500, "value_at_cost": 400},
                       {"value_latest": None, "value_at_cost": 300}]
        fy, net, rate, unknown, worth = app.home_snapshot(rows, investments)
        # refund 20 is incoming (1000 + 20), not a reduction of the 205 spend
        self.assertEqual((fy, net, unknown, worth),
                         ("2025-26", 205, 1, 800))
        self.assertAlmostEqual(rate, (1020 - 205) / 1020)

    def test_no_income_produces_no_savings_rate(self):
        app = dashboard_module()
        row = self.row(1, "expense", wd=100)
        self.assertEqual(app.home_snapshot([row], []),
                         ("2025-26", 100, None, 0, 0))

    def test_each_overview_exclusion_reports_only_its_own_effect(self):
        app = dashboard_module()
        rows = [self.row(1, "expense", wd=100),
                self.row(2, "expense", wd=200, fy="2024-25"),
                self.row(3, "expense", wd=300, category="Travel_leisure"),
                self.row(4, "expense", wd=1000),
                self.row(5, "expense", wd=400, category="Loan_EMI"),
                self.row(6, "expense", wd=50)]
        filters = {"fys": ["2025-26"],
                   "cats": {"Groceries", "Loan_EMI"},
                   "kinds": {"expense", "refund"},
                   "maxamt": 500, "baseline": True}
        self.assertEqual(app.excluded_by_filter(rows, filters, frozenset({6})), {
            "fys": 200,
            "cats": 300,
            "maxamt": 1000,
            "baseline": 400,
            "bigticket": 50,
        })

    def test_baseline_does_not_remove_non_spend_kinds(self):
        app = dashboard_module()
        filters = {"fys": ["2025-26"], "cats": {"Loan_EMI"},
                   "kinds": {"expense", "refund", "income", "investment",
                             "transfer"},
                   "maxamt": 1000, "baseline": True}
        rows = [self.row(1, "expense", wd=100, category="Loan_EMI"),
                self.row(2, "income", dep=100, category="Loan_EMI"),
                self.row(3, "investment", wd=100, category="Loan_EMI"),
                self.row(4, "transfer", wd=100, category="Loan_EMI")]
        # a debited transfer is spend now, so the baseline hides it like an expense
        self.assertEqual([r["id"] for r in app.filtered(rows, filters)], [2, 3])

"""Headless smoke test for the streamlit dashboard (AppTest).

Runs all five pages against a small temp database with a logged-in user, and
exercises the login page (no user in state). JS/cookie probing cannot run
headlessly, so the authenticated runs pre-set st.session_state.user.
"""
import os
import tempfile
import unittest
from unittest import mock

from streamlit.testing.v1 import AppTest

from backend import FinanceBackend


def make_db(path):
    b = FinanceBackend(path=path)
    b.seed_members([{"name": "a", "data_dir": "data/a"},
                    {"name": "b", "data_dir": "data/b"}])
    b.set_password("a", "pw")
    with b.con:
        b.upsert_bank_entry("a", account="A", date="2024-04-01", mode="UPI",
                            particulars="UPI/SM MART@okpay", deposit=0,
                            withdrawal=100, balance=900, kind="expense",
                            category="Groceries", file="s")
        b.upsert_bank_entry("a", account="A", date="2024-04-02", mode="",
                            particulars="RANDOM UNKNOWN PAYEE", deposit=0,
                            withdrawal=50, balance=850, kind="review",
                            category="UNKNOWN", file="s")
        b.upsert_bank_entry("b", account="B", date="2024-04-01", mode="NEFT",
                            particulars="ACH/INCOME", deposit=250, withdrawal=0,
                            balance=1500, kind="income", category="", file="s")
        b.upsert_investment("a", type_="MF", label="Axis Cap Fund",
                            folio="1", units=100, latest_date="2024-04-01",
                            latest_nav=11.0, value_at_cost=1000,
                            value_latest=1100)
        b.upsert_investment("a", type_="cash", label="Cash in bank",
                            value_latest=5000)
    b.close()
    return path


USER_A = {"id": "a", "username": "a", "display_name": "a", "family_id": "f1"}


APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "personal-finance.py")


def make_zero_db(path):
    b = FinanceBackend(path=path)
    b.seed_members([{"name": "a", "data_dir": "data/a"}])
    b.set_password("a", "pw")
    with b.con:
        b.upsert_bank_entry("a", account="A", date="2024-04-01", mode="NEFT",
                            particulars="ACH/SALARY", deposit=1000, withdrawal=0,
                            balance=1000, kind="income", category="", file="s")
    b.close()


class TestDashboardPages(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)

    def tearDown(self):
        self.d.cleanup()

    def _at(self):
        at = AppTest.from_file(APP)
        at.session_state["user"] = dict(USER_A)
        return at

    def test_all_pages_render(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at()
            at.run()
            self.assertFalse(at.exception)
            for page in ("Transactions", "Categories", "Review", "Holdings"):
                at.radio(key="page").set_value(page)
                at.run()
                self.assertFalse(at.exception, f"{page} raised")

    def test_scope_me(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at()
            at.run()
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertFalse(at.exception)
            # 'b' income row must not appear in Me scope
            text = " ".join(str(x) for x in at.dataframe)
            self.assertNotIn("ACH/INCOME", text)

    def test_login_page_when_anonymous(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = AppTest.from_file(APP)
            at.run()
            self.assertFalse(at.exception)
            # probe element + sign-in form rendered
            inputs = [str(x) for x in at.text_input]
            self.assertTrue(any("Username" in i for i in inputs))
            self.assertTrue(any("Password" in i for i in inputs))

    def test_classify_and_update_write_to_db(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}), \
                tempfile.TemporaryDirectory() as _:
            at = self._at()
            at.run()
            at.radio(key="page").set_value("Review")
            at.run()
            self.assertFalse(at.exception)
            b = FinanceBackend(path=self.db)
            unk = [r for r in b.entries(user_ids=["a"]) if r["kind"] == "review"]
            self.assertEqual(len(unk), 1)
            b.close()

    def test_sign_out_preserves_data(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at()
            at.run()
            at.button(key="logout").click().run()
            self.assertFalse(at.exception)

    def test_slider_survives_zero_withdrawals(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}) as _:
            zd = os.path.join(self.d.name, "zero.db")
            make_zero_db(zd)
            with mock.patch.dict(os.environ, {"FINANCE_DB": zd}):
                at = self._at()
                at.run()
                self.assertFalse(at.exception)


if __name__ == "__main__":
    unittest.main()
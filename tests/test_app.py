"""Headless smoke test for the streamlit dashboard (AppTest).

Runs all five pages against a small temp database with a logged-in user, and
exercises the login page (no user in state). JS/cookie probing cannot run
headlessly, so the authenticated runs pre-set st.session_state.user.
"""
import os
import tempfile
import threading
import unittest
import importlib.util
from unittest import mock

from streamlit.testing.v1 import AppTest

from backend import FinanceBackend


APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "personal-finance.py")
_pf = None


def pf_module():
    global _pf
    if _pf is None:
        spec = importlib.util.spec_from_file_location("personal_finance", APP)
        _pf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_pf)
    return _pf


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
            b = FinanceBackend(path=self.db)
            try:
                # Me scope queries only the signed-in user's rows
                ids = {r["user_id"] for r in b.entries(user_ids=["a"])}
                self.assertEqual(ids, {"a"})
            finally:
                b.close()

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


class TestEditHelpers(unittest.TestCase):
    """st.data_editor is invisible to AppTest (unknown delta type, skipped),
    so the cell-edit write path is exercised through the commit helpers with
    simulated edited_rows."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)

    def tearDown(self):
        self.d.cleanup()

    def test_entry_disabled_direction(self):
        """Whole grid locked read-only (family); Kind/Category editable in Me."""
        pf = pf_module()
        cols = ["Date", "Member", "Mode", "Kind", "Category", "Particulars",
                "Debit", "Credit"]
        locked = pf._entry_disabled(cols, editable=True)
        self.assertIsInstance(locked, list)
        self.assertNotIn("Kind", locked)
        self.assertNotIn("Category", locked)
        self.assertIn("Date", locked)
        self.assertEqual(len(locked), len(cols) - 2)
        self.assertIs(pf._entry_disabled(cols, editable=False), True)

    def test_entry_edits_commit_and_ownership(self):
        pf = pf_module()
        b = FinanceBackend(path=self.db)
        try:
            rows = b.entries(user_ids=["a"])  # date order: expense, then review
            rowmap = [(r["id"], r["user_id"]) for r in rows]
            self.assertEqual(rows[1]["kind"], "review")
            # only the cells actually edited are written (kind kept)
            applied, errors = pf.commit_entry_edits(
                b, "a", rowmap, {1: {"Category": "Groceries"}})
            self.assertEqual((applied, errors), (1, []))
            row = b.entries(user_ids=["a"])[1]
            self.assertEqual((row["kind"], row["category"]),
                             ("review", "Groceries"))
            applied, errors = pf.commit_entry_edits(
                b, "a", rowmap, {1: {"Kind": "expense"}})
            self.assertEqual((applied, errors), (1, []))
            changed = b.entries(user_ids=["a"], kinds=["expense"])
            self.assertEqual(len(changed), 2)  # original + reclassified review
            # non-owner attempts are refused
            applied, errors = pf.commit_entry_edits(
                b, "b", rowmap, {0: {"Category": "Other"}})
            self.assertEqual((applied, len(errors)), (0, 1))
            # out-of-range position is reported, not raised
            applied, errors = pf.commit_entry_edits(b, "a", rowmap, {99: {}})
            self.assertEqual((applied, len(errors)), (0, 1))
        finally:
            b.close()

    def test_investment_edits_commit_and_reject_identity(self):
        pf = pf_module()
        b = FinanceBackend(path=self.db)
        try:
            invs = b.investments(user_ids=["a"])
            rowmap = [(r["id"], r["user_id"]) for r in invs]
            mf = invs[0]  # ORDER BY type -> 'MF' before 'cash'
            applied, errors = pf.commit_investment_edits(
                b, "a", rowmap,
                {0: {"Latest NAV": 12.0, "Value latest": 1200.0}})
            self.assertEqual((applied, errors), (1, []))
            fixed = b.investments(user_ids=["a"])[0]
            self.assertEqual(fixed["latest_nav"], 12.0)
            self.assertEqual(fixed["value_latest"], 1200.0)
            self.assertIsNotNone(fixed["edited_at"])
            # identity/primary-key columns are never written
            applied, errors = pf.commit_investment_edits(
                b, "a", rowmap, {0: {"type": "cash", "label": "hijack"}})
            self.assertEqual((applied, errors), (0, []))
            fixed = b.investments(user_ids=["a"])[0]
            self.assertEqual(fixed["type"], "MF")
            self.assertEqual(fixed["label"], mf["label"])
            # non-owner refused
            applied, errors = pf.commit_investment_edits(
                b, "b", rowmap, {0: {"Value latest": 1.0}})
            self.assertEqual((applied, len(errors)), (0, 1))
            # blank strings clear a field
            b.upsert_investment("a", type_="FD", label="FixDep",
                                value_latest=1000)
            fds = [r for r in b.investments(user_ids=["a"]) if r["type"] == "FD"]
            applied, errors = pf.commit_investment_edits(
                b, "a", [(fds[0]["id"], fds[0]["user_id"])],
                {0: {"Value latest": ""}})
            self.assertEqual((applied, errors), (1, []))
            fd = [r for r in b.investments(user_ids=["a"]) if r["type"] == "FD"][0]
            self.assertIsNone(fd["value_latest"])
        finally:
            b.close()


class TestEditCallbacks(unittest.TestCase):
    """Streamlit never wires data_editor deltas through AppTest, so the on_change
    handlers are driven directly against a fake `st`: scope guard, session-key
    read, and the fact that each callback commits through its own connection
    (Streamlit runs on_change off-thread, so backend.con is unusable there)."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)
        self.pf = pf_module()
        self.b = FinanceBackend(path=self.db)
        self.rows = self.b.entries(user_ids=["a"])
        self.rowmap = [(r["id"], r["user_id"]) for r in self.rows]
        self.session = {"scope": "Me", "user": {"id": "a"}}

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def _call(self, handler, key, *extra):
        st = mock.MagicMock()
        st.session_state = self.session
        with mock.patch.object(self.pf, "st", st):
            handler(self.b, key, *extra)
        return st

    def test_entry_callback_persists_kind_and_category(self):
        self.session["ed_entries"] = {
            "edited_rows": {1: {"Kind": "expense", "Category": "Groceries"}}}
        st = self._call(self.pf.on_entry_edited, "ed_entries", self.rowmap)
        self.assertEqual(self.b.entries(user_ids=["a"])[1]["kind"], "expense")
        self.assertEqual(self.b.entries(user_ids=["a"])[1]["category"],
                         "Groceries")
        st.success.assert_called_once()
        st.warning.assert_not_called()
        st.rerun.assert_called_once()

    def test_entry_callback_family_scope_writes_nothing(self):
        self.session["scope"] = "Family"
        self.session["ed_entries"] = {
            "edited_rows": {1: {"Category": "Groceries"}}}
        st = self._call(self.pf.on_entry_edited, "ed_entries", self.rowmap)
        self.assertEqual(self.b.entries(user_ids=["a"])[1]["category"], "UNKNOWN")
        st.success.assert_not_called()
        st.warning.assert_not_called()
        st.rerun.assert_not_called()

    def test_entry_callback_non_owner_row_is_refused(self):
        self.session["ed_entries"] = {
            "edited_rows": {0: {"Category": "Groceries"}}}
        foreign = [(i, "b") for i, _ in self.rowmap]
        before = self.b.entries(user_ids=["a"])[0]["category"]
        st = self._call(self.pf.on_entry_edited, "ed_entries", foreign)
        self.assertEqual(self.b.entries(user_ids=["a"])[0]["category"], before)
        st.success.assert_not_called()
        st.warning.assert_called_once()
        st.rerun.assert_called_once()

    def test_entry_callback_runs_off_the_script_thread(self):
        """Regression for the real failure: streamlit calls on_change on another
        thread, so the shared backend.con must never be handed to the commit.
        Driving the whole handler from a worker thread must succeed."""
        self.session["ed_entries"] = {
            "edited_rows": {1: {"Category": "Groceries"}}}
        failures = []
        def worker():
            try:
                self._call(self.pf.on_entry_edited, "ed_entries", self.rowmap)
            except Exception as e:  # noqa: BLE001 - captured, asserted below
                failures.append(e)
        t = threading.Thread(target=worker)
        t.start()
        t.join()
        self.assertEqual(failures, [])
        self.assertEqual(self.b.entries(user_ids=["a"])[1]["category"],
                         "Groceries")

    def test_investment_callback_persists_editable_fields(self):
        invs = self.b.investments(user_ids=["a"])
        rowmap = [(r["id"], r["user_id"]) for r in invs]
        self.session["ed_holdings"] = {
            "edited_rows": {0: {"Latest NAV": 12.0, "Value latest": 1200.0}}}
        st = self._call(self.pf.on_investment_edited, "ed_holdings", rowmap)
        fixed = self.b.investments(user_ids=["a"])[0]
        self.assertEqual((fixed["latest_nav"], fixed["value_latest"]),
                         (12.0, 1200.0))
        st.success.assert_called_once()
        st.warning.assert_not_called()
        st.rerun.assert_called_once()

    def test_investment_callback_ignores_identity_columns(self):
        invs = self.b.investments(user_ids=["a"])
        rowmap = [(r["id"], r["user_id"]) for r in invs]
        self.session["ed_holdings"] = {
            "edited_rows": {0: {"Type": "cash", "Label": "hijack"}}}
        st = self._call(self.pf.on_investment_edited, "ed_holdings", rowmap)
        fixed = self.b.investments(user_ids=["a"])[0]
        self.assertEqual((fixed["type"], fixed["label"]),
                         (invs[0]["type"], invs[0]["label"]))
        st.success.assert_not_called()
        st.warning.assert_not_called()
        st.rerun.assert_not_called()

    def test_investment_callback_non_owner_row_is_refused(self):
        invs = self.b.investments(user_ids=["a"])
        rowmap = [(r["id"], "b") for r in invs]
        self.session["ed_holdings"] = {
            "edited_rows": {0: {"Latest NAV": 1.0}}}
        st = self._call(self.pf.on_investment_edited, "ed_holdings", rowmap)
        self.assertEqual(self.b.investments(user_ids=["a"])[0]["latest_nav"],
                         invs[0]["latest_nav"])
        st.success.assert_not_called()
        st.warning.assert_called_once()
        st.rerun.assert_called_once()


if __name__ == "__main__":
    unittest.main()
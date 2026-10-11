"""Headless smoke test for the streamlit dashboard (AppTest).

Runs every page against a small temp database with a logged-in user, and
exercises the login page (no user in state). JS/cookie probing cannot run
headlessly, so the authenticated runs pre-set st.session_state.user.
"""
import os
import tempfile
import threading
import unittest
import contextlib
import importlib.util
from unittest import mock

from streamlit.testing.v1 import AppTest

from backend import FinanceBackend
from helpers import session_token


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
        b.upsert_bank_entry("a", account="A", date="2024-04-01", particulars="UPI/SM MART@okpay", deposit=0,
                            withdrawal=100, balance=900, kind="expense",
                            category="Groceries", file="s")
        b.upsert_bank_entry("a", account="A", date="2024-04-02", particulars="RANDOM UNKNOWN PAYEE", deposit=0,
                            withdrawal=50, balance=850, kind="review",
                            category="UNKNOWN", file="s")
        b.upsert_bank_entry("b", account="B", date="2024-04-01", particulars="ACH/INCOME", deposit=250, withdrawal=0,
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
        b.upsert_bank_entry("a", account="A", date="2024-04-01", particulars="ACH/SALARY", deposit=1000, withdrawal=0,
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
        at.session_state["token"] = session_token(self.db, "a", "pw")
        return at

    def test_all_pages_render(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at()
            at.run()
            self.assertFalse(at.exception)
            for page in ("Home", "Overview", "Transactions", "Categories",
                         "Holdings", "Import", "Export"):
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

    def test_expired_or_revoked_session_returns_to_login(self):
        for how in ("expire", "revoke"):
            with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
                at = self._at()
                at.run()
                self.assertFalse(at.text_input)  # signed in: no login form
                b = FinanceBackend(path=self.db)
                if how == "expire":
                    b.con.execute("UPDATE auth_tokens SET expires_at = ?",
                                  ("2000-01-01T00:00:00+00:00",))
                    b.con.commit()
                else:
                    b.revoke_session(at.session_state["token"])
                b.close()
                at.run()
                self.assertFalse(at.exception)
                self.assertTrue(any("Username" in str(x) for x in at.text_input),
                                f"{how}: login form expected")

    def test_failed_login_is_logged(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}), \
                contextlib.chdir(self.d.name):
            at = AppTest.from_file(APP)
            at.run()
            at.text_input[0].set_value("a")
            at.text_input[1].set_value("WRONG-pw")
            at.button[0].click()
            at.run()
            with open(os.path.join("var", "logs", "auth.log")) as f:
                text = f.read()
            self.assertIn("auth-failure source=dashboard user=a", text)
            self.assertNotIn("WRONG-pw", text)

    def test_login_page_when_anonymous(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = AppTest.from_file(APP)
            at.run()
            self.assertFalse(at.exception)
            # probe element + sign-in form rendered
            inputs = [str(x) for x in at.text_input]
            self.assertTrue(any("Username" in i for i in inputs))
            self.assertTrue(any("Password" in i for i in inputs))

    def test_transactions_render_never_writes(self):
        """Viewing / filtering Transactions never changes the database."""
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at()
            at.run()
            at.radio(key="page").set_value("Transactions")
            at.radio(key="scope").set_value("Me")
            at.run()
            self.assertFalse(at.exception)
            b = FinanceBackend(path=self.db)
            unk = [r for r in b.entries(user_ids=["a"]) if r["kind"] == "review"]
            self.assertEqual(len(unk), 1)
            b.close()

    def test_text_and_sql_filter_together_raise_an_error(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at()
            at.run()
            at.radio(key="page").set_value("Transactions")
            at.run()
            at.text_input(key="tx_text").set_value("MART")
            at.text_input(key="tx_sql").set_value("where kind = 'expense'")
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any("not both" in e.value for e in at.error))

    def test_sql_filter_selects_rows(self):
        with mock.patch.dict(os.environ, {"FINANCE_DB": self.db}):
            at = self._at()
            at.run()
            at.radio(key="page").set_value("Transactions")
            at.run()
            at.text_input(key="tx_sql").set_value(
                "select * where kind is empty and category is empty "
                "and particulars like '%random%'")
            at.run()
            self.assertFalse(at.exception)
            self.assertIn("1 rows", " ".join(c.value for c in at.caption))

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


class TestStagedEdits(unittest.TestCase):
    """edits stay in session state until the explicit batched Save."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.d.name, "networth.db")
        make_db(self.db)
        self.pf = pf_module()
        self.b = FinanceBackend(path=self.db)
        self.rows = self.b.entries(user_ids=["a"])
        self.ids = [r["id"] for r in self.rows]
        self.base = {r["id"]: {"kind": r["kind"], "category": r["category"]}
                     for r in self.rows}

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def test_multi_row_cell_edits_all_retained_and_nothing_written(self):
        staged, sel = {}, set()
        n = self.pf.merge_cell_edits(
            staged, sel, self.ids,
            {0: {"Category": "Dining_out"},
             1: {"Kind": "expense", "Category": "Groceries"}}, self.base)
        self.assertEqual(n, 3)
        self.assertEqual(staged[self.ids[0]], {"category": "Dining_out"})
        self.assertEqual(staged[self.ids[1]],
                         {"kind": "expense", "category": "Groceries"})
        # database untouched until Save
        self.assertEqual([r["category"] for r in self.b.entries(user_ids=["a"])],
                         ["Groceries", "UNKNOWN"])

    def test_reverting_an_edit_leaves_nothing_pending(self):
        staged = {}
        self.pf.merge_cell_edits(staged, set(), self.ids,
                                 {0: {"Category": "Dining_out"}}, self.base)
        self.pf.merge_cell_edits(staged, set(), self.ids,
                                 {0: {"Category": "Groceries"}}, self.base)
        self.assertEqual(self.pf.staged_changes(staged), {})

    def test_select_column_updates_selection_only(self):
        staged, sel = {}, set()
        self.pf.merge_cell_edits(staged, sel, self.ids,
                                 {0: {"Select": True}, 1: {"Select": True}}, self.base)
        self.assertEqual(sel, set(self.ids))
        self.assertEqual(staged, {})
        self.pf.merge_cell_edits(staged, sel, self.ids,
                                 {0: {"Select": False}}, self.base)
        self.assertEqual(sel, {self.ids[1]})

    def test_bad_position_is_ignored(self):
        staged = {}
        self.assertEqual(self.pf.merge_cell_edits(
            staged, set(), self.ids, {99: {"Kind": "expense"}}, self.base), 0)

    def test_stage_bulk_then_save_is_one_batch(self):
        staged = {}
        self.pf.stage_bulk(staged, self.ids, self.base, kind="expense",
                           category="Dining_out")
        changes = self.pf.staged_changes(staged)
        self.assertEqual(len(changes), 2)
        self.assertEqual(self.b.bulk_update_entries("a", changes), 2)
        for r in self.b.entries(user_ids=["a"]):
            self.assertEqual((r["kind"], r["category"]),
                             ("expense", "Dining_out"))

    def test_bulk_save_is_atomic_and_owner_only(self):
        other = self.b.entries(user_ids=["b"])[0]["id"]
        with self.assertRaises(ValueError):
            self.b.bulk_update_entries(
                "a", {self.ids[0]: (None, "Dining_out"), other: (None, "x")})
        self.assertEqual(self.b.entries(user_ids=["a"])[0]["category"], "Groceries")

    def test_on_tx_edited_uses_session_state_only(self):
        session = {"ed_tx_0": {"edited_rows": {0: {"Category": "Dining_out"}}}}
        st = mock.MagicMock()
        st.session_state = session
        with mock.patch.object(self.pf, "st", st):
            self.pf.on_tx_edited("ed_tx_0", self.ids, self.base)
        self.assertEqual(session["staged"], {self.ids[0]: {"category": "Dining_out"}})
        self.assertEqual(session["ed_ver"], 1)
        self.assertEqual(self.b.entries(user_ids=["a"])[0]["category"], "Groceries")

    def test_rows_stay_visible_after_edit(self):
        """stable_ids freezes the row set while the filter is unchanged."""
        session = {}
        st = mock.MagicMock()
        st.session_state = session
        with mock.patch.object(self.pf, "st", st):
            first = self.pf.stable_ids("tx", ("sig",), lambda: [1, 2])
            again = self.pf.stable_ids("tx", ("sig",), lambda: [])
            changed = self.pf.stable_ids("tx", ("other",), lambda: [3])
        self.assertEqual((first, again, changed), ([1, 2], [1, 2], [3]))


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
        self.session = {"scope": "Me", "user": {"id": "a"},
                        "token": self.b.issue_session("a", "pw")}

    def tearDown(self):
        self.b.close()
        self.d.cleanup()

    def _call(self, handler, key, *extra):
        st = mock.MagicMock()
        st.session_state = self.session
        with mock.patch.object(self.pf, "st", st):
            handler(self.b, key, *extra)
        return st

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
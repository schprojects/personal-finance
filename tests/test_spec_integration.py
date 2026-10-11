"""SQLite integration contracts from auth-specs and backend-specs."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend import FinanceBackend, auth


class TestBackendSpecIntegration(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / 'networth.db')
        self.b = FinanceBackend(path=self.path)
        self.addCleanup(self.b.close)
        self.b.seed_members([{'name': name, 'data_dir': 'data/' + name}
                             for name in ('alex', 'blair')])

    def test_seed_is_idempotent_and_passwordless_members_cannot_login(self):
        self.b.seed_members([{'name': 'alex', 'data_dir': 'data/alex'}])
        self.assertEqual(len(self.b.users()), 2)
        self.assertEqual(self.b.family_user_ids('alex'), ['alex', 'blair'])
        self.assertFalse(self.b.check_password('alex', 'password'))
        self.assertIsNone(self.b.issue_session('alex', 'password'))

    def test_session_persists_digest_only_with_configured_ttl(self):
        self.b.set_password('alex', 'password')
        self.b.set_setting('session_ttl_seconds', '120')
        now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        with mock.patch.object(auth, 'now_utc', return_value=now):
            token = self.b.issue_session('alex', 'password')
            stored = dict(self.b.con.execute('SELECT * FROM auth_tokens').fetchone())
            self.assertEqual(stored['token_hash'], auth.token_digest(token))
            self.assertNotIn(token, stored.values())
            self.assertEqual(dt.datetime.fromisoformat(stored['expires_at']),
                             now + dt.timedelta(seconds=120))
        other = FinanceBackend(path=self.path)
        self.addCleanup(other.close)
        with mock.patch.object(auth, 'now_utc', return_value=now + dt.timedelta(seconds=119)):
            self.assertEqual(other.user_for_session(token)['id'], 'alex')
        with mock.patch.object(auth, 'now_utc', return_value=now + dt.timedelta(seconds=121)):
            self.assertIsNone(other.user_for_session(token))

    def test_password_change_revokes_all_own_sessions_only(self):
        for name in ('alex', 'blair'):
            self.b.set_password(name, 'old-password')
        own = [self.b.issue_session('alex', 'old-password') for _ in range(2)]
        foreign = self.b.issue_session('blair', 'old-password')
        with self.assertRaises(ValueError):
            self.b.set_password('alex', 'new-password', current_password='wrong')
        self.assertTrue(all(self.b.user_for_session(token) for token in own))
        self.b.set_password('alex', 'new-password', current_password='old-password')
        self.assertTrue(all(self.b.user_for_session(token) is None for token in own))
        self.assertEqual(self.b.user_for_session(foreign)['id'], 'blair')
        self.assertFalse(self.b.check_password('alex', 'old-password'))
        self.assertTrue(self.b.check_password('alex', 'new-password'))

    def test_identical_natural_keys_are_distinct_per_owner_and_edits_survive_reimport(self):
        data = dict(account='A', date='2024-04-01', particulars='Shop',
                    deposit=0, withdrawal=50, balance=950, kind='expense', category='UNKNOWN')
        for name in ('alex', 'blair'):
            self.assertEqual(self.b.upsert_bank_entry(name, **data), 'created')
        row = self.b.entries(user_ids=['alex'])[0]
        with self.assertRaises(ValueError):
            self.b.update_entry(row['id'], 'blair', category='Groceries')
        self.assertEqual(self.b.entries(user_ids=['alex'])[0], row)
        self.b.update_entry(row['id'], 'alex', category='Groceries')
        edited = self.b.entries(user_ids=['alex'])[0]
        self.assertEqual(self.b.upsert_bank_entry('alex', **data), 'manual_override')
        self.assertEqual(self.b.entries(user_ids=['alex'])[0], edited)
        self.assertEqual(self.b.entries(user_ids=['blair'])[0]['category'], 'UNKNOWN')

    def test_configured_fy_is_stored_and_combined_filters_are_intersections(self):
        self.b.set_setting('fy_start_month', '1')
        for date, account, mode, part in (
                ('2024-01-01', 'A', 'UPI', 'Target shop'),
                ('2024-01-02', 'B', 'UPI', 'Target shop'),
                ('2024-01-03', 'A', 'NEFT', 'Target shop'),
                ('2024-01-04', 'A', 'UPI', 'Other shop')):
            self.b.upsert_bank_entry('alex', account=account, date=date,
                                    particulars=part, deposit=0, withdrawal=50,
                                    balance=950, kind='expense', category='Groceries')
        rows = self.b.entries(user_ids=['alex'], fy='2024-25', account='A', q='Target', kinds=['expense'], category='Groceries',
                              date_from='2024-01-01', date_to='2024-01-01')
        self.assertEqual([r['date'] for r in rows], ['2024-01-01'])

    def test_optional_investment_history_and_identity_are_preserved(self):
        for name in ('alex', 'blair'):
            self.b.upsert_investment(name, type_='MF', label='Sample fund', folio='F1',
                                     value_latest=1200)
        row = self.b.investments(user_ids=['alex'])[0]
        self.assertIsNone(row['purchase_date'])
        self.assertIsNone(row['value_at_cost'])
        for field in ('id', 'user_id', 'type', 'label', 'folio'):
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                self.b.update_investment_fields(row['id'], 'alex', **{field: 'changed'})
        with self.assertRaises(ValueError):
            self.b.update_investment_fields(row['id'], 'blair', value_latest=1)
        self.assertEqual(self.b.investments(user_ids=['alex'])[0], row)

    def test_income_always_uses_the_default_category_on_every_edit_path(self):
        """importer-exporter-specs: income has ONLY the default category."""
        self.b.upsert_bank_entry(
            'alex', account='A', date='2024-04-01', particulars='Salary',
            deposit=1000, withdrawal=0, balance=1000, kind='income',
            category='Should_be_ignored')
        row = self.b.entries(user_ids=['alex'])[0]
        self.assertEqual(row['category'], '')

        # A category-only dashboard edit must preserve the same invariant.
        self.b.update_entry(row['id'], 'alex', category='Groceries')
        edited = self.b.entries(user_ids=['alex'])[0]
        self.assertEqual((edited['kind'], edited['category']), ('income', ''))

    def test_bulk_update_is_atomic_when_any_row_is_not_owned(self):
        common = dict(account='A', date='2024-04-01', deposit=0,
                      withdrawal=50, balance=950, kind='expense',
                      category='UNKNOWN')
        self.b.upsert_bank_entry('alex', particulars='Alex row', **common)
        self.b.upsert_bank_entry('blair', particulars='Blair row', **common)
        alex = self.b.entries(user_ids=['alex'])[0]
        blair = self.b.entries(user_ids=['blair'])[0]

        with self.assertRaises(ValueError):
            self.b.bulk_update_entries('alex', {
                alex['id']: ('expense', 'Groceries'),
                blair['id']: ('expense', 'Groceries'),
            })
        self.assertEqual(self.b.entries(user_ids=['alex'])[0]['category'], 'UNKNOWN')
        self.assertEqual(self.b.entries(user_ids=['blair'])[0]['category'], 'UNKNOWN')

    def test_setting_boundaries_change_financial_year_assignment(self):
        cases = [('4', '2024-03-31', '2023-24'),
                 ('4', '2024-04-01', '2024-25'),
                 ('1', '2024-01-01', '2024-25'),
                 ('7', '2024-06-30', '2023-24'),
                 ('7', '2024-07-01', '2024-25')]
        for start, date, expected in cases:
            with self.subTest(start=start, date=date):
                self.b.set_setting('fy_start_month', start)
                self.assertEqual(self.b.fy_of(date), expected)

    def test_investment_upsert_keeps_unspecified_history_and_updates_values(self):
        self.b.upsert_investment(
            'alex', type_='MF', label='Index fund', folio='F1', units=10,
            purchase_date='2024-04-01', purchase_nav=100,
            latest_date='2025-04-01', latest_nav=120,
            value_at_cost=1000, value_latest=1200)
        self.assertEqual(self.b.upsert_investment(
            'alex', type_='MF', label='Index fund', folio='F1',
            latest_date='2026-04-01', latest_nav=130, value_latest=1300),
            'updated')
        row = self.b.investments(user_ids=['alex'])[0]
        self.assertEqual((row['purchase_date'], row['purchase_nav'], row['units']),
                         ('2024-04-01', 100, 10))
        self.assertEqual((row['latest_date'], row['latest_nav'], row['value_latest']),
                         ('2026-04-01', 130, 1300))

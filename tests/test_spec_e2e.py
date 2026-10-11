"""Spec workflows through real CLI processes and the Streamlit AppTest UI.

Only a copy of the runtime sources executes, inside a temporary directory.
No CLI/domain/backend mocks; all CSVs, credentials and databases are synthetic.
AppTest cannot execute browser JavaScript, so cookie transport is not covered.
"""
import csv
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from contextlib import contextmanager


@contextmanager
def open_db(path):
    """sqlite3's own context manager only commits; this also closes."""
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class TestSpecEndToEnd(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for source in ('backend', 'finance'):
            shutil.copytree(ROOT / source, self.root / source,
                            ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / '.streamlit', self.root / '.streamlit')
        for source in ('impex.py', 'personal-finance.py', 'ui_theme.py', 'requirements.txt', 'auto-rules.example.csv', 'members.example.yaml', 'README.md',
                       'Dockerfile', 'docker-compose.yml', '.dockerignore'):
            shutil.copy2(ROOT / source, self.root / source)
        (self.root / 'scripts').mkdir()
        shutil.copy2(ROOT / 'scripts/make_dist.py', self.root / 'scripts/make_dist.py')
        (self.root / 'members.yaml').write_text(
            'members:\n  - name: alex\n    data_dir: data/alex\n'
            '  - name: blair\n    data_dir: data/blair\n')
        self.db = self.root / 'var/networth.db'
        self.env = dict(os.environ, FINANCE_DB=str(self.db), PYTHONDONTWRITEBYTECODE='1')
        self.password = 'synthetic-test-password'
        for name in ('alex', 'blair'):
            self.cli('auth', 'set-password', '--user', name, '--password-stdin',
                     input=self.password + '\n')

    def cli(self, *args, input=None, success=True):
        result = subprocess.run([sys.executable, str(self.root / 'impex.py'), *args],
                                cwd=self.root, env=self.env, input=input,
                                capture_output=True, text=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def write_csv(self, relative, rows):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', newline='') as file:
            csv.writer(file).writerows(rows)
        return path

    def carrier(self, owner='alex', account='A', category='Groceries'):
        return self.write_csv(f'data/{owner}/bank-statements/2024-25/s.csv', [
            ['MEMBER', 'ACCOUNT', 'DATE', 'MODE', 'PARTICULARS', 'DEPOSIT',
             'WITHDRAWAL', 'BALANCE', 'KIND', 'CATEGORY', 'FY'],
            ['ignored-owner', account, '01-04-2024', 'UPI', 'Shop, weekly', 0,
             100, 900, 'expense', category, 'incorrect-fy'],
            ['ignored-owner', account, '02-04-2024', 'NEFT', 'Salary', 500,
             0, 1400, 'income', 'Must be ignored', 'incorrect-fy']])

    def import_bank(self, owner='alex', success=True, password=None):
        return self.cli('import', '--user', owner, '--type', 'bank-statement',
                        '--password-stdin', input=(password or self.password) + '\n',
                        success=success)

    def rows(self, table):
        self.assertIn(table, ('entries', 'investments', 'auth_tokens', 'users'))
        with open_db(self.db) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute('SELECT * FROM ' + table)]

    def export(self, *filters, owner='alex', filename='export.csv'):
        path = self.root / filename
        self.cli('export', '--login', owner, '--password-stdin', '--out', str(path),
                 *filters, input=self.password + '\n')
        with path.open(newline='') as file:
            return list(csv.DictReader(file))

    def test_import_export_roundtrip_preserves_classification_and_quoted_text(self):
        source = self.carrier()
        original = source.read_bytes()
        self.import_bank()
        first = self.rows('entries')
        self.assertEqual({r['user_id'] for r in first}, {'alex'})
        self.assertEqual({r['fy'] for r in first}, {'2024-25'})
        exported = self.export('--user', 'alex')
        self.assertEqual(exported[0]['PARTICULARS'], 'Shop, weekly')
        self.assertEqual(exported[0]['WITHDRAWAL'], '100.00')
        self.assertEqual(exported[1]['CATEGORY'], '')
        shutil.copy2(self.root / 'export.csv', source)
        self.import_bank()
        self.assertEqual(self.rows('entries'), first)
        source.write_bytes(original)
        self.import_bank()
        self.assertEqual(source.read_bytes(), original)

    def test_family_export_and_all_bank_filters(self):
        for owner, account in (('alex', 'A'), ('blair', 'B')):
            self.carrier(owner, account)
            self.import_bank(owner)
        self.assertEqual({r['MEMBER'] for r in self.export()}, {'alex', 'blair'})
        filtered = self.export('--user', 'alex', '--fy', '2024-25', '--kind', 'expense',
                               '--category', 'Groceries', '--account', 'A', '--q', 'weekly')
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['MEMBER'], 'alex')
        self.assertEqual(self.export('--q', 'absent'), [])

    def test_export_can_select_other_family_member(self):
        for owner, account in (('alex', 'A'), ('blair', 'B')):
            self.carrier(owner, account)
            self.import_bank(owner)
        exported = self.export('--user', 'blair')
        self.assertEqual({r['MEMBER'] for r in exported}, {'blair'})

    def test_wrong_password_does_not_import_or_overwrite_export(self):
        self.carrier()
        before = self.rows('entries')
        self.import_bank(password='wrong-password', success=False)
        self.assertEqual(self.rows('entries'), before)
        output = self.root / 'existing.csv'
        output.write_text('keep this file\n')
        self.cli('export', '--login', 'alex', '--password-stdin', '--out', str(output),
                 input='wrong-password\n', success=False)
        self.assertEqual(output.read_text(), 'keep this file\n')

    def test_cli_auth_failure_is_logged(self):
        self.import_bank(password='wrong-password', success=False)
        logs = list((self.root / 'var/logs').glob('*.log'))
        self.assertTrue(logs, 'auth-specs requires failed CLI authentication to be logged')
        self.assertTrue(any('auth' in path.read_text().lower() for path in logs))
        self.assertTrue(all('wrong-password' not in path.read_text() for path in logs))

    def test_password_file_authenticates_without_exposing_password(self):
        self.carrier()
        password_file = self.root / 'credential.txt'
        password_file.write_text(self.password + '\n')
        result = self.cli('import', '--user', 'alex', '--password-file', str(password_file))
        self.assertEqual(len(self.rows('entries')), 2)
        self.assertNotIn(self.password, result.stdout + result.stderr)
        self.assertTrue(all(self.password not in path.read_text()
                            for path in (self.root / 'var/logs').glob('*.log')))

    def test_reconcile_failure_continues_and_failed_rows_are_deduplicated(self):
        self.carrier()
        self.write_csv('data/alex/bank-statements/2024-25/bad.csv', [
            ['Statement of Transactions in SavingsNumber: BAD'],
            ['DATE', 'MODE', 'PARTICULARS', 'DEPOSITS', 'WITHDRAWALS', 'BALANCE'],
            ['01-04-2024', '', 'B/F', 0, 0, 1000],
            ['02-04-2024', 'UPI', 'Bad shop', 0, 100, 900],
            ['03-04-2024', 'UPI', 'Bad closing', 0, 50, 999]])
        self.import_bank()
        self.assertEqual(len(self.rows('entries')), 2)
        failed = self.root / 'output/2024-25/failed-1.csv'
        with failed.open(newline='') as file:
            rows = list(csv.DictReader(file))
        self.assertEqual([r['PARTICULARS'] for r in rows], ['Bad shop', 'Bad closing'])
        self.assertEqual(rows[1]['BALANCE'], '999.0')
        original = failed.read_bytes()
        self.import_bank()
        self.assertEqual(list(failed.parent.glob('failed-*.csv')), [failed])
        self.assertEqual(failed.read_bytes(), original)
        log_text = '\n'.join(p.read_text() for p in (self.root / 'var/logs').glob('*.log'))
        self.assertIn('FAIL', log_text)
        self.assertIn('closing=', log_text)

    def test_investment_import_export_without_optional_history(self):
        self.write_csv('data/alex/investments/mfs.csv', [
            ['Scheme Name', 'Folio No', 'Unit Balance', 'NAV Date', 'NAV', 'Market Value'],
            ['Sample fund', 'F1', 10, '01-Apr-2024', 120, 1200]])
        self.write_csv('data/alex/investments/assets.csv', [
            ['Cash', 500], ['Fixed deposits', 1000], ['EPF', 2000], ['To-receive', 300]])
        args = ('import', '--user', 'alex', '--type', 'investments', '--password-stdin')
        self.cli(*args, input=self.password + '\n')
        before = self.rows('investments')
        self.cli(*args, input=self.password + '\n')
        self.assertEqual(self.rows('investments'), before)
        exported = self.export('--type', 'investments')
        self.assertEqual({r['TYPE'] for r in exported}, {'MF', 'cash', 'FD', 'EPF', 'loan-receivable'})
        fund = next(r for r in exported if r['TYPE'] == 'MF')
        self.assertEqual(fund['VALUE_LATEST'], '1200.00')
        self.assertEqual(fund['PURCHASE_DATE'], '')
        self.assertEqual(fund['VALUE_AT_COST'], '')

    def test_investment_import_preserves_optional_purchase_history(self):
        """backend-specs: authored MF purchase date/NAV/cost must round-trip."""
        self.write_csv('data/alex/investments/mfs-history.csv', [
            ['Scheme Name', 'Folio No', 'Unit Balance', 'Purchase Date',
             'Purchase NAV', 'Cost Value', 'NAV Date', 'NAV', 'Market Value'],
            ['Sample history fund', 'F-HISTORY', 10, '01-Apr-2023', 100, 1000,
             '01-Apr-2024', 120, 1200]])
        self.cli('import', '--user', 'alex', '--type', 'investments',
                 '--password-stdin', input=self.password + '\n')
        exported = self.export('--type', 'investments')
        fund = next(r for r in exported if r['FOLIO'] == 'F-HISTORY')
        self.assertEqual(fund['PURCHASE_DATE'], '01-04-2023')
        self.assertEqual(fund['PURCHASE_NAV'], '100.00')
        self.assertEqual(fund['VALUE_AT_COST'], '1000.00')
        self.assertEqual(fund['LATEST_DATE'], '01-04-2024')
        self.assertEqual(fund['LATEST_NAV'], '120.00')
        self.assertEqual(fund['VALUE_LATEST'], '1200.00')

    def test_failed_row_deduplication_is_scoped_per_owner(self):
        """importer spec: the natural key, including failure dedup, is per user."""
        statement = [
            ['Statement of Transactions in SavingsNumber: SAME'],
            ['DATE', 'MODE', 'PARTICULARS', 'DEPOSITS', 'WITHDRAWALS', 'BALANCE'],
            ['01-04-2024', '', 'B/F', 0, 0, 1000],
            ['02-04-2024', 'UPI', 'Shared synthetic merchant', 0, 100, 999],
        ]
        for owner in ('alex', 'blair'):
            self.write_csv(f'data/{owner}/bank-statements/2024-25/bad.csv', statement)
            self.import_bank(owner)

        recorded = []
        for path in sorted((self.root / 'output/2024-25').glob('failed-*.csv')):
            with path.open(newline='') as file:
                recorded.extend(csv.DictReader(file))
        self.assertEqual([(r['MEMBER'], r['PARTICULARS']) for r in recorded],
                         [('alex', 'Shared synthetic merchant'),
                          ('blair', 'Shared synthetic merchant')])

    def test_backup_restores_entries_and_rejects_non_application_database(self):
        self.carrier()
        self.import_bank()
        before = self.rows('entries')
        self.cli('backup')
        backups = list((self.root / 'backup').glob('networth-*.db'))
        self.assertEqual(len(backups), 1)
        with open_db(self.db) as connection:
            connection.execute('DELETE FROM entries')
        self.cli('restore', '--from', str(backups[0]))
        self.assertEqual(self.rows('entries'), before)
        invalid = self.root / 'invalid.db'
        with open_db(invalid) as connection:
            connection.execute('CREATE TABLE unrelated (id INTEGER)')
        self.cli('restore', '--from', str(invalid), success=False)
        self.assertEqual(self.rows('entries'), before)

    def login(self):
        at = AppTest.from_file(str(self.root / 'personal-finance.py'))
        at.query_params['probe'] = '1'
        at.run()
        self.assertFalse(at.exception)
        at.text_input[0].set_value('alex')
        at.text_input[1].set_value(self.password)
        next(button for button in at.button if button.label == 'Sign in').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['user']['id'], 'alex')
        return at

    def test_dashboard_real_login_navigation_and_logout_revoke_token(self):
        self.carrier()
        self.import_bank()
        with mock.patch.dict(os.environ, {'FINANCE_DB': str(self.db)}):
            at = self.login()
            for page in ('Home', 'Overview', 'Transactions', 'Categories', 'Holdings', 'Import', 'Export'):
                at.radio(key='page').set_value(page).run()
                self.assertFalse(at.exception, page)
            before = self.rows('entries')
            at.button(key='logout').click().run()
            self.assertFalse(at.exception)
            self.assertTrue(all(r['revoked'] == 1 for r in self.rows('auth_tokens')))
            self.assertEqual(self.rows('entries'), before)
            self.assertTrue(any(x.label == 'Password' for x in at.text_input))

    def test_dashboard_revoked_session_requires_login_on_next_render(self):
        with mock.patch.dict(os.environ, {'FINANCE_DB': str(self.db)}):
            at = self.login()
            with open_db(self.db) as connection:
                connection.execute('UPDATE auth_tokens SET revoked = 1')
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any(x.label == 'Password' for x in at.text_input),
                            'auth-specs requires revoked sessions to re-present login')

    def test_dashboard_expired_session_requires_login_on_next_render(self):
        with mock.patch.dict(os.environ, {'FINANCE_DB': str(self.db)}):
            at = self.login()
            with open_db(self.db) as connection:
                connection.execute("UPDATE auth_tokens SET expires_at = '2000-01-01T00:00:00+00:00'")
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any(x.label == 'Password' for x in at.text_input),
                            'auth-specs requires expired sessions to re-present login')

    def test_carrier_preserves_each_rows_account(self):
        source = self.carrier()
        with source.open(newline='') as file:
            rows = list(csv.reader(file))
        rows[2][1] = 'SECOND-ACCOUNT'
        self.write_csv(str(source.relative_to(self.root)), rows)
        self.import_bank()
        self.assertEqual([r['ACCOUNT'] for r in self.export()], ['A', 'SECOND-ACCOUNT'])

    def test_distribution_archive_is_runnable_and_excludes_personal_data(self):
        import zipfile
        self.carrier()
        self.import_bank()
        result = subprocess.run([sys.executable, 'scripts/make_dist.py'],
                                cwd=self.root, env=self.env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(self.root / 'dist.zip') as archive:
            names = set(archive.namelist())
            for expected in ('impex.py', 'personal-finance.py', 'README.md',
                             'requirements.txt', 'backend/model.py', 'finance/classify.py'):
                self.assertIn('dist/' + expected, names)
            self.assertNotIn('dist/members.yaml', names)
            self.assertFalse(any(name.startswith(('dist/data/', 'dist/var/')) for name in names))
            archive.extractall(self.root / 'unpacked')
        result = subprocess.run([sys.executable, 'impex.py', '--help'],
                                cwd=self.root / 'unpacked/dist', env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('import', result.stdout)

    def test_distribution_can_include_chosen_seed_database(self):
        self.carrier()
        self.import_bank()
        result = subprocess.run([sys.executable, 'scripts/make_dist.py', '--with-db', str(self.db)],
                                cwd=self.root, env=self.env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with open_db(self.root / 'dist/var/networth.db') as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM entries').fetchone()[0], 2)
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_dashboard_missing_token_requires_login_on_next_render(self):
        """auth-specs: absence of a session token re-presents login."""
        with mock.patch.dict(os.environ, {'FINANCE_DB': str(self.db)}):
            at = self.login()
            del at.session_state['token']
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any(x.label == 'Password' for x in at.text_input))

    def test_dashboard_login_failure_does_not_reveal_username_existence(self):
        """auth-specs: login failures are generic for all usernames."""
        errors = []
        with mock.patch.dict(os.environ, {'FINANCE_DB': str(self.db)}):
            for username in ('alex', 'nonexistent-member'):
                at = AppTest.from_file(str(self.root / 'personal-finance.py'))
                at.query_params['probe'] = '1'
                at.run()
                at.text_input[0].set_value(username)
                at.text_input[1].set_value('wrong-password')
                next(b for b in at.button if b.label == 'Sign in').click().run()
                self.assertFalse(at.exception)
                self.assertTrue(at.error)
                errors.append([e.value for e in at.error])
                self.assertEqual(self.rows('auth_tokens'), [])
        self.assertEqual(errors[0], errors[1])
        self.assertNotIn('alex', ' '.join(errors[0]))
        self.assertNotIn('nonexistent-member', ' '.join(errors[1]))

    def test_failed_block_skips_entries_already_in_database(self):
        """Importer spec: a natural key already in the DB is skipped on failure."""
        self.carrier()
        self.import_bank()
        before = self.rows('entries')
        self.write_csv('data/alex/bank-statements/2024-25/bad.csv', [
            ['Statement of Transactions in SavingsNumber: A'],
            ['DATE', 'MODE', 'PARTICULARS', 'DEPOSITS', 'WITHDRAWALS', 'BALANCE'],
            ['01-04-2024', '', 'B/F', 0, 0, 1000],
            ['01-04-2024', 'UPI', 'Shop, weekly', 0, 100, 999]])
        self.import_bank()
        self.assertEqual(self.rows('entries'), before)
        self.assertEqual(list((self.root / 'output').glob('**/failed-*.csv')), [],
                         'An entry already stored must not be recorded as a failed entry')

# Business-logic specification coverage

This matrix maps the acceptance criteria in `specs/` to automated tests. It deliberately excludes visual styling and screenshot comparison. All fixtures use temporary databases, synthetic members, synthetic credentials, and synthetic CSV data.

Status meanings:

- **Pass** — exercised by an enabled automated assertion.
- **Fail** — an enabled assertion currently demonstrates a specification violation.
- **Environment limit** — the contract is covered below the unavailable browser boundary and the limitation is explicit.

## Unit contracts

| Specification behavior | Coverage | Status |
| --- | --- | --- |
| PBKDF2-HMAC-SHA256, random salt, ≥600k iterations, constant-time verification | `test_spec_unit.TestPasswordContract` | Pass |
| Opaque ≥256-bit session token; only SHA-256 digest is persisted | `test_spec_unit`, `test_spec_integration` | Pass |
| Statement opening-balance reconciliation and complete failed blocks | `test_spec_unit.TestReconciliationContract`, `test_classifier` | Pass |
| Net spend = expense rows (`withdrawal − deposit`) + debited transfers; refunds are incoming | `test_spec_unit.TestOverviewAccountingContract`, `test_dashboard_values.TestNetSpendDefinition` | Pass |
| Credited income/refund/transfer are incoming | `test_spec_unit...test_incoming_uses_the_kinds_named_by_the_specification` | Pass (BL-001 fixed) |
| Investment total return/XIRR; do not fabricate returns without history | `test_spec_unit.TestInvestmentReturnContract`, `test_holdings` | Pass (BL-003 fixed) |
| Baseline excludes exactly four categories and leaves non-spend kinds alone | `test_spec_unit.TestOverviewAccountingContract`, `test_unified.TestBaselineExplanation` | Pass |
| Each overview exclusion reports only its own amount | `test_spec_unit...test_each_overview_exclusion_reports_only_its_own_effect` | Pass |
| Big-ticket candidates are positive spend, focus-year scoped, sorted, limited | `test_spec_unit...test_big_ticket_is_positive_spend_in_focus_year_sorted_and_limited` | Pass |
| Auto-rules come from auto-rules.csv (standard path): case-insensitive, `\|` alternatives, @dir/@amt qualifiers, first-match-wins, never override CSV values, bad lines reported | `test_unified.TestRules`, `TestRulesFileOnly`, `TestImporting` | Pass |
| Plain text xor SQL filtering; read-only WHERE variants and empty shorthand | `test_unified.TestQuery`, `test_app`, `test_dashboard_values` | Pass |
| Export dates/amounts and collision-safe numeric suffixes | `test_unified.TestExporting` | Pass |

## Integration contracts

| Specification behavior | Coverage | Status |
| --- | --- | --- |
| Schema migrations, required initial categories/kinds, canonical labels | `test_backend.TestSchema`, category migration tests, `test_unified.TestBackendAdditions` | Pass |
| Member seeding, family grouping, password bootstrap/change, session revocation/expiry | `test_spec_integration.TestBackendSpecIntegration`, `test_backend` | Pass |
| Configurable financial-year boundary and TTL | `test_spec_integration` | Pass |
| Entry create/update/skip, owner-scoped natural keys, manual override survival | `test_backend.TestEntries`, `test_spec_integration` | Pass |
| Owner-only, all-or-nothing batched transaction updates | `test_app.TestStagedEdits`, `test_spec_integration...test_bulk_update_is_atomic_when_any_row_is_not_owned` | Pass |
| Income always uses its only/default category on every write path | `test_spec_integration...test_income_always_uses_the_default_category_on_every_edit_path` | Pass (BL-004 fixed) |
| Investment identity, optional fields, updates and owner authorization | `test_backend.TestInvestments`, `test_spec_integration` | Pass |
| Import preview/rules are in memory; explicit save writes | `test_unified.TestImporting`, `test_unified.TestImportAndExportViews` | Pass |
| Transactions retain multi-row staged edits until explicit batched save | `test_app.TestStagedEdits` | Pass |
| Family scope is read-only; Me scope exposes owner edits | `test_dashboard_values`, `test_app.TestEditCallbacks` | Pass |
| Overview scope/FY/category/filter calculations and member totals | `test_dashboard_values`, `test_unified.TestBaselineExplanation` | Pass |

## End-to-end contracts

| Specification workflow | Coverage | Status |
| --- | --- | --- |
| Authenticated CLI import/export; wrong password has no side effects | `test_spec_e2e` | Pass |
| Password stdin/file handling; no credential disclosure in output/logs | `test_spec_e2e` | Pass |
| Classified carrier CSV round-trip, quoting, account and family/member filters | `test_spec_e2e` | Pass |
| Reconcile failure continues, logs, writes unique files, and deduplicates reruns | `test_spec_e2e` | Pass |
| Failed-row natural-key deduplication is per owner | `test_spec_e2e...test_failed_row_deduplication_is_scoped_per_owner` | Pass (BL-005 fixed) |
| Investment import/export supports types and missing optional history | `test_spec_e2e` | Pass |
| Authored MF purchase date/NAV/cost survives import/export | `test_spec_e2e...test_investment_import_preserves_optional_purchase_history` | Pass (BL-002 fixed) |
| Dashboard login, navigation, scopes, expiry/revocation/logout | `test_spec_e2e`, `test_app` | Pass below browser cookie transport |
| Category creation and kind creation from Me scope only | `test_dashboard_values.TestCategoriesValues` | Pass (BL-006 fixed) |
| In-app import preview/rules/save/navigation and export non-overwrite | `test_unified.TestImportAndExportViews` | Pass |
| Backup/restore and invalid-backup rejection | `test_spec_e2e`, `test_impex` | Pass |
| Fresh clone: empty db on first use, pinned requirements, clear missing-members message, folder-for-file mounts tolerated | `test_unified.TestRulesFileOnly`, `test_docker.TestLicenseAndPins`, `test_spec_e2e` | Pass |
| Database path: FINANCE_DB, then `.env`, then var/networth.db | `test_unified.TestConnectionOwnership.test_db_path_resolution_order`, `test_backend` | Pass |
| Top category / Top categories chart total each category across years | `test_unified.TestMultiYearCategoryAggregation`, `test_dashboard_values` | Pass |
| Distribution archive, optional seed DB, no personal data | `test_spec_e2e`, `test_docker` | Pass |
| Docker image build, CLI persistence/auth/backup, dashboard health, no baked-in personal data | `RUN_DOCKER_TESTS=1 test_docker` | Pass |

## Functional findings (all fixed)

| ID | Finding | Business impact |
| --- | --- | --- |
| BL-001 | `incoming()` excludes credited refunds/transfers and includes credited expense rows. | Incoming and savings-rate figures can be incorrect. |
| BL-002 | MF import does not carry authored purchase date or purchase NAV into the database/export. | Historical investment data is lost and XIRR cannot be shown. |
| BL-003 | Dashboard XIRR calculation negates `None` when dates exist but cost/latest value is missing. | Holdings can crash instead of showing current value without XIRR. |
| BL-004 | A category-only edit can assign a non-default category to an income row. | Violates the single default category invariant for income. |
| BL-005 | Prior failed-row deduplication ignores member identity. | One member's failed transaction suppresses an identical transaction for another member. |
| BL-006 | Submitting Create kind triggers a Streamlit runtime parse error after the write. | The kind is persisted, but the user workflow crashes rather than completing normally. |

## Known automation boundary

Streamlit `AppTest` executes the real dashboard script and database workflows but does not execute browser JavaScript or cookie transport. Token issuance, digest storage, TTL, restoration logic, revocation, absent-token behavior, and generic login failures are covered; a true browser-level assertion for the `document.cookie` bridge remains an environment boundary. Container tests verify that the deployed dashboard becomes healthy.

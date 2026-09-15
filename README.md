# Expense Tracker

Family expense and net-worth dashboard — Simplifi / Mint style, backed by an
sqlite database (see **Backend**) that the importer feeds and members edit
(classify UNKNOWN rows, refresh holding values) with password login.

## Quick start

```sh
# one-time setup
python3 -m venv .venv
.venv/bin/pip install streamlit plotly pyyaml

# bootstrap a member password (each member does this once)
.venv/bin/python impex.py auth set-password --user [MEMBER]

# import bank statements (existing/auto rules) and investment sheets
.venv/bin/python impex.py import --user [MEMBER] --type bank-statement --mode existing
.venv/bin/python impex.py import --user [MEMBER] --type investments
# (add --mode auto to also consult OpenRouter and grow auto-rules.csv)

# run the dashboard -- sign in with your member password
.venv/bin/streamlit run app.py
```

Every `impex.py` subcommand asks for that member's password (or `--password-file
PATH` / `--password-stdin`); a wrong password exits non-zero before anything is
read or written. The database lives at `var/networth.db` (`FINANCE_DB` to
override), and every import writes a run log under `var/logs/`.

Open the URL streamlit prints (default `http://localhost:8501`).

## Run the tests

```sh
.venv/bin/python -m unittest discover -s tests -v
```

## How it works

### Members

`members.yaml` maps each member name to their data directory:

```yaml
members:
  - name: [MEMBER]
    data_dir: data/[MEMBER]
  - name: [MEMBER]
    data_dir: data/[MEMBER]
```

Each member's data lives under their data directory:

```
data/<member>/bank-statements/<FY>/<file>.csv   raw statements
data/<member>/investments/<file>.csv            authored holdings
```

### Importer / exporter (`impex.py`)

The single gate for reads and writes (auth-first). Bank statements
become `entries` and investment sheets become `investments` in the database;
the only surviving CSV artifact is a reconcile-failure.

```
# bootstrap a member password (requires the current one once set)
impex.py auth set-password --user [MEMBER]

# import, classified by the rules files
impex.py import --user [MEMBER] --type bank-statement --mode existing
impex.py import --user [MEMBER] --type bank-statement --mode auto   # + OpenRouter
impex.py import --user [MEMBER]    --type investments

# read path: CSV exports, whole family by default (--user to narrow)
impex.py export --login [MEMBER] --type bank --fy 2024-25 --out bank.csv
impex.py export --login [MEMBER] --type investments

# safety net
impex.py backup                 # -> backup/networth-<ts>.db
impex.py restore --from backup/networth-<ts>.db
```

Password sources: prompt, `--password-file PATH`, or `--password-stdin`
(saved only in `backend/`, never argv/environ). A wrong password exits non-zero
before anything is touched. Every import appends a run log under `var/logs/`.

Rules (`--mode`): `existing` = manual-rules.csv (if present) then auto-rules.csv;
`manual` / `auto` restrict to one file; `both` is identical to `existing`.
First match wins; no match → the row is kept as `review` + `UNKNOWN` (editable
in the dashboard). `auto` mode additionally classifies never-seen payees via
OpenRouter (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL` default `openrouter/auto`)
and appends the suggested keyword rules to `auto-rules.csv` (deduped).

Import semantics: rows are keyed by account + date + particulars + deposit +
withdrawal — created when new, updated when the classification changed (never
for rows you manually overrode), superseded (skipped) when identical. Statement
blocks must reconcile first (`opening + Σdep − Σwd == closing`); a FAILING
block no longer stops the run — its rows go to `output/<FY>/failed-<N>.csv`
(complete rows, deduped across runs) and the failure is logged.

`classify.py`, `repo/`, `review-*.csv`, `rules.txt`, and `manual.csv` are
retired. `data/` stays the import source; `auto-rules.csv` is the committed
rule base.

### Dashboard (`app.py`)

Password-login pages (see **Backend** for the session store). Reads `entries` /
`investments` from the database on every render; a sidebar "Family / me" radio
scopes reads and a member can edit only their own rows. Sidebar filters (FY,
category, kind, mode, max amount) and a "Daily-spend baseline" toggle apply
across five sections:

| Section | What it shows |
|---|---|
| **Overview** | net spend, savings rate, income vs spend per FY, top category, unreviewed count; focus-year monthly trend vs prior FY + cumulative spend line; top-8 category donut |
| **Transactions** | full classified table: search, kind/category filter, review-only toggle, ₹-formatted |
| **Categories** | per-category monthly trend across FYs + top payees within it |
| **Review** | UNKNOWN rows grouped by payee (count + ₹), per-payee drill, and a classify form that sets kind + category on the member's own row |
| **Holdings** | household net worth from the investments table; MF schemes (units, cost, latest NAV, absolute return % **+ XIRR**); cash/asset rows; per-member donut + largest-schemes bar; an edit form to refresh the latest value + as-of date of an owned holding |

Edits commit `manual_override`, so a later re-import never touches them.
To see **XIRR (annualized)** for a scheme, its MF sheet must author a
`Purchase Date` column (`dd-mm-yyyy`, e.g. in
`data/[MEMBER]/investments/[MF-PORTFOLIO].csv`): the app values cost at that date vs
market at the latest NAV. No column, no XIRR — the cell shows `—`.

### Shared logic (`common.py`)

`fy_of`, `inr`, `money`, `payee_key`, `load_members`, `statements_for`,
`investments_for`, `BASELINE_EXCLUDED` -- imported by both `classify.py` and
`app.py`.

### Tests (`tests/`)

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Covers: `common` helpers, the rule language and reconciliation math, holdings
sheet parsing and return calculation, the `backend/` database (migrations,
PBKDF2 passwords + session tokens, entry create/update/skip, UNKNOWN editing,
investment upsert), the `impex` CLI (import/upsert semantics,
reconcile-FAIL → `failed-<N>.csv` dedup, investment typing, export scope and
format, backup/restore round-trip, auth gate, OpenRouter rule extraction), and
the dashboard via streamlit's `AppTest` (`tests/test_app.py` — all five pages,
family/me scope, login gate).

## Backend (database)

`backend/` is the system of record: an sqlite database at
`var/networth.db` (override with `FINANCE_DB`), versioned via `PRAGMA
user_version`, owned by the `FinanceBackend` facade. It stores members and
users, expense/income/investment entries, investment holdings, hashed
passwords (PBKDF2), and opaque session tokens. Dates are ISO in the database;
fy defaults to April and the session TTL to 10 minutes — both tunable in
`settings`. `impex.py` is the bulk writer; the dashboard reads and edits it.
Dashboard sessions ride a same-Site `stmtok` cookie bridged through `st.html`
with `unsafe_allow_javascript=True` (Streamlit has no cookie API), validated
server-side; `Sign out` revokes the token.

## Files tracked in git

| File | Purpose |
|---|---|
| `common.py` | shared helpers |
| `rules.py` | rule language (Rule / RuleSet) for `rules.txt`, `auto-rules.csv`, `manual-rules.csv` |
| `auto-rules.csv` | LLM/generated rules (seeded from `rules.txt`, appended in auto mode) |
| `classify.py` | legacy CSV classifier (retired with the importer) |
| `impex.py` | gated importer/exporter CLI (bank → entries, sheets → investments, export/backup/restore) |
| `llm.py` | OpenRouter auto-mode client (stdlib-only) |
| `scripts/seed_auto_rules.py` | regenerates `auto-rules.csv` from `rules.txt` (equivalence-checked) |
| `review.py` | rules.txt suggestions from the latest `review-<N>.csv` |
| `app.py` | streamlit dashboard (DB-backed, cookie sessions, owner edits) |
| `holdings.py` | investment sheet parser + XIRR |
| `backend/` | sqlite system of record (schema, model, auth) |
| `tests/` | unit tests |
| `specs/` | project contracts |
| `.gitignore` | excludes `.venv/`, `data/`, `output/`, `repo/`, `var/`, `backup/`, `dist/`, `members.yaml`, `rules.txt`, `manual-rules.csv` |

## Out of scope (for now)

- YNAB-style budgeting (no budget data exists)
- Historic/seasonal MF tracking
- PDF export (kaleido via plotly `fig.write_image()` is a drop-in)
- Calendar heatmap, monthly per-category drill tables

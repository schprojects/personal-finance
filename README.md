# Expense Tracker

View-only family expense and net-worth dashboard — Simplifi / Mint style,
built on classified ICICI bank-statement CSVs and authored investment sheets.

## Quick start

```sh
# one-time setup
python3 -m venv .venv
.venv/bin/pip install streamlit plotly pyyaml

# classify bank statements -> output/ + repo/
.venv/bin/python classify.py

# run the dashboard
.venv/bin/streamlit run app.py
```

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

### Classifier (`classify.py`)

The only writer. Reads every member's bank statements, classifies each
transaction via the external, gitignored `rules.txt` (first-match-wins,
) plus manual overrides (`manual.csv`), and writes numbered,
non-destructive runs:

| File | Purpose |
|---|---|
| `output/classified-<N>.csv` | all classified rows (all members) |
| `output/review-<N>.csv` | rows needing a human ruling |
| `output/<FY>/classified-<N>.csv` | per-FY slices |
| `repo/classified-main-<N>.csv` | consolidated inventory (what the app reads) |

`N` = 1 + max run number already in `output/`, so re-runs never clobber old
history. `data/` is never written to.

A row in `review-<N>.csv` exits review after a human adds a rule for that
payee, then re-runs the classifier:

```sh
# see what's stuck in review, grouped by payee + suggested rule lines:
.venv/bin/python review.py
# e.g. paste    expense,My category,BIGPAYEE   into rules.txt, then:
.venv/bin/python classify.py
```

`rules.txt` lives at the repo root but is **gitignored** — rule syntax is
`KIND,CATEGORY,KEYWORDS [@mode:M] [@amt<=n|@amt>n]` (KEYWORDS: space-phrases,
`|` = any-of; see the header comment in the file). If it is ever missing,
`classify.py` fails loudly instead of guessing.

### Dashboard (`app.py`)

Reads the latest `repo/classified-main-*.csv` on every render. Sidebar
filters (FY, category, kind, max amount) and a "Daily-spend baseline" toggle
apply across five sections:

| Section | What it shows |
|---|---|
| **Overview** | net spend, savings rate, income vs spend per FY, top category, unreviewed count; focus-year monthly trend vs prior FY + cumulative spend line; top-8 category donut |
| **Transactions** | full classified table: search, kind/category filter, review-only toggle, ₹-formatted |
| **Categories** | per-category monthly trend across FYs + top payees within it |
| **Review** | unresolved rows grouped by payee (count + ₹), per-payee drill, and a copy-paste `rules.txt` line to auto-classify that payee |
| **Holdings** | household net worth from authored investment sheets; MF scheme tables (units, cost, latest NAV, absolute return % **+ XIRR**); label/value asset tables; per-member donut + largest-schemes bar |

Holdings parses two auto-detected sheet shapes:
- **MF detail** (has `scheme name` + `market value` columns) -- per-scheme table
  with cost value, units, latest NAV + date, and return %
- **Label/value** (everything else) -- "category, ₹" rows with optional
  stated-Total cross-check

To see **XIRR (annualized)** for a scheme, add a `Purchase Date` column
(`dd-mm-yyyy`) to its MF sheet (e.g. `data/[MEMBER]/investments/[MF-PORTFOLIO].csv`):
the app then values cost at that date vs market at the latest NAV. No column,
no XIRR — the cell shows `—` and the caption explains why.

### Shared logic (`common.py`)

`fy_of`, `inr`, `money`, `payee_key`, `load_members`, `statements_for`,
`investments_for`, `BASELINE_EXCLUDED` -- imported by both `classify.py` and
`app.py`.

### Tests (`tests/`)

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Covers: `common` helpers, classifier rules / reconciliation / numbered outputs,
holdings sheet parsing and return calculation, and the `backend/` database
(migrations, PBKDF2 passwords + session tokens, entry create/update/skip,
UNKNOWN editing, investment upsert).

## Backend (database)

`backend/` is the system of record: an sqlite database at
`var/networth.db` (override with `FINANCE_DB`), versioned via `PRAGMA
user_version`, owned by the `FinanceBackend` facade. It stores members and
users, expense/income/investment entries, investment holdings, hashed
passwords (PBKDF2), and opaque session tokens. Dates are ISO in the database;
fy defaults to April and the session TTL to 10 minutes — both tunable in
`settings`. The importer (`impex.py`, arriving in the next phase) is the bulk
writer; the dashboard reads and edits it.

## Files tracked in git

| File | Purpose |
|---|---|
| `common.py` | shared helpers |
| `rules.py` | rule language (Rule / RuleSet) for `rules.txt`, `auto-rules.csv`, `manual-rules.csv` |
| `auto-rules.csv` | LLM/generated rules (seeded from `rules.txt`, appended in auto mode) |
| `classify.py` | legacy CSV classifier (retired when `impex.py` lands) |
| `scripts/seed_auto_rules.py` | regenerates `auto-rules.csv` from `rules.txt` (equivalence-checked) |
| `review.py` | rules.txt suggestions from the latest `review-<N>.csv` |
| `app.py` | streamlit dashboard (view-only) |
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

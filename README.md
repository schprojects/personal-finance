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
transaction via a keyword `RULES` table (first-match-wins) plus manual
overrides (`manual.csv`), and writes numbered, non-destructive runs:

| File | Purpose |
|---|---|
| `output/classified-<N>.csv` | all classified rows (all members) |
| `output/review-<N>.csv` | rows needing a human ruling |
| `output/<FY>/classified-<N>.csv` | per-FY slices |
| `repo/classified-main-<N>.csv` | consolidated inventory (what the app reads) |

`N` = 1 + max run number already in `output/`, so re-runs never clobber old
history. `data/` is never written to.

A row in `review-<N>.csv` exits review after a human adds a rule or
`manual.csv` entry for that payee, then re-runs the classifier.

### Dashboard (`app.py`)

Reads the latest `repo/classified-main-*.csv` on every render. Sidebar
filters (FY, category, kind, max amount) and a "Daily-spend baseline" toggle
apply across five sections:

| Section | What it shows |
|---|---|
| **Overview** | net spend, per-year average, top category, unreviewed count; focus-year monthly trend vs prior FY; top-8 category donut |
| **Transactions** | full classified table: search, kind/category filter, review-only toggle, ₹-formatted |
| **Categories** | per-category monthly trend across FYs + top payees within it |
| **Review** | unresolved rows grouped by payee (count + ₹), with per-payee drill |
| **Holdings** | household net worth from authored investment sheets; MF scheme tables (units, cost, latest NAV, absolute return %); label/value asset tables; per-member donut |

Holdings parses two auto-detected sheet shapes:
- **MF detail** (has `scheme name` + `market value` columns) -- per-scheme table
  with cost value, units, latest NAV + date, and return %
- **Label/value** (everything else) -- "category, ₹" rows with optional
  stated-Total cross-check

Purchase dates are not yet available for MF entries; only current value is
shown. When purchase dates appear in `[MF-PORTFOLIO].csv`, XIRR can be added in
`holdings.py` with no app changes.

### Shared logic (`common.py`)

`fy_of`, `inr`, `money`, `payee_key`, `load_members`, `statements_for`,
`investments_for`, `BASELINE_EXCLUDED` -- imported by both `classify.py` and
`app.py`.

### Tests (`tests/`)

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Covers: `common` helpers, classifier rules / reconciliation / numbered outputs,
holdings sheet parsing and return calculation.

## Files tracked in git

| File | Purpose |
|---|---|
| `common.py` | shared helpers |
| `classify.py` | classifier (the only writer) |
| `app.py` | streamlit dashboard (view-only) |
| `holdings.py` | investment sheet parser |
| `tests/` | unit tests |
| `specs/` | project contracts |
| `.gitignore` | excludes `.venv/`, `data/`, `output/`, `repo/`, `members.yaml` |

## Out of scope (for now)

- YNAB-style budgeting (no budget data exists)
- Database backend (plain CSVs are the truth by spec)
- Historic/seasonal MF tracking
- PDF export (kaleido via plotly `fig.write_image()` is a drop-in)
- Calendar heatmap, monthly per-category drill tables

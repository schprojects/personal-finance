# Personal Finance

Family expense and net-worth dashboard — Simplifi / Mint style, backed by an
sqlite database (see **Backend**) that the importer feeds and members edit
(classify UNKNOWN rows, refresh holding values) with password login.

## Quick start

```sh
# one-time setup
python3 -m venv .venv
.venv/bin/pip install streamlit plotly pyyaml

# bootstrap a member password (each member does this once)
.venv/bin/python impex.py auth set-password --user <member>

# import bank statements (existing/auto rules) and investment sheets
.venv/bin/python impex.py import --user <member> --type bank-statement --mode existing
.venv/bin/python impex.py import --user <member> --type investments
# (add --mode auto to also consult OpenRouter and grow auto-rules.csv)

# run the dashboard -- sign in with your member password
.venv/bin/streamlit run personal-finance.py
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

> New to Python or web frameworks? Start with **[diagrams.md](diagrams.md)** —
> a visual on-ramp to the codebase (system context, web primer, code map,
> sequence flows).

### Members

`members.yaml` maps each member name to their data directory:

```yaml
members:
  - name: <member>
    data_dir: data/<member>
  - name: <member-2>
    data_dir: data/<member-2>
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
impex.py auth set-password --user <member>

# import, classified by the rules files
impex.py import --user <member> --type bank-statement --mode existing
impex.py import --user <member> --type bank-statement --mode auto   # + OpenRouter
impex.py import --user <member>    --type investments

# read path: CSV exports, whole family by default (--user to narrow)
impex.py export --login <member> --type bank --fy 2024-25 --out bank.csv
impex.py export --login <member> --type investments

# safety net
impex.py backup                 # -> backup/networth-<ts>.db
impex.py restore --from backup/networth-<ts>.db
```

Password sources: prompt, `--password-file PATH`, or `--password-stdin`
(saved only in `backend/`, never argv/environ). A wrong password exits non-zero
before anything is touched. Every import appends a run log under `var/logs/`.

### Deployable bundle (`dist/`)

```sh
.venv/bin/python scripts/make_dist.py                # fresh bundle -> dist/ + dist.zip
.venv/bin/python scripts/make_dist.py --with-db var/networth.db  # seed with a database
```

`dist/` ships the app + importer + rule engine + `requirements.txt` + this
README, minus runtime data and secrets; running `streamlit run
personal-finance.py` inside `dist/` works as here. `--with-db <path>` copies an
sqlite db to `dist/var/networth.db`, which is exactly where the app and
`impex.py` look by default, so the bundle starts on that data with no extra
configuration. Both `dist/` and `dist.zip` are gitignored build
artifacts.

Rules (`--mode`): `existing` = manual-rules.csv (if present) then auto-rules.csv;
`manual` / `auto` restrict to one file; `both` is identical to `existing`.
First match wins. Rules can be direction-scoped with `@dir:dep` (credits) or
`@dir:wd` (debits). No match → default-kind fallback: credit = income,
debit = expense, category `UNKNOWN` (editable in the dashboard); investment
buys come from the `@dir:wd` rules in your local rule files.
CSV rows that already carry a `KIND`/`CATEGORY` (e.g. an `impex.py export`
file re-imported after edits) are used **as-is** — no rules, no LLM, no
default-kind fallback, and no deposit→credit rewrite. Income,
specified or inferred, always lands on its default category.
`auto` mode additionally classifies never-seen payees via OpenRouter
(`OPENROUTER_API_KEY`, `OPENROUTER_MODEL` default `openrouter/auto`) and
appends the suggested keyword rules to `auto-rules.csv` (created on first
append, deduped) — so a fresh clone with the key set bootstraps its own rules
and never requires a committed rule file.

Import semantics: rows are keyed by account + date + particulars + deposit +
withdrawal — created when new, updated when the classification changed (never
for rows you manually overrode), superseded (skipped) when identical. Statement
blocks must reconcile first (`opening + Σdep − Σwd == closing`); a FAILING
block no longer stops the run — its rows go to `output/<FY>/failed-<N>.csv`
(complete rows, deduped across runs) and the failure is logged.

`finance/classify.py`'s write path, `repo/`, `review-*.csv`, `rules.txt`, and
`manual.csv` are retired. `data/` stays the import source; `auto-rules.csv`,
`manual-rules.csv`
and `rules.txt` are all gitignored — the rule files are personal-local, like
`members.yaml` and the database.

### Dashboard (`personal-finance.py`)

Password-login pages (see **Backend** for the session store). Reads `entries` /
`investments` from the database on every render; a sidebar "Family / me" radio
scopes reads. The **family view is read-only**; the Me view adds edit forms for
the member's own rows. Sidebar filters (FY, category, kind,
mode, max amount) and a "Daily-spend baseline" toggle apply across five
sections:

| Section | What it shows |
|---|---|
| **Overview** | net spend, savings rate, income vs spend per FY, top category, unreviewed count; focus-year monthly trend vs prior FY + cumulative spend line; top-8 category donut. Incoming / net spend / savings rate aggregate over the current scope and selected FYs (income and credit always fund the Incoming card, even though the Kinds filter defaults to spend kinds — bug-fix-001) |
| **Transactions** | full classified table: search, kind/category filter, UNKNOWN-row toggle, ₹-formatted; Me view edits Kind/Category per row via dropdowns, or bulk-applies one kind/category to the rows ticked in the Select column (or all shown) after a confirm checkbox |
| **Categories** | per-category monthly trend across FYs + top payees within it |
| **Review** | UNKNOWN rows grouped by payee (count + ₹), per-payee drill; Me view edits Kind/Category per row via dropdowns, or bulk-applies one kind/category to the rows ticked in the Select column (or all shown) after a confirm checkbox |
| **Holdings** | household net worth from the investments table; MF schemes (units, cost, latest NAV, absolute return % **+ XIRR**); cash/asset rows; per-member donut + largest-schemes bar; a Me-view-only edit form to refresh the latest value + as-of date of an owned holding |

Edits commit `manual_override`, so a later re-import never touches them.
To see **XIRR (annualized)** for a scheme, its MF sheet must author a
`Purchase Date` column (`dd-mm-yyyy`, e.g. in
   `data/<member>/investments/mfs-<member>.csv`): the app values cost at that date vs
market at the latest NAV. No column, no XIRR — the cell shows `—`.

### Shared logic (`finance/common.py`)

`fy_of`, `inr`, `money`, `payee_key`, `load_members`, `statements_for`,
`investments_for`, `BASELINE_EXCLUDED` — shared by the importer and the
dashboard, living in the `finance/` package next to the rest of the domain
modules (`rules`, `holdings`, `llm`, `classify`).

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
family/me scope, login gate; `tests/test_dashboard_values.py` — value-level
assertions over a clean two-member, two-FY sample: exact Overview figures per
scope/FY, row counts, category totals/shares, and Family-view read-only
behaviour, see bug-fix-001.md).

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

To use a specific database (any copy you keep elsewhere, or a backup),
export its path before running the app or any `impex.py` subcommand —
both then read and write that file instead of `var/networth.db`:

```sh
export FINANCE_DB=/path/to/networth.db
.venv/bin/streamlit run personal-finance.py
```

## Files tracked in git

| File | Purpose |
|---|---|
| `finance/common.py` | shared helpers |
| `finance/rules.py` | rule language (Rule / RuleSet) for `rules.txt`, `auto-rules.csv`, `manual-rules.csv` |
| `finance/classify.py` | statement parser + reconciliation (parsing reused by impex) |
| `finance/holdings.py` | investment sheet parser + XIRR |
| `finance/llm.py` | OpenRouter auto-mode client (stdlib-only) |
| `requirements.txt` | pip deps for the dist bundle (streamlit, plotly, pyyaml) |
| `scripts/seed_auto_rules.py` | regenerates local `auto-rules.csv` from `rules.txt` (equivalence-checked) |
| `scripts/make_dist.py` | assembles `dist/` + `dist.zip` |
| `review.py` | rules.txt suggestions from the latest `review-<N>.csv` |
| `personal-finance.py` | streamlit dashboard (DB-backed, cookie sessions, owner edits) |
| `impex.py` | gated importer/exporter CLI (bank → entries, sheets → investments, export/backup/restore) |
| `backend/` | sqlite system of record (schema, model, auth) |
| `tests/` | unit tests |
| `specs/` | project contracts |
| `diagrams.md` | beginner-friendly visual on-ramp (system context, code map, sequence flows) |
| `.gitignore` | excludes `.venv/`, `data/`, `output/`, `repo/`, `var/`, `backup/`, `dist/`, `members.yaml`, `rules.txt`, `manual-rules.csv`, `auto-rules.csv`, `review_Upis.txt` |

## Out of scope (for now)

- YNAB-style budgeting (no budget data exists)
- Historic/seasonal MF tracking
- PDF export (kaleido via plotly `fig.write_image()` is a drop-in)
- Calendar heatmap, monthly per-category drill tables

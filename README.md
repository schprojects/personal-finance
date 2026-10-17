# Personal Finance

Family expense and net-worth dashboard — Simplifi / Mint style, backed by an
sqlite database (see **Backend**) that the importer feeds and members edit
(classify UNKNOWN rows, refresh holding values) with password login.

## Quick start

```sh
# one-time setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# create your two local files first (both are gitignored, so a fresh clone has
# neither): members.yaml (see **Members**) and, for Import rules, auto-rules.csv
cp members.example.yaml members.yaml          # then edit the member names
cp auto-rules.example.csv auto-rules.csv      # optional, see *Writing auto-rules*

# bootstrap a member password (each member does this once)
.venv/bin/python impex.py auth set-password --user <member>

# import bank statements and investment sheets (entries are assumed
# already classified; see Importer / exporter)
.venv/bin/python impex.py import --user <member> --type bank-statement
.venv/bin/python impex.py import --user <member> --type investments

# run the dashboard -- sign in with your member password
.venv/bin/streamlit run personal-finance.py
```

`impex.py` `import`/`export` ask for that member's password (or `--password-file
PATH` / `--password-stdin`); a wrong password exits non-zero before anything is
read or written. (`auth` sets passwords; `backup`/`restore` are unauthenticated
by design.) The database lives at `var/networth.db` (`FINANCE_DB` to
override), and every import writes a run log under `var/logs/`.

Open the URL streamlit prints (default `http://localhost:8501`).

## Run with Docker

No Python setup needed: the image holds only the app code and its
requirements. **Personal data is never baked in** — you supply it from the host
and it stays there (`.dockerignore` keeps it out of the build).

```sh
# 1. supply your data next to docker-compose.yml (same layout as above)
mkdir -p var data output backup          # create them yourself: docker would
                                         # otherwise create root-owned ones
cp members.example.yaml members.yaml     # then edit the names; see **Members**
cp auto-rules.example.csv auto-rules.csv  # your Import rules, see *Writing auto-rules*
# data/<member>/bank-statements/<FY>/*.csv, data/<member>/investments/*.csv

# 2. build once, bootstrap a password (compose runs as your UID:GID)
docker compose build
echo 'my-password' | docker compose run --rm -T impex auth set-password --user <member> --password-stdin

# 3. import (same flags as the CLI; --password-stdin since there is no TTY, or
#    drop -T and use `docker compose run --rm impex ...` to be prompted)
echo 'my-password' | docker compose run --rm -T impex import --user <member> --type bank-statement --password-stdin
echo 'my-password' | docker compose run --rm -T impex import --user <member> --type investments --password-stdin

# 4. dashboard on http://localhost:8501
docker compose up -d
docker compose logs -f app               # optional
docker compose down                      # stop; var/networth.db stays on the host

# export / backup / restore work the same way
echo 'my-password' | docker compose run --rm -T impex export --login <member> --type bank --out /app/output/bank.csv --password-stdin
docker compose run --rm impex backup     # -> ./backup/networth-<ts>.db
```

`./var` (database, logs), `./data`, `./output`, `./backup` and `./members.yaml`
(read-only) are bind-mounted, so the database survives rebuilds and `docker
compose down`. Seeding works unchanged: `scripts/make_dist.py --with-db
var/networth.db` puts the database at `dist/var/networth.db`, which `docker
compose up` in the unzipped `dist/` mounts as-is. If your host UID is not 1000,
`export UID GID` first (or the mounted folders will not be writable). The
`impex` service sits behind the `tools` profile so `docker compose up` starts
only the dashboard.

**Create `members.yaml` and `auto-rules.csv` before the first `docker compose`
command.** If a bind-mounted file is missing, Docker creates a *folder* with
that name instead; the app then ignores the rules and `impex` stops with a
"members.yaml not found" message. Delete the folder and copy the example file.

## Deploy the course demo for free

Before using either host, commit and push `demo/demo.db` and `demo/README.md`
to the GitHub repository. The database contains synthetic data and
intentionally public credentials. It is safe only for a public course
demo—never add personal or confidential data.

Both services start from the files committed to GitHub. Changes that visitors
make to the hosted SQLite database remain only in that running instance; they
are not committed back to GitHub. A restart, sleep, or redeployment may restore
the checked-in seed and discard those changes.

### Streamlit Community Cloud (recommended)

[Streamlit Community Cloud](https://docs.streamlit.io/deploy/streamlit-community-cloud)
is the simplest option for this Streamlit course demo:

1. Sign in to the Community Cloud workspace and connect the GitHub account that
   can access the repository.
2. Click **Create app**, select the repository and branch, and set the entrypoint
   file to `personal-finance.py`.
3. Open **Advanced settings** and paste this root-level TOML entry into the
   **Secrets** field (do not place it under a TOML section):

   ```toml
   FINANCE_DB = "demo/demo.db"
   ```

4. Save the settings and deploy the app.

Root-level Streamlit secrets are exposed as environment variables, so the
existing `FINANCE_DB` setting works without an application change. See the
official guides for the [deployment workflow](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
and [Community Cloud secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management).

Sign in to the deployed app with either public demo account:

| Username | Password |
|---|---|
| `user-A` | `demo` |
| `user-B` | `demo` |

Community Cloud watches the selected GitHub branch and automatically updates
or redeploys the app after a push. Apps without traffic for 12 hours go to
sleep and wake when visited. Local SQLite storage is not guaranteed to persist,
so a reboot, redeployment, or platform cleanup may restore `demo/demo.db` from
GitHub. See Streamlit's documentation on [app management and sleep](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)
and [local-file persistence](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data#a-simple-starting-point---using-a-local-sqlite-database).

### Render Free Web Service

Use Render when the lesson should demonstrate a general-purpose web-service
deployment rather than a Streamlit-specific host:

1. In the Render dashboard, choose **New > Web Service**, connect GitHub if
   needed, and select this repository.
2. Select the **Free** instance type and use this build command:

   ```sh
   pip install -r requirements.txt
   ```

3. Use this start command:

   ```sh
   streamlit run personal-finance.py --server.address 0.0.0.0 --server.port $PORT --server.headless true
   ```

4. Add the environment variable `FINANCE_DB` with the value `demo/demo.db`,
   then create the web service.

Sign in with `user-A` / `demo` or `user-B` / `demo`. These credentials and all
seeded data are public; do not upload or enter personal or confidential data.

A free Render web service spins down after 15 minutes without an inbound HTTP
request or WebSocket message. A connected Streamlit session can therefore keep
it active by sending WebSocket messages. The next request or WebSocket
connection wakes a sleeping service, with a cold start of about one minute.
Render's filesystem is ephemeral, so changes to `demo/demo.db` are lost on a
sleep, restart, or redeploy. See Render's official [free-service documentation](https://render.com/docs/free)
for current limits.

For the shortest course setup, prefer Streamlit Community Cloud. Choose Render
when the deployment itself should illustrate a conventional web service.
Neither free option is suitable when database edits must persist. Durable
SQLite requires persistent storage on a paid service or a VM-based host.

## Writing auto-rules

The Import view classifies rows from `auto-rules.csv` in the project folder (the
standard path; in Docker it is bind-mounted read-only, so create it first —
`cp auto-rules.example.csv auto-rules.csv`). It is a plain text file anyone can
edit and share; nothing is stored in the database. A missing file
means no rules (the screen says where to put it). One rule per line:

```
KIND,CATEGORY,EXPRESSION
```

First matching rule wins, so put specific rules above general ones. A rule only
fills rows whose kind **and** category are empty (a value in the CSV, and later
your own edits, always win). Categories may be written with spaces or `&`
(`Loan & EMI` = `Loan_EMI`); leave the category empty for income. Lines that
cannot be read are skipped and listed on the Import screen.

| Rule line | Meaning |
|---|---|
| `expense,Groceries,SM MART` | particulars contain `SM MART` (any case) |
| `expense,Dining out,SWIGGY\|ZOMATO\|DOMINOS` | any one of the alternatives (`\|` = or) |
| `income,,ACH/ @dir:dep` | empty category (income uses the default); credits only |
| `expense,Cash withdrawals,ATM @dir:wd` | debits only (`@dir:dep` = credits, `@dir:wd` = debits) |
| `expense,Transport & fuel,SOME PAYEE @amt<=10000` | amount at most 10000 |
| `expense,Loan & EMI,SOME PAYEE @amt>10000` | amount above 10000 (operators `<` `<=` `>` `>=` `=`) |
| `expense,Shopping goods,AMAZON @dir:wd @amt>=500` | qualifiers combine; all must hold |
| `expense,Bank & DP charges,@mode:BANK CHARGES` | legacy `@mode:TEXT` is read as "particulars contain TEXT" (there is no payment mode any more) |
| `investment,,ZERODHA\|GROWW @dir:wd` | any kind from the kinds list (expense, income, credit, refund, transfer, investment, review, reimbursed, or one you created) |
| `transfer,,TO FAMILY @dir:wd` | a debited transfer is spend, category defaults to `Transfer` |
| `# a comment` and blank lines | ignored |

## Run the tests

```sh
.venv/bin/python -m unittest discover -s tests -v
RUN_DOCKER_TESTS=1 .venv/bin/python -m unittest tests.test_docker -v   # builds + runs the image (needs a docker daemon)
```

Business-logic coverage is mapped directly to the acceptance criteria in
[`tests/SPEC_COVERAGE.md`](tests/SPEC_COVERAGE.md). The matrix records enabled
failing assertions for any known specification violations; visual appearance
is deliberately outside this suite.

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
the CSV artifacts are reconcile failures and the exports (`impex.py export`, or the dashboard's Export view, which never overwrites an existing file).
The dashboard's **Import** view does the same bank-statement import interactively (preview, optional auto-rules, then Save); the dashboard has no save-to-CSV.

```
# bootstrap a member password (requires the current one once set)
impex.py auth set-password --user <member>

# import (KIND/CATEGORY used as specified; else credit=income, debit=expense/UNKNOWN)
impex.py import --user <member> --type bank-statement
impex.py import --user <member>    --type investments

# read path: CSV exports, whole family by default (--user to narrow)
impex.py export --login <member> --type bank --fy 2024-25 --out bank.csv  # --user <m> narrows to one member
impex.py export --login <member> --type investments

# safety net
impex.py backup                 # -> backup/networth-<ts>.db
impex.py restore --from backup/networth-<ts>.db
```

Password sources: prompt, `--password-file PATH`, or `--password-stdin`
(saved only in `backend/`, never argv/environ). A wrong password exits non-zero
before anything is touched. Failed attempts (importer and dashboard login) are
logged to `var/logs/auth.log` (attempted username only). Every import appends a run log under `var/logs/`.

### Deployable bundle (`dist/`)

```sh
.venv/bin/python scripts/make_dist.py                # fresh bundle -> dist/ + dist.zip
.venv/bin/python scripts/make_dist.py --with-db var/networth.db  # seed with a database
```

`dist/` ships the app + importer + `requirements.txt` + the container files
(`Dockerfile`, `docker-compose.yml`, `.dockerignore`) + this
README, minus runtime data and secrets; running `streamlit run
personal-finance.py` inside `dist/` works as here. `--with-db <path>` copies an
sqlite db to `dist/var/networth.db`, which is exactly where the app and
`impex.py` look by default, so the bundle starts on that data with no extra
configuration. Both `dist/` and `dist.zip` are gitignored build
artifacts.

Classification: the importer assumes entries are already classified. Rows
that carry a `KIND`/`CATEGORY` (e.g. an `impex.py export` file re-imported
after edits, or a hand-authored statement with those columns) are used **as
specified**, with no deposit→credit rewrite; dates may be
`dd-mm-yyyy` or `dd/mm/yyyy`. Where kind/category is unassigned (e.g. a raw
bank statement) credits become `income` and debits `expense` with category
`UNKNOWN` (editable in the dashboard). Income always lands on its default
(empty) category. `impex.py` has no rule files and no LLM step; auto-rules exist only in the dashboard's Import view and are read from `auto-rules.csv` in the project folder, never the database. A credited `transfer` is stored as `income`; a debited `transfer` is spend with the default category `Transfer` and known category misspellings (`Loan & EMO`, `Bank & DP Charges`, `nan`) are folded to canonical labels on import and on dashboard edit. Category labels never contain spaces or `&` — `Home & maintenance` is stored as `Home_maintenance`, whatever the CSV or dashboard input; opening an older database upgrades it automatically (schema v9: adds kinds and the 25 initial categories, the `Transfer` category, and drops the old `auto_rules` table — any stored rules are first appended to `auto-rules.csv`) — take `impex.py backup` first.

Import semantics: rows are keyed by account + date + particulars + deposit +
withdrawal — created when new, updated when the classification changed (never
for rows you manually overrode), superseded (skipped) when identical. Statement
blocks must reconcile first (`opening + Σdep − Σwd == closing`); a FAILING
block no longer stops the run — its rows go to `output/<FY>/failed-<N>.csv`
(complete rows; rows already in the database or a prior failed file are skipped) and the failure is logged.

`finance/classify.py`'s write path, `repo/`, `review-*.csv`, `rules.txt`, and
`manual.csv` are retired, as are `manual-rules.csv`, the OpenRouter client and `review.py`.
`data/` stays the import source.

### Dashboard (`personal-finance.py`)

Look and feel: a calm-fintech theme that follows your OS/browser
light or dark setting. Colours, corner radius and chart palette live in
`.streamlit/config.toml` (edit it to rebrand; Streamlit reads it from the folder
you start the app in); layout helpers and the CSS are in `ui_theme.py`. The login
banner text is the `banner` setting.

Password-login pages (see **Backend** for the session store). Reads `entries` /
`investments` from the database on every render. Layout follows
`ui-specs/Personal-finance.pdf`: a sidebar with the signed-in member, Sign out,
the section list and a **Scope** radio (Family | your name). The **family view
is read-only**; the Me view lets you edit your own rows. Sections:

| Section | What it shows |
|---|---|
| **Home** | hero banner, a latest-FY snapshot (net spend, savings rate, unclassified rows with a one-click review, net worth) and a card per section; an onboarding card when the database is empty |
| **Overview** | net spend (expense rows — credits on an expense reduce it — plus debited transfers, category `Transfer`), per year, top category, unclassified; **Incoming** = credited income / credit / refund / transfer; multi-year monthly line, category donut, incoming-vs-spend bars with savings rate, cumulative spend per selected year, "How is Net spend calculated?" expander; **big-ticket spends** of the focus year with Exclude checkboxes that drop them from every figure. Sidebar filters: years, categories, kinds, max amount, and the **daily-spend baseline** (leaves Income_tax, Vehicles, Loan_EMI and Bank_DP_charges out of the figures; the expander lists each category with its ₹ amount and whether it is excluded or would be excluded; small-misc stays in). The bank's MODE column is ignored on import and never stored or exported |
| **Categories** | per-category monthly trend + top payees; create a category and create a kind (Me) |
| **Transactions** | year(s) in focus; **plain-text filter or SQL query** (`where kind is empty and category is empty and particulars like '%x%'`; both at once is an error); row checkboxes + Select all; kind/category pickers + *Apply to selected*; table cell edits. Everything is **staged in memory**: nothing is written until **Save** (one batch); **Discard** drops it; edited rows stay visible until *Refresh view* |
| **Holdings** | net worth, MF schemes (units, cost, NAV, return %, XIRR), assets, allocation + largest schemes; type filter; Me view edits dates/NAV/values (never the identity columns) |
| **Import** | choose a file (found under your data folder or a typed full path), year, **Apply auto-rules** toggle, preview with *matched by rule* / *UNKNOWN* counts, **Save import to database** → opens Transactions. Precedence: CSV value → auto-rule → UNKNOWN/income default. Rules are read from `auto-rules.csv` in the project folder (see *Writing auto-rules*); the screen shows the path, the rule count and any skipped lines |
| **Export** | members, years, file path (default `output/export-<members>-<years>.csv`); an existing file is never overwritten — `-1`, `-2` … is appended |

Saved edits set `manual_override`, so a later re-import never touches them.
To see **XIRR (annualized)** for a scheme, its MF sheet must author a
`Purchase Date` column (`dd-mm-yyyy`, e.g. in
   `data/<member>/investments/mfs-<member>.csv`) and cost/market values: the app
values cost at that date vs market at the latest NAV. An optional `Purchase NAV`
column is stored too. The import keeps both purchase fields. Partial
history (missing date, cost or latest value) never errors: the cell shows `—`.

### Shared logic (`finance/common.py`)

`fy_of`, `inr`, `money`, `payee_key`, `load_members`, `statements_for`,
`investments_for`, `BASELINE_EXCLUDED` — shared by the importer and the
dashboard, living in the `finance/` package next to the rest of the domain
modules (`holdings`, `classify`, `rules`, `query`, `importing`, `exporting`).

### Tests (`tests/`)

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Covers: `common` helpers, the rule language and reconciliation math, holdings
sheet parsing and return calculation, the `backend/` database (migrations,
PBKDF2 passwords + session tokens, entry create/update/skip, UNKNOWN editing,
investment upsert), the `impex` CLI (import/upsert semantics,
reconcile-FAIL → `failed-<N>.csv` dedup, investment typing, export scope and
format, backup/restore round-trip, auth gate), and
the dashboard via streamlit's `AppTest` (`tests/test_app.py` — every page,
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
server-side; `Sign out` revokes the token; the token is revalidated on every rerun, so expiry or revocation ends the session at once.

To use a specific database (any copy you keep elsewhere, or a backup),
export its path before running the app or any `impex.py` subcommand —
both then read and write that file instead of `var/networth.db`:

```sh
export FINANCE_DB=/path/to/networth.db
.venv/bin/streamlit run personal-finance.py
```

To make a host checkout use your real database every time, put the line
`FINANCE_DB=my-data/networth.db` (relative to the project folder) in a
`.env` file next to `personal-finance.py` (gitignored, never copied into the
image or `dist/`). Order: `FINANCE_DB` environment variable, then `.env`, then
`var/networth.db`. Docker keeps using the compose `FINANCE_DB`.

## Files tracked in git

| File | Purpose |
|---|---|
| `finance/common.py` | shared helpers |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | container deployment: app + on-demand `impex` service, data bind-mounted |
| `finance/classify.py` | statement parser + reconciliation (parsing reused by impex) |
| `finance/holdings.py` | investment sheet parser + XIRR |
| `finance/rules.py`, `finance/query.py` | auto-rules (`auto-rules.csv` parser + matcher) and the read-only SQL WHERE filter for Transactions |
| `finance/importing.py`, `finance/exporting.py` | staged import (stage → resolve → commit) and CSV export with numeric suffix, shared by the dashboard and `impex.py` |
| `ui-specs/Personal-finance.pdf` | hand-drawn UI layout the dashboard follows |
| `LICENSE` | 0BSD, free use with no attribution |
| `requirements.txt` | pinned pip deps (streamlit, plotly, pyyaml), the versions the tests run against; used by the Docker image and the dist bundle |
| `scripts/make_dist.py` | assembles `dist/` + `dist.zip` |
| `personal-finance.py` | streamlit dashboard (DB-backed, cookie sessions, owner edits) |
| `ui_theme.py`, `.streamlit/config.toml` | visual design system: CSS, layout helpers, Plotly style, light/dark theme |
| `impex.py` | gated importer/exporter CLI (bank → entries, sheets → investments, export/backup/restore) |
| `backend/` | sqlite system of record (schema, model, auth) |
| `tests/` | unit tests |
| `specs/` | project contracts |
| `diagrams.md` | beginner-friendly visual on-ramp (system context, code map, sequence flows) |
| `.gitignore` | excludes `.venv/`, `__pycache__/`, `data/`, `output/`, `repo/`, `var/`, `backup/`, `dist/`, `dist.zip`, `[DB-FOLDER]/`, `members.yaml`, `rules.txt`, `manual-rules.csv`, `auto-rules.csv`, `review_Upis.txt` |

## Out of scope (for now)

- YNAB-style budgeting (no budget data exists)
- Historic/seasonal MF tracking
- PDF export (kaleido via plotly `fig.write_image()` is a drop-in)
- Calendar heatmap, monthly per-category drill tables

## License

0BSD (BSD Zero Clause): use, change and share this software freely, for any purpose,
with no attribution required. See `LICENSE`.

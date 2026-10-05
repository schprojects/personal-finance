# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Family expense / net-worth dashboard (streamlit) backed by sqlite, fed by an importer CLI. `README.md` is the runbook, `diagrams.md` a beginner on-ramp.

## Commands

Always use the venv (`.venv/`); deps are `streamlit plotly pyyaml` (`requirements.txt`).

```sh
.venv/bin/python -m unittest discover -s tests -v                 # all tests
.venv/bin/python -m unittest tests.test_impex -v                  # one module
.venv/bin/python -m unittest tests.test_impex.<Class>.<test> -v   # one test
.venv/bin/streamlit run personal-finance.py                       # dashboard
.venv/bin/python impex.py <auth|import|export|backup|restore> ... # importer/exporter CLI
.venv/bin/python scripts/make_dist.py [--with-db var/networth.db] # build dist/ + dist.zip
```

`members.yaml` (gitignored, personal) must exist before `impex.py` auth/import work. `FINANCE_DB` overrides the db path (default `var/networth.db`).

## Architecture

- **Database is the truth.** `backend/` (`FinanceBackend` facade in `model.py`, `schema.py` append-only migrations via `PRAGMA user_version`, `auth.py` PBKDF2 passwords + opaque session tokens, `db.py`, `paths.py`) is the only writer. `output/` holds only per-FY `failed-<N>.csv` reconcile failures.
- **Two entry points, one data path.** `impex.py` is the auth-gated bulk importer/exporter (bank statements → `entries`, investment sheets → `investments`); `personal-finance.py` (single-file streamlit app: Overview / Transactions / Categories / Review / Holdings) reads the db and lets a member edit only their own rows in the "Me" scope (Family scope is read-only). Dashboard sessions use a `stmtok` cookie bridged via `st.html` + JS.
- **`finance/` domain package**: `common` (FY, money, payee_key, members), `classify` (statement parser + reconcile `opening + Σdep − Σwd == closing`), `holdings` (investment sheet parser + XIRR). There are no rule files or LLM.
- **Import semantics**: rows keyed by account+date+particulars+deposit+withdrawal; created / updated (classification changed) / skipped (identical); rows with `manual_override` are never touched on re-import. Dashboard edits set `manual_override`. The importer assumes entries are classified: CSV KIND/CATEGORY are used as specified, otherwise credit→income, debit→expense/UNKNOWN; income always lands on its default category.
- Personal data is gitignored: `data/`, `var/`, `output/`, `members.yaml`, `dist/`, `dist.zip`.

## Project rules (from `specs/AGENTS.md`)

- Every change must update `README.md`; the app must run exactly as the README says.
- Keep tests up to date with changes.
- Keep code simple: OOAD / clean architecture, stdlib-first.
- Never commit data or secrets unless explicitly asked. `specs/` holds the project contracts (`SPEC.md`, per-area specs) — consult before changing behavior.

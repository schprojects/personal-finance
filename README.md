# Expense Tracker

View-only dashboard for the classified bank-statement expenses, shaped like a
market-leading tracker (Simplifi / Mint style). The CSVs written by
`classify.py` are the source of truth; the app reads them fresh on every render
and never writes or edits anything.

## Run

```sh
# one-time setup (isolated environment)
python3 -m venv .venv
.venv/bin/pip install streamlit plotly

# run the dashboard
.venv/bin/streamlit run app.py
```

Open the URL streamlit prints (default `http://localhost:8501`).

## Requirements

- Python 3 (tested on 3.14; the app itself is stdlib-only)
- Data already classified — from the project root run:

  ```sh
  python3 classify.py
  ```

  This writes `classified.csv`, `review_me.csv`, and `expense_summary.csv` in
  each year folder, plus `expense_summary_all_years.csv` at the root.

## Sections (sidebar)

- **Overview** — net spend and per-year averages, top category, unreviewed
  amount; monthly spend line for a focus year vs the prior year; top-8
  categories donut.
- **Transactions** — every classified row: free-text search (particulars /
  payee / category), filters, review-only toggle, sortable ₹-formatted table.
- **Categories** — pick a category: its monthly trend across years, top payees
  within it.
- **Review** — unresolved rows grouped by payee (rows, ₹), with per-payee drill.
- **Holdings** — household net worth from the asset CSVs (`sch-*` = husband,
  `mch-*` = spouse): per-member totals + tables, one asset-class donut. Values
  shown exactly as authored; warns if an asset sheet's stated Total disagrees
  with its rows.

Shared sidebar filters — financial years, categories, row kinds, max single
amount — apply to every section. **"Daily-spend baseline"** hides
tax / loans / cars / bank-charges / misc to show day-to-day spending only.

## Files

| File | Purpose |
|---|---|
| `app.py` | the dashboard (run with streamlit) |
| `classify.py` | the classifier — the only data-entry tool |
| `*/classified.csv` | classified transactions (truth, per year) |
| `*/review_me.csv` | rows the classifier couldn't confidently classify |
| `expense_summary_all_years.csv` | cross-year rollup written by classify.py |
| `[MF-PORTFOLIO].csv` | husband's mutual-fund holdings (market value per scheme) |
| `[SHARES-PORTFOLIO]-gold.csv` | husband's other assets / claims (label, ₹) |
| `[ALL-INVESTMENTS].csv` | spouse's net assets (category, ₹) — detail files (`mch-*.csv`) drop in later |

## Export to PDF (later)

Plotly figures can be exported via `fig.write_image()` / `kaleido` without
touching the app's data path. Not wired up yet.
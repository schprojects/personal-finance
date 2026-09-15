# Bug-fix-001 — Overview metrics not scoped to user/FY; family view editable

Status: Fixed (2026-09-15)

## Symptom

On the Overview page the Incoming / Net-spend / Savings-rate cards showed wrong
values — most visibly Incoming read **₹0.00** and Savings rate read **"—"**
even though the scope had income, and the figures did not track the selected
financial years. Separately, the family view still allowed classifying Unknown
rows and updating holding values, contradicting the dashboard spec's "in the
family view nothing should be editable".

## Root cause

`page_overview` derived Incoming and the savings rate from
`incoming(filtered(rows, f))`, where `filtered()` restricts rows to the sidebar
"Kinds" multiselect. That multiselect **defaults to expense/refund/review**
(`personal-finance.py`), so income (`ACH/...`) and credit deposits were dropped
by default — Incoming read zero and the rate had no denominator. Selecting the
income kind by hand "fixed" it, which masked the bug as a per-FY scoping issue.
The edit forms were gated only by ownership, not by scope.

## Fix

1. **Overview funding is scope-scoped.** Incoming is computed with the same
   FY / mode / max-amount / category / user-scope filter as the view, but with
   the effective kinds widened to include `income` and `credit`:
   `kinds = f["kinds"] | {"income", "credit"}`. Net spend stays expense net of
   refunds; savings rate is the scope aggregate over the selected FYs (a single
   selected FY gives the exact per-year number). No other formula changed.
2. **Family view is read-only.** `main()` passes `editable = (who == "Me")` to
   `page_review` and `page_holdings`; the classify form (Review) and update
   form (Holdings) render only in the Me view, with a "Family view is
   read-only" caption in Family. Server-side owner checks are unchanged.
3. **Value-level tests.** `tests/test_dashboard_values.py` runs the dashboard
   through Streamlit's `AppTest` over a clean two-member, two-FY sample and
   asserts exact values.

## Expected values (sample fixture in tests/test_dashboard_values.py)

| Scope / FY              | Incoming      | Net spend | Per year | Top category | Savings rate |
|-------------------------|---------------|-----------|----------|--------------|--------------|
| Family, all FYs         | ₹3,70,000.00  | ₹8,500.00 | ₹4,250.00| Groceries ₹3,500.00 | 98%      |
| Me, all FYs             | ₹2,70,000.00  | ₹7,000.00 | ₹3,500.00| Clothing ₹3,000.00  | 97%      |
| Family, FY 2025-26 only | ₹1,60,000.00  | ₹2,500.00 | ₹2,500.00| Groceries ₹2,500.00 | 98%      |

Transaction row counts: Family 6 / Me 5 with default kinds, Family 12 / Me 9
with income+transfer included. Categories "Groceries": Total ₹6,000.00
/ 70.6% share (Family), ₹4,500.00 / 64.3% (Me). Holdings net worth: ₹8,300.00
(Family), ₹6,100.00 (Me).

## Verification

- `python -m unittest discover -s tests` → 86 passing (13 new value-level
  tests covering every section, both scopes, FY scoping, and the edit-form
  gating).
- Real database (`[DB-FOLDER]/networth.db`, [N] entries): default-login
  Overview now reads Incoming ₹[AMOUNT].69 (previously ₹0.00), savings rate
  -88% scope-aggregate; Family Review shows the read-only caption for both
  members; Me Review/Holdings still render the edit forms.
- Residue test database `var/networth.db` (dummy user, single entry) deleted.

## Files changed

- `personal-finance.py` — Overview `_f_inc` scoping; `editable` gating in
  `main` / `page_review` / `page_holdings`.
- `tests/test_dashboard_values.py` — new value-level suite.

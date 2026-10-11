"""Bank-statement import, shared by the Import view and `impex.py`.

Two steps so the dashboard can preview in memory and write only on Save:
  stage_statement(path)  -> parse + reconcile; no classification, no db
  resolve(rows, rules)   -> kind/category precedence
  commit_rows(backend)   -> the only step that writes
Precedence: value from the CSV  >  auto-rule (only when kind AND category are
empty)  >  default (credit -> income, debit -> expense/UNKNOWN). A user's later
edit in Transactions sets manual_override and beats all of these.
"""
import os

from backend.model import default_category, settle_kind
from .classify import parse_blocks, reconcile
from .rules import suggest


def iso(date_s):
    """dd-mm-yyyy / yyyy-mm-dd / dd-Mon-yyyy / dd/mm/yyyy -> ISO date."""
    import datetime as dt
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime((date_s or "").strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return (date_s or "").strip()


def stage_statement(path):
    """Parse one statement/carrier CSV. Returns (rows, failed, log).

    rows: reconciled transactions as dicts (account, date ISO,
    particulars, deposit, withdrawal, balance, kind, category, file) where kind
    and category are exactly what the CSV carried ('' when absent).
    failed: [(account, [raw tx])] blocks whose reconcile check failed."""
    rows, failed, log = [], [], []
    base = os.path.basename(path)
    for acct, txs in parse_blocks(path):
        carrier = bool(txs and txs[0].get("_carrier"))
        if carrier:
            ok, msg = True, "carrier csv (kind/category as-is)"
        else:
            _opening, ok, msg, txs = reconcile(acct, txs)
        log.append(f"[{acct}] {msg} {'PASS' if ok else 'FAIL'}")
        if not ok:
            failed.append((acct, txs))
            continue
        for t in txs:
            rows.append({
                "account": acct, "date": iso(t["date"]),
                "particulars": t["part"], "deposit": t["dep"],
                "withdrawal": t["wd"], "balance": t["bal"],
                "kind": t.get("kind", ""), "category": t.get("category", ""),
                "file": base})
    return rows, failed, log


def resolve(rows, rules=()):
    """Final kind/category per row. Returns new dicts with `rule` = True when
    an auto-rule supplied the values. Never overrides a CSV value."""
    out = []
    for r in rows:
        r = dict(r, rule=False)
        if not r["kind"] and not r["category"]:
            m = suggest(rules, r) if rules else None
            if m:
                r["kind"], r["category"], r["rule"] = m.kind, m.category, True
        if not r["kind"]:
            r["kind"] = "income" if r["deposit"] > 0 else "expense"
        # Same settling the database applies: a credit-only transfer is income;
        # income has only the default category; a transfer defaults to Transfer.
        kind, cat = settle_kind(r["kind"], r["category"], r["deposit"],
                                r["withdrawal"])
        r["kind"], r["category"] = kind, cat or default_category(kind)
        out.append(r)
    return out


def commit_rows(backend, owner, rows):
    """Write resolved rows; returns {created, updated, skipped, manual_override}."""
    counts = dict(created=0, updated=0, skipped=0, manual_override=0)
    for r in rows:
        action = backend.upsert_bank_entry(
            owner, account=r["account"], date=r["date"],
            particulars=r["particulars"], deposit=r["deposit"],
            withdrawal=r["withdrawal"], balance=r["balance"], kind=r["kind"],
            category=r["category"], file=r["file"])
        counts[action] += 1
    backend.con.commit()
    return counts

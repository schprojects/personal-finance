"""Statement parser + reconciliation (used by impex).

parse_blocks reads an ICICI statement CSV (or a columnar CSV that already
carries KIND/CATEGORY); reconcile verifies opening + deposits - withdrawals
== closing. Classification is not done here: the importer assumes entries
are already classified (importer-exporter-specs.md).
"""
import csv
import re

from .common import inr, money


def _canon(col):
    """Exporter/ICICI column name -> canonical key (DEPOSIT vs DEPOSITS)."""
    u = (col or "").strip().upper()
    if u == "DEPOSITS":
        u = "DEPOSIT"
    if u == "WITHDRAWALS":
        u = "WITHDRAWAL"
    return u


def _is_carrier_header(first):
    if not first:
        return False
    keys = {_canon(c) for c in first}
    return "DATE" in keys and "PARTICULARS" in keys


def _parse_carrier(rows):
    """Rows that already carry KIND/CATEGORY (exporter round-trip CSVs).
    One block per ACCOUNT value (first-seen order), so a multi-account export
    keeps each row's account; blank accounts share the "?" block."""
    idx = {}
    for i, c in enumerate(rows[0]):
        k = _canon(c)
        if k not in idx:
            idx[k] = i
    get = lambda k: rows[i][idx[k]] if k in idx and idx[k] < len(rows[i]) else ""  # noqa: E731
    blocks = {}
    for i, r in enumerate(rows[1:], start=1):
        if not any(c.strip() for c in r):
            continue
        date = get("DATE").strip()
        if not date:
            continue
        tx = {"date": date, "mode": get("MODE").strip(),
              "part": get("PARTICULARS").strip(), "dep": money(get("DEPOSIT")),
              "wd": money(get("WITHDRAWAL")), "bal": money(get("BALANCE")),
              "_carrier": True}
        if (get("KIND") or "").strip():
            tx["kind"] = get("KIND").strip().lower()
        if (get("CATEGORY") or "").strip():
            tx["category"] = get("CATEGORY").strip()
        blocks.setdefault(get("ACCOUNT").strip() or "?", []).append(tx)
    return list(blocks.items())


def parse_blocks(path):
    """Yield (account, [tx dicts]) blocks from one statement CSV.

    Also accepts columnar files that already carry KIND/CATEGORY (e.g. the
    exporter's own CSVs re-imported): those rows are tagged `_carrier` and the
    importer uses the given values verbatim instead of re-inferring.
    """
    with open(path, newline="") as f:
        rows = [r + [""] * (6 - len(r)) for r in csv.reader(f)]

    first = next((r for r in rows if any(c.strip() for c in r)), None)
    if _is_carrier_header(first):
        return _parse_carrier(rows)

    blocks = []
    i = 0
    n = len(rows)
    while i < n:
        cell = (rows[i][0] or "").strip()
        if "STATEMENT OF TRANSACTIONS" not in cell.upper():
            i += 1
            continue
        acct = re.search(r"SavingsNumber:\s*(\S+)", cell)
        acct = acct.group(1) if acct else "?"
        j = i + 1
        while j < n and not (rows[j][0] or "").strip():
            j += 1
        j += 1  # header row DATE,MODE,PARTICULARS,...
        txs = []
        while j < n:
            r = rows[j]
            first = (r[0] or "").strip()
            if not any(c.strip() for c in r):
                j += 1
                continue
            if first and re.match(r"^\d{2}-\d{2}-\d{4}$", first):
                txs.append({
                    "date": first,
                    "mode": (r[1] or "").strip(),
                    "part": (r[2] or "").strip(),
                    "dep": money(r[3]), "wd": money(r[4]), "bal": money(r[5]),
                })
                j += 1
            elif first == "":
                # continuation row: merge particulars, expect all-zero numbers
                dep, wd, bal = money(r[3]), money(r[4]), money(r[5])
                if txs and abs(dep) < 1e-9 and abs(wd) < 1e-9 and abs(bal) < 1e-9:
                    txs[-1]["part"] += " " + (r[2] or "").strip()
                j += 1
            else:
                if "STATEMENT OF TRANSACTIONS" in first.upper():
                    break
                j += 1
        blocks.append((acct, txs))
        i = j
    return blocks


def reconcile(acct, txs):
    """Verify every row was captured: opening + dep - wd == closing."""
    opening = None
    others = []
    for t in txs:
        if t["part"].upper().strip() == "B/F":
            opening = t["bal"]
        else:
            others.append(t)
    if opening is None:
        return None, False, "no B/F row found", others
    closing = others[-1]["bal"]
    calc = opening + sum(t["dep"] for t in others) - sum(t["wd"] for t in others)
    ok = abs(calc - closing) < 0.01
    return opening, ok, f"opening={inr(opening)} closing={inr(closing)} calc={inr(calc)}", others

"""CSV export of bank entries, shared by the Export view and `impex.py export`.

The database stays the truth; the exported CSV round-trips through the importer
(KIND/CATEGORY are carried). Existing files are never overwritten: a numeric
suffix is added instead (dashboard-specs: exports view).
"""
import csv
import datetime as dt
import os

EXPORT_COLS = ["MEMBER", "ACCOUNT", "DATE", "PARTICULARS", "DEPOSIT",
               "WITHDRAWAL", "BALANCE", "KIND", "CATEGORY", "FY"]


def _fmt(x):
    return "" if x is None else f"{x:.2f}"


def _ddmmyyyy(iso):
    try:
        return dt.date.fromisoformat(iso).strftime("%d-%m-%Y")
    except (TypeError, ValueError):
        return iso or ""


def entry_row(r):
    return [r["user_id"], r["account"], _ddmmyyyy(r["date"]),
            r["particulars"], _fmt(r["deposit"]), _fmt(r["withdrawal"]),
            _fmt(r["balance"]), r["kind"], r["category"], r["fy"]]


def write_entries(rows, out):
    """Write entry dicts as CSV to an open text file; returns the row count."""
    w = csv.writer(out)
    w.writerow(EXPORT_COLS)
    w.writerows(entry_row(r) for r in rows)
    return len(rows)


def default_export_path(members, fys, base="output"):
    """output/export-<members>-<years>.csv"""
    m = "+".join(sorted(members)) or "all"
    y = "+".join(sorted(fys)) or "all"
    return os.path.join(base, f"export-{m}-{y}.csv")


def unique_path(path):
    """`path` if free, else `stem-1.ext`, `stem-2.ext`, ..."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{stem}-{n}{ext}"):
        n += 1
    return f"{stem}-{n}{ext}"


def export_to_path(rows, path):
    """Write to `path` (parents created) without overwriting; returns the
    final path used and the row count."""
    final = unique_path(path)
    os.makedirs(os.path.dirname(final) or ".", exist_ok=True)
    with open(final, "w", newline="") as f:
        n = write_entries(rows, f)
    return final, n

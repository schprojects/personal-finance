#!/usr/bin/env python3
"""Parse members' investment sheets from data/<member>/investments/.

Two sheet shapes (auto-detected by header):
  - MF detail sheet (e.g. [MF-PORTFOLIO].csv): per-scheme folio rows with
    cost value, units, latest NAV + date and market value.
  - Label/value sheet (e.g. [SHARES-PORTFOLIO]-gold.csv, [ALL-INVESTMENTS].csv): "category, ₹"
    rows, with an optional stated "Total" row used for cross-checks.

Pure stdlib — no streamlit/plotly, so it is unit-testable on its own.
"""
import csv
import os

MF_KEYWORDS = ("scheme name", "unit balance", "market value")
MF_COLS = {
    "scheme": ("scheme name",),
    "folio": ("folio no",),
    "cost": ("cost value",),
    "units": ("unit balance",),
    "nav_date": ("nav date",),
    "nav": ("nav",),
    "market": ("market value",),
}
NAV_EXCLUDE = ("date",)  # "NAV Date" should not satisfy the "nav" value lookup


def _find_col(header, *keywords, exclude=()):
    for i, h in enumerate(header):
        hl = (h or "").strip().lower()
        if any(k in hl for k in keywords) and not any(x in hl for x in exclude):
            return i
    return None


def is_mf_sheet(header):
    f = {k: _find_col(header, *kw) for k, kw in MF_COLS.items()}
    return f["scheme"] is not None and f["market"] is not None


def absolute_return_pct(cost, market):
    """Total return on a holding, or None when not computable."""
    if cost is None or market is None or cost <= 0:
        return None
    return (market - cost) / cost * 100.0


def parse_mf_sheet(header, rows):
    c = {k: _find_col(header, *kw, exclude=NAV_EXCLUDE if k == "nav" else ())
         for k, kw in MF_COLS.items()}
    schemes, total = [], None
    for r in rows:
        if any((x or "").strip().lower() == "total" for x in r):
            total = _num(r[c["market"]]) if c["market"] is not None and c["market"] < len(r) else None
            continue
        scheme = (r[c["scheme"]] if c["scheme"] is not None else "").strip()
        if not scheme:
            continue
        def g(k):
            i = c[k]
            return _num(r[i]) if i is not None and i < len(r) else None
        cost = g("cost")
        market = g("market")
        schemes.append({
            "scheme": scheme,
            "folio": r[c["folio"]].strip() if c["folio"] is not None else "",
            "units": g("units"),
            "cost": cost,
            "nav_date": r[c["nav_date"]].strip() if c["nav_date"] is not None else "",
            "nav": g("nav"),
            "market": market,
            "ret_pct": absolute_return_pct(cost, market),
        })
    return schemes, total


def parse_label_rows(rows):
    """label/value rows with an optional stated Total."""
    assets, total = [], None
    for r in rows:
        if len(r) < 2:
            continue
        label = (r[0] or "").strip()
        if not label:
            continue
        val = _num(r[1])
        if val is None:
            continue
        if label.lower() == "total":
            total = val
            continue
        assets.append({"label": label, "value": val})
    return assets, total


def _num(s):
    s = ((s or "").strip().replace(",", "").replace('"', "") or "0")
    try:
        return float(s)
    except ValueError:
        return None


def parse_investments(members):
    """{'<member>': {'schemes': [...], 'assets': [...], 'total': float|None}}"""
    out = {}
    for m in members:
        name, root = m["name"], os.path.join(m["data_dir"], "investments")
        entry = {"schemes": [], "assets": [], "total": None}
        if not os.path.isdir(root):
            out[name] = entry
            continue
        for fn in sorted(os.listdir(root)):
            if not fn.lower().endswith(".csv"):
                continue
            path = os.path.join(root, fn)
            with open(path, newline="") as f:
                data = list(csv.reader(f))
            if not data:
                continue
            header = data[0] + [""] * 9
            if is_mf_sheet(header):
                schemes, total = parse_mf_sheet(header, data[1:])
                entry["schemes"] += schemes
                if total is not None:
                    entry["total"] = entry["total"] or 0.0
                    entry["total"] += total
            else:
                assets, total = parse_label_rows(data)
                entry["assets"] += assets
                if total is not None:
                    entry["total"] = entry["total"] or 0.0
                    entry["total"] += total
        out[name] = entry
    return out


def household_value(investments):
    """Named-asset total: sum of marked schemes + labeled assets."""
    total = 0.0
    for entry in investments.values():
        total += sum(s["market"] or s["cost"] or 0 for s in entry["schemes"])
        total += sum(a["value"] for a in entry["assets"])
    return total
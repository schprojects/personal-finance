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
import datetime as dt
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
    "purchase_date": ("purchase date",),
}
NAV_EXCLUDE = ("date", "purchase")  # "NAV Date"/"Purchase NAV" vs the NAV value


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


def xirr(flows, tol=1e-9, max_iter=200):
    """Annualized return (fraction) for [(date, amount), ...] using 365-day
    discounting. Bisection on NPV, which is monotone in r for r > -1. Returns
    None when the flows have no sign change (no valid rate)."""
    if len(flows) < 2 or all(a > 0 for _, a in flows) or all(a < 0 for _, a in flows):
        return None
    d0 = min(d for d, _ in flows)
    fs = [((d - d0).days, a) for d, a in flows]

    def npv(rate):
        return sum(a / (1 + rate) ** (days / 365.0) for days, a in fs)

    lo, hi = -0.9999, max(1.0, abs(npv(-0.9999)) * 2)
    if npv(lo) * npv(hi) > 0:
        for _ in range(20):
            lo *= 2  # widen; still fails if days==0 collapses
        if npv(lo) * npv(hi) > 0:
            return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        v = npv(mid)
        if abs(v) < tol:
            return mid
        if npv(lo) * v > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _day(s):
    for fmt in ("%d-%m-%Y", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def _purchase_xirr(cost, market, purchase_s, nav_s):
    """XIRR from one purchase + latest valuation. None when not authored."""
    if cost is None or market is None or not purchase_s or not nav_s:
        return None
    pd, nd = _day(purchase_s), _day(nav_s)
    if pd is None or nd is None or pd >= nd:
        return None
    return xirr([(pd, -cost), (nd, market)])


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
            "purchase_date": (r[c["purchase_date"]].strip()
                              if c["purchase_date"] is not None else ""),
            "xirr": _purchase_xirr(cost, market,
                                   r[c["purchase_date"]].strip()
                                   if c["purchase_date"] is not None else "",
                                   r[c["nav_date"]].strip()
                                   if c["nav_date"] is not None else ""),
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
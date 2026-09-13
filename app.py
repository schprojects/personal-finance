#!/usr/bin/env python3
"""Portfolio expense tracker — view-only, Simplifi/Mint-style.

Truth = per-folder classified.csv written by classify.py. The app reads them
fresh on every render and never writes, edits, or caches data.

Run:  .venv/bin/streamlit run app.py
"""
import csv
import datetime as dt
import glob
import os
import re

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASELINE_EXCLUDED = {"Income tax", "Vehicles", "Loan & EMI",
                     "Bank & DP charges", "Misc & small"}
PAGES = ("Overview", "Transactions", "Categories", "Review", "Holdings")


# --------------------------------------------------------------------------
# data plumbing
# --------------------------------------------------------------------------
def load_rows():
    rows = []
    for path in sorted(glob.glob("*/classified.csv")):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                d = dt.datetime.strptime(r["DATE"], "%d-%m-%Y").date()
                rows.append({
                    "file": r["FILE"], "date": d, "date_s": d.strftime("%d-%m-%Y"),
                    "mode": r["MODE"], "part": r["PARTICULARS"],
                    "dep": float(r["DEPOSIT"]), "wd": float(r["WITHDRAWAL"]),
                    "kind": r["KIND"], "category": r["CATEGORY"] or "—",
                    "fy": fy_of(d), "ym": d.strftime("%Y-%m"),
                    "payee": payee_key(r["PARTICULARS"]),
                })
    return rows


def fy_of(d):
    yy = d.year if d.month >= 4 else d.year - 1
    return f"{yy}-{str(yy + 1)[-2:]}"


def payee_key(p):
    if p.startswith("UPI/"):
        m = re.search(r"([A-Za-z0-9._@-]+@[A-Za-z]+)", p)
        return "UPI:" + (m.group(1).split("@")[0] if m else p.split("/")[1])
    if p.startswith(("VPS/", "IPS/", "VSI/")):
        return p.split("/")[0] + ":" + p.split("/")[1]
    if p.startswith("BIL/"):
        return "BIL:" + (p.split("/")[2] if len(p.split("/")) > 2 else "?")
    if p.startswith("MMT/"):
        m = re.search(r"([A-Za-z0-9._@-]+@[A-Za-z]+)", p)
        return "MMT:" + m.group(1).split("@")[0] if m else "MMT:" + p.split("/")[-1]
    if p.startswith(("NEFT-", "RTGS-")):
        return "BANK:" + p[:25]
    if p.startswith("VIN/") or p.startswith("GIB/"):
        return p.split("/")[0] + ":" + p.split("/")[1]
    return "OTHER:" + p[:24]


def inr(x):
    neg = x < 0
    s = f"{abs(x):,.2f}".split(".")
    head = re.sub(r"[^0-9,]", "", s[0]).lstrip("0") or "0"
    return ("-" if neg else "") + "₹" + head + "." + s[1]


def money(s):
    return float(str(s or "").replace(",", "").replace('"', "").strip()) if s else 0.0


def frame(rows):
    """Spend frame (expense net of refunds) keyed by (fy, category)."""
    agg = {}
    for r in rows:
        if r["kind"] == "expense":
            agg[(r["fy"], r["category"])] = agg.get((r["fy"], r["category"]), 0.0) + r["wd"]
        elif r["kind"] == "refund":
            k = (r["fy"], r["category"])
            agg[k] = agg.get(k, 0.0) - r["dep"]
    return agg


# --------------------------------------------------------------------------
# shared sidebar filters
# --------------------------------------------------------------------------
def sidebar_filters(rows):
    fis = sorted({r["fy"] for r in rows})
    cats = sorted({r["category"] for r in rows if r["kind"] in ("expense", "refund")})
    kinds = sorted({r["kind"] for r in rows})
    maxwd = int(max(r["wd"] for r in rows) or 1)
    with st.sidebar:
        st.caption("view-only · data via classify.py / CSVs")
        page = st.radio("Section", PAGES)
        fys = st.multiselect("Financial years", fis, key="fys",
                             default=list(fis))
        cats_raw = st.multiselect("Categories", cats, key="cats",
                                  default=list(cats))
        kinds = st.multiselect("Kinds", kinds, key="kinds",
                               default=["expense", "refund", "review"])
        maxamt = st.slider("Max single amount", 1, maxwd, maxwd, key="maxamt")
        baseline = st.checkbox("Daily-spend baseline (hide tax / loans / cars / misc)",
                               key="baseline")
    eff_cats = [c for c in cats_raw if not baseline or c not in BASELINE_EXCLUDED]
    return {
        "page": page, "fys": fys, "cats": set(eff_cats), "kinds": set(kinds),
        "maxamt": maxamt, "cats_raw": cats_raw,
    }


def filtered(rows, f):
    return [r for r in rows
            if r["fy"] in f["fys"] and r["kind"] in f["kinds"] and r["wd"] <= f["maxamt"]
            and (r["kind"] not in ("expense", "refund") or r["category"] in f["cats"])]


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
def page_overview(rows, f):
    spend = [r for r in filtered(rows, f) if r["kind"] in ("expense", "refund")]
    agg = frame(spend)
    net = sum(agg.values())
    rev = [r for r in filtered(rows, f) if r["kind"] == "review"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Net spend", inr(net))
    c2.metric("Per year", inr(net / len({k[0] for k in agg})) if agg else "₹0")
    top = max(agg.items(), key=lambda kv: kv[1]) if agg else None
    c3.metric("Top category", (top[0][1] if top else "—"),
              inr(top[1]) if top else "")
    c4.metric("Unreviewed", inr(sum(r["wd"] for r in rev)) + f"  ({len(rev)} rows)")

    fis = sorted({r["fy"] for r in rows})
    focus = st.pills("Focus year", fis, default=fis[-1], selection_mode="single")
    fys = [focus] + ([fis[fis.index(focus) - 1]] if focus != fis[0] else [])
    fig = go.Figure()
    for fy in fys:
        ys = {}
        for r in spend:
            if r["fy"] == fy and r["kind"] == "expense":
                ys[r["ym"]] = ys.get(r["ym"], 0.0) + r["wd"]
            elif r["fy"] == fy and r["kind"] == "refund":
                ys[r["ym"]] = ys.get(r["ym"], 0.0) - r["dep"]
        xs = sorted(ys)
        lbl = fy if fy == focus else f"{fy} (prior)"
        fig.add_trace(go.Scatter(x=xs, y=[ys[k] for k in xs], name=lbl,
                                 mode="lines+markers",
                                 line={"dash": "solid" if fy == focus else "dash"}))
    fig.update_layout(height=380, xaxis_title="Month", yaxis_title="₹",
                      yaxis_tickformat="~s", margin=dict(t=10))
    st.plotly_chart(fig, width="stretch")

    if agg:
        cats = sorted(agg, key=lambda k: -abs(agg[k]))[:8]
        fig = px.pie(names=[k[1] for k in cats], values=[abs(agg[k]) for k in cats],
                     hole=0.45)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=True)
        st.plotly_chart(fig, width="stretch")


def load_assets():
    """Member asset sheets. sch-* = husband, mch-* = spouse (CSV as truth)."""
    assets, mch_total = [], None
    if os.path.exists("[MF-PORTFOLIO].csv"):
        with open("[MF-PORTFOLIO].csv", newline="") as f:
            for r in csv.DictReader(f):
                if (r.get("Folio No.") or "").strip().lower() == "total":
                    continue
                assets.append({"member": "sch", "cat": "MF",
                               "value": money(r.get("Market Value (INR)"))})
    for path, member in (("[SHARES-PORTFOLIO]-gold.csv", "sch"), ("[ALL-INVESTMENTS].csv", "mch")):
        if not os.path.exists(path):
            continue
        with open(path, newline="") as f:
            for row in csv.reader(f):
                if len(row) < 2:
                    continue
                lbl, val = (row[0] or "").strip(), money(row[1])
                if not lbl:
                    continue
                if lbl.lower() == "total":
                    if member == "mch":
                        mch_total = val
                    continue
                assets.append({"member": member, "cat": lbl, "value": val})
    return assets, mch_total


def page_holdings():
    st.markdown("##### Holdings — as authored in the asset CSVs (view-only)")
    assets, mch_total = load_assets()
    if not assets:
        st.info("No asset CSVs (sch-*.csv / mch-*.csv) found.")
        return
    by_member = {"sch": 0.0, "mch": 0.0}
    for a in assets:
        by_member[a["member"]] += a["value"]
    total = sum(by_member.values())
    c1, c2, c3 = st.columns(3)
    c1.metric("Household net worth", inr(total))
    c2.metric("Husband (sch-*)", inr(by_member["sch"]))
    c3.metric("Spouse (mch-*)", inr(by_member["mch"]))
    if mch_total is not None and abs(mch_total - by_member["mch"]) > 0.5:
        st.error(f"[ALL-INVESTMENTS].csv Total ({inr(mch_total)}) ≠ Σ rows ({inr(by_member['mch'])})")

    agg = {}
    for a in assets:
        agg[(a["member"], a["cat"])] = agg.get((a["member"], a["cat"]), 0.0) + a["value"]
    for member, name in (("sch", "Husband"), ("mch", "Spouse")):
        rows = [{"Category": c, "Amount": inr(v)}
                for (m, c), v in sorted(agg.items(), key=lambda kv: -abs(kv[1]))
                if m == member]
        if rows:
            st.markdown(f"**{name}**")
            st.dataframe(rows, width="stretch", height=min(360, 35 * len(rows) + 40),
                         hide_index=True)

    fig = px.pie(names=[c for (_m, c), _v in agg.items()],
                 values=[v for _k, v in agg.items()], hole=0.45)
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=True)
    st.plotly_chart(fig, width="stretch")


def page_transactions(rows, f):
    q = st.text_input("Search particulars / payee / category", key="q")
    only_review = st.toggle("Review rows only", key="only_rev")
    rr = filtered(rows, f)
    if q:
        ql = q.lower()
        rr = [r for r in rr
              if ql in r["part"].lower() or ql in r["payee"].lower()
              or ql in r["category"].lower()]
    if only_review:
        rr = [r for r in rr if r["kind"] == "review"]
    rr.sort(key=lambda r: (r["date"], r["ym"]), reverse=True)
    tbl = [{"Date": r["date_s"], "Mode": r["mode"], "Category": r["category"],
            "Kind": r["kind"], "Particulars": r["part"][:120],
            "Debit": r["wd"] or None, "Credit": r["dep"] or None}
           for r in rr]
    st.caption(f"{len(rr)} rows")
    st.dataframe(
        tbl,
        width="stretch",
        height=560,
        hide_index=True,
        column_config={
            "Debit": st.column_config.NumberColumn(format="₹ %d"),
            "Credit": st.column_config.NumberColumn(format="₹ %d"),
        },
    )


def page_categories(rows, f):
    cats = sorted({r["category"] for r in filtered(rows, f) if r["kind"] == "expense"})
    if not cats:
        st.info("No categories in the current filter.")
        return
    pick = st.selectbox("Category", cats, key="pickcat")
    rs = [r for r in filtered(rows, f) if r["category"] == pick]
    agg = frame(rs)

    fig = go.Figure()
    for fy in sorted({k[0] for k in agg}):
        xs = sorted({r["ym"] for r in rs if r["fy"] == fy})
        ys = [agg.get((fy, m), 0.0) for m in xs]
        if any(abs(y) > 0.01 for y in ys):
            fig.add_trace(go.Scatter(x=xs, y=ys, name=fy, mode="lines+markers"))
    fig.update_layout(height=360, xaxis_title="Month", yaxis_title="₹",
                      yaxis_tickformat="~s", margin=dict(t=10))
    st.plotly_chart(fig, width="stretch")

    pa = {}
    for r in rs:
        if r["kind"] == "expense":
            pa[r["payee"]] = pa.get(r["payee"], 0.0) + r["wd"]
        elif r["kind"] == "refund":
            pa[r["payee"]] = pa.get(r["payee"], 0.0) - r["dep"]
    pa = {k: v for k, v in pa.items() if abs(v) > 0.01}
    c1, c2 = st.columns([1, 1])
    top = sorted(pa.items(), key=lambda kv: -abs(kv[1]))[:12]
    if top:
        fig = px.bar(x=[k for k, _ in top], y=[v for _, v in top],
                     labels={"x": "Payee", "y": "₹"})
        fig.update_layout(yaxis_tickformat="~s", margin=dict(t=10, b=40),
                          xaxis_tickangle=-30, height=360)
        c1.plotly_chart(fig, width="stretch")
    near = sorted(pa.items(), key=lambda kv: -abs(kv[1]))[:12]
    if near:
        c2.dataframe(
            [{"Payee": k, "Amount": inr(v)} for k, v in near],
            width="stretch", height=360, hide_index=True,
        )


def page_review(rows, f):
    rev = [r for r in filtered(rows, f) if r["kind"] == "review"]
    c1, c2 = st.columns(2)
    c1.metric("Rows", len(rev))
    c2.metric("Amount", inr(sum(r["wd"] for r in rev)))
    if not rev:
        st.info("Nothing in review for these filters.")
        return
    grp = {}
    for r in rev:
        a = grp.setdefault(r["payee"], [0, 0.0])
        a[0] += 1
        a[1] += r["wd"]
    grp = sorted(grp.items(), key=lambda kv: -kv[1][1])
    v = [x[1][1] for x in grp]
    fig = px.bar(x=[k for k, _ in grp][:40], y=v[:40],
                 labels={"x": "Payee", "y": "₹"})
    fig.update_layout(yaxis_tickformat="~s", margin=dict(t=10, b=40),
                      xaxis_tickangle=-30, height=360)
    st.plotly_chart(fig, width="stretch")
    pick = st.selectbox("Inspect payee", [k for k, _ in grp], key="revpick")
    rows_p = [r for r in rev if r["payee"] == pick]
    st.dataframe(
        [{"Date": r["date_s"], "Particulars": r["part"][:120],
          "Amount": r["wd"]} for r in rows_p],
        width="stretch", height=280, hide_index=True,
        column_config={"Amount": st.column_config.NumberColumn(format="₹ %d")},
    )


# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Expense tracker", layout="wide")
    st.title("Expense tracker")
    rows = load_rows()
    if not rows:
        st.warning("No classified.csv found. Run `python3 classify.py` first.")
        return
    f = sidebar_filters(rows)
    if f["page"] == "Overview":
        page_overview(rows, f)
    elif f["page"] == "Transactions":
        page_transactions(rows, f)
    elif f["page"] == "Categories":
        page_categories(rows, f)
    elif f["page"] == "Holdings":
        page_holdings()
    else:
        page_review(rows, f)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""Family expense & net-worth dashboard — view-only, Simplifi/Mint-style.

Truth = latest repo/classified-main-*.csv (written by classify.py) plus the
members' investment sheets under data/<member>/investments. The app reads them
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

from common import BASELINE_EXCLUDED, fy_of, inr, load_members, money, payee_key
from holdings import household_value, parse_investments, absolute_return_pct

PAGES = ("Overview", "Transactions", "Categories", "Review", "Holdings")


# --------------------------------------------------------------------------
# data plumbing
# --------------------------------------------------------------------------
def latest_inventory():
    """Latest repo/classified-main-*.csv path, or None."""
    files = sorted(glob.glob(os.path.join("repo", "classified-main-*.csv")))
    return files[-1] if files else None


def load_rows():
    path = latest_inventory()
    if not path:
        return []
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            d = dt.datetime.strptime(r["DATE"], "%d-%m-%Y").date()
            rows.append({
                "member": r["MEMBER"], "file": r["FILE"], "date": d,
                "date_s": d.strftime("%d-%m-%Y"),
                "mode": r["MODE"], "part": r["PARTICULARS"],
                "dep": float(r["DEPOSIT"]), "wd": float(r["WITHDRAWAL"]),
                "kind": r["KIND"], "category": r["CATEGORY"] or "—",
                "fy": fy_of(d), "ym": d.strftime("%Y-%m"),
                "payee": payee_key(r["PARTICULARS"]),
            })
    return rows


def load_assets():
    return parse_investments(load_members())


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


_INTERNAL = ("transfer", "investment", "refund", "reimbursed")


def incoming(rows):
    """Deposits that fund spending: income/credit, excluding internal moves."""
    return [r for r in rows if r["dep"] > 0 and r["kind"] not in _INTERNAL]


# --------------------------------------------------------------------------
# shared sidebar filters
# --------------------------------------------------------------------------
def sidebar_filters(rows):
    fis = sorted({r["fy"] for r in rows})
    cats = sorted({r["category"] for r in rows if r["kind"] in ("expense", "refund")})
    kinds = sorted({r["kind"] for r in rows})
    maxwd = int(max(r["wd"] for r in rows) or 1)
    with st.sidebar:
        st.caption("view-only · data via classify.py / authored CSVs")
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

    inc = incoming(filtered(rows, f))
    inc_fy, spend_fy = {}, {}
    for r in inc:
        inc_fy[r["fy"]] = inc_fy.get(r["fy"], 0.0) + r["dep"]
    for (fy, _), v in agg.items():
        spend_fy[fy] = spend_fy.get(fy, 0.0) + v
    fys = sorted(set(fy for (fy, _) in agg) | set(inc_fy))
    inc_t = sum(inc_fy.values())
    spend_t = sum(spend_fy.values())
    c1, c2 = st.columns([2, 1])
    if fys:
        fig = go.Figure()
        fig.add_bar(x=fys, y=[inc_fy.get(fy, 0.0) for fy in fys], name="Incoming")
        fig.add_bar(x=fys, y=[spend_fy.get(fy, 0.0) for fy in fys], name="Net spend")
        fig.update_layout(height=360, barmode="group", xaxis_title="Financial year",
                          yaxis_title="₹", yaxis_tickformat="~s", margin=dict(t=10))
        c1.plotly_chart(fig, width="stretch")
    c2.metric("Incoming", inr(inc_t))
    c2.metric("Net spend", inr(spend_t))
    c2.metric("Savings rate", f"{100 * (inc_t - spend_t) / inc_t:.0f}%"
              if inc_t > 0 else "—",
              delta=f"{inr(inc_t - spend_t)} saved")
    if focus:
        cum, run = {}, 0.0
        for r in sorted(spend, key=lambda x: x["ym"]):
            if r["fy"] != focus:
                continue
            run += r["wd"] - (r["dep"] if r["kind"] == "refund" else 0)
            cum[r["ym"]] = run
        xs = sorted(cum)
        fig = px.line(x=xs, y=[cum[k] for k in xs], markers=True,
                      title=f"Cumulative spend — {focus}")
        fig.update_layout(height=320, xaxis_title="Month", yaxis_title="₹",
                          yaxis_tickformat="~s", margin=dict(t=40))
        st.plotly_chart(fig, width="stretch")


def page_holdings(assets):
    st.markdown("##### Holdings — as authored in the members' investment CSVs (view-only)")
    if not assets:
        st.info("No investment CSVs found under any member's investments/.")
        return

    total = household_value(assets)
    by_member = {name: (
        sum(s["market"] or s["cost"] or 0 for s in e["schemes"])
        + sum(a["value"] for a in e["assets"])
    ) for name, e in assets.items()}
    c1, c2, c3 = st.columns(3)
    c1.metric("Household net worth", inr(total))
    st.caption("Net worth = marked schemes + authored asset rows (no debts tracked).")

    for name, entry in assets.items():
        st.markdown(f"**{name.title()}**")
        if entry["total"] is not None:
            rows_sum = sum(s["market"] or s["cost"] or 0 for s in entry["schemes"])
            rows_sum += sum(a["value"] for a in entry["assets"])
            if abs(rows_sum - entry["total"]) > 0.5:
                st.error(
                    f"{name}: sheets' stated Total ({inr(entry['total'])}) ≠ "
                    f"Σ rows ({inr(rows_sum)})")
        if entry["schemes"]:
            has_xirr = any(s["xirr"] is not None for s in entry["schemes"])
            st.caption(
                "Mutual funds / schemes (absolute return = latest vs cost; "
                "XIRR shown when the sheet authors a Purchase Date)" if has_xirr
                else "Mutual funds / schemes (absolute return = latest vs cost)")
            tbl = [{
                "Scheme": s["scheme"][:60], "Folio": s["folio"],
                "Units": (f"{s['units']:,.4f}" if s["units"] is not None else ""),
                "Latest NAV": (f"{s['nav']:,.4f} @ {s['nav_date']}"
                               if s["nav"] is not None else ""),
                "Value at cost": inr(s["cost"]) if s["cost"] is not None else "—",
                "Value latest": inr(s["market"]) if s["market"] is not None else "—",
                "Return": (f"{s['ret_pct']:+.1f}%" if s["ret_pct"] is not None else "—"),
                "XIRR (ann.)": (f"{s['xirr'] * 100:+.1f}%"
                                if s["xirr"] is not None else "—"),
            } for s in entry["schemes"]]
            st.dataframe(tbl, width="stretch", hide_index=True,
                         height=min(400, 35 * len(tbl) + 40))
        if entry["assets"]:
            st.dataframe(
                [{"Asset / claim": a["label"], "Amount": inr(a["value"])}
                 for a in sorted(entry["assets"], key=lambda a: -a["value"])],
                width="stretch", height=min(400, 35 * len(entry["assets"]) + 40),
                hide_index=True,
            )

    agg = {}
    for name, e in assets.items():
        for s in e["schemes"]:
            agg[f"{name.title()}: MF"] = agg.get(f"{name.title()}: MF", 0.0) + (s["market"] or 0.0)
        for a in e["assets"]:
            agg[f"{name.title()}: {a['label']}"] = agg.get(f"{name.title()}: {a['label']}", 0.0) + a["value"]
    fig = px.pie(names=list(agg), values=list(agg.values()), hole=0.45)
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=True)
    st.plotly_chart(fig, width="stretch")

    schemes = [(f"{name.title()}: {s['scheme']}", s["market"] or s["cost"] or 0)
               for name, e in assets.items() for s in e["schemes"]]
    schemes.sort(key=lambda kv: -kv[1])
    top = schemes[:10]
    if top:
        fig = px.bar(x=[k[:40] for k, _ in top], y=[v for _, v in top],
                     title="Largest schemes", labels={"x": "", "y": "₹"})
        fig.update_layout(height=320, yaxis_tickformat="~s",
                          xaxis_tickangle=-30, margin=dict(t=40))
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
    tbl = [{"Date": r["date_s"], "Member": r["member"], "Mode": r["mode"],
            "Category": r["category"], "Kind": r["kind"],
            "Particulars": r["part"][:120],
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
    total = sum(agg.values())
    all_spend = sum(1.0 * v for (fy, _), v in frame(filtered(rows, f)).items())
    c1, c2 = st.columns(2)
    c1.metric(f"Total — {pick}", inr(total))
    c2.metric("Share of filtered spend",
              f"{100 * total / all_spend:.1f}%" if all_spend else "—")

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
    top = sorted(pa.items(), key=lambda kv: -abs(kv[1]))[:12]
    if top:
        fig = px.bar(x=[k for k, _ in top], y=[v for _, v in top],
                     labels={"x": "Payee", "y": "₹"})
        fig.update_layout(yaxis_tickformat="~s", margin=dict(t=10, b=40),
                          xaxis_tickangle=-30, height=360)
        st.plotly_chart(fig, width="stretch")


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
    if rows_p and rows_p[0]["part"]:
        kw = rows_p[0]["part"].split("/")[1] if "/" in rows_p[0]["part"] else None
        if kw:
            st.code(f"expense,CATEGORY,{kw}")
            st.caption("paste into rules.txt to auto-classify this payee, "
                       "then re-run classify.py")
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
        st.warning("No repo/classified-main-*.csv found. Run `python3 classify.py` first.")
        return
    f = sidebar_filters(rows)
    if f["page"] == "Overview":
        page_overview(rows, f)
    elif f["page"] == "Transactions":
        page_transactions(rows, f)
    elif f["page"] == "Categories":
        page_categories(rows, f)
    elif f["page"] == "Holdings":
        page_holdings(load_assets())
    else:
        page_review(rows, f)


if __name__ == "__main__":
    main()
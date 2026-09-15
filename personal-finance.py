#!/usr/bin/env python3
"""Family expense & net-worth dashboard — database-backed, Simplifi/Mint-style.

Truth = the sqlite database (backend.FinanceBackend): bank-statement rows as
`entries`, investment sheets as `investments`. Reads fresh from the DB on every
render. Authentication: local passwords (PBKDF2) + a session cookie bridged via
a small same-site JS helper (st.html, unsafe_allow_javascript) because
Streamlit has no public cookie API. Any member may view the whole family; only
the owner of a row may edit it.

Run:  .venv/bin/streamlit run personal-finance.py
"""
import datetime as dt

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from backend import FinanceBackend
from common import BASELINE_EXCLUDED, inr, payee_key
from holdings import absolute_return_pct, xirr

PAGES = ("Overview", "Transactions", "Categories", "Review", "Holdings")
KINDS = ("expense", "income", "investment", "transfer",
         "reimbursed", "refund", "credit")
INTERNAL = ("transfer", "investment", "refund", "reimbursed")

COOKIE = "stmtok"
JS_PROBE = f"""
<script>
const p = new URLSearchParams(location.search);
if (!p.has('{COOKIE}')) {{
  const m = document.cookie.match(/(?:^|; ){COOKIE}=([^;]*)/);
  if (m) {{ p.set('{COOKIE}', encodeURIComponent(m[1]));
            location.replace('?' + p.toString()); }}
  else {{ p.set('probe', '1'); location.replace('?' + p.toString()); }}
}}
</script>
"""
JS_CLEAR = f"""
<script>document.cookie='{COOKIE}=; Max-Age=0; Path=/; SameSite=Lax';</script>
"""


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def session_user():
    me = st.session_state.get("user")
    return me if me and me.get("id") else None


def restore_token(backend):
    """Relay the cookie into the session: URL -> session -> clear the URL."""
    q = st.query_params
    if q.get(COOKIE):
        u = backend.user_for_session(q[COOKIE])
        if not u:
            q.clear()
            st.html(JS_CLEAR, unsafe_allow_javascript=True)
        else:
            st.session_state.user = u
            st.session_state.token = q[COOKIE]
            q.clear()
            st.rerun()


def login_ui(backend):
    st.title("Personal Finance")
    c = st.columns([1, 1, 1])[1]
    with c:
        st.markdown("#### Sign in")
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            ok = st.form_submit_button("Sign in")
        if ok:
            raw = backend.issue_session(username.strip(), password)
            if not raw:
                st.error("Invalid username or password")
            else:
                st.session_state.user = backend.user_for_session(raw)
                st.session_state.token = raw
                ttl = int(backend.get_setting("session_ttl_seconds", 600))
                st.html(
                    f"<script>document.cookie='{COOKIE}={raw}; SameSite=Lax; "
                    f"Path=/; Max-Age={ttl}';</script>",
                    unsafe_allow_javascript=True)
                st.rerun()


def auth_gate(backend):
    me = session_user()
    if not me:
        if not st.query_params.get("probe"):
            st.html(JS_PROBE, unsafe_allow_javascript=True)
        restore_token(backend)
        if not session_user():
            login_ui(backend)
        return None
    return me


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def load_rows(backend, user_ids):
    rows = []
    for r in backend.entries(user_ids=list(user_ids)):
        d = dt.date.fromisoformat(r["date"])
        rows.append({
            "id": r["id"], "member": r["user_id"], "date": d,
            "date_s": d.strftime("%d-%m-%Y"), "mode": r["mode"],
            "part": r["particulars"], "dep": r["deposit"] or 0.0,
            "wd": r["withdrawal"] or 0.0, "kind": r["kind"],
            "category": r["category"] or "—", "fy": r["fy"],
            "ym": d.strftime("%Y-%m"), "payee": payee_key(r["particulars"]),
        })
    return rows


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


def incoming(rows):
    """Deposits that fund spending: income/credit, excluding internal moves."""
    return [r for r in rows if r["dep"] > 0 and r["kind"] not in INTERNAL]


# --------------------------------------------------------------------------
# shared sidebar filters
# --------------------------------------------------------------------------
def sidebar_filters(rows):
    fis = sorted({r["fy"] for r in rows})
    cats = sorted({r["category"] for r in rows if r["kind"] in ("expense", "refund")})
    kinds = sorted({r["kind"] for r in rows})
    accts = sorted({r["mode"] for r in rows if r["mode"]})
    maxwd = int(max(r["wd"] for r in rows) or 1)
    with st.sidebar:
        fys = st.multiselect("Financial years", fis, key="fys",
                             default=list(fis))
        cats_raw = st.multiselect("Categories", cats, key="cats",
                                  default=list(cats))
        kinds = st.multiselect("Kinds", kinds, key="kinds",
                               default=[k for k in ("expense", "refund", "review")
                                        if k in kinds])
        modes = st.multiselect("Modes", accts, key="modes", default=[],
                               placeholder="any mode")
        maxamt = st.slider("Max single amount", 1, maxwd, maxwd, key="maxamt")
        baseline = st.checkbox("Daily-spend baseline (hide tax / loans / cars / misc)",
                               key="baseline")
    eff_cats = [c for c in cats_raw if not baseline or c not in BASELINE_EXCLUDED]
    return {
        "fys": fys, "cats": set(eff_cats), "kinds": set(kinds),
        "modes": set(modes), "maxamt": maxamt, "cats_raw": cats_raw,
    }


def filtered(rows, f):
    return [r for r in rows
            if r["fy"] in f["fys"] and r["kind"] in f["kinds"]
            and r["wd"] <= f["maxamt"] and (not f["modes"] or r["mode"] in f["modes"])
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


def page_review(backend, rows, f, me):
    """UNKNOWN rows grouped by payee; the owner may classify a row."""
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
    fig = px.bar(x=[k for k, _ in grp][:40], y=[v[1][1] for v in grp[:40]],
                 labels={"x": "Payee", "y": "₹"})
    fig.update_layout(yaxis_tickformat="~s", margin=dict(t=10, b=40),
                      xaxis_tickangle=-30, height=360)
    st.plotly_chart(fig, width="stretch")

    pick = st.selectbox("Inspect payee", [k for k, _ in grp], key="revpick",
                        format_func=lambda k: f"{k} — {dict(grp)[k][0]} rows")
    rows_p = [r for r in rev if r["payee"] == pick]
    st.dataframe(
        [{"id": r["id"], "Date": r["date_s"], "Particulars": r["part"][:120],
          "Member": r["member"], "Amount": r["wd"]} for r in rows_p],
        width="stretch", height=280, hide_index=True,
        column_config={"Amount": st.column_config.NumberColumn(format="₹ %d")},
    )

    st.markdown("#### Classify a row")
    owned = [r for r in rows_p if r["member"] == me["id"]]
    if not owned:
        st.caption("Only the owner of a row can classify it (this payee's rows "
                   "belong to someone else in this view).")
        return
    idx = st.selectbox("Row", range(len(owned)), key="revrow",
                       format_func=lambda i: f"{owned[i]['date_s']}  "
                       f"{owned[i]['part'][:60]}  ₹{owned[i]['wd']:,.0f}")
    cats = sorted({c["label"] for c in backend.categories() if c["label"] != "UNKNOWN"})
    with st.form("classify"):
        kind = st.selectbox("Kind", list(KINDS), key="ckind")
        cat = st.selectbox("Category", cats, key="ccat",
                           placeholder="pick an existing category")
        new_cat = st.text_input("…or a new category (takes precedence)", key="cnew")
        if st.form_submit_button("Classify"):
            target = owned[idx]
            cat_eff = (new_cat.strip() or cat or None)
            if cat_eff:
                try:
                    backend.classify_unknown(target["id"], me["id"],
                                             kind, cat_eff)
                    st.success(f"Classified row as {kind} / {cat_eff}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.error("Choose or type a category")


def _ret_pct(cost, latest):
    return absolute_return_pct(cost, latest)


def _xirr_of(inv):
    if not inv["purchase_date"] or not inv["latest_date"]:
        return None
    return xirr([(dt.date.fromisoformat(inv["purchase_date"]), -inv["value_at_cost"]),
                 (dt.date.fromisoformat(inv["latest_date"]), inv["value_latest"])])


def page_holdings(backend, user_ids, me):
    invs = backend.investments(user_ids=list(user_ids))
    st.markdown("##### Holdings — from the investments database")
    if not invs:
        st.info("No investments recorded for this scope.")
        return

    total = sum((r["value_latest"] if r["value_latest"] is not None
                 else r["value_at_cost"] or 0.0) for r in invs)
    c1, c2 = st.columns([2, 1])
    c1.metric("Household net worth", inr(total))
    c2.caption("Net worth = latest market values (cost where latest is missing).")

    names = {u["id"]: u["display_name"] or u["username"]
             for u in backend.users()}
    tfilter = st.selectbox("Type", ["All", *sorted({r["type"] for r in invs})])
    shown = [r for r in invs if tfilter == "All" or r["type"] == tfilter]

    for user_id in sorted({r["user_id"] for r in shown}):
        mine = [r for r in shown if r["user_id"] == user_id]
        st.markdown(f"**{names.get(user_id, user_id).title()}**")
        schemes = [r for r in mine if r["type"] == "MF"]
        assets = [r for r in mine if r["type"] != "MF"]
        if schemes:
            tbl = [{
                "Label": r["label"][:60],
                "Units": (f"{r['units']:,.4f}" if r["units"] is not None else ""),
                "Latest NAV": (f"{r['latest_nav']:,.4f} @ {r['latest_date']}"
                               if r["latest_nav"] is not None else ""),
                "Value at cost": inr(r["value_at_cost"])
                                 if r["value_at_cost"] is not None else "—",
                "Value latest": inr(r["value_latest"])
                                if r["value_latest"] is not None else "—",
                "Return": (f"{_ret_pct(r['value_at_cost'], r['value_latest']):+.1f}%"
                           if _ret_pct(r["value_at_cost"], r["value_latest"]) is not None else "—"),
                "XIRR": (f"{_xirr_of(r) * 100:+.1f}%" if _xirr_of(r) else "—"),
            } for r in schemes if r["label"]]
            st.caption("Mutual-fund schemes")
            st.dataframe(tbl, width="stretch", hide_index=True,
                         height=min(400, 35 * len(tbl) + 40))
        if assets:
            st.dataframe(
                [{"Type": r["type"], "Asset / claim": r["label"],
                  "Amount": inr(r["value_latest"] if r["value_latest"] is not None
                                else r["value_at_cost"] or 0.0)}
                 for r in sorted(assets, key=lambda r: -(
                     r["value_latest"] if r["value_latest"] is not None
                     else r["value_at_cost"] or 0.0))],
                width="stretch", hide_index=True,
                height=min(400, 35 * len(assets) + 40))

    agg = {}
    for r in shown:
        v = r["value_latest"] if r["value_latest"] is not None else r["value_at_cost"] or 0.0
        key = f"{names.get(r['user_id'], r['user_id']).title()} · {r['type']}"
        agg[key] = agg.get(key, 0.0) + v
    if agg:
        fig = px.pie(names=list(agg), values=list(agg.values()), hole=0.45)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=True)
        st.plotly_chart(fig, width="stretch")

    top = sorted(
        ((f"{names.get(r['user_id'], r['user_id']).title()}: {r['label'][:40]}",
          r["value_latest"] if r["value_latest"] is not None
          else r["value_at_cost"] or 0.0)
         for r in shown if r["type"] == "MF"),
        key=lambda kv: -kv[1])[:10]
    if top:
        fig = px.bar(x=[k for k, _ in top], y=[v for _, v in top],
                     title="Largest schemes", labels={"x": "", "y": "₹"})
        fig.update_layout(height=320, yaxis_tickformat="~s",
                          xaxis_tickangle=-30, margin=dict(t=40))
        st.plotly_chart(fig, width="stretch")

    if tfilter == "All":
        st.markdown("#### Update a holding (your rows)")
        mine = [r for r in shown if r["user_id"] == me["id"]]
        if not mine:
            st.caption("No holdings to edit in this view.")
            return
        sel = st.selectbox("Holding", range(len(mine)), key="hsel",
                           format_func=lambda i:
                           f"{mine[i]['type']} · {mine[i]['label'][:60]}")
        inv = mine[sel]
        with st.form("hold"):
            val = st.number_input("Latest market value (₹)",
                                  value=float(inv["value_latest"]
                                              if inv["value_latest"] is not None
                                              else inv["value_at_cost"] or 0.0),
                                  min_value=0.0, key="hval")
            d = st.date_input("As of date", value=(dt.date.fromisoformat(
                inv["latest_date"]) if inv["latest_date"] else dt.date.today()),
                key="hdate")
            if st.form_submit_button("Update"):
                try:
                    backend.update_investment_latest(
                        inv["id"], me["id"], float(val), d.isoformat())
                    st.success(f"Updated {inv['label'][:40]}.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Personal Finance", layout="wide")
    backend = FinanceBackend()
    me = auth_gate(backend)
    if not me:
        backend.close()
        return
    try:
        family = backend.family_user_ids(me["id"])
        with st.sidebar:
            st.caption(f"family · one unit — signed in as "
                       f"{me.get('display_name') or me['username']}")
            if st.button("Sign out", key="logout"):
                backend.revoke_session(st.session_state.get("token"))
                st.session_state.clear()
                st.html(JS_CLEAR, unsafe_allow_javascript=True)
                st.rerun()
            page = st.radio("Section", PAGES, key="page")
            who = st.radio("Scope", ("Family", "Me"), key="scope")
        scope = family if who == "Family" else [me["id"]]
        rows = load_rows(backend, scope)
        if not rows:
            st.warning("No bank entries in the database for this scope. "
                       "Run `impex.py import` first.")
        else:
            with st.sidebar:
                f = sidebar_filters(rows)
            if page == "Overview":
                page_overview(rows, f)
            elif page == "Transactions":
                page_transactions(rows, f)
            elif page == "Categories":
                page_categories(rows, f)
            elif page == "Holdings":
                page_holdings(backend, scope, me)
            else:
                page_review(backend, rows, f, me)
    finally:
        backend.close()


if __name__ == "__main__":
    main()
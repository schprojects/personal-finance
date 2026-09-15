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
from contextlib import contextmanager

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from backend import FinanceBackend, UNKNOWN
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
# editable tables (st.data_editor) — Me scope only
# --------------------------------------------------------------------------
def commit_entry_edits(backend, me_id, rowmap, edited):
    """Apply {display_pos: {Kind:…, Category:…}} cell edits from an entries
    editor. rowmap[display_pos] = (entry_id, owner_id). pos 0-based *display*
    order (dataframe positions). Returns (applied, errors)."""
    applied, errors = 0, []
    for pos, ch in (edited or {}).items():
        try:
            entry_id, owner = rowmap[int(str(pos))]
        except (KeyError, IndexError, TypeError, ValueError):
            errors.append(f"row {pos}: not in the current view")
            continue
        if owner != me_id:
            errors.append(f"row {pos}: only the owner of a row can edit it")
            continue
        kind = ch.get("Kind")
        cat = (ch.get("Category") or "").strip() or None
        if kind is None and cat is None:
            continue
        try:
            backend.update_entry(entry_id, me_id,
                                 kind=kind if kind is not None else None,
                                 category=cat)
            applied += 1
        except ValueError as e:
            errors.append(f"row {pos}: {e}")
    return applied, errors


HOLDING_FIELD = {"Units": "units", "Purchase date": "purchase_date",
                 "Purchase NAV": "purchase_nav", "Latest date": "latest_date",
                 "Latest NAV": "latest_nav", "Value at cost": "value_at_cost",
                 "Value latest": "value_latest"}


def commit_investment_edits(backend, me_id, rowmap, edited):
    """Apply {display_pos: {field: value}} cell edits from the holdings editor.
    rowmap[display_pos] = (investment_id, owner_id). Editor columns are keyed
    by their display titles; titles outside HOLDING_FIELD (identity/primary-key
    columns) are never written."""
    applied, errors = 0, []
    for pos, ch in (edited or {}).items():
        try:
            inv_id, owner = rowmap[int(str(pos))]
        except (KeyError, IndexError, TypeError, ValueError):
            errors.append(f"row {pos}: not in the current view")
            continue
        if owner != me_id:
            errors.append(f"row {pos}: only the owner can edit it")
            continue
        fields = {}
        for title, v in ch.items():
            field = HOLDING_FIELD.get(title)
            if field is None:
                continue
            fields[field] = v if not isinstance(v, str) else (v.strip() or None)
        if not fields:
            continue
        try:
            backend.update_investment_fields(inv_id, me_id, **fields)
            applied += 1
        except ValueError as e:
            errors.append(f"row {pos}: {e}")
    return applied, errors


@contextmanager
def _write_backend(backend):
    """Streamlit runs on_change callbacks on a different thread than the script
    run, so the shared backend.con (bound to the script thread) must never be
    used in a callback. Each callback opens its own short-lived connection."""
    w = FinanceBackend(backend.path)
    try:
        yield w
    finally:
        w.close()


def on_entry_edited(backend, key, rowmap):
    """data_editor on_change for Transactions/Review: commit cell edits."""
    if st.session_state.get("scope") != "Me":
        return
    me = session_user()
    if not me:
        return
    edited = (st.session_state.get(key) or {}).get("edited_rows", {}) or {}
    with _write_backend(backend) as w:
        applied, errors = commit_entry_edits(w, me["id"], rowmap, edited)
    if errors:
        st.warning("Some rows were not changed: " + "; ".join(errors))
    if applied:
        st.success(f"Updated {applied} row{'s' if applied != 1 else ''}.")
    if applied or errors:
        st.rerun()


def on_investment_edited(backend, key, rowmap):
    """data_editor on_change for Holdings: commit cell edits."""
    if st.session_state.get("scope") != "Me":
        return
    me = session_user()
    if not me:
        return
    edited = (st.session_state.get(key) or {}).get("edited_rows", {}) or {}
    with _write_backend(backend) as w:
        applied, errors = commit_investment_edits(w, me["id"], rowmap, edited)
    if errors:
        st.warning("Some holdings were not changed: " + "; ".join(errors))
    if applied:
        st.success(f"Updated {applied} holding{'s' if applied != 1 else ''}.")
    if applied or errors:
        st.rerun()


def _cat_opts(backend, rr):
    labels = {c["label"] for c in backend.categories()}
    for r in rr:
        labels.add(r["category"])
    return sorted(labels)


def _entry_disabled(cols, editable):
    """Columns locked in the entries editors: everything but Kind/Category
    when editable; the whole grid when read-only (family view)."""
    return [c for c in cols if c not in ("Kind", "Category")] if editable else True


def entry_editor(backend, key, rr, editable):
    """Shared Kind + Category dropdown editor over a row list. Me scope edits
    (on_change -> on_entry_edited); Family stays display-only."""
    if not rr:
        return
    if not editable:
        st.caption("Family view is read-only — switch to Me to edit a row.")
    rowmap = [(r["id"], r["member"]) for r in rr]
    tbl = [{"Date": r["date_s"], "Member": r["member"], "Mode": r["mode"] or "—",
            "Kind": r["kind"], "Category": r["category"],
            "Particulars": r["part"][:120],
            "Debit": r["wd"] or None, "Credit": r["dep"] or None}
           for r in rr]
    st.data_editor(
        tbl,
        key=key,
        on_change=on_entry_edited,
        args=(backend, key, rowmap),
        disabled=_entry_disabled(list(tbl[0]), editable),
        column_config={
            "Kind": st.column_config.SelectboxColumn(
                "Kind", options=sorted(set(KINDS) | {r["kind"] for r in rr}),
                required=True),
            "Category": st.column_config.SelectboxColumn(
                "Category", options=_cat_opts(backend, rr), required=True),
            "Debit": st.column_config.NumberColumn(format="₹ %d"),
            "Credit": st.column_config.NumberColumn(format="₹ %d"),
        },
        width="stretch", height=560, hide_index=True,
    )


# --------------------------------------------------------------------------
# shared sidebar filters
# --------------------------------------------------------------------------
def sidebar_filters(rows):
    fis = sorted({r["fy"] for r in rows})
    cats = sorted({r["category"] for r in rows if r["kind"] in ("expense", "refund")})
    kinds = sorted({r["kind"] for r in rows})
    accts = sorted({r["mode"] for r in rows if r["mode"]})
    maxwd = max(2, int(max(r["wd"] for r in rows) or 1))
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
    rev = [r for r in filtered(rows, f) if r["category"] == UNKNOWN]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Net spend", inr(net))
    c2.metric("Per year", inr(net / len({k[0] for k in agg})) if agg else "₹0")
    top = max(agg.items(), key=lambda kv: kv[1]) if agg else None
    c3.metric("Top category", (top[0][1] if top else "—"),
              inr(top[1]) if top else "")
    c4.metric("Unreviewed", inr(sum(r["wd"] + r["dep"] for r in rev)) + f"  ({len(rev)} rows)")

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

    # Income must survive the spend-oriented Kinds multiselect (default excludes
    # income/credit) or Incoming/Savings rate read zero. Scoped like the rest
    # of the view: same FYs / modes / maxamt, same user scope.
    f_inc = dict(f, kinds=f["kinds"] | {"income", "credit"})
    inc = incoming(filtered(rows, f_inc))
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
        if xs:
            fig = px.line(x=xs, y=[cum[k] for k in xs], markers=True,
                          title=f"Cumulative spend — {focus}")
            fig.update_layout(height=320, xaxis_title="Month", yaxis_title="₹",
                              yaxis_tickformat="~s", margin=dict(t=40))
            st.plotly_chart(fig, width="stretch")


def page_transactions(backend, rows, f, me, editable):
    q = st.text_input("Search particulars / payee / category", key="q")
    only_review = st.toggle("UNKNOWN rows only", key="only_rev")
    rr = filtered(rows, f)
    if q:
        ql = q.lower()
        rr = [r for r in rr
              if ql in r["part"].lower() or ql in r["payee"].lower()
              or ql in r["category"].lower()]
    if only_review:
        rr = [r for r in rr if r["category"] == UNKNOWN]
    rr.sort(key=lambda r: (r["date"], r["ym"]), reverse=True)
    st.caption(f"{len(rr)} rows")
    entry_editor(backend, "ed_entries", rr, editable)


def page_categories(backend, rows, f, me, editable):
    if editable:
        st.markdown("#### Create a category")
        with st.form("newcat"):
            lbl = st.text_input("Category label", key="catname")
            kd = st.selectbox("Kind", list(KINDS), key="catkind")
            if st.form_submit_button("Create category"):
                try:
                    res = backend.create_category(lbl, kd)
                    if res == "created":
                        st.success("Category created.")
                    else:
                        st.info("Category already exists.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
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


def page_review(backend, rows, f, me, editable):
    """UNKNOWN-category rows; the owner classifies a row's kind + category."""
    rev = [r for r in filtered(rows, f) if r["category"] == UNKNOWN]
    c1, c2, c3 = st.columns(3)
    c1.metric("Rows", len(rev))
    c2.metric("Credit", inr(sum(r["dep"] for r in rev)))
    c3.metric("Debit", inr(sum(r["wd"] for r in rev)))
    if not rev:
        st.info("Nothing in review for these filters.")
        return
    grp = {}
    for r in rev:
        a = grp.setdefault(r["payee"], [0, 0.0])
        a[0] += 1
        a[1] += r["wd"] + r["dep"]
    grp = sorted(grp.items(), key=lambda kv: -kv[1][1])
    fig = px.bar(x=[k for k, _ in grp][:40], y=[v[1][1] for v in grp[:40]],
                 labels={"x": "Payee", "y": "₹"})
    fig.update_layout(yaxis_tickformat="~s", margin=dict(t=10, b=40),
                      xaxis_tickangle=-30, height=360)
    st.plotly_chart(fig, width="stretch")
    entry_editor(backend, "ed_review", rev, editable)


def _ret_pct(cost, latest):
    return absolute_return_pct(cost, latest)


def _xirr_of(inv):
    if not inv["purchase_date"] or not inv["latest_date"]:
        return None
    return xirr([(dt.date.fromisoformat(inv["purchase_date"]), -inv["value_at_cost"]),
                 (dt.date.fromisoformat(inv["latest_date"]), inv["value_latest"])])


def page_holdings(backend, user_ids, me, editable):
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
            st.caption("Mutual-fund schemes")
            if editable and user_id == me["id"]:
                edt = [{
                    "Scheme": r["label"][:60],
                    "Units": r["units"],
                    "Purchase date": r["purchase_date"],
                    "Purchase NAV": r["purchase_nav"],
                    "Latest date": r["latest_date"],
                    "Latest NAV": r["latest_nav"],
                    "Value at cost": r["value_at_cost"],
                    "Value latest": r["value_latest"],
                    "Return": (f"{_ret_pct(r['value_at_cost'], r['value_latest']):+.1f}%"
                               if _ret_pct(r["value_at_cost"], r["value_latest"]) is not None else "—"),
                    "XIRR": (f"{_xirr_of(r) * 100:+.1f}%" if _xirr_of(r) else "—"),
                } for r in schemes if r["label"]]
                st.data_editor(
                    edt, key="ed_holdings", on_change=on_investment_edited,
                    args=(backend, "ed_holdings",
                          [(r["id"], r["user_id"]) for r in schemes if r["label"]]),
                    disabled=[c for c in edt[0]
                              if c not in ("Units", "Purchase date",
                                           "Purchase NAV", "Latest date",
                                           "Latest NAV", "Value at cost",
                                           "Value latest")],
                    column_config={
                        "Units": st.column_config.NumberColumn(
                            "Units", min_value=0.0, format="%.4f"),
                        "Purchase date": st.column_config.TextColumn(
                            "Purchase date", help="ISO date YYYY-MM-DD"),
                        "Purchase NAV": st.column_config.NumberColumn(
                            "Purchase NAV", min_value=0.0),
                        "Latest date": st.column_config.TextColumn(
                            "Latest date", help="ISO date YYYY-MM-DD"),
                        "Latest NAV": st.column_config.NumberColumn(
                            "Latest NAV", min_value=0.0),
                        "Value at cost": st.column_config.NumberColumn(
                            "Value at cost", min_value=0.0, format="₹ %.0f"),
                        "Value latest": st.column_config.NumberColumn(
                            "Value latest", min_value=0.0, format="₹ %.0f"),
                    },
                    width="stretch", height=min(400, 35 * len(edt) + 40),
                    hide_index=True,
                )
            else:
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

    if not editable:
        st.caption("Family view is read-only — switch to Me to update a holding.")

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
        editable = who == "Me"
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
                page_transactions(backend, rows, f, me, editable)
            elif page == "Categories":
                page_categories(backend, rows, f, me, editable)
            elif page == "Holdings":
                page_holdings(backend, scope, me, editable)
            else:
                page_review(backend, rows, f, me, editable)
    finally:
        backend.close()


if __name__ == "__main__":
    main()
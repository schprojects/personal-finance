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
import os
from contextlib import contextmanager

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from backend import FinanceBackend, UNKNOWN, auth
from finance.common import BASELINE_EXCLUDED, inr, payee_key
from finance import spend as sp
from finance.holdings import absolute_return_pct, investment_xirr
import ui_theme as ui

PAGES = ("Home", "Overview", "Categories", "Transactions", "Holdings",
         "Import", "Export")

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
def session_user(backend):
    """The signed-in user, revalidated against the token store on every call:
    an expired, revoked or missing token drops the session state (auth-specs)."""
    me = st.session_state.get("user")
    if not (me and me.get("id")):
        return None
    if backend.user_for_session(st.session_state.get("token")) is None:
        st.session_state.pop("user", None)
        st.session_state.pop("token", None)
        return None
    return me


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
    ui.hero(backend.get_setting("banner", "Personal Finance"),
            "Your family's expenses and net worth, in one place.",
            kicker="Welcome")
    c = st.columns([1, 1.2, 1])[1]
    with c, st.container(border=True):
        ui.section("Sign in", "Use the username and password set with impex.py auth.")
        with st.form("login", border=False):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            ok = st.form_submit_button("Sign in", type="primary",
                                       width="stretch")
        if ok:
            raw = backend.issue_session(username.strip(), password)
            if not raw:
                auth.log_auth_failure("dashboard", username)
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
    me = session_user(backend)
    if not me:
        if not st.query_params.get("probe"):
            st.html(JS_PROBE, unsafe_allow_javascript=True)
        restore_token(backend)
        if not session_user(backend):
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
            "date_s": d.strftime("%d-%m-%Y"),
            "part": r["particulars"], "dep": r["deposit"] or 0.0,
            "wd": r["withdrawal"] or 0.0, "kind": r["kind"],
            "category": r["category"] or "—", "fy": r["fy"],
            "ym": d.strftime("%Y-%m"), "payee": payee_key(r["particulars"]),
        })
    return rows


def incoming(rows):
    """Credited income/credit/refund/transfer rows (single definition: spend.py)."""
    return sp.incoming(rows)


def frame(rows):
    """Spend frame (expense net of refunds) keyed by (fy, category)."""
    return sp.by_fy_category(rows)


# --------------------------------------------------------------------------
# staged edits — Transactions, Me scope only
#
# Cell edits and "Apply to selected" only touch session state (`staged`:
# {entry_id: {"kind": k, "category": c}}). Nothing reaches the database until
# the explicit Save, which writes the whole batch in one transaction.
# --------------------------------------------------------------------------
def merge_cell_edits(staged, selected, rowmap, edited, base):
    """Fold data_editor `edited_rows` ({pos: {Select|Kind|Category: v}}) into
    `staged` / `selected` (both mutated). rowmap[pos] = entry_id; base[id] =
    {"kind", "category"} as stored. A staged value equal to the stored one is
    dropped, so undoing an edit leaves nothing pending. Returns #cells merged."""
    n = 0
    for pos, ch in (edited or {}).items():
        try:
            rid = rowmap[int(str(pos))]
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        for col, v in ch.items():
            if col == "Select":
                (selected.add if v else selected.discard)(rid)
                continue
            field = {"Kind": "kind", "Category": "category"}.get(col)
            if field is None:
                continue
            cur = staged.setdefault(rid, {})
            if v is None or v == base.get(rid, {}).get(field):
                cur.pop(field, None)
            else:
                cur[field] = v
            n += 1
        _income_has_no_category(staged, rid, base)
    return n


def stage_bulk(staged, ids, base, kind=None, category=None):
    """Stage one kind and/or category for every id (not saved)."""
    for rid in ids:
        cur = staged.setdefault(rid, {})
        for field, v in (("kind", kind), ("category", category)):
            if not v:
                continue
            if v == base.get(rid, {}).get(field):
                cur.pop(field, None)
            else:
                cur[field] = v
        _income_has_no_category(staged, rid, base)


def _income_has_no_category(staged, rid, base):
    """An income row only ever has the default category: drop any
    staged category for it, and forget the row when nothing is left."""
    cur = staged.get(rid)
    if cur is None:
        return
    if cur.get("kind", base.get(rid, {}).get("kind")) == "income":
        cur.pop("category", None)
    if not cur:
        staged.pop(rid, None)


def staged_changes(staged):
    """{id: (kind|None, category|None)} for backend.bulk_update_entries."""
    return {rid: (c.get("kind"), c.get("category"))
            for rid, c in staged.items() if c}


def stable_ids(key, signature, compute):
    """Row ids shown in Transactions, frozen until `signature` changes (or the
    user clicks Refresh): an edit or save that changes a row's kind/category
    must never make it vanish from an active filter."""
    cur = st.session_state.get("frozen")
    if cur is None or cur[0] != (key, signature):
        cur = ((key, signature), compute())
        st.session_state["frozen"] = cur
    return cur[1]


def on_tx_edited(key, rowmap, base):
    """data_editor on_change: session-state only, no database access."""
    s = st.session_state
    edited = (s.get(key) or {}).get("edited_rows", {}) or {}
    merge_cell_edits(s.setdefault("staged", {}), s.setdefault("tx_sel", set()),
                     rowmap, edited, base)
    s["ed_ver"] = s.get("ed_ver", 0) + 1  # fresh widget over the new overlay


def query_frame(rr):
    """Rows as the SQL view sees them: raw category ('' for income), ISO date."""
    return [{"id": r["id"], "member": r["member"], "date": r["date"].isoformat(),
             "particulars": r["part"],
             "payee": r["payee"], "deposit": r["dep"], "withdrawal": r["wd"],
             "kind": r["kind"],
             "category": "" if r["category"] == "—" else r["category"],
             "fy": r["fy"]} for r in rr]


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

def on_investment_edited(backend, key, rowmap):
    """data_editor on_change for Holdings: commit cell edits."""
    if st.session_state.get("scope") != "Me":
        return
    with _write_backend(backend) as w:
        me = session_user(w)
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


def _kind_opts(backend, rr):
    return sorted(set(backend.kinds()) | {r["kind"] for r in rr})


def go_to(page):
    """Navigate: applied by main() before the sidebar radio is built."""
    st.session_state["nav"] = page


def go_review():
    """Home 'Review unclassified' button: open Transactions pre-filtered."""
    s = st.session_state
    s["tx_sql"], s["tx_text"] = "where category is empty", ""
    s.pop("frozen", None)
    s["nav"] = "Transactions"


def _greeting():
    h = dt.datetime.now().hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


def home_snapshot(rows, invs):
    """Latest-FY snapshot for the Home KPI strip: (fy, net spend, savings rate
    or None, unclassified rows, net worth). Pure; reuses spend.total/incoming."""
    fy = max((r["fy"] for r in rows), default=None)
    mine = [r for r in rows if r["fy"] == fy]
    net = sp.total([r for r in mine if r["kind"] in sp.SPEND_KINDS])
    inc = sp.incoming_total(mine)
    rate = (inc - net) / inc if inc > 0 else None
    unclassified = sum(1 for r in mine if r["category"] == UNKNOWN)
    worth = sum((r["value_latest"] if r["value_latest"] is not None
                 else r["value_at_cost"] or 0.0) for r in invs)
    return fy, net, rate, unclassified, worth


def page_home(backend, me, scope):
    name = (me.get("display_name") or me["username"]).title()
    ui.hero(backend.get_setting("banner", "Personal Finance"),
            dt.date.today().strftime("%A, %d %B %Y"),
            kicker=f"{_greeting()}, {name}")
    rows = load_rows(backend, scope)
    if not rows:
        with st.container(border=True):
            ui.section("Let's get started",
                       "There are no bank entries yet. Import a statement to see "
                       "your overview, categories and transactions.")
            st.button("Open Import →", key="home_start", on_click=go_to,
                      args=("Import",), type="primary")
    else:
        fy, net, rate, unclassified, worth = home_snapshot(
            rows, backend.investments(user_ids=list(scope)))
        ui.section(f"Snapshot — FY {fy}", "Latest financial year in the chosen scope.")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            ui.kpi("Net spend", inr(net), icon=":material/payments:")
        with c2:
            ui.kpi("Savings rate", f"{100 * rate:.0f}%" if rate is not None else "—",
                   icon=":material/savings:")
        with c3:
            ui.kpi("Unclassified", f"{unclassified} rows",
                   icon=":material/label_important:")
            if unclassified:
                st.button("Review them →", key="home_review", on_click=go_review)
        with c4:
            ui.kpi("Net worth", inr(worth), icon=":material/account_balance:")
    ui.section("Where to?")
    cols = st.columns(3)
    for i, p in enumerate(PAGES[1:]):
        with cols[i % 3], st.container(border=True, key=f"card_{p}"):
            st.html(f'<div class="pf-card-icon">{ui.ICONS[p]}</div>'
                    f'<p class="pf-card-title">{p}</p>'
                    f'<p class="pf-card-text">{ui.BLURBS[p]}</p>')
            st.button("Open →", key=f"home_{p}", on_click=go_to, args=(p,),
                      width="stretch")


def page_transactions(backend, rows, me, editable):
    s = st.session_state
    ui.page_header("Transactions",
                   "Filter, classify and bulk-edit entries. Changes are saved "
                   "only when you press Save.", ui.ICONS["Transactions"])
    fis = sorted({r["fy"] for r in rows})
    with st.container(border=True):
        ui.section("Find transactions",
                   "Use the text filter or a SQL condition — not both.")
        years = st.multiselect("Year in focus", fis, default=fis[-1:], key="tx_years")
        c1, c2 = st.columns(2)
        text = c1.text_input("Plain-text filter (particulars / payee / category)",
                             key="tx_text", placeholder="e.g. swiggy")
        sql = c2.text_input(
            "SQL query", key="tx_sql",
            placeholder="where kind is empty and category is empty "
                        "and particulars like '%swiggy%'",
            help="Only the WHERE condition is used. Columns: id, member, date, "
                 "particulars, payee, deposit, withdrawal, kind, category, fy. "
                 "`kind is empty` / `category is empty` also match review / UNKNOWN.")
    pool = [r for r in rows if r["fy"] in years]
    shown = []
    try:
        ids = stable_ids("tx", (tuple(years), text, sql, st.session_state.get("scope")),
                         lambda: [r["id"] for r in _filtered_tx(pool, text, sql)])
        by_id = {r["id"]: r for r in pool}
        shown = [by_id[i] for i in ids if i in by_id]
    except ValueError as e:
        st.error(str(e))
        return
    shown.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    st.caption(f"{len(shown)} rows")
    if st.button("Refresh view", key="tx_refresh",
                 help="Re-run the filter against the saved data."):
        s.pop("frozen", None)
        st.rerun()
    if not shown:
        st.info("No rows match.")
        return
    if not editable:
        st.caption("Family view is read-only — switch to Me to edit a row.")
        import pandas as pd
        st.dataframe(pd.DataFrame([{"Date": r["date_s"], "Member": r["member"],
                       "Kind": r["kind"], "Category": r["category"],
                       "Particulars": r["part"][:120],
                       "Debit": float(r["wd"]) if r["wd"] else None,
                    "Credit": float(r["dep"]) if r["dep"] else None}
                      for r in shown]), width="stretch", hide_index=True,
                     height=min(560, 36 * len(shown) + 40))
        return

    staged = s.setdefault("staged", {})
    sel = s.setdefault("tx_sel", set())
    base = {r["id"]: {"kind": r["kind"], "category": r["category"]} for r in rows}

    pending = staged_changes(staged)
    with st.container(border=True):
        ui.section("Edit selected rows",
                   "Tick rows below, pick a kind and/or category, apply, then save.")
        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("Select all shown", key="tx_selall"):
            sel.update(r["id"] for r in shown)
            s["ed_ver"] = s.get("ed_ver", 0) + 1
            st.rerun()
        if b2.button("Clear selection", key="tx_selnone"):
            sel.clear()
            s["ed_ver"] = s.get("ed_ver", 0) + 1
            st.rerun()
        n_sel = sum(1 for r in shown if r["id"] in sel)
        b3.html(ui.chip(f"{n_sel} selected", "ok" if n_sel else ""))
        none = "—"
        k1, k2, k3 = st.columns([2, 2, 1], vertical_alignment="bottom")
        kind = k1.selectbox("Kind", [none, *_kind_opts(backend, shown)], key="tx_kind")
        cat = k2.selectbox("Category", [none, *_cat_opts(backend, shown)],
                           key="tx_cat")
        target = [r["id"] for r in shown if r["id"] in sel]
        if k3.button(f"Apply to {len(target)} selected", key="tx_apply",
                     disabled=not target or (kind == none and cat == none),
                     width="stretch"):
            stage_bulk(staged, target, base, None if kind == none else kind,
                       None if cat == none else cat)
            s["ed_ver"] = s.get("ed_ver", 0) + 1
            st.rerun()

        # explicit, batched save
        p1, p2, p3 = st.columns([2, 1, 1], vertical_alignment="center")
        p1.html(ui.chip(f"{len(pending)} unsaved change"
                        f"{'s' if len(pending) != 1 else ''}",
                        "warn" if pending else ""))
        if p2.button("Save", key="tx_save", type="primary", disabled=not pending,
                     width="stretch", icon=":material/save:"):
            try:
                n = backend.bulk_update_entries(me["id"], pending)
            except ValueError as e:
                st.error(f"Nothing saved: {e}")
            else:
                staged.clear()
                sel.clear()
                s["ed_ver"] = s.get("ed_ver", 0) + 1
                st.success(f"Saved {n} row{'s' if n != 1 else ''}.")
                st.rerun()
        if p3.button("Discard", key="tx_discard", disabled=not pending,
                     width="stretch"):
            staged.clear()
            s["ed_ver"] = s.get("ed_ver", 0) + 1
            st.rerun()

    rowmap = [r["id"] for r in shown]
    tbl = []
    for r in shown:
        ov = staged.get(r["id"], {})
        tbl.append({"Select": r["id"] in sel, "Date": r["date_s"],
                    "Member": r["member"],
                    "Kind": ov.get("kind", r["kind"]),
                    "Category": ov.get("category", r["category"]),
                    "Particulars": r["part"][:120],
                    "Debit": inr(r["wd"]) if r["wd"] else "",
                    "Credit": inr(r["dep"]) if r["dep"] else ""})
    key = f"ed_tx_{s.get('ed_ver', 0)}"
    import pandas as pd  # shipped with streamlit; NaN renders as a blank cell
    st.data_editor(
        pd.DataFrame(tbl), key=key, on_change=on_tx_edited, args=(key, rowmap, base),
        disabled=[c for c in tbl[0] if c not in ("Select", "Kind", "Category")],
        column_config={
            "Select": st.column_config.CheckboxColumn("Select", default=False,
                                                      width="small"),
            "Date": st.column_config.TextColumn(width="small"),
            "Member": st.column_config.TextColumn(width="small"),
            "Particulars": st.column_config.TextColumn(width="medium"),
            "Kind": st.column_config.SelectboxColumn(
                "Kind", options=_kind_opts(backend, shown), required=True),
            "Category": st.column_config.SelectboxColumn(
                "Category", options=_cat_opts(backend, shown), required=True),
            "Debit": st.column_config.TextColumn(width="medium"),
            "Credit": st.column_config.TextColumn(width="medium"),
        }, width="stretch", height=min(560, 36 * len(tbl) + 40), hide_index=True)


def _filtered_tx(pool, text, sql):
    """Text filter xor SQL query over the pool (ValueError when both)."""
    from finance.query import filter_rows
    frame_ = query_frame(pool)
    ok = {r["id"] for r in filter_rows(frame_, text=text, sql=sql)}
    return [r for r in pool if r["id"] in ok]


def page_import(backend, me, family):
    s = st.session_state
    from finance import importing
    from finance.common import BASE
    from finance.rules import RULES_FILE, load_rules
    names = {u["id"]: u["display_name"] or u["username"] for u in backend.users()}
    ui.page_header("Import", "Load a bank statement, preview the classification, "
                   "then save it to the database.", ui.ICONS["Import"])
    ui.stepper(["Choose file", "Review", "Save"], 2 if s.get("imp") else 1)
    choose = st.container(border=True)
    member = choose.selectbox("Member", family, key="imp_member",
                              index=family.index(me["id"]) if me["id"] in family else 0,
                              format_func=lambda i: names.get(i, i))
    if member != me["id"]:
        st.warning("You can only import rows for yourself (an entry may be "
                   "changed only by its owner).")
        return
    data_dir = backend.data_dir(member)
    found = []
    root = os.path.join(BASE, data_dir) if data_dir else ""
    for dp, _dn, fn in os.walk(root) if root and os.path.isdir(root) else []:
        found += [os.path.join(dp, f) for f in fn if f.lower().endswith(".csv")]
    pick = choose.selectbox("Files found under your data folder", ["—", *sorted(found)],
                            key="imp_pick",
                            format_func=lambda p: p if p == "—" else os.path.relpath(p, BASE))
    path = choose.text_input("…or full path of the CSV to import",
                             value="" if pick == "—" else pick, key=f"imp_path_{pick}")
    if path:
        choose.caption(f"Full path: `{os.path.abspath(os.path.expanduser(path))}`")
    if choose.button("Load file", key="imp_load", disabled=not path,
                     icon=":material/upload_file:"):
        full = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(full):
            st.error(f"No such file: {full}")
        else:
            try:
                rows_, failed, log = importing.stage_statement(full)
            except Exception as e:  # unreadable / not a statement
                st.error(f"Could not read {full}: {e}")
            else:
                s["imp"] = {"rows": rows_, "failed": failed, "log": log,
                            "path": full}
    imp = s.get("imp")
    if not imp:
        return
    for line in imp["log"]:
        st.caption(line)
    fys = sorted({backend.fy_of(r["date"]) for r in imp["rows"]})
    year = st.selectbox("Year", ["All years", *fys], key="imp_year")
    use_rules = st.checkbox("Apply auto-rules", value=True, key="imp_rules")
    rules, rule_warnings = load_rules(kinds=set(backend.kinds())) if use_rules else ([], [])
    if use_rules:
        if not os.path.isfile(RULES_FILE):
            st.info(f"No rules file found. Put `auto-rules.csv` at `{RULES_FILE}` "
                    "(one `KIND,CATEGORY,EXPRESSION` rule per line; see the README).")
        else:
            st.caption(f"{len(rules)} rule(s) read from `{RULES_FILE}`.")
        if rule_warnings:
            st.warning(f"{len(rule_warnings)} rule line(s) skipped: "
                       + "; ".join(rule_warnings[:5])
                       + (" …" if len(rule_warnings) > 5 else ""))
    raw = [r for r in imp["rows"]
           if year == "All years" or backend.fy_of(r["date"]) == year]
    final = importing.resolve(raw, rules)
    matched = sum(r["rule"] for r in final)
    unknown = sum(r["category"] == UNKNOWN for r in final)
    m1, m2, m3 = st.columns(3)
    with m1:
        ui.kpi("Rows", len(final), icon=":material/list:")
    with m2:
        ui.kpi("Matched by auto-rule", matched, icon=":material/auto_fix_high:")
    with m3:
        ui.kpi("UNKNOWN", unknown, icon=":material/help:")
    ui.section("Preview", "Nothing is written until you press Save.")
    import pandas as pd
    st.dataframe(pd.DataFrame([{"Date": r["date"], "Particulars": r["particulars"][:100],
                   "Debit": float(r["withdrawal"]) if r["withdrawal"] else None,
                   "Credit": float(r["deposit"]) if r["deposit"] else None,
                   "Kind": r["kind"], "Category": r["category"] or "—",
                   "By rule": r["rule"]} for r in final]),
                 width="stretch", hide_index=True,
                 height=min(420, 36 * len(final) + 40),
                 column_config={
                     "Debit": st.column_config.NumberColumn(format="₹ %.2f"),
                     "Credit": st.column_config.NumberColumn(format="₹ %.2f"),
                     "By rule": st.column_config.CheckboxColumn(width="small")})
    if imp["failed"]:
        st.warning(f"{len(imp['failed'])} block(s) failed reconciliation; their "
                   "rows go to output/<FY>/failed-N.csv on import.")
    if st.button("Save import to database", key="imp_save", type="primary",
                 icon=":material/save:", disabled=not final and not imp["failed"]):
        counts = importing.commit_rows(backend, me["id"], final)
        if imp["failed"]:
            import impex
            failed = {}
            for acct, txs in imp["failed"]:
                for t in txs:
                    failed.setdefault(backend.fy_of(importing.iso(t["date"])),
                                      []).append((
                        {"member": me["id"], "file": os.path.basename(imp["path"]),
                         "account": acct}, t))
            impex.write_failed(failed, [], backend)
        s.pop("imp", None)
        s.pop("frozen", None)
        s["staged"], s["tx_sel"] = {}, set()
        s["flash"] = f"Import saved: {counts}"
        go_to("Transactions")
        st.rerun()


def page_export(backend, me, family):
    from finance.exporting import default_export_path, export_to_path
    names = {u["id"]: u["display_name"] or u["username"] for u in backend.users()}
    ui.page_header("Export", "Write entries to a CSV file. Existing files are "
                   "never overwritten.", ui.ICONS["Export"])
    with st.container(border=True):
        ui.section("What to export")
        c1, c2 = st.columns(2)
        members = c1.multiselect("Member", family, default=[me["id"]],
                                 key="exp_members",
                                 format_func=lambda i: names.get(i, i))
        all_rows = backend.entries(user_ids=members) if members else []
        fis = sorted({r["fy"] for r in all_rows})
        years = c2.multiselect("Year", fis, default=fis, key="exp_years")
        default = default_export_path([names.get(m, m) for m in members], years)
        path = st.text_input("File path", value=default, key=f"exp_path_{default}",
                             help="Existing files are never overwritten: a number "
                                  "is appended instead.")
        rows = [r for r in all_rows if r["fy"] in years]
        st.caption(f"{len(rows)} rows selected")
        if st.button("Export", key="exp_go", type="primary", icon=":material/download:",
                     disabled=not rows or not path):
            final, n = export_to_path(rows, os.path.expanduser(path))
            st.success(f"Exported {n} rows to `{os.path.abspath(final)}`")


# --------------------------------------------------------------------------
# shared sidebar filters
# --------------------------------------------------------------------------
def sidebar_filters(rows):
    # Widget keys carry the scope: option lists differ between Family and Me,
    # so a selection made in one scope must never leak into the other.
    sc = st.session_state.get("scope", "Family")
    fis = sorted({r["fy"] for r in rows})
    cats = sorted({r["category"] for r in rows if r["kind"] in sp.SPEND_KINDS})
    kinds = sorted({r["kind"] for r in rows})
    maxwd = max(2, int(max(r["wd"] for r in rows) or 1))
    with st.sidebar:
        fys = st.multiselect("Financial years", fis, key=f"fys_{sc}",
                             default=list(fis))
        cats_raw = st.multiselect("Categories", cats, key=f"cats_{sc}",
                                  default=list(cats))
        kinds = st.multiselect("Kinds", kinds, key=f"kinds_{sc}",
                               default=[k for k in ("expense", "transfer", "review")
                                        if k in kinds])
        maxamt = st.slider("Max single amount", 1, maxwd, maxwd, key=f"maxamt_{sc}")
        baseline = st.checkbox(
            "Daily-spend baseline", key="baseline",
            help="Leaves " + ", ".join(sorted(BASELINE_EXCLUDED)) + " out of "
                 "the figures. See 'How is Net spend calculated?' for the amounts.")
    return {
        "fys": fys, "cats": set(cats_raw), "kinds": set(kinds),
        "maxamt": maxamt, "cats_raw": cats_raw,
        "baseline": baseline, "all_cats": cats, "maxwd": maxwd,
    }


def _passes(r, f, skip=None):
    """Row passes every sidebar filter except the named `skip` one."""
    return ((skip == "fys" or r["fy"] in f["fys"])
            and (skip == "kinds" or r["kind"] in f["kinds"])
            and (skip == "maxamt" or r["wd"] <= f["maxamt"])
            and (skip == "baseline" or not f["baseline"]
                 or r["kind"] not in sp.SPEND_KINDS
                 or r["category"] not in BASELINE_EXCLUDED)
            and (skip == "cats" or r["kind"] not in sp.SPEND_KINDS
                 or r["category"] in f["cats"]))


def filtered(rows, f):
    return [r for r in rows if _passes(r, f)]


def excluded_by_filter(rows, f, big=frozenset()):
    """Net spend each filter / option removes on its own, so the number is
    explainable: {name: net spend of spend-kind rows that fail ONLY that one}.
    `big` = ids of big-ticket rows the user excluded; they count only under
    "bigticket" (rows that otherwise pass every filter)."""
    spend_rows = [r for r in rows if r["kind"] in sp.SPEND_KINDS]
    kept = [r for r in spend_rows if r["id"] not in big]
    out = {name: sp.total([r for r in kept
                           if _passes(r, f, skip=name) and not _passes(r, f)])
           for name in ("fys", "cats", "maxamt", "baseline")}
    out["bigticket"] = sp.total([r for r in spend_rows
                                 if r["id"] in big and _passes(r, f)])
    return out


def baseline_breakdown(rows, f, big=frozenset()):
    """Per baseline category: net spend over the selected years (rows that pass
    every other filter), so the user sees exactly what the option hides."""
    out = {c: 0.0 for c in sorted(BASELINE_EXCLUDED)}
    for r in rows:
        if (r["kind"] in sp.SPEND_KINDS and r["category"] in out
                and r["id"] not in big and _passes(r, f, skip="baseline")):
            out[r["category"]] += sp.delta(r)
    return out


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------
def explain_net_spend(rows, f, net, big=frozenset()):
    """Make the Net spend figure auditable: formula, what each filter and option
    removed (incl. the daily-spend baseline, category by category), and a
    per-member table that sums to the Family figure."""
    sel = [r for r in filtered(rows, f)
           if r["kind"] in sp.SPEND_KINDS and r["id"] not in big]
    with st.expander("How is Net spend calculated?"):
        st.markdown(
            "**Net spend** = Σ (withdrawal − deposit) over `expense` rows, plus "
            "the withdrawal of every debited `transfer` (category Transfer), "
            "for rows that pass the sidebar filters. A credit on an expense row "
            "(a reversal) reduces it. **Incoming** is money credited as income, "
            "credit, refund or transfer — refunds are incoming, not a reduction "
            "of spend. Investments are never spend.")
        st.caption(
            f"{len(sel)} rows · FYs {', '.join(f['fys']) or '—'} · "
            f"max single amount {inr(f['maxamt'])} · "
            f"baseline {'on' if f['baseline'] else 'off'}")
        ex = excluded_by_filter(rows, f, big)
        labels = {"fys": "Financial-year selection",
                  "cats": "Category selection",
                  "maxamt": "Max single amount",
                  "bigticket": "Big-ticket exclusions (ticked below)",
                  "baseline": "Daily-spend baseline"}
        st.dataframe([{"Filter / option": labels[k], "Net spend removed": inr(v)}
                      for k, v in ex.items()], width="stretch", hide_index=True)
        bb = baseline_breakdown(rows, f, big)
        status = "excluded" if f["baseline"] else "would be excluded"
        st.markdown(
            "**Daily-spend baseline — " + ("ON" if f["baseline"] else "OFF") + "**  \n"
            "When on, spend in these categories is left out of every figure "
            "because it is large or irregular and hides day-to-day spending. "
            "Income, investments and transfers are not affected.")
        st.dataframe([{"Category": c, "Net spend": inr(v), "Status": status}
                      for c, v in bb.items()]
                     + [{"Category": "Total", "Net spend": inr(sum(bb.values())),
                         "Status": status}],
                     width="stretch", hide_index=True)
        mem = sp.by_member(sel)
        tbl = [{"Member": m, "Rows": v["rows"], "Debits": inr(v["debits"]),
                "Credits (refunds)": inr(v["credits"]), "Net spend": inr(v["net"])}
               for m, v in sorted(mem.items())]
        if len(tbl) > 1:
            tbl.append({"Member": "Family total",
                        "Rows": sum(v["rows"] for v in mem.values()),
                        "Debits": inr(sum(v["debits"] for v in mem.values())),
                        "Credits (refunds)": inr(sum(v["credits"] for v in mem.values())),
                        "Net spend": inr(net)})
        st.dataframe(tbl, width="stretch", hide_index=True)
        biggest = sorted(sel, key=lambda r: -sp.delta(r))[:5]
        st.markdown("**Largest contributing rows**")
        st.dataframe([{"Date": r["date_s"], "Member": r["member"],
                       "Category": r["category"], "Particulars": r["part"][:60],
                       "Amount": inr(sp.delta(r))} for r in biggest],
                     width="stretch", hide_index=True)


BIG_TICKET_N = 8


def big_ticket(spend, fy, n=BIG_TICKET_N):
    """The n largest net-spend rows of one FY (candidates for exclusion)."""
    return sorted((r for r in spend if r["fy"] == fy and sp.delta(r) > 0),
                  key=lambda r: -sp.delta(r))[:n]


def page_overview(rows, f):
    ui.page_header("Overview", "Spending, income and savings for your selection.",
                   ui.ICONS["Overview"])
    fis = sorted({r["fy"] for r in rows})
    focus = st.pills("Focus year", fis, default=fis[-1], selection_mode="single") \
        or fis[-1]
    # Big-ticket exclusions come from checkbox state rendered further down, so
    # they are read first and drive every figure on the page.
    all_spend = [r for r in filtered(rows, f) if r["kind"] in sp.SPEND_KINDS]
    big = big_ticket(all_spend, focus)
    excluded = {r["id"] for r in big if st.session_state.get(f"bt_{r['id']}")}
    spend = [r for r in all_spend if r["id"] not in excluded]
    agg = frame(spend)
    net = sum(agg.values())
    rev = [r for r in filtered(rows, f) if r["category"] == UNKNOWN]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        ui.kpi("Net spend", inr(net), icon=":material/payments:",
               help=f"{len(excluded)} big-ticket row(s) excluded" if excluded else None)
    n_fy = len(f["fys"])
    with c2:
        ui.kpi("Per year", inr(net / n_fy) if agg and n_fy else "₹0",
               icon=":material/calendar_month:",
               help=f"Net spend ÷ {n_fy} selected financial year(s) "
                    f"(a partial year counts as a whole year).")
    top = max(sp.by_category(spend).items(), key=lambda kv: kv[1], default=None)
    with c3:
        ui.kpi("Top category", (top[0] if top else "—"),
               inr(top[1]) if top else "", icon=":material/sell:")
    with c4:
        ui.kpi("Unclassified", inr(sum(r["wd"] + r["dep"] for r in rev)),
               f"{len(rev)} rows", icon=":material/label_important:", neutral=True)

    fys = [fy for fy in fis if fy in f["fys"]] or [focus]
    left, right = st.columns([3, 2])
    with left, st.container(border=True):
        ui.section("Monthly net spend", "One line per selected financial year.")
        fig = go.Figure()
        for fy in fys:
            ys = {}
            for r in spend:
                if r["fy"] == fy:
                    ys[r["ym"]] = ys.get(r["ym"], 0.0) + sp.delta(r)
            xs = sorted(ys)
            fig.add_trace(go.Scatter(x=xs, y=[ys[k] for k in xs], name=fy,
                                     mode="lines+markers",
                                     line={"dash": "solid" if fy == focus else "dash",
                                           "shape": "spline", "smoothing": .6}))
        fig.update_xaxes(type="category")
        ui.chart(fig, 340)
    with right, st.container(border=True):
        ui.section("Top categories", "Share of net spend (top 8).")
        if agg:
            cats = sp.top_categories(spend)
            fig = px.pie(names=[c for c, _ in cats],
                         values=[abs(v) for _, v in cats], hole=0.58,
                         color_discrete_sequence=ui.palette())
            fig.update_traces(textinfo="percent", sort=False)
            ui.chart(fig, 340)
        else:
            st.caption("No spend in this selection.")

    # Income must survive the spend-oriented Kinds multiselect (default excludes
    # income/credit) or Incoming/Savings rate read zero. Scoped like the rest
    # of the view: same FYs / maxamt, same user scope.
    f_inc = dict(f, kinds=f["kinds"] | set(sp.INCOMING_KINDS))
    inc = sp.incoming(filtered(rows, f_inc))
    inc_fy, spend_fy = {}, {}
    for r in inc:
        inc_fy[r["fy"]] = inc_fy.get(r["fy"], 0.0) + r["dep"]
    for (fy, _), v in agg.items():
        spend_fy[fy] = spend_fy.get(fy, 0.0) + v
    fys = sorted(set(fy for (fy, _) in agg) | set(inc_fy))
    inc_t = sum(inc_fy.values())
    spend_t = sum(spend_fy.values())
    c1, c2 = st.columns([2, 1])
    with c1, st.container(border=True):
        ui.section("Income vs spend", "Incoming money and net spend per financial year.")
        if fys:
            fig = go.Figure()
            fig.add_bar(x=fys, y=[inc_fy.get(fy, 0.0) for fy in fys], name="Incoming")
            fig.add_bar(x=fys, y=[spend_fy.get(fy, 0.0) for fy in fys], name="Net spend")
            fig.update_layout(barmode="group", bargap=.35)
            ui.chart(fig, 320)
    with c2:
        ui.kpi("Incoming", inr(inc_t), icon=":material/trending_up:")
        ui.kpi("Savings rate", f"{100 * (inc_t - spend_t) / inc_t:.0f}%"
               if inc_t > 0 else "—",
               delta=f"{inr(inc_t - spend_t)} saved", icon=":material/savings:")
    explain_net_spend(rows, f, spend_t, frozenset(excluded))

    fig = go.Figure()
    for fy in [fy for fy in fis if fy in f["fys"]]:
        cum, run = {}, 0.0
        for r in sorted((r for r in spend if r["fy"] == fy), key=lambda x: x["date"]):
            run += sp.delta(r)
            cum[r["ym"]] = run
        xs = sorted(cum)
        if xs:
            fig.add_trace(go.Scatter(x=xs, y=[cum[k] for k in xs], name=fy,
                                     mode="lines+markers"))
    if fig.data:
        with st.container(border=True):
            ui.section("Cumulative spend trend",
                       "Running total through the year, one line per financial year.")
            fig.update_xaxes(type="category")
            ui.chart(fig, 320)

    with st.container(border=True):
        ui.section(f"Big-ticket spends — {focus}",
                   "Tick a spend to exclude it from every figure on this page.")
        for r in big:
            st.checkbox(f"{r['date_s']} · {r['category']} · {r['part'][:60]} · "
                        f"{inr(sp.delta(r))}", key=f"bt_{r['id']}")


def page_categories(backend, rows, f, me, editable):
    ui.page_header("Categories", "Where the money goes, and how to organise it.",
                   ui.ICONS["Categories"])
    if editable:
        exp = st.expander("➕ New category or kind")
        with exp, st.form("newcat", border=False):
            lbl = st.text_input("Category label", key="catname")
            kd = st.selectbox("Kind", backend.kinds(), key="catkind")
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
        with exp, st.form("newkind", border=False):
            kn = st.text_input("Kind name", key="kindname")
            if st.form_submit_button("Create kind"):
                try:
                    res = backend.create_kind(kn)
                    if res == "created":
                        st.success("Kind created.")
                    else:
                        st.info("Kind already exists.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    cats = sorted({r["category"] for r in filtered(rows, f)
                  if r["kind"] in sp.SPEND_KINDS})
    if not cats:
        st.info("No categories in the current filter.")
        return
    pick = st.selectbox("Category", cats, key="pickcat")
    rs = [r for r in filtered(rows, f) if r["category"] == pick]
    agg = frame(rs)
    total = sum(agg.values())
    all_spend = sum(1.0 * v for (fy, _), v in frame(filtered(rows, f)).items())
    c1, c2 = st.columns(2)
    with c1:
        ui.kpi(f"Total — {pick}", inr(total), icon=":material/sell:")
    with c2:
        ui.kpi("Share of filtered spend",
               f"{100 * total / all_spend:.1f}%" if all_spend else "—",
               icon=":material/pie_chart:")

    fig = go.Figure()
    for fy in sorted({r["fy"] for r in rs}):
        by_month = {}
        for r in rs:
            if r["fy"] == fy and r["kind"] in sp.SPEND_KINDS:
                by_month[r["ym"]] = by_month.get(r["ym"], 0.0) + sp.delta(r)
        xs = sorted(by_month)
        if any(abs(by_month[m]) > 0.01 for m in xs):
            fig.add_trace(go.Scatter(x=xs, y=[by_month[m] for m in xs], name=fy,
                                     mode="lines+markers"))
    with st.container(border=True):
        ui.section(f"{pick} — monthly trend", "One line per financial year.")
        fig.update_xaxes(type="category")
        ui.chart(fig, 340)

    pa = {}
    for r in rs:
        if r["kind"] in sp.SPEND_KINDS:
            pa[r["payee"]] = pa.get(r["payee"], 0.0) + sp.delta(r)
    pa = {k: v for k, v in pa.items() if abs(v) > 0.01}
    top = sorted(pa.items(), key=lambda kv: -abs(kv[1]))[:12]
    if top:
        fig = px.bar(x=[k for k, _ in top], y=[v for _, v in top],
                     color_discrete_sequence=ui.palette())
        fig.update_layout(xaxis_tickangle=-30, bargap=.3)
        with st.container(border=True):
            ui.section("Top payees", f"Largest payees within {pick}.")
            ui.chart(fig, 340, legend=False)


def _ret_pct(cost, latest):
    return absolute_return_pct(cost, latest)


def _xirr_of(inv):
    return investment_xirr(inv)  # None for partial history; never raises


def page_holdings(backend, user_ids, me, editable):
    invs = backend.investments(user_ids=list(user_ids))
    ui.page_header("Holdings", "Investments and net worth from the database.",
                   ui.ICONS["Holdings"])
    if not invs:
        st.info("No investments recorded for this scope.")
        return

    total = sum((r["value_latest"] if r["value_latest"] is not None
                 else r["value_at_cost"] or 0.0) for r in invs)
    cost = sum(r["value_at_cost"] or 0.0 for r in invs if r["value_at_cost"])
    gain_base = [r for r in invs if r["value_at_cost"] and r["value_latest"] is not None]
    gain = sum(r["value_latest"] - r["value_at_cost"] for r in gain_base)
    c1, c2, c3 = st.columns(3)
    with c1:
        ui.kpi("Household net worth", inr(total), icon=":material/account_balance:",
               help="Latest market values (cost where latest is missing).")
    with c2:
        ui.kpi("Invested at cost", inr(cost), icon=":material/payments:")
    with c3:
        ui.kpi("Unrealised gain", inr(gain), icon=":material/trending_up:",
               delta=f"{100 * gain / sum(r['value_at_cost'] for r in gain_base):+.1f}%"
               if gain_base else None)

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
                    "XIRR": (f"{x * 100:+.1f}%" if (x := _xirr_of(r)) is not None else "—"),
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
                    "XIRR": (f"{x * 100:+.1f}%" if (x := _xirr_of(r)) is not None else "—"),
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
        fig = px.pie(names=list(agg), values=list(agg.values()), hole=0.58,
                     color_discrete_sequence=ui.palette())
        fig.update_traces(textinfo="percent", sort=False)
        with st.container(border=True):
            ui.section("Allocation", "Net worth by member and holding type.")
            ui.chart(fig, 340)

    top = sorted(
        ((f"{names.get(r['user_id'], r['user_id']).title()}: {r['label'][:40]}",
          r["value_latest"] if r["value_latest"] is not None
          else r["value_at_cost"] or 0.0)
         for r in shown if r["type"] == "MF"),
        key=lambda kv: -kv[1])[:10]
    if top:
        fig = px.bar(x=[k for k, _ in top], y=[v for _, v in top],
                     color_discrete_sequence=ui.palette())
        fig.update_layout(xaxis_tickangle=-30, bargap=.3)
        with st.container(border=True):
            ui.section("Largest schemes", "Top mutual-fund schemes by latest value.")
            ui.chart(fig, 340, legend=False)

# --------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Personal Finance", page_icon="₹", layout="wide")
    ui.inject_css()
    backend = FinanceBackend()
    try:
        me = auth_gate(backend)
        if not me:
            return
        family = backend.family_user_ids(me["id"])
        s = st.session_state
        if "nav" in s:  # navigation requested by a button / import success
            s["page"] = s.pop("nav")
        with st.sidebar:
            st.html('<div class="pf-brand"><span class="pf-logo">₹</span>'
                    "Personal Finance</div>")
            st.html(f'<p class="pf-user">Signed in as <b>'
                    f"{me.get('display_name') or me['username']}</b></p>")
            if st.button("Sign out", key="logout", icon=":material/logout:"):
                backend.revoke_session(s.get("token"))
                s.clear()
                st.html(JS_CLEAR, unsafe_allow_javascript=True)
                st.rerun()
            st.html('<p class="pf-label">Navigate</p>')
            page = st.radio("Section", PAGES, key="page",
                            label_visibility="collapsed",
                            format_func=lambda p: f"{ui.ICONS[p]}  {p}")
            st.html('<p class="pf-label">Scope</p>')
            who = st.radio("Scope", ("Family", "Me"), key="scope",
                           label_visibility="collapsed",
                           format_func=lambda v: v if v == "Family" else
                           (me.get("display_name") or me["username"]).title())
        scope = family if who == "Family" else [me["id"]]
        editable = who == "Me"
        if s.get("flash"):
            st.success(s.pop("flash"))
        if page == "Home":
            page_home(backend, me, scope)
        elif page == "Import":
            page_import(backend, me, family)
        elif page == "Export":
            page_export(backend, me, family)
        elif page == "Holdings":
            page_holdings(backend, scope, me, editable)
        else:
            rows = load_rows(backend, scope)
            if not rows:
                st.warning("No bank entries in the database for this scope. "
                           "Open Import to load a statement.")
            elif page == "Transactions":
                page_transactions(backend, rows, me, editable)
            else:
                with st.sidebar:
                    f = sidebar_filters(rows)
                if page == "Overview":
                    page_overview(rows, f)
                else:
                    page_categories(backend, rows, f, me, editable)
    finally:
        backend.close()


if __name__ == "__main__":
    main()

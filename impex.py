#!/usr/bin/env python3
"""Importer-exporter CLI for the family networth tracker.

Gates, imports, exports:
  impex.py auth set-password --user X
  impex.py import --user X --type bank-statement|investments
                   [--mode existing|auto|manual|both] [--manual-rules PATH]
  impex.py export --login X [--user U] [--type bank|investments] [filters] [--out FILE]
  impex.py backup [--dest DIR]
  impex.py restore --from FILE

Every import/export authenticates (interactive password, --password-file, or
--password-stdin); auth failure exits non-zero before any read/write.
The database is the system of record: bank statements become `entries`,
investment sheets become `investments`. Unmatched transactions are marked
category UNKNOWN (editable in the dashboard). Reconcile-failed blocks go to
output/<FY>/failed-<N>.csv (complete rows, deduped). Data files are never
touched.
"""
import argparse
import csv
import datetime as dt
import getpass
import os
import re
import shutil
import sqlite3
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from backend import FinanceBackend, UNKNOWN
from backend.model import nkey
from backend.paths import db_path
from finance.classify import parse_blocks, reconcile
from finance.common import load_members, money, statements_for
from finance.holdings import is_mf_sheet, parse_label_rows, parse_mf_sheet
from finance.llm import classify_unmatched
from finance.rules import RuleSet, _suggest_keyword

AUTO_RULES = os.path.join(BASE, "auto-rules.csv")
DEFAULT_MANUAL = os.path.join(BASE, "manual-rules.csv")
FAILED_COLS = ["MEMBER", "FILE", "ACCOUNT", "DATE", "MODE", "PARTICULARS",
               "DEPOSIT", "WITHDRAWAL", "BALANCE"]
EXPORT_COLS = ["MEMBER", "ACCOUNT", "DATE", "MODE", "PARTICULARS", "DEPOSIT",
               "WITHDRAWAL", "BALANCE", "KIND", "CATEGORY", "FY"]
INVEST_EXPORT_COLS = ["MEMBER", "TYPE", "LABEL", "FOLIO", "UNITS",
                      "PURCHASE_DATE", "PURCHASE_NAV", "LATEST_DATE",
                      "LATEST_NAV", "VALUE_AT_COST", "VALUE_LATEST"]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _iso(date_s):
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime((date_s or "").strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return (date_s or "").strip()


def _ddmmyyyy(iso):
    if not iso:
        return ""
    for fmt in ("%Y-%m-%d", "%d-%b-%Y"):
        try:
            return dt.datetime.strptime(iso, fmt).date().strftime("%d-%m-%Y")
        except ValueError:
            continue
    return iso


def _pw(label, args, flag_prefix):
    """Password from --<prefix>-file / --<prefix>-stdin or interactively."""
    if getattr(args, f"{flag_prefix}_file"):
        with open(getattr(args, f"{flag_prefix}_file")) as f:
            return f.readline().rstrip("\n")
    if getattr(args, f"{flag_prefix}_stdin"):
        return sys.stdin.readline().rstrip("\n")
    return getpass.getpass(f"{label}: ")


def authenticate(backend, username, args):
    pw = _pw(f"password for {username}", args, "password")
    if not backend.check_password(username, pw):
        sys.exit(f"authentication failed for {username!r}; nothing was read or changed")


def finish_run(lines, tag):
    os.makedirs(os.path.join("var", "logs"), exist_ok=True)
    path = os.path.join("var", "logs",
                        f"{tag}-{time.strftime('%Y%m%d-%H%M%S')}.log")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


def rules_for_mode(mode, manual_path):
    # manual-rules.csv first (highest precedence), then auto-rules.csv
    if mode and mode not in ("existing", "manual", "auto", "both"):
        sys.exit(f"unknown mode {mode!r} (existing|manual|auto|both)")
    manual = manual_path or (DEFAULT_MANUAL
                             if os.path.exists(DEFAULT_MANUAL) else None)
    if mode in ("manual",):
        if not manual:
            sys.exit("manual mode needs a rules file (--manual-rules PATH)")
        rs = RuleSet.combined(manual, None)
    elif mode in ("auto",):
        rs = RuleSet.combined(None, AUTO_RULES)
    else:  # existing, both
        rs = RuleSet.combined(manual, AUTO_RULES)
    if not rs.rules:
        print("  no rule files present; unmatched rows fall back to default-kind "
              "(credit=income, debit=expense, category UNKNOWN)")
    return rs


# --------------------------------------------------------------------------
# failed.csv
# --------------------------------------------------------------------------
def prior_failed_keys():
    keys = set()
    if not os.path.isdir("output"):
        return keys
    for fy in os.listdir("output"):
        d = os.path.join("output", fy)
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            if not re.fullmatch(r"failed-\d+\.csv", name):
                continue
            with open(os.path.join(d, name), newline="") as f:
                for r in csv.DictReader(f):
                    keys.add(nkey(r["ACCOUNT"], _iso(r["DATE"]),
                                  r["PARTICULARS"], money(r["DEPOSIT"]),
                                  money(r["WITHDRAWAL"])))
    return keys


def write_failed(failed, log):
    """failed: {fy: [tx-with-meta]}. Writes output/<fy>/failed-<N>.csv."""
    for fy, rows in sorted(failed.items()):
        d = os.path.join("output", fy)
        os.makedirs(d, exist_ok=True)
        n = 0
        for name in os.listdir(d):
            m = re.fullmatch(r"failed-(\d+)\.csv", name)
            if m:
                n = max(n, int(m.group(1)))
        seen = prior_failed_keys()
        kept_rows, skipped = [], 0
        for meta, t in rows:
            key = nkey(meta["account"], _iso(t["date"]), t["part"],
                       t["dep"], t["wd"])
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            kept_rows.append(
                {"MEMBER": meta["member"], "FILE": meta["file"],
                 "ACCOUNT": meta["account"], "DATE": _ddmmyyyy(_iso(t["date"])),
                 "MODE": t["mode"], "PARTICULARS": t["part"],
                 "DEPOSIT": t["dep"], "WITHDRAWAL": t["wd"], "BALANCE": t["bal"]})
        msg = f"  failed {fy}: {len(kept_rows)} rows"
        if kept_rows:
            path = os.path.join(d, f"failed-{n + 1}.csv")
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, FAILED_COLS)
                w.writeheader()
                w.writerows(kept_rows)
            msg += f" -> {os.path.relpath(path)}"
        if skipped:
            msg += f" ({skipped} already recorded, skipped)"
        log.append(msg)
        print(msg)


# --------------------------------------------------------------------------
# auto mode
# --------------------------------------------------------------------------
def append_auto_rules(auto_path, results, keywords):
    """Append expense rules the LLM produced, unless the keyword is taken."""
    existing = set()
    if os.path.exists(auto_path):
        existing = set().union(*(r.keywords for r in RuleSet.from_csv(auto_path).rules))
    added = 0
    with open(auto_path, "a", newline="") as f:
        w = csv.writer(f)
        for idx, cat in sorted(results.items()):
            kw = keywords[idx]
            if cat == UNKNOWN or not kw or kw in existing:
                continue
            w.writerow(["expense", cat, kw])
            existing.add(kw)
            added += 1
    return added


# --------------------------------------------------------------------------
# imports
# --------------------------------------------------------------------------
def import_bank_statements(backend, owner, rules, *, llm_enabled=False,
                           auto_path=AUTO_RULES):
    members = {m["name"]: m for m in load_members()}
    member = members.get(owner)
    if not member:
        sys.exit(f"no such member {owner!r} in members.yaml")

    counts = dict(created=0, updated=0, skipped=0, manual_override=0)
    log = [f"import bank-statements --user {owner}"]
    failed, prep = {}, []
    for m, path in statements_for([member]):
        for acct, txs in parse_blocks(path):
            carrier = bool(txs and txs[0].get("_carrier"))
            if carrier:
                opening, ok, msg = 0.0, True, "carrier csv (kind/category as-is)"
            else:
                opening, ok, msg, txs = reconcile(acct, txs)
            log.append(f"  {os.path.relpath(path)} [{acct}] {msg} "
                       f"{'PASS' if ok else 'FAIL'}")
            print(f"  {os.path.relpath(path)} [{acct}] {msg} "
                  f"{'PASS' if ok else 'FAIL'}")
            if not ok:
                base = os.path.basename(path)
                for t in txs:
                    failed.setdefault(backend.fy_of(_iso(t["date"]), start_month=4),
                                      []).append((
                        {"member": owner, "file": base, "account": acct}, t))
                continue
            for t in txs:
                date_iso = _iso(t["date"])
                dir_ = "dep" if t["dep"] > 0 else "wd"
                specified = bool(t.get("kind") or t.get("category"))
                if specified:
                    rr = (t.get("kind"), t.get("category"))
                else:
                    rr = rules.match(t["part"], t["mode"], t["wd"] or t["dep"],
                                     dir_=dir_)
                prep.append({"account": acct, "date": date_iso, "mode": t["mode"],
                             "part": t["part"], "dep": t["dep"], "wd": t["wd"],
                             "bal": t["bal"], "file": os.path.basename(path),
                             "match": rr, "matched": rr is not None,
                             "carrier": specified})
            backend.con.commit()

    if llm_enabled:
        targets = [p for p in prep if not p["matched"]]
        if targets:
            cats = [c["label"] for c in backend.categories()]
            print(f"  consulting OpenRouter for {len(targets)} unmatched rows...")
            results = classify_unmatched([p["part"] for p in targets], cats)
            added = append_auto_rules(
                auto_path, results, [_suggest_keyword(p["part"]) or ""
                                     for p in targets])
            applied = 0
            for i, p in enumerate(targets):
                if i in results:
                    p["match"] = ("expense", results[i])
                    p["matched"] = True
                    applied += 1
            log.append(f"  LLM: {applied} classified, {len(results)} rules appended, "
                       f"{added} new rules written")
            print(f"  LLM: {applied} classified now, {added} rules appended "
                  f"to {os.path.basename(auto_path)}")

    for p in prep:
        if not p["matched"]:
            if p["dep"] > 0:
                kind, cat = "income", ""
            else:
                kind, cat = "expense", UNKNOWN
        else:
            kind, cat = p["match"]
            if kind is None:
                kind = "income" if p["dep"] > 0 else "expense"
            if p["dep"] > 0 and p["wd"] == 0 and not p["carrier"] \
                    and kind in ("expense", "review"):
                kind, cat = "credit", ""
        if kind == "income":
            cat = ""  # income has only the default category
        action = backend.upsert_bank_entry(
            owner, account=p["account"], date=p["date"], mode=p["mode"],
            particulars=p["part"], deposit=p["dep"], withdrawal=p["wd"],
            balance=p["bal"], kind=kind, category=cat, file=p["file"])
        counts[action] += 1

    write_failed(failed, log)
    log.append(f"  result: {counts}")
    print(f"  result: {counts}")
    path = finish_run(log, "import")
    print(f"  log -> {path}")
    return counts


def _type_of_label(label):
    l = (label or "").lower()
    if "to-receive" in l:
        return "loan-receivable"
    if "fd" in l or "fixed deposit" in l:
        return "FD"
    if "epf" in l or "provident" in l or l in ("pf", "appa"):
        return "EPF"
    if "etf" in l or "exchange" in l:
        return "ETF"
    if "share" in l or "stock" in l or "demat" in l:
        return "share"
    if "mf" in l or "fund" in l:
        return "MF"
    if "savings" in l or "bank" in l or "sbi" in l or "gold" in l or "cash" in l:
        return "cash"
    return "cash"


def import_investments(backend, owner):
    members = {m["name"]: m for m in load_members()}
    member = members.get(owner)
    if not member:
        sys.exit(f"no such member {owner!r} in members.yaml")
    root = os.path.join(member["data_dir"], "investments")
    counts = dict(created=0, updated=0, skipped=0)
    log = [f"import investments --user {owner}"]
    if not os.path.isdir(root):
        log.append("  no investments folder")
        print("  no investments folder")
    else:
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
                schemes, _ = parse_mf_sheet(header, data[1:])
                for s in schemes:
                    action = backend.upsert_investment(
                        owner, type_="MF", label=s["scheme"], folio=s["folio"],
                        units=s["units"], latest_date=_iso(s["nav_date"]),
                        latest_nav=s["nav"], value_at_cost=s["cost"],
                        value_latest=s["market"] or s["cost"])
                    counts[action] += 1
            else:
                assets, _ = parse_label_rows(data)
                for a in assets:
                    action = backend.upsert_investment(
                        owner, type_=_type_of_label(a["label"]),
                        label=a["label"], value_latest=a["value"])
                    counts[action] += 1
    log.append(f"  result: {counts}")
    print(f"  result: {counts}")
    finish_run(log, "import")
    return counts


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------
def _fmt(x):
    return "" if x is None else f"{x:.2f}"


def export_entries(backend, login, args, out):
    scope = [login] if args.user else backend.family_user_ids(login)
    if args.user and args.user not in backend.family_user_ids(login):
        sys.exit(f"{args.user!r} is not in your family")
    if args.type == "investments":
        rows = backend.investments(user_ids=scope)
        cols = INVEST_EXPORT_COLS
        flat = [[r["user_id"], r["type"], r["label"], r["folio"],
                 _fmt(r["units"]), _ddmmyyyy(r["purchase_date"]),
                 _fmt(r["purchase_nav"]), _ddmmyyyy(r["latest_date"]),
                 _fmt(r["latest_nav"]), _fmt(r["value_at_cost"]),
                 _fmt(r["value_latest"])] for r in rows]
    else:
        rows = backend.entries(user_ids=scope, kinds=[args.kind] if args.kind else None,
                               category=args.category, fy=args.fy,
                               account=args.account, q=args.q)
        cols = EXPORT_COLS
        flat = [[r["user_id"], r["account"], _ddmmyyyy(r["date"]), r["mode"],
                 r["particulars"], _fmt(r["deposit"]), _fmt(r["withdrawal"]),
                 _fmt(r["balance"]), r["kind"], r["category"], r["fy"]]
                for r in rows]
    w = csv.writer(out)
    w.writerow(cols)
    w.writerows(flat)
    return len(rows)


# --------------------------------------------------------------------------
# backup / restore
# --------------------------------------------------------------------------
def backup(backend, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, f"networth-{time.strftime('%Y%m%d-%H%M%S')}.db")
    dst = sqlite3.connect(target)
    backend.con.backup(dst)
    dst.close()
    print(f"backup written -> {target}")
    return target


def restore(path):
    if not os.path.exists(path):
        sys.exit(f"no such backup file: {path}")
    con = sqlite3.connect(path)
    version = con.execute("PRAGMA user_version").fetchone()
    con.close()
    if not version or version[0] < 1:
        sys.exit(f"{path} is not an application database")
    live = db_path()
    os.makedirs(os.path.dirname(live) or ".", exist_ok=True)
    shutil.copy2(path, live)
    print(f"restored {path} -> {live}")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="impex", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth")
    a.add_argument("set_password", nargs="?", const="set-password")
    a.add_argument("--user", required=True)
    a.add_argument("--password-file")
    a.add_argument("--password-stdin", action="store_true")
    a.add_argument("--current-password-file")
    a.add_argument("--current-password-stdin", action="store_true")

    im = sub.add_parser("import")
    im.add_argument("--user", required=True)
    im.add_argument("--type", default="bank-statement",
                    choices=["bank-statement", "investments"])
    im.add_argument("--mode", default="existing",
                    choices=["existing", "manual", "auto", "both"])
    im.add_argument("--manual-rules")
    im.add_argument("--password-file")
    im.add_argument("--password-stdin", action="store_true")

    ex = sub.add_parser("export")
    ex.add_argument("--login", required=True)
    ex.add_argument("--user")
    ex.add_argument("--type", default="bank", choices=["bank", "investments"])
    ex.add_argument("--fy")
    ex.add_argument("--kind")
    ex.add_argument("--category")
    ex.add_argument("--account")
    ex.add_argument("--q")
    ex.add_argument("--out")
    ex.add_argument("--password-file")
    ex.add_argument("--password-stdin", action="store_true")

    bk = sub.add_parser("backup")
    bk.add_argument("--dest", default="backup")

    rs = sub.add_parser("restore")
    rs.add_argument("--from", dest="from_", required=True)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    backend = FinanceBackend()
    backend.seed_members(load_members())

    if args.cmd == "auth":
        new = _pw(f"new password for {args.user}", args, "password")
        if not (args.password_file or args.password_stdin):
            confirm = _pw("new password (repeat)", args, "password")
            if confirm != new:
                sys.exit("passwords do not match")
        current = None
        if any(u["username"] == args.user and u["has_password"]
               for u in backend.users()):
            current = _pw(f"current password for {args.user}", args,
                          "current_password")
        try:
            backend.set_password(args.user, new, current)
        except ValueError as e:
            sys.exit(f"auth: {e}")
        print(f"password set for {args.user} (sessions revoked)")
        return

    if args.cmd == "import":
        authenticate(backend, args.user, args)
        if args.type == "investments":
            import_investments(backend, args.user)
        else:
            rules = rules_for_mode(args.mode, args.manual_rules)
            import_bank_statements(
                backend, args.user, rules,
                llm_enabled=args.mode in ("auto", "both"))
        return

    if args.cmd == "export":
        authenticate(backend, args.login, args)
        w = open(args.out, "w", newline="") if args.out else sys.stdout
        try:
            n = export_entries(backend, args.login, args, w)
        finally:
            if args.out:
                w.close()
        print(f"exported {n} {args.type} rows")
        return

    if args.cmd == "backup":
        backup(backend, args.dest)
        return

    if args.cmd == "restore":
        restore(args.from_)
        return


if __name__ == "__main__":
    main()
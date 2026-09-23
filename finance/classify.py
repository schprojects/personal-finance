#!/usr/bin/env python3
"""Classify ICICI bank statement exports into expense categories.

Usage: python3 classify.py

Reads every member's statements (via members.yaml), tags each row, and writes
numbered runs. Raw data files are never touched:

  output/classified-<N>.csv        every classified row (all members)
  output/review-<N>.csv            rows still needing a human ruling
  output/<FY>/classified-<N>.csv   per-FY slices of the run
  repo/classified-main-<N>.csv     consolidated inventory the app reads

N = 1 + highest run number already in output/ so re-runs never clobber.
Excludes investments / income / transfers / reimbursables from expenses.
"""
import csv
import os
import re
import sys

from .common import fy_of_str, inr, load_members, money, payee_key, statements_for
from .rules import RuleSet


RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "rules.txt")


def load_ruleset():
    """RuleSet from the external rules.txt; missing file = clear stop."""
    if not os.path.exists(RULES_PATH):
        sys.exit(f"missing {RULES_PATH}: author classification rules there "
                 "before running (see review.py for suggestions)")
    return RuleSet.from_file(RULES_PATH)


def parse_blocks(path):
    """Yield (account, [tx dicts]) blocks from one statement CSV."""
    with open(path, newline="") as f:
        rows = [r + [""] * (6 - len(r)) for r in csv.reader(f)]

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


MANUAL = {}
_MANUAL_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "manual.csv")
if os.path.exists(_MANUAL_PATH):
    with open(_MANUAL_PATH, newline="") as f:
        for r in csv.DictReader(f):
            MANUAL[(str(r.get("DATE", "")).strip(),
                    (r.get("PARTICULARS", "") or "").strip().upper(),
                    float(r.get("AMOUNT") or 0))] = (r.get("KIND", "").strip(),
                                                     r.get("CATEGORY", "").strip())


MISC_MIN = 1000.0  # ponytail: payees totalling less than this/yr of spend -> "Misc & small"


def bucket_long_tail(rows):
    rev = [r for r in rows if r["kind"] == "review"]
    totals = {}
    for r in rev:
        totals[payee_key(r["part"])] = totals.get(payee_key(r["part"]), 0.0) + r["wd"]
    for r in rev:
        if totals[payee_key(r["part"])] < MISC_MIN:
            r["kind"], r["category"] = "expense", "Misc & small"


# --------------------------------------------------------------------------
# output writers
# --------------------------------------------------------------------------
COLUMNS = ["MEMBER", "FILE", "ACCOUNT", "DATE", "MODE", "PARTICULARS",
           "DEPOSIT", "WITHDRAWAL", "BALANCE", "KIND", "CATEGORY", "FY"]
_ROW_KEY = {"PARTICULARS": "part", "DEPOSIT": "dep", "WITHDRAWAL": "wd",
            "BALANCE": "bal", "MEMBER": "member", "FILE": "file",
            "ACCOUNT": "account", "DATE": "date", "MODE": "mode",
            "KIND": "kind", "CATEGORY": "category", "FY": "fy"}

REVIEW_COLUMNS = ["MEMBER", "FILE", "ACCOUNT", "DATE", "MODE", "PARTICULARS",
                  "WITHDRAWAL", "BALANCE"]


def next_run_number():
    """1 + max run number already in output/, or 1."""
    if not os.path.isdir("output"):
        return 1
    n = 0
    for name in os.listdir("output"):
        m = re.fullmatch(r"classified-(\d+)\.csv", name)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def _row(r):
    return [r[_ROW_KEY[k]] for k in COLUMNS]


def write_run(n, rows):
    """Numbered run files under output/ + per-FY slices + repo/ inventory."""
    os.makedirs("output", exist_ok=True)

    review = [r for r in rows if r["kind"] == "review"]
    with open(os.path.join("output", f"classified-{n}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in sorted(rows, key=lambda r: r["date"]):
            w.writerow(_row(r))
    with open(os.path.join("output", f"review-{n}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(REVIEW_COLUMNS)
        for r in review:
            w.writerow([r["member"], r["file"], r["account"], r["date"], r["mode"],
                        r["part"], r["wd"], r["bal"]])

    by_fy = {}
    for r in rows:
        by_fy.setdefault(r["fy"], []).append(r)
    for fy, fr in sorted(by_fy.items()):
        os.makedirs(os.path.join("output", fy), exist_ok=True)
        with open(os.path.join("output", fy, f"classified-{n}.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS)
            for r in sorted(fr, key=lambda r: r["date"]):
                w.writerow(_row(r))

    os.makedirs("repo", exist_ok=True)
    with open(os.path.join("repo", f"classified-main-{n}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for r in sorted(rows, key=lambda r: (r["fy"], r["date"])):
            w.writerow(_row(r))
    return by_fy


# --------------------------------------------------------------------------
def main():
    members = load_members()
    if not members:
        sys.exit("members.yaml has no members")

    rules = load_ruleset()
    all_rows = []
    ok_all = True
    for member, path in statements_for(members):
        for acct, txs in parse_blocks(path):
            opening, ok, msg, txs = reconcile(acct, txs)
            if not ok:
                ok_all = False
            print(f"{member['name']:>9}  {os.path.relpath(path)}  [{acct}]  {msg}  "
                  f"{'PASS' if ok else 'FAIL'}")
            if not ok:
                continue
            for t in txs:
                amt = t["wd"] or t["dep"]
                mkey = (t["date"], (t["part"] or "").strip().upper(), amt)
                if mkey in MANUAL:
                    kind, cat = MANUAL[mkey]
                else:
                    rr = rules.match(t["part"], t["mode"], amt)
                    kind, cat = rr if rr else ("review", "REVIEW")
                if t["dep"] > 0 and t["wd"] == 0 and kind in ("expense", "review"):
                    kind, cat = "credit", ""  # deposits aren't expenses
                t.update(member=member["name"], file=os.path.basename(path), account=acct,
                         kind=kind, category=cat, fy=fy_of_str(t["date"]))
                all_rows.append(t)

    if not ok_all:
        sys.exit(1)  # at least one block failed reconciliation; totals unsafe

    bucket_long_tail(all_rows)
    n = next_run_number()
    by_fy = write_run(n, all_rows)

    print()
    net = sum(r["wd"] - r["dep"] for r in all_rows if r["kind"] in ("expense", "refund"))
    n_review = sum(1 for r in all_rows if r["kind"] == "review")
    print(f"run #{n}: {len(all_rows)} rows, {len(by_fy)} FYs")
    for fy, fr in sorted(by_fy.items()):
        fy_net = sum(r["wd"] - r["dep"] for r in fr if r["kind"] in ("expense", "refund"))
        print(f"  {fy:<10} {len(fr):>5} rows  net {inr(fy_net)}")
    print(f"  review: {n_review} rows")
    print(f"wrote output/classified-{n}.csv, output/review-{n}.csv, "
          f"output/<FY>/classified-{n}.csv, repo/classified-main-{n}.csv")


if __name__ == "__main__":
    main()
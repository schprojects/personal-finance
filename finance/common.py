#!/usr/bin/env python3
"""Shared helpers for the classifier and dashboard (single source of truth).

Lives in the finance/ package; CLI tools and the dashboard import it from
there. Never writes data.
"""
import datetime as dt
import glob
import os
import re

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KINDS = ["expense", "refund", "review", "income", "investment", "transfer",
         "reimbursed", "charge", "cash", "credit"]

# Daily-spend baseline hides these in the dashboard (dashboard-specs.md). The
# small-misc category is deliberately NOT here: it is day-to-day spending.
BASELINE_EXCLUDED = {"Income_tax", "Vehicles", "Loan_EMI", "Bank_DP_charges"}


def load_members():
    """members.yaml -> [{'name', 'data_dir'}] from the project root."""
    path = os.path.join(BASE, "members.yaml")
    if not os.path.isfile(path):
        raise SystemExit(f"members.yaml not found at {path}: copy "
                         "members.example.yaml to members.yaml and edit the names "
                         "(with Docker, create it BEFORE `docker compose up`, or "
                         "Docker makes a folder with that name)")
    with open(path) as f:
        doc = yaml.safe_load(f)
    members = []
    for m in doc.get("members", []):
        name = m["name"].strip().lower()
        data_dir = os.path.normpath(os.path.join(BASE, m["data_dir"]))
        members.append({"name": name, "data_dir": data_dir})
    return members


def money(s):
    s = (s or "").strip().replace(",", "")
    return float(s) if s else 0.0


def inr(x):
    neg = x < 0
    x = abs(x)
    s = f"{x:.2f}".split(".")
    head = s[0]
    if len(head) > 3:
        last3 = head[-3:]
        rest = re.sub(r"\B(?=(\d{2})+(?!\d))", ",", head[:-3])
        head = rest + "," + last3
    return ("-" if neg else "") + "₹" + head + "." + s[1]


def fy_of(d):
    """FY label from a date, e.g. 2024-04-01 -> '2024-25'."""
    yy = d.year if d.month >= 4 else d.year - 1
    return f"{yy}-{str(yy + 1)[-2:]}"


def fy_of_str(date_s):
    """FY label from a dd-mm-yyyy string."""
    return fy_of(dt.datetime.strptime(date_s, "%d-%m-%Y").date())


def payee_key(p):
    """Coarse merchant id for lumping / long-tail decisions."""
    if p.startswith("UPI/"):
        m = re.search(r"([A-Za-z0-9._@-]+@[A-Za-z]+)", p)
        return "UPI:" + (m.group(1).split("@")[0] if m else p.split("/")[1])
    t = p.split("/")
    if p.startswith(("VPS/", "IPS/", "VSI/")):
        return t[0] + ":" + t[1]
    if p.startswith("BIL/"):
        for t in p.split("/")[1:]:
            if t in ("ONL", "INFT", "NEFT", "RTGS", "DTAX", "IMPS") or t.isdigit():
                continue
            if len(t) >= 3:
                return "BIL:" + t
        return "BIL:?"
    if p.startswith("MMT/"):
        m = re.search(r"([A-Za-z0-9._@-]+@[A-Za-z]+)", p)
        if m:
            return "MMT:" + m.group(1).split("@")[0]
        return "MMT:" + (t[-1] if len(t) > 1 else "?")
    if p.startswith(("NEFT-", "RTGS-")):
        return "BANK:" + p[:25]
    if p.startswith(("VIN/", "GIB/")):
        return t[0] + ":" + (t[1] if len(t) > 1 else "?")
    return "OTHER:" + p[:24]


def statements_for(members):
    """Yield (member, path) for every bank statement CSV found per member."""
    for m in members:
        root = os.path.join(m["data_dir"], "bank-statements")
        for pat in ("**/*.csv", "**/*.CSV"):
            for p in sorted(glob.glob(os.path.join(root, pat), recursive=True)):
                yield m, p


def investments_for(members):
    """Yield (member, path) for every investment CSV found per member."""
    for m in members:
        root = os.path.join(m["data_dir"], "investments")
        for pat in ("**/*.csv", "**/*.CSV"):
            for p in sorted(glob.glob(os.path.join(root, pat), recursive=True)):
                yield m, p
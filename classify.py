#!/usr/bin/env python3
"""Classify ICICI bank statement exports into expense categories.

Usage: python3 classify.py [statement1.csv statement2.csv ...]
Defaults to all *.csv/*.CSV in this directory except known outputs.
Excludes investments / income / transfers / reimbursables from expenses.
"""
import csv
import glob
import os
import re
import sys


# --------------------------------------------------------------------------
# Editable rule table. First match wins (order = precedence).
#   ("income"/"investment"/"transfer"/"refund"/"charge"/"cash"/"reimbursed", "", <kw>)  -> non-expense
#   ("category_name", <kw>)                                                           -> expense category
# --------------------------------------------------------------------------
FAMILY = ["PAPA", "MOM ", "MOM_", "MOM-", "APPA", "ARCHANA", "[MEMBER]", "DHRITI",
          "DURGA", "GIRIYAPPA", "HUNASIKATTI", "B S CHARATI", "BS CHARATI"]

RULES = [
    # ---- income (excluded) ----
    ("income", "", "INT.PD"),
    ("income", "", "ACH/"),
    ("income", "", "BHIM REWARD"),
    ("income", "", "SBI DIVIDEND"),
    ("income", "", "UIIC HEALTH"),
    ("income", "", "FC SBI"),
    ("income", "", "NAVNEET"),
    ("income", "", "NTPC"),
    ("income", "", "GAIL"),
    ("income", "", "POWERGRID"),
    ("income", "", "IDFC"),
    ("income", "", "BIOCON"),
    ("income", "", "PETRONET"),
    ("income", "", "BHEL"),
    # ---- investment (excluded) ----
    ("investment", "", "EBA/MF-"),
    ("investment", "", "MF-ICICI"),
    ("investment", "", "MF-FRANKLIN"),
    ("investment", "", "MUTUAL FUND"),
    ("investment", "", "MF REDEMPT"),
    ("investment", "", "REDEMPTION"),
    ("investment", "", "COMMON REDEMPTI"),
    ("investment", "", "BIRLA SUNLIFE"),
    ("investment", "", "L AND T FUND"),
    ("investment", "", "KOTAK MF"),
    ("investment", "", "SUNDARAM SELECT MIDCAP"),
    ("investment", "", "DSP BLACKROCK"),
    ("investment", "", "SBIRED"),
    ("investment", "", "4[BROKER]"),
    ("investment", "", "/[BROKER]"),
    ("investment", "", "[BROKER]"),
    ("investment", "", "BSESTAR"),
    ("investment", "", "CMS/"),
    # ---- own-account funding sweeps (excluded) ----
    ("transfer", "", "INF/"),
    ("transfer", "", "INDIAN CLEARING"),
    ("transfer", "", "FORHARYA"),
    ("transfer", "", "FOR_HARYA"),
    ("transfer", "", "HARISH VENKATES"),
    ("transfer", "", "FROM_SCHARATI_XFER_M"),
    ("transfer", "", "ARIHANT"),
    ("transfer", "", "TEST_SCHARATI"),
    ("transfer", "", "TEST_XFER"),
    ("transfer", "", "SPECS_RETURN"),
    ("transfer", "", "[REDACTED]@UPI"),
    ("transfer", "", "[REDACTED]@UPI"),
    ("transfer", "", "[REDACTED]@UPI"),
    ("transfer", "", "[REDACTED]"),
    # ---- reimbursable (excluded, per user) ----
    ("reimbursed", "", "AMEX"),
    ("reimbursed", "", "AMERICAN E"),
    # ---- refunds / reversals (net against expenses) ----
    ("refund", "", "REF "),
    ("refund", "", "REF-"),
    ("refund", "", "REV OF PMT"),
    ("refund", "", "REV UPI"),
    ("refund", "", "PAYMNT RVSL"),
    ("refund", "", "WDL RVSL"),
    ("refund", "", "IPS REF DT"),
    ("refund", "", "DT:1"),  # partial-charge refund lines
    ("refund", "", "REV PMT"),
    ("refund", "", "REVERSAL"),
    # ---- bank / demat charges (mode-based, not suffix noise) ----
    ("charge", "Bank & DP charges", "DP CHGS"),
    # ---- legacy-religion override (before FAMILY "DURGA" substring match) ----
    ("donation", "Donations & gifts", "[REDACTED]"),
    # ---- family support (checked before generic transport/comms) ----
    *(("family", "Family support", kw) for kw in FAMILY),
    # ---- cash withdrawals ----
    ("cash", "Cash withdrawals", "CASH WDL"),
    ("cash", "Cash withdrawals", "ATM/"),
    # ---- homeowners & utilities ----
    ("home", "Home & maintenance", "5P105"),
    ("home", "Home & maintenance", "ARYA HAMSA"),
    ("home", "Home & maintenance", "DUES"),
    ("home", "Home & maintenance", "HRG"),
    ("home", "Home & maintenance", "BBMP"),
    ("home", "Home & maintenance", "PROP"),
    ("home", "Home & maintenance", "[REDACTED]"),
    ("home", "Home & maintenance", "VISHNU HARAPANA"),
    ("home", "Home & maintenance", "[REDACTED]"),
    ("home", "Home & maintenance", "[REDACTED]"),
    ("home", "Home & maintenance", "[REDACTED]"),
    ("home", "Home & maintenance", "[REDACTED]"),
    ("home", "Home & maintenance", "[REDACTED]"),
    ("home", "Home & maintenance", "[REDACTED]"),
    ("home", "Home & maintenance", "[REDACTED]"),
    ("utilities", "Utilities", "BESCOM"),
    ("utilities", "Utilities", "ELECTRIC"),
    ("utilities", "Utilities", "CYLINDER"),
    ("utilities", "Utilities", "EBIZ.BP"),
    ("utilities", "Utilities", "WATER"),
    # ---- internet & phone ----
    ("comms", "Internet & phone", "RECHARGE"),
    ("comms", "Internet & phone", "TALKTIME"),
    ("comms", "Internet & phone", "AIRTEL"),
    ("comms", "Internet & phone", "AIRT0"),
    ("comms", "Internet & phone", "AIRTE"),
    ("comms", "Internet & phone", "FIXED_LINE"),
    ("comms", "Internet & phone", "MY_MOBI"),
    ("comms", "Internet & phone", "AIRTELPAYMENTS"),
    ("comms", "Internet & phone", "PREPAID"),
    ("comms", "Internet & phone", "DATA_"),
    ("comms", "Internet & phone", "MOB_RE"),
    ("comms", "Internet & phone", "MOBI_RE"),
    # ---- kids & school ----
    ("kids", "Kids & school", "AECS"),
    ("kids", "Kids & school", "MAGNOLIA"),
    ("kids", "Kids & school", "SCHOOL"),
    ("kids", "Kids & school", "PROELIUM"),
    ("kids", "Kids & school", "SCHOOL DRES"),
    ("kids", "Kids & school", "NARAYANA NE"),
    ("kids", "Kids & school", "NARAYANA"),
    ("kids", "Kids & school", "FLIPLEARN"),
    ("kids", "Kids & school", "GRAYQ"),
    ("kids", "Kids & school", "LOVE OF LE"),
    ("kids", "Kids & school", "AMRITH"),
    ("kids", "Kids & school", "[REDACTED]"),
    ("kids", "Kids & school", "[REDACTED]"),
    # ---- learning & upskilling ----
    ("learning", "Learning & upskilling", "PLURALSIGHT"),
    ("learning", "Learning & upskilling", "UDACITY"),
    ("learning", "Learning & upskilling", "FULLSTACK"),
    ("learning", "Learning & upskilling", "HFDESIGN"),
    ("learning", "Learning & upskilling", "HFOOAD"),
    ("learning", "Learning & upskilling", "MOZILLA"),
    ("learning", "Learning & upskilling", "CODE_"),
    ("learning", "Learning & upskilling", "COURSE"),
    ("learning", "Learning & upskilling", "BOOK"),
    # ---- subscriptions & digital ----
    ("digital", "Subscriptions & digital", "PLAYSTORE"),
    ("digital", "Subscriptions & digital", "NETFLIX"),
    ("digital", "Subscriptions & digital", "AMAZON"),
    ("digital", "Subscriptions & digital", "PRIME"),
    ("digital", "Subscriptions & digital", "LINKEDIN"),
    ("digital", "Subscriptions & digital", "TATA_SKY"),
    ("digital", "Subscriptions & digital", "TATASKY"),
    ("digital", "Subscriptions & digital", "TATA SKY"),
    # ---- healthcare ----
    ("health", "Healthcare & pharmacy", "MEDPLUS"),
    ("health", "Healthcare & pharmacy", "APOLLO"),
    ("health", "Healthcare & pharmacy", "MANIPAL"),
    ("health", "Healthcare & pharmacy", "ST JOHNS"),
    ("health", "Healthcare & pharmacy", "DENTAL"),
    ("health", "Healthcare & pharmacy", "OPTOLAB"),
    ("health", "Healthcare & pharmacy", "OPTICAL"),
    ("health", "Healthcare & pharmacy", "NETHRADHAMA"),
    ("health", "Healthcare & pharmacy", "LAKSHMI MED"),
    ("health", "Healthcare & pharmacy", "SUMAN"),
    ("health", "Healthcare & pharmacy", "HEALTHWAY"),
    ("health", "Healthcare & pharmacy", "MEGA XPRESS"),
    ("health", "Healthcare & pharmacy", "SHANTHI"),
    ("health", "Healthcare & pharmacy", "SRL LTD"),
    ("health", "Healthcare & pharmacy", "PIRAMAL"),
    ("health", "Healthcare & pharmacy", "EVER FINE"),
    ("health", "Healthcare & pharmacy", "TRAUMA"),
    ("health", "Healthcare & pharmacy", "EXPRESS HYP"),
    ("health", "Healthcare & pharmacy", "GKB"),
    ("health", "Healthcare & pharmacy", "ASHWAGANDHA"),
    ("health", "Healthcare & pharmacy", "PHARMA"),
    ("health", "Healthcare & pharmacy", "DRUGS"),
    ("health", "Healthcare & pharmacy", "HOSPIT"),
    ("health", "Healthcare & pharmacy", "OTTO BOCK"),
    ("health", "Healthcare & pharmacy", "CLIN"),
    ("health", "Healthcare & pharmacy", "HYGIENE"),
    ("health", "Healthcare & pharmacy", "[REDACTED]"),
    ("health", "Healthcare & pharmacy", "[REDACTED]"),
    # ---- transport & fuel ----
    ("transport", "Transport & fuel", "OLACABS"),
    ("transport", "Transport & fuel", "OLAM"),
    ("transport", "Transport & fuel", "OLAMONE"),
    ("transport", "Transport & fuel", "BOOKING.COM"),
    ("transport", "Transport & fuel", "REDBUS"),
    ("transport", "Transport & fuel", "FUEL"),
    ("transport", "Transport & fuel", "MOBIL SERVI"),
    ("transport", "Transport & fuel", "PETROL"),
    ("transport", "Transport & fuel", "IRB"),
    ("transport", "Transport & fuel", "TOLL"),
    ("transport", "Transport & fuel", "AUTOMOBILES"),
    ("transport", "Transport & fuel", "SIDDHARAJ"),
    ("transport", "Transport & fuel", "BANGALORE M"),
    ("transport", "Transport & fuel", "GARUDA"),
    ("transport", "Transport & fuel", "SRS"),
    ("transport", "Transport & fuel", "OLA"),
    ("transport", "Transport & fuel", "BUS"),
    # ---- travel & leisure (region trips) ----
    ("travel", "Travel & leisure", "ROYAL TOURIST"),
    ("travel", "Travel & leisure", "TIRUPATI"),
    ("travel", "Travel & leisure", "TICKET"),
    ("travel", "Travel & leisure", "_TKT"),
    ("travel", "Travel & leisure", "TRIP"),
    ("travel", "Travel & leisure", "VISIT"),
    ("travel", "Travel & leisure", "RESORTS"),
    ("travel", "Travel & leisure", "DASARA"),
    ("travel", "Travel & leisure", "BLR-CKD"),
    ("travel", "Travel & leisure", "CHIKODI"),
    ("travel", "Travel & leisure", "HARUGERI"),
    ("travel", "Travel & leisure", "DHARWAD"),
    ("travel", "Travel & leisure", "GOIBIBO"),
    ("travel", "Travel & leisure", "VRLONLIN"),
    ("travel", "Travel & leisure", "REDBUS"),
    ("travel", "Travel & leisure", "[REDACTED]"),
    ("travel", "Travel & leisure", "[REDACTED]"),
    # ---- groceries ----
    ("grocery", "Groceries", "[MERCHANT]"),
    ("grocery", "Groceries", "ARJAY"),
    ("grocery", "Groceries", "SM MART"),
    ("grocery", "Groceries", "BIG BASKET"),
    ("grocery", "Groceries", "BIG BAZAAR"),
    ("grocery", "Groceries", "INSTAKART"),
    ("grocery", "Groceries", "MARIYA MART"),
    ("grocery", "Groceries", "MAX RETAIL"),
    ("grocery", "Groceries", "NAGALAKSHMI"),
    ("grocery", "Groceries", "GRAMIN"),
    ("grocery", "Groceries", "MD USMAN"),
    ("grocery", "Groceries", "SWIGGY"),
    ("grocery", "Groceries", "ORIENTAL PA"),
    ("grocery", "Groceries", "STAR BAZAAR"),
    ("grocery", "Groceries", "SRI LAKSHMI"),
    # ---- dining out ----
    ("dining", "Dining out", "KAKAL"),
    ("dining", "Dining out", "TAMARIN"),
    ("dining", "Dining out", "SEA SPICE"),
    ("dining", "Dining out", "EPICURUS"),
    ("dining", "Dining out", "SREE ANNAPO"),
    ("dining", "Dining out", "POLAR BEAR"),
    ("dining", "Dining out", "METRO COOL"),
    ("dining", "Dining out", "LOVELY"),
    ("dining", "Dining out", "NAVAYUGA"),
    ("dining", "Dining out", "KAMAT"),
    ("dining", "Dining out", "SUN TIMES"),
    ("dining", "Dining out", "CELEBRATION"),
    ("dining", "Dining out", "SUKH SAGAR"),
    ("dining", "Dining out", "GURU PALACE"),
    ("dining", "Dining out", "DEJA VU"),
    ("dining", "Dining out", "TANDOORI"),
    ("dining", "Dining out", "CRYSTAL"),
    ("dining", "Dining out", "UTSAV"),
    ("dining", "Dining out", "ANAND SWEET"),
    ("dining", "Dining out", "MULTI CAKES"),
    ("dining", "Dining out", "KANTI SWEET"),
    ("dining", "Dining out", "ANSATA"),
    ("dining", "Dining out", "NANDI ECONO"),
    ("dining", "Dining out", "NANDI HIGH"),
    ("dining", "Dining out", "MTR MAIYAS"),
    ("dining", "Dining out", "NANDHANA"),
    ("dining", "Dining out", "ASHOKA"),
    ("dining", "Dining out", "RIDDHI SIDD"),
    ("dining", "Dining out", "HOTEL"),
    ("dining", "Dining out", "REST"),
    # ---- shopping & household goods ----
    ("shopping", "Shopping & goods", "FLIPKART"),
    ("shopping", "Shopping & goods", "AMAZON@"),
    ("shopping", "Shopping & goods", "IKEA"),
    ("shopping", "Shopping & goods", "STEELCASE"),
    ("shopping", "Shopping & goods", "FEATHERLITE"),
    ("shopping", "Shopping & goods", "VIDEOTRONIC"),
    ("shopping", "Shopping & goods", "MP_FLIPKART"),
    ("shopping", "Shopping & goods", "SHOPPERS"),
    ("shopping", "Shopping & goods", "BATA"),
    ("shopping", "Shopping & goods", "CYCLE WORLD"),
    ("shopping", "Shopping & goods", "LIFE STYLE"),
    ("shopping", "Shopping & goods", "ROYAL GARME"),
    ("shopping", "Shopping & goods", "LATEX"),
    ("shopping", "Shopping & goods", "TOWELS"),
    ("shopping", "Shopping & goods", "USB"),
    ("shopping", "Shopping & goods", "PRINTER"),
    ("shopping", "Shopping & goods", "LAPPIE"),
    ("shopping", "Shopping & goods", "COVERANDGLASS"),
    ("shopping", "Shopping & goods", "MICROTEK"),
    ("shopping", "Shopping & goods", "SENORITA"),
    ("shopping", "Shopping & goods", "INNOVATIVE"),
    ("shopping", "Shopping & goods", "CRACKER"),
    ("shopping", "Shopping & goods", "VIBHA"),
    ("shopping", "Shopping & goods", "VYBHAVA"),
    ("shopping", "Shopping & goods", "PARKVIEW"),
    ("shopping", "Shopping & goods", "FKRT"),
    ("shopping", "Shopping & goods", "ABLOOM"),
    ("shopping", "Shopping & goods", "DIARY"),
    # ---- insurance ----
    ("insurance", "Insurance", "HDFC ERGO"),
    ("insurance", "Insurance", "TATA AIA"),
    ("insurance", "Insurance", "SBILLIFE"),
    ("insurance", "Insurance", "SBI LIFE"),
    ("insurance", "Insurance", "HDFC_PREMIUM"),
    ("insurance", "Insurance", "PREMIUM"),
    # ---- legal & professional ----
    ("legal", "Legal & professional", "SAGARE"),
    ("legal", "Legal & professional", "NOTARY"),
    ("legal", "Legal & professional", "ADVOC"),
    ("legal", "Legal & professional", "FILING"),
    ("legal", "Legal & professional", "DURGEKAR"),
    ("legal", "Legal & professional", "KAANOON"),
    ("legal", "Legal & professional", "KAA."),
    ("legal", "Legal & professional", "LAWYER"),
    ("legal", "Legal & professional", "ANIL KALGI"),
    ("legal", "Legal & professional", "GERAHALLI"),
    ("legal", "Legal & professional", "RAJSHARADHI"),
    ("legal", "Legal & professional", "[REDACTED]"),
    ("legal", "Legal & professional", "[REDACTED]"),
    # ---- donations & gifts ----
    ("donation", "Donations & gifts", "RASTREEYA"),
    ("donation", "Donations & gifts", "RASHTRIYA"),
    ("donation", "Donations & gifts", "SWAYAM"),
    ("donation", "Donations & gifts", "[REDACTED]"),
    ("donation", "Donations & gifts", "[REDACTED]"),
    ("donation", "Donations & gifts", "[REDACTED]"),
    ("donation", "Donations & gifts", "[REDACTED]"),
    # ---- income tax (cash need, but not consumption) ----
    ("tax", "Income tax", "DTAX"),
    # ---- bill aggregators (real recurring spend) ----
    ("bills", "Bills & aggregators", "BBPS"),
    ("bills", "Bills & aggregators", "BILLDESK"),
    ("bills", "Bills & aggregators", "BILLPAY"),
    # ---- unclassified-but-real spend (user rulings) ----
    ("misc", "Misc & small", "[REDACTED]"),
    ("misc", "Misc & small", "[REDACTED]"),
    ("misc", "Misc & small", "[REDACTED]"),
    # ---- vehicles (car purchase + service) ----
    ("cars", "Vehicles", "MANDOV"),
    ("cars", "Vehicles", "Q27701469"),
    ("cars", "Vehicles", "NEO MOTORS"),
    ("cars", "Vehicles", "AANOKHI"),
    ("cars", "Vehicles", "[REDACTED]"),
    # ---- loan EMI repayments ----
    ("loan", "Loan & EMI", "EMI"),
    ("loan", "Loan & EMI", "LOAN_REPAY"),
    ("loan", "Loan & EMI", "HOMELOAN"),
    ("loan", "Loan & EMI", "M_LOAN"),
    ("loan", "Loan & EMI", "FOR_LOAN_AC"),
    ("loan", "Loan & EMI", "SBIN0015644"),
    ("loan", "Loan & EMI", "[REDACTED]"),
    ("loan", "Loan & EMI", "[REDACTED]"),
    ("loan", "Loan & EMI", "[REDACTED]"),
]


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
_MANUAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manual.csv")
if os.path.exists(_MANUAL_PATH):
    with open(_MANUAL_PATH, newline="") as f:
        for r in csv.DictReader(f):
            MANUAL[(str(r.get("DATE","")).strip(),
                    (r.get("PARTICULARS","") or "").strip().upper(),
                    float(r.get("AMOUNT") or 0))] = (r.get("KIND","").strip(),
                                                      r.get("CATEGORY","").strip())


def tag(part, mode, amt=0.0):
    u = part.upper()
    if mode.strip().upper() == "BANK CHARGES":
        return ("expense", "Bank & DP charges")
    if "[MERCHANT]" in u or "[MERCHANT]" in u or "[REDACTED]" in u:
        return ("expense", "Loan & EMI") if amt > 10000 else ("expense", "Transport & fuel")
    for rule in RULES:
        kind, cat, kw = rule
        if kw in u:
            return (kind, "") if cat == "" else ("expense", cat)
    return ("review", "REVIEW")


MISC_MIN = 1000.0  # ponytail: payees totalling less than this/yr of spend -> "Misc & small"


def payee_key(p):
    """Coarse merchant id for lumping/long-tail decisions."""
    if p.startswith("UPI/"):
        m = re.search(r"([A-Za-z0-9._@-]+@[A-Za-z]+)", p)
        return "UPI:" + (m.group(1).split("@")[0] if m else p.split("/")[1])
    t = p.split("/")
    if p.startswith(("VPS/", "IPS/", "VSI/")):
        return t[0] + ":" + t[1]
    if p.startswith("BIL/"):
        return "BIL:" + (t[2] if len(t) > 2 else "?")
    if p.startswith("MMT/"):
        m = re.search(r"([A-Za-z0-9._@-]+@[A-Za-z]+)", p)
        if m:
            return "MMT:" + m.group(1).split("@")[0]
        return "MMT:" + (t[-1] if len(t) > 1 else "?")
    if p.startswith(("NEFT-", "RTGS-")):
        return "BANK:" + p[:25]
    if p.startswith("VIN/") or p.startswith("GIB/"):
        return p.split("/")[0] + ":" + p.split("/")[1]
    return "OTHER:" + p[:24]


def bucket_long_tail(rows):
    rev = [r for r in rows if r["kind"] == "review"]
    totals = {}
    for r in rev:
        totals[payee_key(r["part"])] = totals.get(payee_key(r["part"]), 0.0) + r["wd"]
    for r in rev:
        if totals[payee_key(r["part"])] < MISC_MIN:
            r["kind"], r["category"] = "expense", "Misc & small"


def fy_of(date):
    """FY label from a dd-mm-yyyy date, e.g. 01-04-2017 -> 2017-18."""
    d, m, y = date.split("-")
    yy = int(y) if int(m) >= 4 else int(y) - 1
    return f"{yy}-{str(yy + 1)[-2:]}"


def summarize(rows):
    expense = [r for r in rows if r["kind"] == "expense"]
    refunds = [r for r in rows if r["kind"] == "refund"]
    by_cat = {}
    for r in expense:
        by_cat[r["category"]] = by_cat.get(r["category"], 0.0) + r["wd"]
    for r in refunds:
        c = tag(r["part"], r["mode"], r["dep"] or r["wd"])[1] or "Refunds"
        by_cat[c] = by_cat.get(c, 0.0) - (r["dep"] or 0.0)
    monthly = {}
    for r in expense:
        ym = r["date"][-7:]
        monthly[ym] = monthly.get(ym, 0.0) + r["wd"]
    for r in refunds:
        ym = r["date"][-7:]
        monthly[ym] = monthly.get(ym, 0.0) - (r["dep"] or 0.0)
    kinds = {}
    for r in rows:
        if r["kind"] in ("expense", "review"):
            continue
        kinds[r["kind"]] = kinds.get(r["kind"], 0.0) + r["wd"] + r["dep"]
    return {
        "expense": len(expense), "refund": len(refunds),
        "n_review": sum(1 for r in rows if r["kind"] == "review"),
        "by_cat": by_cat, "monthly": monthly, "kinds": kinds,
        "net": sum(by_cat.values()),
    }


def write_folder_outputs(dr, rows):
    review = [r for r in rows if r["kind"] == "review"]
    s = summarize(rows)
    with open(os.path.join(dr, "classified.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["FILE", "ACCOUNT", "DATE", "MODE", "PARTICULARS",
                    "DEPOSIT", "WITHDRAWAL", "BALANCE", "KIND", "CATEGORY"])
        for r in sorted(rows, key=lambda r: r["date"]):
            w.writerow([r["file"], r["account"], r["date"], r["mode"], r["part"],
                        r["dep"], r["wd"], r["bal"], r["kind"], r["category"]])
    with open(os.path.join(dr, "review_me.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["FILE", "ACCOUNT", "DATE", "MODE", "PARTICULARS",
                    "WITHDRAWAL", "BALANCE"])
        for r in review:
            w.writerow([r["file"], r["account"], r["date"], r["mode"],
                        r["part"], r["wd"], r["bal"]])
    with open(os.path.join(dr, "expense_summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["SECTION", "NAME", "AMOUNT"])
        for c, v in sorted(s["by_cat"].items(), key=lambda kv: -abs(kv[1])):
            if abs(v) > 0.01:
                w.writerow(["category", c, f"{v:.2f}"])
        for ym in sorted(s["monthly"]):
            if abs(s["monthly"][ym]) > 0.01:
                w.writerow(["month", ym, f"{s['monthly'][ym]:.2f}"])
        for k, v in sorted(s["kinds"].items()):
            w.writerow([f"excluded:{k}", "", f"{v:.2f}"])
        w.writerow(["excluded:refund_net", "",
                    f"-{sum(r['dep'] for r in rows if r['kind'] == 'refund'):.2f}"])
        w.writerow(["TOTAL_EXPENSE", "net of refunds", f"{s['net']:.2f}"])


def write_aggregate(by_fy, net_total):
    with open("expense_summary_all_years.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["FY", "SECTION", "NAME", "AMOUNT"])
        for fy in sorted(by_fy):
            s = by_fy[fy]
            w.writerow([fy, "total", "net expense", f"{s['net']:.2f}"])
            w.writerow([fy, "n_review", "", s["n_review"]])
            for c, v in sorted(s["by_cat"].items(), key=lambda kv: -abs(kv[1])):
                if abs(v) > 0.01:
                    w.writerow([fy, "category", c, f"{v:.2f}"])
            for ym in sorted(s["monthly"]):
                if abs(s["monthly"][ym]) > 0.01:
                    w.writerow([fy, "month", ym, f"{s['monthly'][ym]:.2f}"])
        w.writerow(["ALL", "grand_total", "net expense", f"{net_total:.2f}"])
        if by_fy:
            w.writerow(["ALL", "avg_per_fy", "net expense",
                        f"{net_total / len(by_fy):.2f}"])


def find_inputs(args):
    outs = {"classified.csv", "review_me.csv", "expense_summary.csv",
            "expense_summary_all_years.csv"}
    if args:
        return args
    files = []
    for pat in ("*.csv", "*.CSV", "**/*.csv", "**/*.CSV"):
        files += glob.glob(pat, recursive=True)
    seen, keep = set(), []
    for p in sorted(files, key=os.path.normcase):
        if p in seen:
            continue
        seen.add(p)
        if os.path.basename(p).lower() in outs or os.path.dirname(p) == "":
            continue  # outputs and root-level CSVs
        keep.append(p)
    return keep


def review_by_payee(rows):
    agg = {}
    for r in rows:
        if r["kind"] != "review":
            continue
        k = payee_key(r["part"])
        a = agg.setdefault(k, [0, 0.0])
        a[0] += 1
        a[1] += r["wd"]
    return sorted(agg.items(), key=lambda kv: -kv[1][1])


def main():
    by_dir = {}
    by_fy = {}
    all_rows = []
    ok_all = True
    for path in find_inputs(sys.argv[1:]):
        for acct, txs in parse_blocks(path):
            opening, ok, msg, txs = reconcile(acct, txs)
            if not ok:
                ok_all = False
            print(f"{path}  [{acct}]  {msg}  -> {'PASS' if ok else 'FAIL'}")
            if not ok:
                continue
            for t in txs:
                amt = t["wd"] or t["dep"]
                mkey = (t["date"], (t["part"] or "").strip().upper(), amt)
                if mkey in MANUAL:
                    kind, cat = MANUAL[mkey]
                else:
                    kind, cat = tag(t["part"], t["mode"], amt)
                if t["dep"] > 0 and t["wd"] == 0 and kind in ("expense", "review"):
                    kind, cat = "credit", ""  # deposits aren't expenses
                t.update(file=os.path.basename(path), account=acct,
                         kind=kind, category=cat, fy=fy_of(t["date"]))
                by_dir.setdefault(os.path.dirname(path) or ".", []).append(t)
                by_fy.setdefault(t["fy"], []).append(t)
                all_rows.append(t)

    if not ok_all:
        sys.exit(1)  # at least one block failed reconciliation; totals unsafe

    bucket_long_tail(all_rows)
    top_review = review_by_payee(all_rows)

    stats = {fy: summarize(rows) for fy, rows in sorted(by_fy.items())}
    for dr, rows in sorted(by_dir.items()):
        write_folder_outputs(dr, rows)

    net_total = sum(s["net"] for s in stats.values())
    reviewed_total = sum(s["n_review"] for s in stats.values())
    write_aggregate(stats, net_total)

    print()
    print(f"FY".ljust(10) + f"{'net exp':>13}  {'*tx':>5} {'rev':>4}")
    for fy, s in stats.items():
        n = len(by_fy[fy])
        print(f"{fy:<10}{inr(s['net']):>13} {n:>5} {s['n_review']:>4}")
    print(f"{'TOTAL':<10}{inr(net_total):>13}")
    if stats:
        print(f"{'AVG/FY':<10}{inr(net_total / len(stats)):>13}")
        print(f"{'REVIEW':<10}{reviewed_total:>18}")
    if top_review:
        print()
        print(f"TOP REVIEW PAYEES (unclassified debits, {len(top_review)} payees):")
        for k, (n, v) in top_review[:40]:
            print(f"  {k:<28} n={n:>4}  {inr(v)}")
    print()
    print("Wrote: per-folder classified.csv / review_me.csv / expense_summary.csv")
    print("       + expense_summary_all_years.csv (root)")


if __name__ == "__main__":
    main()
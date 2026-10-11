"""Net-spend and incoming arithmetic — the single definition used by the dashboard
(dashboard-specs.md).

Spend rows are `expense` and `transfer`:
  * `expense`  contributes `withdrawal - deposit`: a debit adds, a credit on an
    expense row (a reversal) subtracts, so a credit typed `expense` is never
    silently dropped;
  * `transfer` contributes its `withdrawal` only: a debited transfer is spend
    (default category `Transfer`); the credit side of a transfer is incoming and
    is never netted against spend.
Refunds are not netted against spend: they are incoming, together with credited
`income`, `credit` and `transfer` rows. A credited expense row is not incoming.
Investments are neither spend nor incoming.
"""
SPEND_KINDS = ("expense", "transfer")
INCOMING_KINDS = ("income", "credit", "refund", "transfer")


def delta(r):
    """Signed net-spend contribution of one row (0 for non-spend kinds)."""
    if r["kind"] == "expense":
        return r["wd"] - r["dep"]
    if r["kind"] == "transfer":
        return r["wd"]
    return 0.0


def debits(r):
    """Debit side counted as spend for a row (member breakdown)."""
    return r["wd"] if r["kind"] in SPEND_KINDS else 0.0


def credits(r):
    """Credit side that reduces spend (only a credited expense does)."""
    return r["dep"] if r["kind"] == "expense" else 0.0


def incoming(rows):
    """Credited rows that count as incoming."""
    return [r for r in rows if r["dep"] > 0 and r["kind"] in INCOMING_KINDS]


def incoming_total(rows):
    return sum(r["dep"] for r in incoming(rows))


def by_fy_category(rows):
    """{(fy, category): net spend} over the spend rows."""
    agg = {}
    for r in rows:
        if r["kind"] in SPEND_KINDS:
            k = (r["fy"], r["category"])
            agg[k] = agg.get(k, 0.0) + delta(r)
    return agg


def by_category(rows):
    """{category: net spend} totalled across every financial year in `rows`."""
    agg = {}
    for (_, cat), v in by_fy_category(rows).items():
        agg[cat] = agg.get(cat, 0.0) + v
    return agg


def top_categories(rows, n=8):
    """[(category, net spend)] largest first by magnitude, at most `n`,
    consolidated across years (one entry per category)."""
    agg = by_category(rows)
    return sorted(agg.items(), key=lambda kv: -abs(kv[1]))[:n]


def by_member(rows):
    """{member: {'debits', 'credits', 'net', 'rows'}} so members sum to family."""
    out = {}
    for r in rows:
        if r["kind"] not in SPEND_KINDS:
            continue
        m = out.setdefault(r["member"], {"debits": 0.0, "credits": 0.0,
                                         "net": 0.0, "rows": 0})
        m["debits"] += debits(r)
        m["credits"] += credits(r)
        m["net"] += delta(r)
        m["rows"] += 1
    return out


def total(rows):
    return sum(delta(r) for r in rows)

#!/usr/bin/env python3
"""Classification rules - external, human-authored rules.txt (legacy) and,
since the database spec, manual-rules.csv / auto-rules.csv.

Syntax of a rules file (first matching line wins):
    KIND,CATEGORY,<keywords> [@mode:<M>] [@amt<=<n>|@amt><n>]
  KIND       income|investment|transfer|reimbursed|refund|charge|expense
  CATEGORY   display category; non-empty always classifies as an expense
  keywords   PARTICULARS (uppercased) must contain ANY of these words,
             '|' = any-of; a trailing space is a sentinel ("REF " != "REFILL")
  @mode:<M>  restrict to row MODE == M (uppercased)
  @amt<=<n>  amount must be <= n     @amt><n>  amount must be > n
Lines starting with '#' are comments. .txt files are parsed line-by-line;
.csv files via the csv module (KIND,CATEGORY,EXPRESSION per row).
Rule precedence is manual-rules.csv first, then auto-rules.csv
(RuleSet.combined).

Amount = the row's outgoing amount (withdrawal, or deposit if none).
"""
import csv
import glob
import os
import re

from common import payee_key


class Rule:
    """One match rule; all constraints must hold for it to fire."""

    def __init__(self, kind, category, keywords=(), mode=None, amt_min=None, amt_max=None):
        self.kind = kind
        self.category = category
        self.keywords = tuple(keywords)   # ANY-of must appear in particulars
        self.mode = mode                  # exact MODE when set
        self.amt_min = amt_min            # amount > amt_min
        self.amt_max = amt_max            # amount <= amt_max

    def match(self, part, mode, amt=0.0):
        u = (part or "").upper()
        if self.keywords and not any(k in u for k in self.keywords):
            return False
        if self.mode is not None and mode.strip().upper() != self.mode:
            return False
        if self.amt_min is not None and not amt > self.amt_min:
            return False
        if self.amt_max is not None and not amt <= self.amt_max:
            return False
        return True

    def __repr__(self):
        return f"Rule({self.kind!r}, {self.category!r}, {self.keywords!r})"


class RuleSet:
    """Ordered rule list; the first match wins (falls back to review)."""

    def __init__(self, rules=()):
        self.rules = list(rules)

    @classmethod
    def parse(cls, text):
        rules = []
        for raw in text.splitlines():
            line = raw.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            rules.append(parse_line(line))
        return cls(rules)

    @classmethod
    def from_file(cls, path):
        if str(path).lower().endswith(".csv"):
            return cls.from_csv(path)
        with open(path) as f:
            return cls.parse(f.read())

    @classmethod
    def from_csv(cls, path):
        """Rules from KIND,CATEGORY,EXPRESSION rows (manual- or auto-rules.csv)."""
        rules = []
        with open(path, newline="") as f:
            for row in csv.reader(f):
                if not row or not row[0].strip() or row[0].lstrip().startswith("#"):
                    continue
                if len(row) < 3:
                    raise ValueError(
                        f"rules file: need KIND,CATEGORY,EXPR -> {','.join(row)!r}")
                rules.append(_rule_from_expr(row[0].strip().lower(),
                                             row[1].strip(), row[2]))
        return cls(rules)

    @classmethod
    def combined(cls, manual_path=None, auto_path=None):
        """Manual rules first (highest precedence), then auto rules."""
        rules = []
        for p in (manual_path, auto_path):
            if p and os.path.exists(p):
                rules += cls.from_csv(p).rules
        return cls(rules)

    def match(self, part, mode, amt=0.0):
        """(kind, category), or None when no rule fires (goes to review)."""
        for r in self.rules:
            if r.match(part, mode, amt):
                return ("expense", r.category) if r.category else (r.kind, "")
        return None


class RuleSuggester:
    """Suggests rules.txt lines from the latest review-<N>.csv payees.

    The suggested line is authored by a human: pick/confirm the CATEGORY and
    paste the line into rules.txt, then re-run classify.py.
    """

    def __init__(self, review_path):
        self.path = review_path

    @staticmethod
    def latest():
        """Path of the latest output/review-*.csv, or None."""
        files = sorted(glob.glob(os.path.join("output", "review-*.csv")))
        return files[-1] if files else None

    def by_payee(self):
        rows = []
        with open(self.path, newline="") as f:
            for r in csv.DictReader(f):
                amt = float(r["WITHDRAWAL"] or 0)
                part = r["PARTICULARS"]
                rows.append((payee_key(part), part, amt))
        agg = {}
        for key, part, amt in rows:
            a = agg.setdefault(key, [0, 0.0, ""])
            a[0] += 1
            a[1] += amt
            if not a[2]:
                a[2] = part
        return sorted(agg.items(), key=lambda kv: -kv[1][1])

    def suggested(self, category="CATEGORY", limit=40):
        lines = [f"# {self.path}: {len(list(open(self.path)))-1} review rows"]
        for key, (n, amt, sample) in self.by_payee()[:limit]:
            kw = _suggest_keyword(sample)
            lines.append(f"# {key:<24} n={n:>4}  {amt:>12,.2f}  e.g. {sample[:80]}")
            if kw:
                lines.append(f"expense,{category},{kw}")
        return lines


def _suggest_keyword(sample):
    """A keyword fragment likely to recur: reuse payee_key's id when sane."""
    if sample.startswith("UPI/"):
        return sample.split("/")[1].split("@")[0] if "/" in sample else None
    return sample.split("/")[1][:40] if "/" in sample else (sample or None)[:40]


def parse_line(line):
    fields = line.split(",")
    if len(fields) < 3:
        raise ValueError(f"rules file: need KIND,CATEGORY,EXPR -> {line!r}")
    return _rule_from_expr(fields[0].strip().lower(), fields[1].strip(), fields[2])


def _rule_from_expr(kind, category, expr):
    # Everything before the first '@' is the keyword clause, kept verbatim
    # (a trailing space is a sentinel, e.g. "REF " must not match "REFILL").
    parts = re.split(r"(?:\s+|^)@", expr)
    keywords = [kw for kw in parts[0].split("|") if kw]
    mode = amt_min = amt_max = None
    for q in parts[1:]:
        q = q.strip()
        if q.startswith("mode:"):
            mode = q[5:].upper()
        elif q.startswith("amt<="):
            amt_max = float(q[5:])
        elif q.startswith("amt>"):
            amt_min = float(q[4:])
        else:
            raise ValueError(f"rules file: unknown qualifier {q!r}")
    return Rule(kind, category, keywords, mode, amt_min, amt_max)
"""Auto-rules: read from `auto-rules.csv` at the standard path (the
project folder), never from the database. Pure parsing/matching plus one small
file loader.

One rule per line: `KIND,CATEGORY,EXPRESSION`. The expression is text to find
in PARTICULARS (case-insensitive; `A|B` = any alternative) followed by optional
qualifiers: `@dir:dep|wd` (credit / debit only), `@amt<=N` (also `<`, `>`,
`>=`, `=`), and the legacy `@mode:TEXT`, read as more text to find (payment mode
no longer exists). All qualifiers must hold; the first matching rule wins.
`#` lines and blank lines are ignored; unreadable lines are reported, not fatal.
"""
import os
import re
from dataclasses import dataclass

from .common import BASE

RULES_FILE = os.path.join(BASE, "auto-rules.csv")

_QUAL = re.compile(r"@(dir|amt|mode)(?![A-Za-z0-9])\s*:?\s*", re.I)


@dataclass(frozen=True)
class Rule:
    kind: str
    category: str = ""
    texts: tuple = ()          # any one must be a substring of the particulars
    direction: str = ""        # "", "dep" (credit) or "wd" (debit)
    amount_op: str = ""        # "", "<", "<=", ">", ">=", "="
    amount: float = 0.0
    line: int = 0

    def matches(self, row):
        text = str(row.get("particulars", "")).lower()
        if not any(t.lower() in text for t in self.texts if t):
            return False
        dep, wd = float(row.get("deposit") or 0), float(row.get("withdrawal") or 0)
        if self.direction == "dep" and dep <= 0:
            return False
        if self.direction == "wd" and wd <= 0:
            return False
        if self.amount_op:
            amt = dep if dep > 0 else wd
            ok = {"<": amt < self.amount, "<=": amt <= self.amount,
                  ">": amt > self.amount, ">=": amt >= self.amount,
                  "=": amt == self.amount}[self.amount_op]
            if not ok:
                return False
        return True


def suggest(rules, row):
    """First matching rule for a row, or None."""
    for rule in rules:
        if rule.matches(row):
            return rule
    return None


def apply_rules(rules, rows):
    """Copies of `rows` with kind/category filled by the first matching rule,
    only where both are empty; `rule` is True where a rule supplied them."""
    out = []
    for r in rows:
        r = dict(r, rule=False)
        if not r.get("kind") and not r.get("category"):
            m = suggest(rules, r)
            if m:
                r["kind"], r["category"], r["rule"] = m.kind, m.category, True
        out.append(r)
    return out


def parse_expression(expr):
    """(texts, direction, amount_op, amount) or raise ValueError."""
    parts = _QUAL.split(expr)
    texts = [t for t in parts[0].strip().split("|")]
    direction, op, amount = "", "", 0.0
    # parts after the first: name, value, name, value ...
    for name, value in zip(parts[1::2], parts[2::2]):
        name, value = name.lower(), value.strip()
        if name == "dir":
            d = value.split()[0].lower() if value else ""
            if d not in ("dep", "wd"):
                raise ValueError(f"@dir must be dep or wd, not {value!r}")
            direction = d
            value = value[len(d):].strip()
        elif name == "amt":
            m = re.match(
                r"(<=|>=|<|>|=)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", value)
            if not m:
                raise ValueError(f"bad @amt condition {value!r}")
            op, amount = m.group(1), float(m.group(2).replace(",", ""))
            value = value[m.end():].strip()
        else:  # mode: legacy, now just more text to find
            alt = value.split("@")[0].strip()
            texts += alt.split("|")
            value = ""
        if value:
            raise ValueError(f"unexpected text {value!r}")
    texts = [t.strip() for t in texts if t.strip()]
    if not texts:
        raise ValueError("no text to match")
    return tuple(texts), direction, op, amount


def parse_rules(lines, kinds=None):
    """(rules, warnings) from CSV text lines. `kinds`, when given, is the set
    of valid kinds; a rule with another kind is skipped with a warning."""
    from backend.model import canon_category
    rules, warnings = [], []
    for n, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s or s.lstrip('"').startswith("#"):
            continue
        cols = s.split(",", 2)
        try:
            if len(cols) != 3:
                raise ValueError("expected KIND,CATEGORY,EXPRESSION")
            kind, cat, expr = (c.strip() for c in cols)
            if not kind or (kinds is not None and kind not in kinds):
                raise ValueError(f"unknown kind {kind!r}")
            texts, direction, op, amount = parse_expression(expr)
            rules.append(Rule(kind, canon_category(cat) if cat else "", texts,
                              direction, op, amount, n))
        except ValueError as e:
            warnings.append(f"line {n}: {e}")
    return rules, warnings


def load_rules(path=None, kinds=None):
    """(rules, warnings) from the rules file; a missing file is no rules."""
    path = path or RULES_FILE
    try:
        with open(path, encoding="utf-8-sig") as f:
            return parse_rules(f.read().splitlines(), kinds)
    except OSError:  # missing, or a directory Docker made for a missing bind mount
        return [], []

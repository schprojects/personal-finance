#!/usr/bin/env python3
"""Seed auto-rules.csv from the legacy rules.txt (the initial "major rules").

Run once (part of the P1 harness): every non-comment rules.txt line becomes a
KIND,CATEGORY,EXPRESSION csv row; qualifiers (@mode/@amt) ride along inside the
EXPRESSION column. Before writing, the parsed result must be rule-for-rule
equivalent to the source — otherwise the script exits without writing.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rules import RuleSet


def _key(rule):
    return (rule.kind, rule.category, rule.keywords,
            rule.mode, rule.amt_min, rule.amt_max)


def main():
    src, dst = "rules.txt", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "auto-rules.csv")
    dst = os.path.normpath(dst)
    if not os.path.exists(src):
        sys.exit(f"missing {src}: nothing to seed from")
    text = open(src).read()
    lines = [l for l in text.splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    source = RuleSet.parse("\n".join(lines))

    with open(dst, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["# auto-rules.csv - classification rules for auto mode, "
                    "seeded from rules.txt; appended by LLM consultation"])
        w.writerow(["# KIND", "CATEGORY", "EXPRESSION (@mode/@amt qualifiers)"])
        for line in lines:
            w.writerow(line.split(","))

    seeded = RuleSet.from_csv(dst)
    if [r.kind for r in seeded.rules] != [r.kind for r in source.rules]:
        sys.exit("error: rules diverged in count/order")
    for i, (a, b) in enumerate(zip(source.rules, seeded.rules)):
        if _key(a) != _key(b):
            sys.exit(f"error: rule {i} differs\n  src {_key(a)}\n  csv {_key(b)}")
    print(f"wrote auto-rules.csv ({len(seeded.rules)} rules, equivalence verified)")


if __name__ == "__main__":
    main()
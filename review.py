#!/usr/bin/env python3
"""Show what sits in the latest review-<N>.csv and propose rules.txt lines.

Usage: python3 review.py [N]

Prints payees (grouped by common.payee_key) with row counts and spend, then a
ready-to-paste rules.txt block. Edit rules.txt and re-run classify.py to shrink
the review file towards zero.
"""
import sys

from finance.common import inr
from finance.rules import RuleSuggester


def main():
    su = RuleSuggester(sys.argv[1] if len(sys.argv) > 1 else RuleSuggester.latest())
    if not su.path or not __import__("os").path.exists(su.path):
        sys.exit("no output/review-*.csv found - run classify.py first")

    print(f"# {su.path}\n")
    for key, (n, amt, sample) in su.by_payee():
        print(f"{key:<28} n={n:>4}  {inr(amt):>14}   e.g. {sample[:90]}")
    print("\n# suggested rules.txt additions:")
    print("\n".join(su.suggested(locals().get("category", "CATEGORY"))))


if __name__ == "__main__":
    main()
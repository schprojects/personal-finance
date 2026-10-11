#!/usr/bin/env python3
"""Assemble the deployable dist/ and archive it as dist.zip (deploy-backup-specs.md).

Usage:
    python scripts/make_dist.py                # fresh dist (no database)
    python scripts/make_dist.py --with-db PATH # seed dist/var/networth.db

Ships: personal-finance.py, impex.py, ui_theme.py, .streamlit/ (theme),
finance/ (common,
holdings, classify), backend/ (schema/model/auth/db/paths),
requirements.txt, README.md, and the container files (Dockerfile,
docker-compose.yml, .dockerignore). Runtime dirs (data/, var/, backup/,
output/, tests/, specs/, members.yaml) are intentionally
excluded.
"""
import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

SOURCES = ["personal-finance.py", "impex.py", "ui_theme.py", ".streamlit",
           "finance", "backend",
           "Dockerfile", "docker-compose.yml", ".dockerignore",
           "README.md", "requirements.txt", "auto-rules.example.csv",
           "members.example.yaml", "LICENSE"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--with-db", metavar="PATH",
                    help="seed dist/var/networth.db from this sqlite file")
    args = ap.parse_args()

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    for name in SOURCES:
        src, dst = ROOT / name, DIST / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, dst)

    if (ROOT / "auto-rules.csv").exists():  # personal rules travel with --with-db users
        shutil.copy(ROOT / "auto-rules.csv", DIST / "auto-rules.csv")

    if args.with_db:
        (DIST / "var").mkdir()
        shutil.copy(args.with_db, DIST / "var" / "networth.db")

    zip_path = ROOT / "dist.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(DIST.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(ROOT))

    missing = [n for n in SOURCES + (["var/networth.db"] if args.with_db else [])
               if not (DIST / n).exists()]
    if missing:
        print("MISSING:", *missing, file=sys.stderr)
        return 1
    print(f"dist/ ({len(list(DIST.rglob('*')))} entries) -> {zip_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
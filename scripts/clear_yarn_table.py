#!/usr/bin/env python3
"""
Clear all rows from the yarn table.

Run before a full re-ingest when schema or parsing logic has changed:
  python scripts/clear_yarn_table.py
  python data/ingestion/rrk_yarn_ingestion.py --file /path/to/file.xlsx
"""

from __future__ import annotations

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.yarn_fabric import Yarn

load_project_env()


def main() -> int:
    db = SessionLocal()
    try:
        deleted = db.query(Yarn).delete()
        db.commit()
        print(f"Deleted {deleted} yarn row(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

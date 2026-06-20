"""(Re)create all Artemis SQL views from database/views/*.sql.

Views are derived objects — they hold no data, they are just stored queries.
So they are not created by Alembic migrations or by the SQLite→Postgres data
migration; they must be (re)applied against the live database whenever the schema
is rebuilt or a view definition changes. This script does exactly that, idempotently.

Each .sql file is expected to be self-contained and safe to re-run (the convention
is `DROP VIEW IF EXISTS ...; CREATE VIEW ...`). Files are applied in sorted order
so dependencies can be encoded by filename prefix if ever needed.

The view definitions are PostgreSQL-native. This script honors DATABASE_URL /
POSTGRES_URL from .env, so it targets whatever the app targets.

Usage:
    python scripts/apply_views.py            # uses .env DATABASE_URL / POSTGRES_URL
    python scripts/apply_views.py --target postgresql+psycopg://...
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

VIEWS_DIR = Path(__file__).resolve().parent.parent / "database" / "views"


def _resolve_target(cli_target: str | None) -> str:
    target = cli_target or os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if not target:
        sys.exit("No target database. Set DATABASE_URL/POSTGRES_URL in .env or pass --target.")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="(Re)create all SQL views.")
    parser.add_argument("--target", help="SQLAlchemy URL (defaults to .env DATABASE_URL/POSTGRES_URL)")
    args = parser.parse_args()

    target = _resolve_target(args.target)
    sql_files = sorted(VIEWS_DIR.glob("*.sql"))
    if not sql_files:
        print(f"No .sql files found in {VIEWS_DIR}")
        return 0

    engine = create_engine(target)
    print(f"Applying {len(sql_files)} view file(s) to {engine.url.render_as_string(hide_password=True)}")

    applied = 0
    with engine.begin() as conn:
        for path in sql_files:
            sql = path.read_text()
            conn.execute(text(sql))
            print(f"  ✓ {path.name}")
            applied += 1

    # Verify each view is queryable and report its row count.
    with engine.connect() as conn:
        view_names = [
            r[0]
            for r in conn.execute(
                text("SELECT viewname FROM pg_views WHERE schemaname = 'public' ORDER BY viewname")
            )
        ]
        for name in view_names:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            print(f"  {name}: {count} rows")

    print(f"Done. {applied} file(s) applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

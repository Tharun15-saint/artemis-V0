#!/usr/bin/env python3
"""
One-time repair: demote over-set is_latest rows on append-only tables.

WHY THIS EXISTS
---------------
The append-only contract (.cursorrules Rule 1) requires mark_latest() to demote
all prior rows for an entity key before a new latest row is inserted, so there is
at most one is_latest=True row per entity key. Several *historical backfill*
scripts inserted rows directly without calling mark_latest(), and because the
is_latest column carries `server_default="1"`, every backfilled row kept the
default True. The live ingestion paths (eia_daily, exchangerate_api) demote
correctly; the backfilled products did not. Result (pre-repair):

    crude_oil:  fred_api 2032/2032 + world_bank 797/797 stuck is_latest=True
                → 68 as_of_dates carry >1 latest row
    fx_rates:   yfinance_historical_weekly 2291/2291 + FRED/AV backfill 783/783
                → 3082/3084 rows is_latest=True (a snapshot table whose pairs are
                  COLUMNS, so the latest set should be one most-recent row)

These are the 68 + 1 = 69 "is_latest" criticals reported by scripts/health_check.py.

WHAT THIS DOES  (demote-only, conflict-scoped)
----------------------------------------------
For each table in health_check.APPEND_ONLY_ENTITY_KEYS (the single source of
truth for what "one latest per entity" means), it finds only the entity-key
partitions that currently hold MORE THAN ONE is_latest=True row, keeps the single
most-recent row in each such partition as latest, and demotes the rest to False.
"Most recent" = ORDER BY as_of_date (or effective_date) DESC, then created_at
DESC, then primary key DESC — a deterministic tie-break.

It is deliberately *demote-only and conflict-scoped*:

  * Partitions that already have 0 or 1 latest row are left untouched. We never
    PROMOTE a row the ingestion layer deliberately left non-latest (e.g. the
    10,002 eia_daily rows the live path demoted on purpose, or per-date single
    rows). The bug is over-setting, so the fix only removes excess True flags.
  * Tables whose entity keys include a surrogate primary key (e.g.
    retailer_intelligence_extract → extract_id) put every row in its own
    partition; they can never have a >1-latest partition, so they are no-ops —
    exactly matching the fact that the health check never flags them.

Keyed off the health check's own config so the two cannot drift; idempotent — a
second run finds no >1-latest partitions and changes nothing.

USAGE
-----
    .venv/bin/python -m scripts.migrations.repair_is_latest            # apply
    .venv/bin/python -m scripts.migrations.repair_is_latest --dry-run  # preview

DB is Postgres (DATABASE_URL in .env); is_latest is a real boolean.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from database.base import Base, SessionLocal
from scripts.health_check import APPEND_ONLY_ENTITY_KEYS

# Columns that order rows newest -> oldest within a partition, most significant
# first. Only those present on a given table are used; the primary key is
# appended as the final deterministic tie-break.
RECENCY_ORDER = ["as_of_date", "effective_date", "created_at"]


def _resolve(table_name: str):
    """Return (table, pk_col_names) for a table, or (None, None)."""
    table = Base.metadata.tables.get(table_name)
    if table is None:
        return None, None
    return table, [c.name for c in table.primary_key.columns]


def _order_by(table, pk_cols: list[str]) -> str:
    cols = [c for c in RECENCY_ORDER if c in table.c]
    cols += [pk for pk in pk_cols if pk not in cols]
    return ", ".join(f"{c} DESC" for c in cols)


def _conflict_partitions(db, table_name: str, entity_keys: list[str]) -> int:
    """Count partitions that currently hold >1 is_latest=True row."""
    if entity_keys:
        cols = ", ".join(entity_keys)
        sql = f"""
            SELECT count(*) FROM (
                SELECT {cols} FROM {table_name}
                WHERE is_latest = true
                GROUP BY {cols} HAVING count(*) > 1
            ) c
        """
        return db.execute(text(sql)).scalar() or 0
    # Empty entity keys => the whole table is one partition.
    latest = db.execute(
        text(f"SELECT count(*) FROM {table_name} WHERE is_latest = true")
    ).scalar() or 0
    return 1 if latest > 1 else 0


def _repair_table(db, table_name: str, entity_keys: list[str]) -> int:
    """Demote over-set is_latest rows for one table. Returns rows changed."""
    table, pk_cols = _resolve(table_name)
    if table is None or "is_latest" not in table.c or not pk_cols:
        return 0

    conflicts = _conflict_partitions(db, table_name, entity_keys)
    if conflicts == 0:
        return 0

    order_by = _order_by(table, pk_cols)
    pk_join = " AND ".join(f"t.{pk} = r.{pk}" for pk in pk_cols)

    if entity_keys:
        # Restrict the rewrite to the conflicting partitions only, so nothing
        # outside an over-set group is ever touched.
        keys = ", ".join(entity_keys)
        sql = text(f"""
            WITH conflicts AS (
                SELECT {keys} FROM {table_name}
                WHERE is_latest = true
                GROUP BY {keys} HAVING count(*) > 1
            ),
            ranked AS (
                SELECT {', '.join(pk_cols)},
                       ROW_NUMBER() OVER (
                           PARTITION BY {keys} ORDER BY {order_by}
                       ) AS rn
                FROM {table_name}
                WHERE ({keys}) IN (SELECT {keys} FROM conflicts)
            )
            UPDATE {table_name} t
            SET is_latest = (r.rn = 1)
            FROM ranked r
            WHERE {pk_join}
              AND t.is_latest IS DISTINCT FROM (r.rn = 1)
        """)
    else:
        # Whole table collapses to a single most-recent latest row.
        sql = text(f"""
            WITH ranked AS (
                SELECT {', '.join(pk_cols)},
                       ROW_NUMBER() OVER (ORDER BY {order_by}) AS rn
                FROM {table_name}
            )
            UPDATE {table_name} t
            SET is_latest = (r.rn = 1)
            FROM ranked r
            WHERE {pk_join}
              AND t.is_latest IS DISTINCT FROM (r.rn = 1)
        """)

    changed = db.execute(sql).rowcount or 0
    latest_after = db.execute(
        text(f"SELECT count(*) FROM {table_name} WHERE is_latest = true")
    ).scalar()
    keydesc = ", ".join(entity_keys) if entity_keys else "(whole table)"
    print(
        f"  - {table_name:<32} keys=[{keydesc}]  "
        f"conflict_partitions={conflicts}  demoted={changed}  latest_now={latest_after}"
    )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair over-set is_latest flags.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print changes, then roll back without committing.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    total = 0
    try:
        print(f"\nrepair_is_latest — {'DRY-RUN (no commit)' if args.dry_run else 'APPLY'}")
        print("=" * 78)
        for table_name, entity_keys in APPEND_ONLY_ENTITY_KEYS.items():
            total += _repair_table(db, table_name, entity_keys)
        print("=" * 78)
        if total == 0:
            print("No over-set is_latest partitions found — nothing to repair.")

        if args.dry_run:
            db.rollback()
            print(f"total rows that would be demoted: {total}  (dry-run: rolled back)")
        else:
            db.commit()
            print(f"total rows demoted: {total}  (committed)")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

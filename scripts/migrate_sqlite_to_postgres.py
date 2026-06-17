"""Migrate Artemis from SQLite to PostgreSQL / TimescaleDB.

High-quality, verifiable, re-runnable data migration:

  1. Builds the target schema from the ORM models (typed: JSON->jsonb,
     Numeric->numeric, Boolean->boolean, DateTime->timestamp).
  2. Reflects and recreates the handful of DB-only tables that exist in SQLite
     but have no current ORM model (e.g. cotton_price_observation).
  3. Copies every row, table-by-table, in foreign-key-safe order, with FK
     enforcement deferred during the bulk load.
  4. Resets every serial/identity sequence to MAX(pk)+1 so new inserts don't
     collide with migrated rows.
  5. Copies the alembic_version stamp so the schema history is continuous.
  6. Verifies row counts table-by-table and exits non-zero on any mismatch.

Usage:
    python scripts/migrate_sqlite_to_postgres.py            # uses .env SQLITE_URL/POSTGRES_URL
    python scripts/migrate_sqlite_to_postgres.py --truncate # wipe target tables first (re-run)
    python scripts/migrate_sqlite_to_postgres.py \
        --source sqlite:///./artemis.db \
        --target postgresql+psycopg://artemis:artemis@localhost:5432/artemis

The migration is read-only against SQLite. It never modifies the source.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Ensure the project root is importable when run as `python scripts/...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    DefaultClause,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
    inspect,
    select,
    text,
)

# Importing the models registers all 127 ORM tables on Base.metadata.
from database.base import Base
import database.models  # noqa: F401

load_dotenv()

BATCH = 1000


def log(msg: str) -> None:
    print(msg, flush=True)


def get_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate Artemis SQLite -> Postgres/Timescale")
    p.add_argument("--source", default=os.getenv("SQLITE_URL", "sqlite:///./artemis.db"))
    p.add_argument("--target", default=os.getenv("POSTGRES_URL"))
    p.add_argument(
        "--truncate",
        action="store_true",
        help="TRUNCATE all target tables before copying (use to re-run a clean migration).",
    )
    p.add_argument(
        "--drop",
        action="store_true",
        help="DROP and recreate the target public schema first (fully clean re-run).",
    )
    return p.parse_args()


def reflect_db_only_tables(source_engine, model_table_names):
    """Reflect SQLite tables that have no ORM model so their data also migrates.

    Returns a MetaData containing only those tables (excludes views and the
    alembic_version bookkeeping table, which are handled separately).
    """
    insp = inspect(source_engine)
    sqlite_tables = set(insp.get_table_names())  # excludes views
    leftover = sorted(sqlite_tables - set(model_table_names) - {"alembic_version"})
    extra_meta = MetaData()
    for name in leftover:
        tbl = Table(name, extra_meta, autoload_with=source_engine)
        # SQLite reflection yields backend-specific type names (e.g. DATETIME)
        # that Postgres rejects. Coerce each column to SQLAlchemy's generic type
        # so DDL renders correctly on Postgres (DATETIME->TIMESTAMP, etc.).
        for col in tbl.columns:
            try:
                col.type = col.type.as_generic()
            except (NotImplementedError, TypeError):
                pass
            # These DB-only tables have no ORM model, so there is nothing to drift
            # from: make their strings unbounded to guarantee no length overflow
            # (SQLite never enforced the declared lengths).
            if isinstance(col.type, String):
                col.type.length = None
            # SQLite reflects boolean defaults as integer literals (1/0); Postgres
            # rejects those on a boolean column. Translate to true/false.
            if isinstance(col.type, Boolean) and col.server_default is not None:
                raw = str(getattr(col.server_default, "arg", "")).strip().strip("'")
                if raw in ("0", "1"):
                    col.server_default = DefaultClause(text("true" if raw == "1" else "false"))
    if leftover:
        log(f"  reflected {len(leftover)} DB-only table(s): {', '.join(leftover)}")
    return extra_meta


def copy_table(source_conn, target_conn, table) -> int:
    """Stream all rows of `table` from source to target. Returns row count."""
    rows = source_conn.execute(select(table)).mappings().all()
    if not rows:
        return 0
    payload = [dict(r) for r in rows]
    for i in range(0, len(payload), BATCH):
        target_conn.execute(insert(table), payload[i : i + BATCH])
    return len(payload)


def reset_sequences(target_engine, tables) -> None:
    """Set each serial/identity sequence to MAX(pk)+1 so new inserts don't collide."""
    with target_engine.begin() as conn:
        for table in tables:
            pk_cols = list(table.primary_key.columns)
            if len(pk_cols) != 1:
                continue
            col = pk_cols[0]
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"),
                {"t": table.name, "c": col.name},
            ).scalar()
            if not seq:
                continue  # no sequence (not autoincrement / composite)
            conn.execute(
                text(
                    f'SELECT setval(:seq, COALESCE((SELECT MAX("{col.name}") '
                    f'FROM "{table.name}"), 0) + 1, false)'
                ),
                {"seq": seq},
            )


def main() -> int:
    args = get_args()
    if not args.target:
        log("ERROR: no target set. Pass --target or set POSTGRES_URL in .env")
        return 2

    log(f"Source : {args.source}")
    log(f"Target : {args.target}")
    log("")

    source_engine = create_engine(args.source)
    target_engine = create_engine(args.target, pool_pre_ping=True)

    # Confirm the target is reachable and is actually Postgres.
    with target_engine.connect() as conn:
        ver = conn.execute(text("SELECT version()")).scalar()
        log(f"Connected: {ver.split(',')[0]}")

    if args.drop:
        log("Dropping and recreating target 'public' schema (--drop)...")
        with target_engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

    model_table_names = list(Base.metadata.tables.keys())
    extra_meta = reflect_db_only_tables(source_engine, model_table_names)

    # ---- 1. Create schema on target ------------------------------------------
    log("\n[1/5] Creating schema on target...")
    Base.metadata.create_all(target_engine)
    extra_meta.create_all(target_engine)

    # FK-safe order: model tables first (topologically sorted), then DB-only tables.
    # Reflecting a DB-only table auto-pulls any table it FKs to (e.g. major_retailers)
    # into extra_meta — exclude those so model tables are not copied twice.
    model_names_set = set(Base.metadata.tables.keys())
    extra_tables = [t for t in extra_meta.sorted_tables if t.name not in model_names_set]
    all_tables = list(Base.metadata.sorted_tables) + extra_tables
    log(f"      {len(all_tables)} tables ready.")

    # ---- 2. Optional truncate (for clean re-runs) ----------------------------
    if args.truncate:
        log("\n[2/5] Truncating target tables (--truncate)...")
        names = ", ".join(f'"{t.name}"' for t in all_tables)
        with target_engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    else:
        # Refuse to copy into non-empty tables to avoid silent duplication.
        with target_engine.connect() as conn:
            for t in all_tables:
                n = conn.execute(text(f'SELECT count(*) FROM "{t.name}"')).scalar()
                if n:
                    log(
                        f"\nERROR: target table '{t.name}' already has {n} rows.\n"
                        f"       Re-run with --truncate to overwrite, or drop the DB first."
                    )
                    return 3

    # ---- 3. Copy data (FK checks deferred during bulk load) ------------------
    log("\n[3/5] Copying data...")
    started = time.time()
    totals = {}
    source_table_names = set(inspect(source_engine).get_table_names())
    with source_engine.connect() as src, target_engine.begin() as dst:
        # session_replication_role=replica disables FK/trigger checks for this
        # superuser session so load order edge-cases (self-refs) can't fail.
        dst.execute(text("SET session_replication_role = replica"))
        for t in all_tables:
            if t.name not in source_table_names:
                # Defined in the models but never created in SQLite — target gets
                # it as an empty table, which is correct.
                continue
            n = copy_table(src, dst, t)
            totals[t.name] = n
            if n:
                log(f"      {t.name:<42} {n:>8} rows")
        dst.execute(text("SET session_replication_role = DEFAULT"))
    log(f"      copied {sum(totals.values())} rows in {time.time()-started:.1f}s")

    # ---- 4. Reset sequences --------------------------------------------------
    log("\n[4/5] Resetting identity sequences...")
    reset_sequences(target_engine, all_tables)

    # Copy the alembic stamp so migration history stays continuous.
    with source_engine.connect() as src:
        stamp = src.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if stamp:
        with target_engine.begin() as dst:
            dst.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            dst.execute(text("DELETE FROM alembic_version"))
            dst.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
                {"v": stamp},
            )
        log(f"      alembic_version stamped at {stamp}")

    # ---- 5. Verify row counts ------------------------------------------------
    log("\n[5/5] Verifying row counts...")
    mismatches = []
    with source_engine.connect() as src, target_engine.connect() as dst:
        for t in all_tables:
            s = (
                src.execute(text(f'SELECT count(*) FROM "{t.name}"')).scalar()
                if t.name in source_table_names
                else 0
            )
            d = dst.execute(text(f'SELECT count(*) FROM "{t.name}"')).scalar()
            if s != d:
                mismatches.append((t.name, s, d))
    if mismatches:
        log("\n  ROW COUNT MISMATCHES:")
        for name, s, d in mismatches:
            log(f"    {name:<42} sqlite={s} postgres={d}")
        log("\nMIGRATION FAILED — counts do not match.")
        return 1

    log("\n  ✓ All table row counts match.")
    log("\nMIGRATION COMPLETE.")
    log("Next: run scripts/timescale_setup.py to convert time-series tables to hypertables,")
    log("then switch DATABASE_URL in .env to the Postgres URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Convert Artemis time-series tables into TimescaleDB hypertables.

Run AFTER scripts/migrate_sqlite_to_postgres.py has loaded the data.

TimescaleDB partitions a table by a time column ("hypertable"), which makes
time-range queries and retention/compression dramatically faster. Its one hard
rule: the partitioning column must be part of every UNIQUE / PRIMARY KEY. Each
target table here has a surrogate integer PK (e.g. crude_oil_id) and a natural
time column (e.g. as_of_date), so for each table we:

  1. enable the timescaledb extension (idempotent),
  2. make the time column NOT NULL (required by Timescale),
  3. replace the single-column PK with a composite (pk, time_col),
  4. call create_hypertable(..., migrate_data => true).

All targets are leaf tables (nothing foreign-keys into them), so changing their
primary key is safe. The script is idempotent: tables already converted are
detected and skipped.

Usage:
    python scripts/timescale_setup.py            # uses .env DATABASE_URL / POSTGRES_URL
    python scripts/timescale_setup.py --target postgresql+psycopg://...
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

# (table, time_column, pk_column, chunk_interval)
HYPERTABLES = [
    ("crude_oil", "as_of_date", "crude_oil_id", "1 year"),
    ("cotton", "as_of_date", "cotton_id", "1 year"),
    ("cotton_price_observation", "as_of_date", "observation_id", "1 year"),
    ("fx_rates", "as_of_date", "fx_rate_id", "1 year"),
    ("fx_forward_curve", "as_of_date", "fwd_id", "3 months"),
    ("fx_volatility", "as_of_date", "vol_id", "1 year"),
    ("fx_interest_rates", "as_of_date", "ir_id", "1 year"),
    ("bunker_fuel_prices", "as_of_date", "bunker_price_id", "1 year"),
    ("retailer_stock_prices", "price_date", "stock_price_id", "1 year"),
    ("cftc_cotton_cot", "report_date", "cot_id", "1 year"),
    ("px_paraxylene", "as_of_date", "px_id", "1 year"),
    ("pta", "as_of_date", "pta_id", "1 year"),
    ("polyester_pet_chips", "as_of_date", "chip_id", "1 year"),
    ("cotton_region_weather", "week_ending", "weather_id", "1 year"),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def is_hypertable(conn, table: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = :t"
            ),
            {"t": table},
        ).scalar()
    )


def table_exists(conn, table: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar())


def pk_constraint_name(conn, table: str):
    return conn.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = to_regclass(:t) AND contype = 'p'"
        ),
        {"t": table},
    ).scalar()


def convert(conn, table, time_col, pk_col, chunk):
    if not table_exists(conn, table):
        log(f"  - {table:<30} SKIP (table not found)")
        return
    if is_hypertable(conn, table):
        log(f"  ✓ {table:<30} already a hypertable")
        return

    # 2. time column NOT NULL
    conn.execute(text(f'ALTER TABLE "{table}" ALTER COLUMN "{time_col}" SET NOT NULL'))

    # 3. composite PK (pk_col, time_col)
    existing_pk = pk_constraint_name(conn, table)
    if existing_pk:
        conn.execute(text(f'ALTER TABLE "{table}" DROP CONSTRAINT "{existing_pk}"'))
    conn.execute(
        text(
            f'ALTER TABLE "{table}" ADD PRIMARY KEY ("{pk_col}", "{time_col}")'
        )
    )

    # 4. create hypertable, moving existing rows into chunks
    conn.execute(
        text(
            "SELECT create_hypertable(:t, :c, "
            "chunk_time_interval => CAST(:chunk AS INTERVAL), migrate_data => true)"
        ),
        {"t": table, "c": time_col, "chunk": chunk},
    )
    n_chunks = conn.execute(
        text(
            "SELECT count(*) FROM timescaledb_information.chunks "
            "WHERE hypertable_name = :t"
        ),
        {"t": table},
    ).scalar()
    log(f"  ✓ {table:<30} hypertable on {time_col}  ({n_chunks} chunks)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--target",
        default=os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL"),
    )
    args = p.parse_args()
    if not args.target or args.target.startswith("sqlite"):
        log("ERROR: target must be a Postgres URL (set DATABASE_URL/POSTGRES_URL).")
        return 2

    engine = create_engine(args.target, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        ver = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar()
        log(f"TimescaleDB extension: {ver}\n")
        log("Converting time-series tables to hypertables:")
        for table, time_col, pk_col, chunk in HYPERTABLES:
            convert(conn, table, time_col, pk_col, chunk)

    log("\nHypertable setup complete.")
    log(
        "\nNOTE: hypertable tables now have a composite primary key "
        "(id, time_col).\nTheir ORM models must declare the same composite PK so "
        "Alembic autogenerate\ndoes not report drift. See docs/POSTGRES_MIGRATION.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

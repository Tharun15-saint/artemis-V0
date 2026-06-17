"""One-time cleanup for crude_oil table.

Run AFTER: alembic upgrade a9b0c1d2e3f4

Fixes:
  1. Duplicate rows: marks lower-id rows for the same as_of_date as is_latest=False.
     Root cause was that 'source' was included in value_kwargs for is_duplicate_row,
     so when the source string changed between script versions, duplicates slipped through.

  2. trend_30d_pct backfill: computes (brent_now - brent_30d_ago) / brent_30d_ago × 100
     for all rows where it is NULL.

  3. INR backfill: populates brent_inr_per_barrel, wti_inr_per_barrel,
     fx_usd_inr_at_ingestion, brent_wti_spread_usd for all rows where NULL.
     Uses closest fx_rates row (by date) for each crude row.
     Rows predating the fx_rates history (before 2004-01-01) will remain NULL.

Usage:
    python -m data.ingestion.crude_oil_cleanup
    python -m data.ingestion.crude_oil_cleanup --dry-run
"""

import argparse
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import asc, desc, text
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models import CrudeOil, FxRates

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def fix_duplicates(db: Session, dry_run: bool) -> int:
    """For each as_of_date with >1 is_latest=True row, keep the highest crude_oil_id
    and mark the rest is_latest=False. The highest ID is the most recently inserted row."""
    rows = db.execute(text("""
        SELECT as_of_date, COUNT(*) AS cnt
        FROM crude_oil
        WHERE is_latest = 1
        GROUP BY as_of_date
        HAVING cnt > 1
        ORDER BY as_of_date
    """)).fetchall()

    if not rows:
        logger.info("No duplicate is_latest=True rows found.")
        return 0

    fixed = 0
    for row in rows:
        as_of = row[0]
        cnt = row[1]
        dupes = (
            db.query(CrudeOil)
            .filter(CrudeOil.as_of_date == as_of, CrudeOil.is_latest == True)
            .order_by(desc(CrudeOil.crude_oil_id))
            .all()
        )
        # Keep the first (highest ID), mark the rest False
        to_demote = dupes[1:]
        ids = [r.crude_oil_id for r in to_demote]
        logger.info(
            f"as_of_date={as_of}: {cnt} duplicates → keeping id={dupes[0].crude_oil_id}, "
            f"demoting {ids}"
        )
        if not dry_run:
            for r in to_demote:
                r.is_latest = False
            db.flush()
        fixed += len(to_demote)

    if not dry_run:
        db.commit()
        logger.info(f"Duplicates fixed: {fixed} rows marked is_latest=False")
    else:
        logger.info(f"[dry-run] Would mark {fixed} rows as is_latest=False")
    return fixed


def _usd_inr_for_date(db: Session, as_of: date) -> Optional[Decimal]:
    """Find the closest fx_rates row (by date) within 7 days of as_of."""
    candidates = (
        db.query(FxRates)
        .filter(
            FxRates.usd_inr.isnot(None),
            FxRates.as_of_date >= as_of - timedelta(days=7),
            FxRates.as_of_date <= as_of + timedelta(days=7),
        )
        .all()
    )
    if not candidates:
        return None
    closest = min(candidates, key=lambda r: abs((r.as_of_date - as_of).days))
    return closest.usd_inr


def _brent_30d_ago(db: Session, as_of: date) -> Optional[Decimal]:
    """Find Brent closest to 30 days before as_of (±7 day window) among is_latest rows."""
    target = as_of - timedelta(days=30)
    candidates = (
        db.query(CrudeOil)
        .filter(
            CrudeOil.is_latest == True,
            CrudeOil.brent_spot.isnot(None),
            CrudeOil.as_of_date >= target - timedelta(days=7),
            CrudeOil.as_of_date <= target + timedelta(days=7),
        )
        .all()
    )
    if not candidates:
        return None
    closest = min(candidates, key=lambda r: abs((r.as_of_date - target).days))
    return closest.brent_spot


def backfill_derived_fields(db: Session, dry_run: bool) -> dict:
    """Backfill trend_30d_pct, spread, and INR fields for all is_latest=True rows."""
    rows = (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest == True)
        .order_by(asc(CrudeOil.as_of_date))
        .all()
    )

    trend_filled = 0
    inr_filled = 0
    spread_filled = 0
    batch_size = 100

    for i, row in enumerate(rows):
        changed = False

        # Spread
        if row.brent_wti_spread_usd is None and row.brent_spot and row.wti_spot:
            spread = row.brent_spot - row.wti_spot
            if not dry_run:
                row.brent_wti_spread_usd = spread
            spread_filled += 1
            changed = True

        # Trend
        if row.trend_30d_pct is None and row.brent_spot and row.as_of_date:
            brent_30d = _brent_30d_ago(db, row.as_of_date)
            if brent_30d and brent_30d > 0:
                trend = (row.brent_spot - brent_30d) / brent_30d * Decimal("100")
                if not dry_run:
                    row.trend_30d_pct = round(trend, 2)
                trend_filled += 1
                changed = True

        # INR
        if row.brent_inr_per_barrel is None and row.brent_spot and row.as_of_date:
            usd_inr = _usd_inr_for_date(db, row.as_of_date)
            if usd_inr:
                if not dry_run:
                    row.brent_inr_per_barrel = row.brent_spot * usd_inr
                    row.wti_inr_per_barrel = row.wti_spot * usd_inr if row.wti_spot else None
                    row.fx_usd_inr_at_ingestion = usd_inr
                inr_filled += 1
                changed = True

        if changed and not dry_run and (i + 1) % batch_size == 0:
            db.flush()
            logger.info(f"Processed {i + 1}/{len(rows)} rows...")

    if not dry_run:
        db.commit()

    summary = {
        "total_rows": len(rows),
        "spread_filled": spread_filled,
        "trend_filled": trend_filled,
        "inr_filled": inr_filled,
    }
    logger.info(
        f"{'[dry-run] ' if dry_run else ''}Backfill complete: "
        f"{spread_filled} spread | {trend_filled} trend | {inr_filled} INR rows updated"
    )
    return summary


def run(dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        logger.info("=== Step 1: Fix duplicate rows ===")
        fix_duplicates(db, dry_run)

        logger.info("=== Step 2: Backfill derived fields (spread, trend, INR) ===")
        result = backfill_derived_fields(db, dry_run)

        logger.info(f"Cleanup complete: {result}")
    except Exception as exc:
        logger.critical(f"Cleanup failed: {exc}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="One-time crude_oil table cleanup")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing to the database.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)

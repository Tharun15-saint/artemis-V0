"""
Backfill comparable_sales_growth_pct on Walmart retailer_financials from 8-K earnings releases.

Updates is_latest rows in place (no append).
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Optional

from sqlalchemy.orm import Session

from data.ingestion import walmart_tier1_ingestion as tier1
from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.ingestion_context import IngestionContext
from database.models.retail import MajorRetailers, RetailerFinancials

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "walmart-comp-sales-backfill-v1.0"
SOURCE_NAME = "walmart_comp_sales_backfill"
SEC_FETCH_DELAY_SECONDS = 0.15


def _get_walmart_retailer_id(db: Session) -> Optional[int]:
    retailer = (
        db.query(MajorRetailers)
        .filter(MajorRetailers.name == tier1.WALMART_NAME)
        .first()
    )
    if retailer is None:
        logger.error("Walmart Inc not found in major_retailers")
        return None
    return retailer.retailer_id


def _quarter_label(row: RetailerFinancials) -> str:
    return f"FY{row.fiscal_year} Q{row.fiscal_quarter}"


def count_backfill_candidates(db: Session) -> int:
    retailer_id = _get_walmart_retailer_id(db)
    if retailer_id is None:
        return 0
    return (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.comparable_sales_growth_pct.is_(None),
            RetailerFinancials.source_8k_url.isnot(None),
        )
        .count()
    )


def run_comp_sales_backfill(db: Session, ctx: IngestionContext) -> dict[str, int]:
    retailer_id = _get_walmart_retailer_id(db)
    if retailer_id is None:
        return {
            "candidates": 0,
            "updated": 0,
            "not_found": 0,
            "skipped_no_url": 0,
            "fetch_failed": 0,
        }

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.comparable_sales_growth_pct.is_(None),
            RetailerFinancials.source_8k_url.isnot(None),
        )
        .order_by(
            RetailerFinancials.period_end_date.desc(),
            RetailerFinancials.fiscal_year.desc(),
            RetailerFinancials.fiscal_quarter.desc(),
        )
        .all()
    )

    stats = {
        "candidates": len(rows),
        "updated": 0,
        "not_found": 0,
        "skipped_no_url": 0,
        "fetch_failed": 0,
    }

    if not rows:
        logger.info(
            "No Walmart rows with NULL comparable_sales_growth_pct and source_8k_url"
        )
        return stats

    logger.info(
        "Walmart comparable sales backfill — %d candidate(s), newest first",
        len(rows),
    )

    for row in rows:
        label = _quarter_label(row)
        if not row.source_8k_url:
            stats["skipped_no_url"] += 1
            logger.info("%s: skipped — no source_8k_url", label)
            continue

        html = tier1._sec_get(row.source_8k_url)
        time.sleep(SEC_FETCH_DELAY_SECONDS)

        if not isinstance(html, str) or not html.strip():
            stats["fetch_failed"] += 1
            logger.warning("%s: SEC fetch failed for %s", label, row.source_8k_url)
            continue

        metrics = tier1._parse_earnings_release(
            html,
            fiscal_quarter=row.fiscal_quarter,
        )
        comp_pct = metrics.get("comparable_sales_growth_pct")
        if comp_pct is None:
            stats["not_found"] += 1
            logger.info(
                "%s: comparable sales not found in %s",
                label,
                row.source_8k_url,
            )
            continue

        row.comparable_sales_growth_pct = comp_pct
        db.commit()
        ctx.inserted()
        stats["updated"] += 1
        logger.info(
            "%s: comparable_sales_growth_pct=%s (%s)",
            label,
            comp_pct,
            row.source_8k_url,
        )

    logger.info(
        "Walmart comparable sales backfill complete — candidates=%d updated=%d "
        "not_found=%d skipped_no_url=%d fetch_failed=%d",
        stats["candidates"],
        stats["updated"],
        stats["not_found"],
        stats["skipped_no_url"],
        stats["fetch_failed"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill Walmart comparable_sales_growth_pct from 8-K earnings releases"
        )
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print candidate count and exit without fetching SEC filings",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        candidates = count_backfill_candidates(db)
        logger.info("Backfill candidates: %d", candidates)

        if args.count_only:
            return 0

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=tier1._SUBMISSIONS_URL,
            db=db,
        ) as ctx:
            stats = run_comp_sales_backfill(db, ctx)
        logger.info("Done — %s", stats)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

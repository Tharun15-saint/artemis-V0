"""
Backfill operating_margin_pct on Target retailer_financials from 10-Q MD&A.

Updates is_latest rows in place (no append).
"""

from __future__ import annotations

import argparse
import logging
import time
from typing import Optional

from sqlalchemy.orm import Session

from data.ingestion import target_tier1_ingestion as tier1
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

SCRIPT_VERSION = "target-operating-margin-backfill-v1.0"
SOURCE_NAME = "target_operating_margin_backfill"
SEC_FETCH_DELAY_SECONDS = 0.15

# Pattern 1 (priority): Operating income margin rate X % / (X) %
# Pattern 2 (fallback):  (operating income / total_net_sales_usd) × 100
# Both reject values outside -5% to +15%.


def _get_target_retailer_id(db: Session) -> Optional[int]:
    retailer = (
        db.query(MajorRetailers)
        .filter(MajorRetailers.name == tier1.TARGET_NAME)
        .first()
    )
    if retailer is None:
        logger.error("Target Corporation not found in major_retailers")
        return None
    return retailer.retailer_id


def _quarter_label(row: RetailerFinancials) -> str:
    return f"FY{row.fiscal_year} Q{row.fiscal_quarter}"


def count_backfill_candidates(db: Session) -> int:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return 0
    return (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.operating_margin_pct.is_(None),
            RetailerFinancials.source_10q_url.isnot(None),
        )
        .count()
    )


def run_operating_margin_backfill(db: Session, ctx: IngestionContext) -> dict[str, int]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return {
            "candidates": 0,
            "updated_rate_table": 0,
            "updated_derivation": 0,
            "not_found": 0,
            "fetch_failed": 0,
        }

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.operating_margin_pct.is_(None),
            RetailerFinancials.source_10q_url.isnot(None),
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
        "updated_rate_table": 0,
        "updated_derivation": 0,
        "not_found": 0,
        "fetch_failed": 0,
    }

    if not rows:
        logger.info(
            "No Target rows with NULL operating_margin_pct and source_10q_url"
        )
        return stats

    logger.info(
        "Operating margin backfill — %d candidate(s), newest first",
        len(rows),
    )

    for row in rows:
        label = _quarter_label(row)
        html = tier1._sec_get(row.source_10q_url)
        time.sleep(SEC_FETCH_DELAY_SECONDS)

        if not isinstance(html, str) or not html.strip():
            stats["fetch_failed"] += 1
            logger.warning("%s: SEC fetch failed for %s", label, row.source_10q_url)
            continue

        text = tier1._strip_html(html)
        margin_pct, pattern = tier1._parse_operating_margin_pct_from_10q_text(text)
        if margin_pct is None and row.total_net_sales_usd is not None:
            margin_pct, pattern = tier1._derive_operating_margin_pct_from_income_statement(
                text,
                row.total_net_sales_usd,
            )

        if margin_pct is None:
            stats["not_found"] += 1
            logger.info(
                "%s: operating margin not found in %s",
                label,
                row.source_10q_url,
            )
            continue

        row.operating_margin_pct = margin_pct
        db.commit()
        ctx.inserted()

        if pattern in ("rate_analysis_table", "ebit_rate_analysis_table"):
            stats["updated_rate_table"] += 1
        else:
            stats["updated_derivation"] += 1

        logger.info(
            "%s: operating_margin_pct=%s via %s",
            label,
            margin_pct,
            pattern,
        )

    logger.info(
        "Operating margin backfill complete — candidates=%d rate_table=%d "
        "derivation=%d not_found=%d fetch_failed=%d",
        stats["candidates"],
        stats["updated_rate_table"],
        stats["updated_derivation"],
        stats["not_found"],
        stats["fetch_failed"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill Target operating_margin_pct from 10-Q filings"
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
            stats = run_operating_margin_backfill(db, ctx)
        logger.info("Done — %s", stats)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

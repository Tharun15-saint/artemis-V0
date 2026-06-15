"""
Derive Target apparel_revenue_usd from Sales by Product Category mix % × total_net_sales_usd.

For quarters where the 10-Q/10-K discloses apparel mix as a percentage (FY2013–FY2017
era and similar) but not a dollar amount. Updates is_latest rows in place.

Derived rows are flagged via retailer_financials.source = DERIVED_SOURCE_FLAG.
"""

from __future__ import annotations

import argparse
import logging
import time
from decimal import Decimal, ROUND_HALF_UP
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

SCRIPT_VERSION = "target-apparel-mix-derivation-v1.0"
SOURCE_NAME = "target_apparel_mix_derivation"
DERIVED_SOURCE_FLAG = "mix_table_derived"
SEC_FETCH_DELAY_SECONDS = 0.15

_MIX_PCT_MIN = Decimal("0.05")
_MIX_PCT_MAX = Decimal("0.50")


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


def _derive_apparel_revenue_usd(
    total_net_sales_usd: Decimal,
    apparel_mix_pct: Decimal,
) -> Decimal:
    return (total_net_sales_usd * apparel_mix_pct).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def count_derivation_candidates(
    db: Session,
    fiscal_year_min: Optional[int] = None,
    fiscal_year_max: Optional[int] = None,
) -> int:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return 0

    query = db.query(RetailerFinancials).filter(
        RetailerFinancials.retailer_id == retailer_id,
        RetailerFinancials.is_latest.is_(True),
        RetailerFinancials.apparel_revenue_usd.is_(None),
        RetailerFinancials.total_net_sales_usd.isnot(None),
        RetailerFinancials.source_10q_url.isnot(None),
    )
    if fiscal_year_min is not None:
        query = query.filter(RetailerFinancials.fiscal_year >= fiscal_year_min)
    if fiscal_year_max is not None:
        query = query.filter(RetailerFinancials.fiscal_year <= fiscal_year_max)
    return query.count()


def run_apparel_mix_derivation(
    db: Session,
    ctx: IngestionContext,
    fiscal_year_min: Optional[int] = None,
    fiscal_year_max: Optional[int] = None,
) -> dict[str, int]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return {
            "candidates": 0,
            "updated": 0,
            "not_found": 0,
            "invalid_mix": 0,
            "skipped_no_url": 0,
            "fetch_failed": 0,
        }

    query = db.query(RetailerFinancials).filter(
        RetailerFinancials.retailer_id == retailer_id,
        RetailerFinancials.is_latest.is_(True),
        RetailerFinancials.apparel_revenue_usd.is_(None),
        RetailerFinancials.total_net_sales_usd.isnot(None),
        RetailerFinancials.source_10q_url.isnot(None),
    )
    if fiscal_year_min is not None:
        query = query.filter(RetailerFinancials.fiscal_year >= fiscal_year_min)
    if fiscal_year_max is not None:
        query = query.filter(RetailerFinancials.fiscal_year <= fiscal_year_max)

    rows = (
        query.order_by(
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
        "invalid_mix": 0,
        "skipped_no_url": 0,
        "fetch_failed": 0,
    }

    if not rows:
        logger.info("No Target rows eligible for apparel mix derivation")
        return stats

    logger.info(
        "Apparel mix derivation — %d candidate(s), newest first",
        len(rows),
    )

    for row in rows:
        label = _quarter_label(row)
        if not row.source_10q_url:
            stats["skipped_no_url"] += 1
            logger.info("%s: skipped — no source_10q_url", label)
            continue

        html = tier1._sec_get(row.source_10q_url)
        time.sleep(SEC_FETCH_DELAY_SECONDS)
        if not isinstance(html, str) or not html.strip():
            stats["fetch_failed"] += 1
            logger.warning(
                "%s: SEC fetch failed for %s",
                label,
                row.source_10q_url,
            )
            continue

        text = tier1._strip_html(html)
        apparel_mix_pct = tier1._parse_apparel_mix_pct_total_from_text(text)
        if apparel_mix_pct is None:
            stats["not_found"] += 1
            logger.info(
                "%s: apparel mix %% not found in %s",
                label,
                row.source_10q_url,
            )
            continue

        if apparel_mix_pct < _MIX_PCT_MIN or apparel_mix_pct > _MIX_PCT_MAX:
            stats["invalid_mix"] += 1
            logger.info(
                "%s: apparel mix %% out of range (%s) — skipping",
                label,
                apparel_mix_pct,
            )
            continue

        apparel_revenue_usd = _derive_apparel_revenue_usd(
            row.total_net_sales_usd,
            apparel_mix_pct,
        )
        row.apparel_revenue_usd = apparel_revenue_usd
        row.apparel_revenue_pct_total = apparel_mix_pct
        row.source = DERIVED_SOURCE_FLAG
        row.data_source_url = row.source_10q_url
        db.commit()
        ctx.inserted()
        stats["updated"] += 1
        logger.info(
            "%s: [DERIVED] mix=%s total_sales=%s apparel_revenue_usd=%s (%s)",
            label,
            apparel_mix_pct,
            row.total_net_sales_usd,
            apparel_revenue_usd,
            row.source_10q_url,
        )

    logger.info(
        "Apparel mix derivation complete — candidates=%d updated=%d not_found=%d "
        "invalid_mix=%d skipped_no_url=%d fetch_failed=%d",
        stats["candidates"],
        stats["updated"],
        stats["not_found"],
        stats["invalid_mix"],
        stats["skipped_no_url"],
        stats["fetch_failed"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Derive Target apparel_revenue_usd from Sales by Product Category "
            "mix percentage × total_net_sales_usd"
        )
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print candidate count and exit without SEC fetches",
    )
    parser.add_argument(
        "--fiscal-year-min",
        type=int,
        default=None,
        help="Optional minimum fiscal year (e.g. 2013)",
    )
    parser.add_argument(
        "--fiscal-year-max",
        type=int,
        default=None,
        help="Optional maximum fiscal year (e.g. 2017)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        count = count_derivation_candidates(
            db,
            fiscal_year_min=args.fiscal_year_min,
            fiscal_year_max=args.fiscal_year_max,
        )
        logger.info("Derivation candidates: %d", count)
        if args.count_only:
            return 0

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=tier1._SUBMISSIONS_URL,
            db=db,
        ) as ctx:
            stats = run_apparel_mix_derivation(
                db,
                ctx,
                fiscal_year_min=args.fiscal_year_min,
                fiscal_year_max=args.fiscal_year_max,
            )
        logger.info("Done — %s", stats)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

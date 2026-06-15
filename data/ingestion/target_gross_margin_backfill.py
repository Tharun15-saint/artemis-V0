"""
Backfill gross_margin_pct on Target retailer_financials from 10-Q MD&A,
then derive inventory_days where inputs are available.

Updates is_latest rows in place (no append).
"""

from __future__ import annotations

import argparse
import logging
import time
from decimal import Decimal
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

SCRIPT_VERSION = "target-gross-margin-backfill-v1.0"
SOURCE_NAME = "target_gross_margin_backfill"
SEC_FETCH_DELAY_SECONDS = 0.15
QUARTER_DAYS = Decimal("91.25")

# Pattern 1 (priority): Gross margin rate\s+([\d.]+)\s*%
# Pattern 2 (fallback):  gross margin rate was\s+([\d.]+)\s*percent
# Both reject values outside 20%–40% (see _parse_gross_margin_pct_from_10q_text).


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


def _compute_inventory_days(
    inventory_usd: Decimal,
    total_net_sales_usd: Decimal,
    gross_margin_pct: Decimal,
) -> Optional[Decimal]:
    cogs_usd = total_net_sales_usd * (Decimal("1") - gross_margin_pct / Decimal("100"))
    if cogs_usd <= 0:
        return None
    daily_cogs = cogs_usd / QUARTER_DAYS
    if daily_cogs <= 0:
        return None
    return (inventory_usd / daily_cogs).quantize(Decimal("0.01"))


def run_gross_margin_backfill(db: Session, ctx: IngestionContext) -> dict[str, int]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return {
            "candidates": 0,
            "updated_rate_table": 0,
            "updated_narrative": 0,
            "not_found": 0,
            "fetch_failed": 0,
        }

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.gross_margin_pct.is_(None),
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
        "updated_narrative": 0,
        "not_found": 0,
        "fetch_failed": 0,
    }

    if not rows:
        logger.info("No Target rows with NULL gross_margin_pct and source_10q_url")
        return stats

    logger.info(
        "Gross margin backfill — %d candidate(s), newest first",
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
        gross_margin_pct, pattern, change_bps = tier1._parse_gross_margin_pct_from_10q_text(
            text
        )
        if gross_margin_pct is None:
            stats["not_found"] += 1
            logger.info(
                "%s: gross margin not found in %s",
                label,
                row.source_10q_url,
            )
            continue

        row.gross_margin_pct = gross_margin_pct
        if change_bps is not None:
            row.gross_margin_change_bps = change_bps
        db.commit()
        ctx.inserted()

        if pattern == "rate_analysis_table":
            stats["updated_rate_table"] += 1
        else:
            stats["updated_narrative"] += 1

        logger.info(
            "%s: gross_margin_pct=%s via %s%s",
            label,
            gross_margin_pct,
            pattern,
            f" change_bps={change_bps}" if change_bps is not None else "",
        )

    logger.info(
        "Gross margin backfill complete — candidates=%d rate_table=%d narrative=%d "
        "not_found=%d fetch_failed=%d",
        stats["candidates"],
        stats["updated_rate_table"],
        stats["updated_narrative"],
        stats["not_found"],
        stats["fetch_failed"],
    )
    return stats


def run_inventory_days_backfill(db: Session, ctx: IngestionContext) -> dict[str, int]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return {"candidates": 0, "updated": 0, "skipped_invalid": 0}

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.inventory_days.is_(None),
            RetailerFinancials.inventory_usd.isnot(None),
            RetailerFinancials.gross_margin_pct.isnot(None),
            RetailerFinancials.total_net_sales_usd.isnot(None),
        )
        .order_by(
            RetailerFinancials.period_end_date.desc(),
            RetailerFinancials.fiscal_year.desc(),
            RetailerFinancials.fiscal_quarter.desc(),
        )
        .all()
    )

    stats = {"candidates": len(rows), "updated": 0, "skipped_invalid": 0}

    if not rows:
        logger.info("No Target rows eligible for inventory_days derivation")
        return stats

    logger.info(
        "Inventory days backfill — %d candidate(s), newest first",
        len(rows),
    )

    for row in rows:
        label = _quarter_label(row)
        days = _compute_inventory_days(
            row.inventory_usd,
            row.total_net_sales_usd,
            row.gross_margin_pct,
        )
        if days is None:
            stats["skipped_invalid"] += 1
            logger.warning(
                "%s: could not derive inventory_days (inv=%s sales=%s gm=%s)",
                label,
                row.inventory_usd,
                row.total_net_sales_usd,
                row.gross_margin_pct,
            )
            continue

        row.inventory_days = days
        db.commit()
        ctx.inserted()
        stats["updated"] += 1
        logger.info("%s: inventory_days=%s", label, days)

    logger.info(
        "Inventory days backfill complete — candidates=%d updated=%d skipped_invalid=%d",
        stats["candidates"],
        stats["updated"],
        stats["skipped_invalid"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill Target gross_margin_pct from 10-Q filings, then derive "
            "inventory_days where inputs are available"
        )
    )
    parser.parse_args()

    db = SessionLocal()
    try:
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=tier1._SUBMISSIONS_URL,
            db=db,
        ) as ctx:
            gm_stats = run_gross_margin_backfill(db, ctx)
            inv_stats = run_inventory_days_backfill(db, ctx)
        logger.info("Done — gross_margin=%s inventory_days=%s", gm_stats, inv_stats)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

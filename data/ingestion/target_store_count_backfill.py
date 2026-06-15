"""
Standalone backfill: populate store_count_total and store_count_net_change on
Target retailer_financials rows from stored 8-K earnings release URLs.

Append-only: demotes prior is_latest rows via mark_latest(), inserts a new row.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Optional

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

SCRIPT_VERSION = "target-store-count-backfill-v1.0"
SOURCE_NAME = "target_store_count_backfill"


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


def _fetch_8k_text(source_8k_url: str) -> Optional[str]:
    body = tier1._sec_get(source_8k_url)
    if not isinstance(body, str) or not body.strip():
        return None
    return tier1._strip_html(body)


def _merge_store_count_fields(
    payload: dict[str, Any],
    metrics: dict[str, Any],
) -> None:
    store_count_total = metrics.get("store_count_total")
    if store_count_total is not None:
        payload["store_count_total"] = int(store_count_total)
    if metrics.get("store_count_net_change") is not None:
        payload["store_count_net_change"] = metrics["store_count_net_change"]


def _sync_row_from_payload(row: RetailerFinancials, payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if hasattr(row, key):
            setattr(row, key, value)


def run_target_store_count_backfill(
    db: Session,
    ctx: IngestionContext,
) -> dict[str, int]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return {
            "candidates": 0,
            "appended": 0,
            "not_found": 0,
            "skipped_no_url": 0,
            "fetch_failed": 0,
        }

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.store_count_total.is_(None),
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
        "appended": 0,
        "not_found": 0,
        "skipped_no_url": 0,
        "fetch_failed": 0,
    }

    if not rows:
        logger.info("No Target retailer_financials rows with NULL store_count_total")
        return stats

    logger.info(
        "Processing %d Target quarter(s) with NULL store_count_total (newest first)",
        len(rows),
    )

    for row in rows:
        label = _quarter_label(row)
        if not row.source_8k_url:
            stats["skipped_no_url"] += 1
            logger.warning("%s: skipped — source_8k_url is NULL", label)
            continue

        text = _fetch_8k_text(row.source_8k_url)
        if text is None:
            stats["fetch_failed"] += 1
            logger.warning(
                "%s: SEC fetch failed for %s",
                label,
                row.source_8k_url,
            )
            continue

        metrics = tier1._extract_target_store_count_metrics(text)
        if metrics.get("store_count_total") is None:
            stats["not_found"] += 1
            logger.info(
                "%s: store count not found in 8-K (%s)",
                label,
                row.source_8k_url,
            )
            continue

        payload = tier1._row_to_financials_payload(row)
        _merge_store_count_fields(payload, metrics)
        tier1._validate_target_payload(ctx, payload)
        tier1._append_retailer_financials(db, ctx, retailer_id, payload)
        _sync_row_from_payload(row, payload)
        db.commit()
        stats["appended"] += 1
        logger.info(
            "%s: appended store_count_total=%s store_count_net_change=%s",
            label,
            payload["store_count_total"],
            payload.get("store_count_net_change"),
        )

    logger.info(
        "Backfill complete — candidates=%d appended=%d not_found=%d "
        "skipped_no_url=%d fetch_failed=%d",
        stats["candidates"],
        stats["appended"],
        stats["not_found"],
        stats["skipped_no_url"],
        stats["fetch_failed"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill store_count_total and store_count_net_change on existing "
            "Target retailer_financials rows from stored 8-K earnings URLs"
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
            run_target_store_count_backfill(db, ctx)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

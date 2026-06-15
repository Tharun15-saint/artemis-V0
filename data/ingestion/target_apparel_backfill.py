"""
Standalone backfill: populate apparel_revenue_usd (and apparel_revenue_pct_total
from 10-Q mix table) on Target retailer_financials rows.

Path 1 — 8-K earnings release (FY2022+ category dollar table).
Path 2 — 10-Q Note 2/3 quarterly Revenues table (FY2019+).
Path 3 — Q4 derived from 10-K annual minus Q1+Q2+Q3 when all three exist.

Updates is_latest rows in place (no append).
"""

from __future__ import annotations

import argparse
import logging
import re
from decimal import Decimal
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

SCRIPT_VERSION = "target-apparel-backfill-v2.0"
SOURCE_NAME = "target_apparel_backfill"

_10Q_URL_RE = re.compile(r"x10q", re.I)
_10K_URL_RE = re.compile(r"x10k|10k", re.I)
_10K_PRIMARY_DOC_RE = re.compile(r"tgt-\d{8}\.htm$", re.I)


def _is_10k_url(url: Optional[str]) -> bool:
    if not url:
        return False
    lower = url.lower()
    if _10Q_URL_RE.search(lower):
        return False
    if _10K_URL_RE.search(lower):
        return True
    return _10K_PRIMARY_DOC_RE.search(lower) is not None


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


def _fetch_html(url: str) -> Optional[str]:
    body = tier1._sec_get(url)
    if not isinstance(body, str) or not body.strip():
        return None
    return body


def _extract_from_8k(html: str, fiscal_quarter: int) -> dict[str, Any]:
    return tier1._parse_8k_guidance(html, fiscal_quarter)


def _extract_from_10q(html: str, fiscal_quarter: int) -> dict[str, Any]:
    return tier1._parse_10q_metrics(html, fiscal_quarter)


def _derive_q4_apparel_from_10k(
    db: Session,
    retailer_id: int,
    row: RetailerFinancials,
    html: str,
) -> Optional[Decimal]:
    annual_apparel = tier1._parse_annual_apparel_revenue_usd_from_text(
        tier1._strip_html(html)
    )
    if annual_apparel is None:
        return None

    prior_quarters: list[Decimal] = []
    for quarter in (1, 2, 3):
        prior = (
            db.query(RetailerFinancials)
            .filter(
                RetailerFinancials.retailer_id == retailer_id,
                RetailerFinancials.is_latest.is_(True),
                RetailerFinancials.fiscal_year == row.fiscal_year,
                RetailerFinancials.fiscal_quarter == quarter,
            )
            .first()
        )
        if prior is None or prior.apparel_revenue_usd is None:
            logger.info(
                "%s: Q4 derivation skipped — FY%s Q%s apparel not populated",
                _quarter_label(row),
                row.fiscal_year,
                quarter,
            )
            return None
        prior_quarters.append(prior.apparel_revenue_usd)

    derived = annual_apparel - sum(prior_quarters, Decimal("0"))
    if derived <= 0:
        logger.warning(
            "%s: Q4 derivation non-positive (%s) — leaving null",
            _quarter_label(row),
            derived,
        )
        return None
    return derived


def _apply_apparel_fields(
    row: RetailerFinancials,
    metrics: dict[str, Any],
) -> None:
    apparel_usd = metrics.get("apparel_revenue_usd")
    if apparel_usd is not None:
        row.apparel_revenue_usd = apparel_usd
    if metrics.get("apparel_revenue_pct_total") is not None:
        row.apparel_revenue_pct_total = metrics["apparel_revenue_pct_total"]


def _try_extract_apparel(
    db: Session,
    retailer_id: int,
    row: RetailerFinancials,
) -> tuple[dict[str, Any], Optional[str]]:
    metrics: dict[str, Any] = {}

    if row.source_8k_url:
        html = _fetch_html(row.source_8k_url)
        if html is None:
            logger.warning(
                "%s: 8-K SEC fetch failed for %s",
                _quarter_label(row),
                row.source_8k_url,
            )
        else:
            metrics = _extract_from_8k(html, row.fiscal_quarter)
            if metrics.get("apparel_revenue_usd") is not None:
                return metrics, "8-K"

    if row.fiscal_quarter == 4 and _is_10k_url(row.source_10q_url):
        if not row.source_10q_url:
            return {}, None
        html = _fetch_html(row.source_10q_url)
        if html is None:
            logger.warning(
                "%s: 10-K SEC fetch failed for %s",
                _quarter_label(row),
                row.source_10q_url,
            )
            return {}, None

        derived = _derive_q4_apparel_from_10k(db, retailer_id, row, html)
        if derived is not None:
            return {"apparel_revenue_usd": derived}, "10-K-derived-Q4"
        return {}, None

    if row.source_10q_url and not _is_10k_url(row.source_10q_url):
        html = _fetch_html(row.source_10q_url)
        if html is None:
            logger.warning(
                "%s: 10-Q SEC fetch failed for %s",
                _quarter_label(row),
                row.source_10q_url,
            )
        else:
            metrics = _extract_from_10q(html, row.fiscal_quarter)
            if metrics.get("apparel_revenue_usd") is not None:
                return metrics, "10-Q"

    return {}, None


def run_target_apparel_backfill(db: Session, ctx: IngestionContext) -> dict[str, int]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return {
            "candidates": 0,
            "updated_8k": 0,
            "updated_10q": 0,
            "updated_q4_derived": 0,
            "not_found": 0,
            "skipped_no_url": 0,
            "fetch_failed": 0,
        }

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.apparel_revenue_usd.is_(None),
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
        "updated_8k": 0,
        "updated_10q": 0,
        "updated_q4_derived": 0,
        "not_found": 0,
        "skipped_no_url": 0,
        "fetch_failed": 0,
    }

    if not rows:
        logger.info("No Target retailer_financials rows with NULL apparel_revenue_usd")
        return stats

    logger.info(
        "Processing %d Target quarter(s) with NULL apparel_revenue_usd (newest first)",
        len(rows),
    )

    for row in rows:
        label = _quarter_label(row)
        if not row.source_8k_url and not row.source_10q_url:
            stats["skipped_no_url"] += 1
            logger.warning("%s: skipped — no source_8k_url or source_10q_url", label)
            continue

        if row.fiscal_quarter == 4 and _is_10k_url(row.source_10q_url):
            stats["not_found"] += 1
            logger.info(
                "%s: deferred Q4 10-K derivation to second pass",
                label,
            )
            continue

        metrics, path_used = _try_extract_apparel(db, retailer_id, row)
        if path_used is None:
            stats["not_found"] += 1
            logger.info(
                "%s: apparel revenue not extractable (8k=%s 10q=%s)",
                label,
                row.source_8k_url,
                row.source_10q_url,
            )
            continue

        _apply_apparel_fields(row, metrics)
        db.commit()
        ctx.inserted()

        if path_used == "8-K":
            stats["updated_8k"] += 1
        elif path_used == "10-K-derived-Q4":
            stats["updated_q4_derived"] += 1
        else:
            stats["updated_10q"] += 1

        logger.info(
            "%s: updated via %s apparel_revenue_usd=%s apparel_revenue_pct_total=%s",
            label,
            path_used,
            row.apparel_revenue_usd,
            row.apparel_revenue_pct_total,
        )

    q4_rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.fiscal_quarter == 4,
            RetailerFinancials.apparel_revenue_usd.is_(None),
            RetailerFinancials.source_10q_url.isnot(None),
        )
        .order_by(
            RetailerFinancials.fiscal_year.asc(),
        )
        .all()
    )

    for row in q4_rows:
        if not _is_10k_url(row.source_10q_url):
            continue
        label = _quarter_label(row)
        metrics, path_used = _try_extract_apparel(db, retailer_id, row)
        if path_used != "10-K-derived-Q4":
            continue
        _apply_apparel_fields(row, metrics)
        db.commit()
        ctx.inserted()
        stats["updated_q4_derived"] += 1
        logger.info(
            "%s: updated via %s (second pass) apparel_revenue_usd=%s",
            label,
            path_used,
            row.apparel_revenue_usd,
        )

    logger.info(
        "Apparel backfill complete — candidates=%d updated_8k=%d updated_10q=%d "
        "updated_q4_derived=%d not_found=%d skipped_no_url=%d fetch_failed=%d",
        stats["candidates"],
        stats["updated_8k"],
        stats["updated_10q"],
        stats["updated_q4_derived"],
        stats["not_found"],
        stats["skipped_no_url"],
        stats["fetch_failed"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill apparel_revenue_usd and apparel_revenue_pct_total on Target "
            "retailer_financials rows from 8-K, 10-Q, or derived Q4 from 10-K"
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
            run_target_apparel_backfill(db, ctx)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

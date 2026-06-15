"""
Standalone backfill: populate store_count_total, walmart_us_store_count, and
sams_club_count on Walmart retailer_financials rows from stored 8-K and 10-K URLs.

Append-only: demotes prior is_latest rows via mark_latest(), inserts a new row.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, Optional

from sqlalchemy import or_
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

SCRIPT_VERSION = "walmart-store-count-backfill-v1.0"
SOURCE_NAME = "walmart_store_count_backfill"


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


def _row_to_financials_payload(row: RetailerFinancials) -> dict[str, Any]:
    return {
        field: getattr(row, field)
        for field in tier1.RETAILER_FINANCIALS_UPDATE_FIELDS
    }


def _fetch_filing_text(url: str) -> Optional[str]:
    body = tier1._sec_get(url)
    if not isinstance(body, str) or not body.strip():
        return None
    return tier1._strip_html(body)


def _preload_q4_segment_counts(
    db: Session,
    retailer_id: int,
) -> dict[int, tuple[Optional[int], Optional[int]]]:
    """Seed carry-forward cache from existing Q4 rows that already have segment counts."""
    cache: dict[int, tuple[Optional[int], Optional[int]]] = {}
    q4_rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.fiscal_quarter == 4,
        )
        .order_by(RetailerFinancials.fiscal_year.desc())
        .all()
    )
    for row in q4_rows:
        if row.walmart_us_store_count is None and row.sams_club_count is None:
            continue
        cache[row.fiscal_year] = (
            row.walmart_us_store_count,
            row.sams_club_count,
        )
    return cache


def _carry_forward_segment_counts(
    fiscal_year: int,
    fiscal_quarter: int,
    q4_cache: dict[int, tuple[Optional[int], Optional[int]]],
) -> tuple[Optional[int], Optional[int]]:
    if fiscal_quarter == 4:
        return None, None
    if fiscal_year in q4_cache:
        return q4_cache[fiscal_year]
    if (fiscal_year - 1) in q4_cache:
        return q4_cache[fiscal_year - 1]
    return None, None


def _apply_annual_metrics(
    metrics: dict[str, Any],
    annual_metrics: dict[str, Any],
) -> None:
    if annual_metrics.get("store_count_total") is not None:
        metrics["store_count_total"] = annual_metrics["store_count_total"]
    if annual_metrics.get("walmart_us_store_count") is not None:
        metrics["walmart_us_store_count"] = annual_metrics["walmart_us_store_count"]
    if annual_metrics.get("sams_club_count") is not None:
        metrics["sams_club_count"] = annual_metrics["sams_club_count"]


def _resolve_10k_url(
    row: RetailerFinancials,
    filing_rows: list[dict[str, Any]],
) -> Optional[str]:
    if row.fiscal_quarter == 4 and row.source_10q_url:
        return row.source_10q_url
    return tier1._find_walmart_10k_doc_url(row.fiscal_year, filing_rows=filing_rows)


def _load_fiscal_year_10k_cache(
    fiscal_year: int,
    q4_cache: dict[int, tuple[Optional[int], Optional[int]]],
    annual_totals: dict[int, int],
    filing_rows: list[dict[str, Any]],
) -> bool:
    """Fetch fiscal-year 10-K from SEC and seed segment/total caches."""
    if fiscal_year in q4_cache:
        return True

    url = tier1._find_walmart_10k_doc_url(fiscal_year, filing_rows=filing_rows)
    if not url:
        logger.warning("FY%s: could not resolve 10-K URL from SEC submissions", fiscal_year)
        return False

    annual_text = _fetch_filing_text(url)
    if annual_text is None:
        logger.warning("FY%s: SEC fetch failed for 10-K %s", fiscal_year, url)
        return False

    annual_metrics = tier1._extract_walmart_store_count_metrics(
        annual_text,
        is_annual=True,
    )
    if (
        annual_metrics.get("walmart_us_store_count") is None
        and annual_metrics.get("sams_club_count") is None
        and annual_metrics.get("store_count_total") is None
    ):
        logger.info("FY%s: no store counts found in 10-K %s", fiscal_year, url)
        return False

    q4_cache[fiscal_year] = (
        annual_metrics.get("walmart_us_store_count"),
        annual_metrics.get("sams_club_count"),
    )
    if annual_metrics.get("store_count_total") is not None:
        annual_totals[fiscal_year] = int(annual_metrics["store_count_total"])

    logger.info(
        "FY%s 10-K cache: store_count_total=%s walmart_us=%s sams=%s (%s)",
        fiscal_year,
        annual_metrics.get("store_count_total"),
        annual_metrics.get("walmart_us_store_count"),
        annual_metrics.get("sams_club_count"),
        url,
    )
    return True


def _extract_q4_10k_store_metrics(
    row: RetailerFinancials,
    filing_rows: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Fetch 10-K and return store count fields to merge into a new payload."""
    label = _quarter_label(row)
    url = _resolve_10k_url(row, filing_rows)
    if not url:
        logger.warning("%s: skipped 10-K — no URL resolved", label)
        return None

    annual_text = _fetch_filing_text(url)
    if annual_text is None:
        logger.warning("%s: SEC fetch failed for 10-K %s", label, url)
        return None

    annual_metrics = tier1._extract_walmart_store_count_metrics(
        annual_text,
        is_annual=True,
    )
    if (
        annual_metrics.get("walmart_us_store_count") is None
        and annual_metrics.get("sams_club_count") is None
        and annual_metrics.get("store_count_total") is None
    ):
        logger.info("%s: no segment store counts found in 10-K", label)
        return None

    return annual_metrics


def _merge_store_count_fields(
    payload: dict[str, Any],
    row: RetailerFinancials,
    metrics: dict[str, Any],
) -> bool:
    """Merge extracted store counts into payload. Returns True if any field changed."""
    changed = False
    store_count_total = metrics.get("store_count_total")
    walmart_us = metrics.get("walmart_us_store_count")
    sams_club = metrics.get("sams_club_count")

    if store_count_total is not None and payload.get("store_count_total") is None:
        payload["store_count_total"] = int(store_count_total)
        changed = True
    if walmart_us is not None and payload.get("walmart_us_store_count") is None:
        payload["walmart_us_store_count"] = int(walmart_us)
        changed = True
    if sams_club is not None and (
        payload.get("sams_club_count") is None
        or (payload.get("sams_club_count") or 0) > 1000
    ):
        payload["sams_club_count"] = int(sams_club)
        changed = True

    return changed


def _update_q4_cache_from_payload(
    payload: dict[str, Any],
    fiscal_year: int,
    fiscal_quarter: int,
    q4_cache: dict[int, tuple[Optional[int], Optional[int]]],
    annual_totals: dict[int, int],
) -> None:
    if fiscal_quarter == 4 and (
        payload.get("walmart_us_store_count") is not None
        or payload.get("sams_club_count") is not None
    ):
        q4_cache[fiscal_year] = (
            payload.get("walmart_us_store_count"),
            payload.get("sams_club_count"),
        )
    if payload.get("store_count_total") is not None:
        annual_totals[fiscal_year] = int(payload["store_count_total"])


def _sync_row_from_payload(row: RetailerFinancials, payload: dict[str, Any]) -> None:
    for key, value in payload.items():
        if hasattr(row, key):
            setattr(row, key, value)


def _append_store_count_row(
    db: Session,
    ctx: IngestionContext,
    retailer_id: int,
    row: RetailerFinancials,
    payload: dict[str, Any],
) -> None:
    tier1._validate_walmart_payload(ctx, payload, prior_row=row)
    tier1._append_retailer_financials(db, ctx, retailer_id, payload)
    _sync_row_from_payload(row, payload)


def _needs_store_count_backfill(row: RetailerFinancials) -> bool:
    return (
        row.store_count_total is None
        or row.walmart_us_store_count is None
        or row.sams_club_count is None
        or (row.sams_club_count is not None and row.sams_club_count > 1000)
    )


def run_walmart_store_count_backfill(
    db: Session,
    ctx: IngestionContext,
) -> dict[str, int]:
    retailer_id = _get_walmart_retailer_id(db)
    if retailer_id is None:
        return {
            "candidates": 0,
            "q4_10k_appended": 0,
            "appended": 0,
            "not_found": 0,
            "skipped_no_url": 0,
            "fetch_failed": 0,
            "carried_forward": 0,
        }

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            or_(
                RetailerFinancials.store_count_total.is_(None),
                RetailerFinancials.walmart_us_store_count.is_(None),
                RetailerFinancials.sams_club_count.is_(None),
                RetailerFinancials.sams_club_count > 1000,
            ),
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
        "q4_10k_appended": 0,
        "appended": 0,
        "not_found": 0,
        "skipped_no_url": 0,
        "fetch_failed": 0,
        "carried_forward": 0,
    }

    if not rows:
        logger.info("No Walmart retailer_financials rows needing store count backfill")
        return stats

    q4_cache = _preload_q4_segment_counts(db, retailer_id)
    annual_totals: dict[int, int] = {}
    submissions = tier1._sec_get(tier1._SUBMISSIONS_URL)
    filing_rows = (
        tier1._load_all_submission_filings(submissions)
        if isinstance(submissions, dict)
        else []
    )

    fiscal_years = sorted({row.fiscal_year for row in rows}, reverse=True)
    for fiscal_year in fiscal_years:
        _load_fiscal_year_10k_cache(
            fiscal_year,
            q4_cache,
            annual_totals,
            filing_rows,
        )

    logger.info(
        "Processing %d Walmart quarter(s) with missing store counts (newest first)",
        len(rows),
    )

    # Pass 1: Q4 rows — populate segment counts from 10-K.
    q4_rows = [row for row in rows if row.fiscal_quarter == 4]
    for row in q4_rows:
        annual_metrics = _extract_q4_10k_store_metrics(row, filing_rows)
        if annual_metrics is None:
            continue

        payload = _row_to_financials_payload(row)
        if not _merge_store_count_fields(payload, row, annual_metrics):
            continue

        _append_store_count_row(db, ctx, retailer_id, row, payload)
        _update_q4_cache_from_payload(
            payload,
            row.fiscal_year,
            row.fiscal_quarter,
            q4_cache,
            annual_totals,
        )
        db.commit()
        stats["q4_10k_appended"] += 1
        logger.info(
            "%s: appended 10-K segment counts store_count_total=%s walmart_us=%s sams=%s",
            _quarter_label(row),
            payload.get("store_count_total"),
            payload.get("walmart_us_store_count"),
            payload.get("sams_club_count"),
        )

    # Pass 2: all rows — 8-K global count, 10-K override on Q4, carry-forward segments.
    for row in rows:
        if not _needs_store_count_backfill(row):
            continue

        label = _quarter_label(row)
        metrics: dict[str, Any] = {}
        carried = False

        if row.source_8k_url:
            release_text = _fetch_filing_text(row.source_8k_url)
            if release_text is None:
                stats["fetch_failed"] += 1
                logger.warning(
                    "%s: SEC fetch failed for 8-K %s",
                    label,
                    row.source_8k_url,
                )
            else:
                metrics = tier1._extract_walmart_store_count_metrics(release_text)
        elif row.store_count_total is None:
            stats["skipped_no_url"] += 1
            logger.warning("%s: skipped — source_8k_url is NULL", label)
            continue

        annual_url = _resolve_10k_url(row, filing_rows)
        if row.fiscal_quarter == 4 and annual_url:
            annual_text = _fetch_filing_text(annual_url)
            if annual_text is None:
                logger.warning(
                    "%s: SEC fetch failed for 10-K %s — using 8-K store count only",
                    label,
                    annual_url,
                )
            else:
                annual_metrics = tier1._extract_walmart_store_count_metrics(
                    annual_text,
                    is_annual=True,
                )
                _apply_annual_metrics(metrics, annual_metrics)
        elif annual_totals.get(row.fiscal_year) is not None:
            metrics.setdefault("store_count_total", annual_totals[row.fiscal_year])

        store_count_total = metrics.get("store_count_total")
        walmart_us = metrics.get("walmart_us_store_count")
        sams_club = metrics.get("sams_club_count")

        if walmart_us is None or sams_club is None:
            carry_wmt, carry_sams = _carry_forward_segment_counts(
                row.fiscal_year,
                row.fiscal_quarter,
                q4_cache,
            )
            if walmart_us is None and carry_wmt is not None:
                walmart_us = carry_wmt
                carried = True
            if sams_club is None and carry_sams is not None:
                sams_club = carry_sams
                carried = True

        if (
            store_count_total is None
            and walmart_us is None
            and sams_club is None
        ):
            stats["not_found"] += 1
            logger.info("%s: store count not found in 8-K or 10-K", label)
            continue

        merge_metrics: dict[str, Any] = {}
        if store_count_total is not None:
            merge_metrics["store_count_total"] = store_count_total
        if walmart_us is not None:
            merge_metrics["walmart_us_store_count"] = walmart_us
        if sams_club is not None:
            merge_metrics["sams_club_count"] = sams_club

        payload = _row_to_financials_payload(row)
        if not _merge_store_count_fields(payload, row, merge_metrics):
            continue

        _append_store_count_row(db, ctx, retailer_id, row, payload)
        _update_q4_cache_from_payload(
            payload,
            row.fiscal_year,
            row.fiscal_quarter,
            q4_cache,
            annual_totals,
        )
        db.commit()
        stats["appended"] += 1
        if carried:
            stats["carried_forward"] += 1

        logger.info(
            "%s: appended store_count_total=%s walmart_us_store_count=%s "
            "sams_club_count=%s%s",
            label,
            payload.get("store_count_total"),
            payload.get("walmart_us_store_count"),
            payload.get("sams_club_count"),
            " (segment counts carried forward from Q4)" if carried else "",
        )

    logger.info(
        "Backfill complete — candidates=%d q4_10k_appended=%d appended=%d "
        "not_found=%d skipped_no_url=%d fetch_failed=%d carried_forward=%d",
        stats["candidates"],
        stats["q4_10k_appended"],
        stats["appended"],
        stats["not_found"],
        stats["skipped_no_url"],
        stats["fetch_failed"],
        stats["carried_forward"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill store_count_total, walmart_us_store_count, and sams_club_count "
            "on Walmart retailer_financials rows from stored SEC URLs"
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
            run_walmart_store_count_backfill(db, ctx)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

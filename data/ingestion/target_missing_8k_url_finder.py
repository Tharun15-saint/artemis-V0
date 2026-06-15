"""
Find and backfill source_8k_url on Target retailer_financials from SEC EDGAR 8-K EX-99 exhibits.

Updates is_latest rows in place (no append).
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date
from typing import Any, Callable, Optional, Union

import requests
from sqlalchemy.orm import Session

from data.ingestion import earnings_8k_url_selection as selection
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

SCRIPT_VERSION = "target-missing-8k-url-finder-v2.0"
SOURCE_NAME = "target_missing_8k_url_finder"
SEC_USER_AGENT = "Artemis Intelligence research@artemis.ai"
SEC_FETCH_DELAY_SECONDS = 0.15
REQUEST_TIMEOUT = 30
EARNINGS_WINDOW_DAYS = 45
MIN_FISCAL_YEAR = 2018

_SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{tier1.TARGET_CIK}.json"
_SUBMISSIONS_FILE_BASE = "https://data.sec.gov/submissions/"
_COMPANY_ACCESSION_PREFIX = selection.TARGET_COMPANY_ACCESSION_PREFIX


def _sec_get(url: str) -> Optional[Union[dict[str, Any], str]]:
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        time.sleep(SEC_FETCH_DELAY_SECONDS)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "json" in content_type or url.endswith(".json"):
            return response.json()
        return response.text
    except requests.RequestException as exc:
        logger.warning("SEC request failed for %s: %s", url, exc)
        return None
    except ValueError as exc:
        logger.warning("SEC response parse failed for %s: %s", url, exc)
        return None


def _parse_filing_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _iter_submission_filings(submissions_payload: dict[str, Any]) -> list[dict[str, Any]]:
    recent = submissions_payload.get("filings", {}).get("recent")
    if recent is None and submissions_payload.get("form"):
        recent = submissions_payload
    if not recent:
        return []

    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    rows: list[dict[str, Any]] = []
    for form, accession, filed in zip(forms, accessions, filing_dates):
        filed_date = _parse_filing_date(filed)
        if filed_date is None:
            continue
        rows.append(
            {
                "form": form,
                "accession": accession,
                "filing_date": filed_date,
            }
        )
    return rows


def _load_all_submission_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _iter_submission_filings(submissions)
    for file_info in submissions.get("filings", {}).get("files", []) or []:
        name = file_info.get("name")
        if not name:
            continue
        payload = _sec_get(f"{_SUBMISSIONS_FILE_BASE}{name}")
        if isinstance(payload, dict):
            rows.extend(_iter_submission_filings(payload))
    rows.sort(key=lambda row: row["filing_date"], reverse=True)
    return rows


def _fetch_index_html(accession: str) -> Optional[str]:
    url = selection.index_htm_url(tier1.CIK_NUM, accession)
    body = _sec_get(url)
    return body if isinstance(body, str) else None


def _find_quarter_earnings_8k_url(
    filing_rows: list[dict[str, Any]],
    period_end_date: date,
) -> Optional[str]:
    return selection.find_quarter_earnings_8k_url(
        filing_rows,
        period_end_date,
        company_accession_prefix=_COMPANY_ACCESSION_PREFIX,
        cik_num=tier1.CIK_NUM,
        fetch_index_html=_fetch_index_html,
        build_document_url=tier1._filing_doc_url,
        window_days=EARNINGS_WINDOW_DAYS,
    )


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


def _candidate_query(db: Session, retailer_id: int, revalidate: bool):
    query = db.query(RetailerFinancials).filter(
        RetailerFinancials.retailer_id == retailer_id,
        RetailerFinancials.is_latest.is_(True),
        RetailerFinancials.source_10q_url.isnot(None),
        RetailerFinancials.fiscal_year >= MIN_FISCAL_YEAR,
    )
    if not revalidate:
        query = query.filter(RetailerFinancials.source_8k_url.is_(None))
    return query


def count_candidates(db: Session, revalidate: bool = False) -> int:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return 0
    return _candidate_query(db, retailer_id, revalidate).count()


def run_missing_8k_url_finder(
    db: Session,
    ctx: IngestionContext,
    filing_rows: list[dict[str, Any]],
    *,
    revalidate: bool = False,
) -> dict[str, int]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return {
            "candidates": 0,
            "found": 0,
            "updated": 0,
            "unchanged": 0,
            "not_found": 0,
        }

    rows = (
        _candidate_query(db, retailer_id, revalidate)
        .order_by(
            RetailerFinancials.period_end_date.desc(),
            RetailerFinancials.fiscal_year.desc(),
            RetailerFinancials.fiscal_quarter.desc(),
        )
        .all()
    )

    stats = {
        "candidates": len(rows),
        "found": 0,
        "updated": 0,
        "unchanged": 0,
        "not_found": 0,
    }
    if not rows:
        logger.info("No Target rows to process for source_8k_url finder")
        return stats

    mode = "revalidate" if revalidate else "missing-only"
    logger.info(
        "Missing 8-K URL finder (%s) — %d candidate(s), newest first",
        mode,
        len(rows),
    )

    for row in rows:
        label = _quarter_label(row)
        if row.period_end_date is None:
            stats["not_found"] += 1
            logger.info("%s: not found — missing period_end_date", label)
            continue

        url = _find_quarter_earnings_8k_url(filing_rows, row.period_end_date)
        if url is None:
            stats["not_found"] += 1
            logger.info(
                "%s: not found — no earnings 8-K within %d days of %s",
                label,
                EARNINGS_WINDOW_DAYS,
                row.period_end_date,
            )
            continue

        stats["found"] += 1
        if row.source_8k_url == url:
            stats["unchanged"] += 1
            logger.info("%s: unchanged %s", label, url)
            continue

        prior = row.source_8k_url
        row.source_8k_url = url
        db.commit()
        ctx.inserted()
        stats["updated"] += 1
        logger.info("%s: updated %s (was %s)", label, url, prior)

    logger.info(
        "Missing 8-K URL finder complete — candidates=%d found=%d updated=%d "
        "unchanged=%d not_found=%d",
        stats["candidates"],
        stats["found"],
        stats["updated"],
        stats["unchanged"],
        stats["not_found"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find Target earnings release 8-K EX-99 URLs for rows missing source_8k_url"
        )
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print candidate count and exit without SEC lookups",
    )
    parser.add_argument(
        "--revalidate",
        action="store_true",
        help=(
            "Re-score all FY2018+ rows with source_10q_url and update source_8k_url "
            "when a higher-priority earnings release is found"
        ),
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        candidates = count_candidates(db, revalidate=args.revalidate)
        logger.info("Backfill candidates: %d", candidates)

        if args.count_only:
            return 0

        submissions = _sec_get(_SUBMISSIONS_URL)
        if not isinstance(submissions, dict):
            logger.error("Failed to fetch Target SEC submissions JSON")
            return 1

        filing_rows = _load_all_submission_filings(submissions)
        logger.info("Loaded %d SEC filing row(s) from submissions", len(filing_rows))

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=_SUBMISSIONS_URL,
            db=db,
        ) as ctx:
            stats = run_missing_8k_url_finder(
                db,
                ctx,
                filing_rows,
                revalidate=args.revalidate,
            )
        logger.info("Done — %s", stats)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

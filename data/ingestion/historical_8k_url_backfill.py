"""
Backfill missing source_8k_url and source_10q_url on retailer_financials from SEC EDGAR.

Processes Target then Walmart. Updates is_latest rows in place (no append).
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Optional, Union

import requests
from sqlalchemy import or_
from sqlalchemy.orm import Session

from data.ingestion import earnings_8k_url_selection as selection
from data.ingestion import target_tier1_ingestion as target_tier1
from data.ingestion import walmart_tier1_ingestion as walmart_tier1
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

SCRIPT_VERSION = "historical-url-backfill-v1.0"
SOURCE_NAME = "historical_8k_url_backfill"
SEC_USER_AGENT = "Artemis Intelligence research@artemis.ai"
SEC_FETCH_DELAY_SECONDS = 0.15
REQUEST_TIMEOUT = 30
EARNINGS_8K_WINDOW_DAYS = 45
PERIODIC_FILING_WINDOW_DAYS = 90

_SUBMISSIONS_FILE_BASE = "https://data.sec.gov/submissions/"


@dataclass(frozen=True)
class RetailerConfig:
    name: str
    cik: str
    cik_num: str
    accession_prefix: str
    ticker_prefix: str
    submissions_url: str
    build_document_url: Callable[[str, str], str]


RETAILER_CONFIGS: tuple[RetailerConfig, ...] = (
    RetailerConfig(
        name=target_tier1.TARGET_NAME,
        cik=target_tier1.TARGET_CIK,
        cik_num=target_tier1.CIK_NUM,
        accession_prefix=selection.TARGET_COMPANY_ACCESSION_PREFIX,
        ticker_prefix="tgt-",
        submissions_url=f"https://data.sec.gov/submissions/CIK{target_tier1.TARGET_CIK}.json",
        build_document_url=target_tier1._filing_doc_url,
    ),
    RetailerConfig(
        name=walmart_tier1.WALMART_NAME,
        cik=walmart_tier1.WALMART_CIK,
        cik_num=walmart_tier1.CIK_NUM,
        accession_prefix=selection.WALMART_COMPANY_ACCESSION_PREFIX,
        ticker_prefix="wmt-",
        submissions_url=f"https://data.sec.gov/submissions/CIK{walmart_tier1.WALMART_CIK}.json",
        build_document_url=walmart_tier1._filing_doc_url,
    ),
)


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


def load_all_submission_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _iter_submission_filings(submissions)
    extra_files = submissions.get("filings", {}).get("files", []) or []
    for file_info in extra_files:
        name = file_info.get("name")
        if not name:
            continue
        payload = _sec_get(f"{_SUBMISSIONS_FILE_BASE}{name}")
        if isinstance(payload, dict):
            rows.extend(_iter_submission_filings(payload))
    rows.sort(key=lambda row: row["filing_date"], reverse=True)
    return rows


def _parse_index_size(raw: str) -> int:
    digits = re.sub(r"[^\d]", "", raw or "")
    return int(digits) if digits else 0


def _parse_index_rows_with_size(index_html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_html in selection._INDEX_ROW_RE.findall(index_html):
        cells = [
            selection._clean_index_cell(cell)
            for cell in selection._INDEX_CELL_RE.findall(row_html)
            if selection._clean_index_cell(cell)
        ]
        if len(cells) < 3:
            continue
        seq = cells[0]
        if not seq.isdigit():
            continue
        document_match = re.search(r"([\w\-]+\.htm)", cells[2], re.I)
        if not document_match:
            continue
        rows.append(
            {
                "seq": seq,
                "description": cells[1],
                "name": document_match.group(1),
                "type": cells[3] if len(cells) > 3 else "",
                "size": _parse_index_size(cells[4] if len(cells) > 4 else "0"),
            }
        )
    return rows


def _fetch_index_html(cik_num: str, accession: str) -> Optional[str]:
    url = selection.index_htm_url(cik_num, accession)
    body = _sec_get(url)
    return body if isinstance(body, str) else None


def _normalize_form_type(doc_type: str) -> str:
    normalized = (doc_type or "").strip().upper()
    if normalized.endswith("/A"):
        normalized = normalized[:-2]
    return normalized


def _is_exhibit_index_row(row: dict[str, Any]) -> bool:
    doc_type = (row.get("type") or "").strip().upper()
    if doc_type.startswith("EX-"):
        return True
    lower = row["name"].lower()
    return "exhibit" in lower or "index" in lower


def _find_primary_periodic_filing_htm(
    index_html: str,
    ticker_prefix: str,
    period_end_date: Optional[date] = None,
    expected_form: str = "10-Q",
) -> Optional[str]:
    """Largest primary .htm in a 10-Q/10-K filing index.

    Prefers ticker-prefixed documents (tgt-/wmt-). Older filings often use
    legacy names (a09-18121_110q.htm, d10q.htm); fall back to index rows whose
    type matches the expected periodic form.
    """
    prefix = ticker_prefix.lower()
    expected = _normalize_form_type(expected_form)
    ticker_candidates: list[tuple[str, int]] = []
    typed_candidates: list[tuple[str, int]] = []

    for row in _parse_index_rows_with_size(index_html):
        name = row["name"]
        lower = name.lower()
        if not lower.endswith(".htm"):
            continue
        if _is_exhibit_index_row(row):
            continue

        size = row["size"]
        if lower.startswith(prefix):
            ticker_candidates.append((name, size))
        if _normalize_form_type(row.get("type", "")) == expected:
            typed_candidates.append((name, size))

    if ticker_candidates:
        if period_end_date is not None:
            dated_name = f"{prefix}{period_end_date.strftime('%Y%m%d')}.htm"
            for name, _ in ticker_candidates:
                if name.lower() == dated_name:
                    return name
        return max(ticker_candidates, key=lambda item: item[1])[0]

    if typed_candidates:
        return max(typed_candidates, key=lambda item: item[1])[0]

    return None


def _find_quarter_periodic_filing_url(
    filing_rows: list[dict[str, Any]],
    period_end_date: date,
    fiscal_quarter: int,
    config: RetailerConfig,
) -> Optional[str]:
    expected_form = "10-K" if fiscal_quarter == 4 else "10-Q"
    window_end = period_end_date + timedelta(days=PERIODIC_FILING_WINDOW_DAYS)

    matches: list[tuple[int, date, str, str]] = []
    for filing in filing_rows:
        form = filing.get("form")
        if form not in (expected_form, f"{expected_form}/A"):
            continue

        filed_date = filing.get("filing_date")
        accession = filing.get("accession")
        if not isinstance(filed_date, date) or not accession:
            continue
        if filed_date < period_end_date or filed_date > window_end:
            continue

        index_html = _fetch_index_html(config.cik_num, accession)
        if not isinstance(index_html, str):
            continue

        primary_doc = _find_primary_periodic_filing_htm(
            index_html,
            config.ticker_prefix,
            period_end_date,
            expected_form=expected_form,
        )
        if primary_doc is None:
            continue

        days_after = (filed_date - period_end_date).days
        matches.append((days_after, filed_date, accession, primary_doc))

    if not matches:
        return None

    matches.sort(key=lambda item: (item[0], item[1]))
    _, _, best_accession, best_document = matches[0]
    return config.build_document_url(best_accession, best_document)


def _find_quarter_earnings_8k_url(
    filing_rows: list[dict[str, Any]],
    period_end_date: date,
    config: RetailerConfig,
) -> Optional[str]:
    return selection.find_quarter_earnings_8k_url(
        filing_rows,
        period_end_date,
        company_accession_prefix=config.accession_prefix,
        cik_num=config.cik_num,
        fetch_index_html=lambda accession: _fetch_index_html(config.cik_num, accession),
        build_document_url=config.build_document_url,
        window_days=EARNINGS_8K_WINDOW_DAYS,
    )


def _get_retailer_id(db: Session, retailer_name: str) -> Optional[int]:
    retailer = (
        db.query(MajorRetailers).filter(MajorRetailers.name == retailer_name).first()
    )
    if retailer is None:
        logger.error("%s not found in major_retailers", retailer_name)
        return None
    return retailer.retailer_id


def _quarter_label(row: RetailerFinancials) -> str:
    return f"FY{row.fiscal_year} Q{row.fiscal_quarter}"


def count_candidates(db: Session) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for config in RETAILER_CONFIGS:
        retailer_id = _get_retailer_id(db, config.name)
        if retailer_id is None:
            counts[config.name] = {
                "rows_any_null": 0,
                "null_8k": 0,
                "null_10q": 0,
            }
            continue

        base = db.query(RetailerFinancials).filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
        )
        counts[config.name] = {
            "rows_any_null": base.filter(
                or_(
                    RetailerFinancials.source_8k_url.is_(None),
                    RetailerFinancials.source_10q_url.is_(None),
                )
            ).count(),
            "null_8k": base.filter(RetailerFinancials.source_8k_url.is_(None)).count(),
            "null_10q": base.filter(
                RetailerFinancials.source_10q_url.is_(None)
            ).count(),
        }
    return counts


def run_retailer_url_backfill(
    db: Session,
    ctx: IngestionContext,
    config: RetailerConfig,
    filing_rows: list[dict[str, Any]],
) -> dict[str, int]:
    retailer_id = _get_retailer_id(db, config.name)
    if retailer_id is None:
        return {
            "candidates": 0,
            "found_8k": 0,
            "found_10q": 0,
            "not_found_8k": 0,
            "not_found_10q": 0,
        }

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            or_(
                RetailerFinancials.source_8k_url.is_(None),
                RetailerFinancials.source_10q_url.is_(None),
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
        "found_8k": 0,
        "found_10q": 0,
        "not_found_8k": 0,
        "not_found_10q": 0,
    }

    if not rows:
        logger.info("%s: no rows with NULL source URLs", config.name)
        return stats

    logger.info(
        "%s: processing %d quarter(s) with NULL source URLs (newest first)",
        config.name,
        len(rows),
    )

    for row in rows:
        label = _quarter_label(row)
        if row.period_end_date is None:
            if row.source_8k_url is None:
                stats["not_found_8k"] += 1
                logger.info(
                    "%s: source_8k_url not found — missing period_end_date",
                    label,
                )
            if row.source_10q_url is None:
                stats["not_found_10q"] += 1
                logger.info(
                    "%s: source_10q_url not found — missing period_end_date",
                    label,
                )
            continue

        row_updated = False

        if row.source_8k_url is None:
            url_8k = _find_quarter_earnings_8k_url(
                filing_rows,
                row.period_end_date,
                config,
            )
            if url_8k is None:
                stats["not_found_8k"] += 1
                logger.info(
                    "%s: source_8k_url not found — no earnings 8-K within %d days of %s",
                    label,
                    EARNINGS_8K_WINDOW_DAYS,
                    row.period_end_date,
                )
            else:
                row.source_8k_url = url_8k
                stats["found_8k"] += 1
                row_updated = True
                logger.info("%s: source_8k_url found %s", label, url_8k)

        if row.source_10q_url is None:
            expected_form = "10-K" if row.fiscal_quarter == 4 else "10-Q"
            url_10q = _find_quarter_periodic_filing_url(
                filing_rows,
                row.period_end_date,
                row.fiscal_quarter,
                config,
            )
            if url_10q is None:
                stats["not_found_10q"] += 1
                logger.info(
                    "%s: source_10q_url not found — no %s within %d days of %s",
                    label,
                    expected_form,
                    PERIODIC_FILING_WINDOW_DAYS,
                    row.period_end_date,
                )
            else:
                row.source_10q_url = url_10q
                stats["found_10q"] += 1
                row_updated = True
                logger.info("%s: source_10q_url found %s", label, url_10q)

        if row_updated:
            db.commit()
            ctx.inserted()

    logger.info(
        "%s backfill complete — candidates=%d found_8k=%d found_10q=%d "
        "not_found_8k=%d not_found_10q=%d",
        config.name,
        stats["candidates"],
        stats["found_8k"],
        stats["found_10q"],
        stats["not_found_8k"],
        stats["not_found_10q"],
    )
    return stats


def run_historical_url_backfill(db: Session, ctx: IngestionContext) -> dict[str, Any]:
    all_stats: dict[str, Any] = {}
    for config in RETAILER_CONFIGS:
        submissions = _sec_get(config.submissions_url)
        if not isinstance(submissions, dict):
            logger.error("Failed to fetch submissions for %s", config.name)
            all_stats[config.name] = {"error": "submissions_fetch_failed"}
            continue

        filing_rows = load_all_submission_filings(submissions)
        extra_pages = len(submissions.get("filings", {}).get("files", []) or [])
        logger.info(
            "%s: loaded %d filing row(s) from submissions (+%d historical pages)",
            config.name,
            len(filing_rows),
            extra_pages,
        )
        all_stats[config.name] = run_retailer_url_backfill(
            db,
            ctx,
            config,
            filing_rows,
        )
    return all_stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill missing source_8k_url and source_10q_url for Target and Walmart"
        )
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print candidate counts and exit without SEC lookups",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        counts = count_candidates(db)
        for retailer_name, retailer_counts in counts.items():
            logger.info(
                "%s candidates — rows_any_null=%d null_8k=%d null_10q=%d",
                retailer_name,
                retailer_counts["rows_any_null"],
                retailer_counts["null_8k"],
                retailer_counts["null_10q"],
            )

        if args.count_only:
            return 0

        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=_SUBMISSIONS_FILE_BASE,
            db=db,
        ) as ctx:
            stats = run_historical_url_backfill(db, ctx)
        logger.info("Done — %s", stats)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

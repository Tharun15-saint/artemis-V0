"""
Standalone backfill: populate guidance_sales_direction and guidance sales range
fields on Target and Walmart retailer_financials rows from 8-K URLs.

Append-only: demotes prior is_latest rows via mark_latest(), inserts a new row.
"""

from __future__ import annotations

import argparse
import logging
import re
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

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

SCRIPT_VERSION = "guidance-backfill-v1.0"
SOURCE_NAME = "guidance_8k_backfill"

_GUIDANCE_SECTION_KEYWORDS = ("guidance", "outlook")
_SALES_CONTEXT_RE = re.compile(r"sales|revenue|net sales", re.I)
_OUTLOOK_VERB_RE = re.compile(r"(?:expect|anticipate)s?", re.I)


def _get_retailer_ids(db: Session) -> dict[str, Optional[int]]:
    names = {
        "target": target_tier1.TARGET_NAME,
        "walmart": walmart_tier1.WALMART_NAME,
    }
    ids: dict[str, Optional[int]] = {}
    for key, name in names.items():
        retailer = (
            db.query(MajorRetailers).filter(MajorRetailers.name == name).first()
        )
        ids[key] = retailer.retailer_id if retailer else None
        if retailer is None:
            logger.error("%s not found in major_retailers", name)
    return ids


def _quarter_label(row: RetailerFinancials) -> str:
    return f"FY{row.fiscal_year} Q{row.fiscal_quarter} (retailer_id={row.retailer_id})"


def _fetch_8k_text(url: str) -> Optional[str]:
    body = target_tier1._sec_get(url)
    if not isinstance(body, str) or not body.strip():
        return None
    return target_tier1._strip_html(body)


def _parse_percent(raw: str) -> Optional[Decimal]:
    return target_tier1._parse_decimal(raw.replace("%", ""))


def _find_target_guidance_block(text: str) -> Optional[str]:
    for keyword in _GUIDANCE_SECTION_KEYWORDS:
        match = re.search(rf"\b{keyword}\b", text, re.I)
        if match:
            return text[match.start() : match.start() + 2500]

    for verb_match in _OUTLOOK_VERB_RE.finditer(text):
        window = text[verb_match.start() : verb_match.start() + 600]
        if _SALES_CONTEXT_RE.search(window):
            return window

    return None


def _find_walmart_guidance_block(text: str) -> Optional[str]:
    guidance_section = re.search(
        r"\bGuidance\b.{0,120}?reflects the Company",
        text,
        re.I | re.S,
    )
    if guidance_section:
        return text[guidance_section.start() : guidance_section.start() + 3500]

    for keyword in (*_GUIDANCE_SECTION_KEYWORDS, "looking ahead"):
        match = re.search(rf"\b{keyword}\b", text, re.I)
        if match:
            return text[match.start() : match.start() + 3500]

    return None


_WALMART_NET_SALES_CC_RANGE_RE = re.compile(
    r"Net sales\s*\(\s*cc\s*\)\s+"
    r"(?:Increase|increase|Decrease|decrease)\s+"
    r"([\d.]+)\s*%\s+to\s+([\d.]+)\s*%",
    re.I,
)
_WALMART_NET_SALES_NARRATIVE_RE = re.compile(
    r"net sales expected to grow\s+([\d.]+)\s*%\s+to\s+([\d.]+)\s*%",
    re.I,
)


def _pick_walmart_net_sales_range_match(text: str) -> Optional[re.Match[str]]:
    matches = list(_WALMART_NET_SALES_CC_RANGE_RE.finditer(text))
    if not matches:
        return None

    for match in reversed(matches):
        tail = text[match.end() : match.end() + 40]
        if re.match(r"\s*Unchanged\b", tail, re.I):
            return match

    for match in reversed(matches):
        snippet = text[max(0, match.start() - 200) : match.end() + 40]
        if re.search(r"Fiscal year\s+\d{4}", snippet, re.I):
            return match

    return matches[-1]


def _direction_from_range(
    low: Optional[Decimal],
    high: Optional[Decimal],
) -> Optional[str]:
    if low is None and high is None:
        return None
    values = [v for v in (low, high) if v is not None]
    if not values:
        return None
    if all(v > 0 for v in values):
        return "growth"
    if all(v < 0 for v in values):
        return "decline"
    if all(v == 0 for v in values):
        return "flat"
    if low is not None and high is not None and low <= 0 <= high:
        return "flat"
    return None


def _direction_from_language(block: str) -> Optional[str]:
    if re.search(
        r"\b(?:unchanged|stable|flat|in line with prior|maintain(?:ing)?)\b",
        block,
        re.I,
    ):
        return "flat"
    if re.search(
        r"\b(?:decline|decrease|lower|down|contract(?:ing)?|fall(?:ing)?)\b",
        block,
        re.I,
    ) and _SALES_CONTEXT_RE.search(block):
        return "decline"
    if re.search(
        r"\b(?:growth|grow|increase|raise|raised|higher|up)\b",
        block,
        re.I,
    ) and _SALES_CONTEXT_RE.search(block):
        return "growth"
    return None


def _extract_target_guidance(text: str) -> dict[str, Any]:
    block = _find_target_guidance_block(text)
    if block is None:
        return {}

    result: dict[str, Any] = {}

    band_match = re.search(
        r"([\d.]+)\s*(?:%|percent)\s*(?:to|-)\s*([\d.]+)\s*(?:%|percent)",
        block,
        re.I,
    )
    if band_match:
        low = _parse_percent(band_match.group(1))
        high = _parse_percent(band_match.group(2))
        if low is not None:
            result["guidance_sales_range_low"] = low
        if high is not None:
            result["guidance_sales_range_high"] = high
        direction = _direction_from_range(low, high)
        if direction:
            result["guidance_sales_direction"] = direction

    if result.get("guidance_sales_direction") is None:
        single_match = re.search(
            r"(?:net sales growth in a range around|growth in a range around|growth of)\s*"
            r"([\d.]+)\s*(?:%|percent)",
            block,
            re.I,
        )
        if single_match:
            midpoint = _parse_percent(single_match.group(1))
            if midpoint is not None:
                result["guidance_sales_range_low"] = midpoint
                result["guidance_sales_range_high"] = midpoint
                result["guidance_sales_direction"] = (
                    "flat" if midpoint == 0 else "growth" if midpoint > 0 else "decline"
                )

    if result.get("guidance_sales_direction") is None:
        direction = _direction_from_language(block)
        result["guidance_sales_direction"] = direction or "not_provided"

    return result


def _extract_walmart_guidance(text: str) -> dict[str, Any]:
    block = _find_walmart_guidance_block(text)
    if block is None and not re.search(
        r"net sales\s*\(\s*cc\s*\)\s+(?:Increase|increase)|net sales expected to grow",
        text,
        re.I,
    ):
        return {}

    search_text = block if block is not None else text
    result: dict[str, Any] = {}

    range_match = _pick_walmart_net_sales_range_match(search_text)
    if range_match is None and block is not None:
        range_match = _pick_walmart_net_sales_range_match(text)
    if range_match is None:
        range_match = _WALMART_NET_SALES_NARRATIVE_RE.search(search_text)

    if range_match:
        low = _parse_percent(range_match.group(1))
        high = _parse_percent(range_match.group(2))
        if low is not None:
            result["guidance_sales_range_low"] = low
        if high is not None:
            result["guidance_sales_range_high"] = high
        direction = _direction_from_range(low, high)
        if direction:
            result["guidance_sales_direction"] = direction

    if result.get("guidance_sales_direction") is None:
        direction = _direction_from_language(search_text)
        result["guidance_sales_direction"] = direction or "not_provided"

    return result


def _extract_guidance_for_retailer(
    retailer_id: int,
    target_id: Optional[int],
    walmart_id: Optional[int],
    text: str,
) -> dict[str, Any]:
    if retailer_id == target_id:
        return _extract_target_guidance(text)
    if retailer_id == walmart_id:
        return _extract_walmart_guidance(text)
    return {}


def _merge_guidance_fields(payload: dict[str, Any], metrics: dict[str, Any]) -> None:
    payload["guidance_sales_direction"] = metrics.get("guidance_sales_direction")
    if metrics.get("guidance_sales_range_low") is not None:
        payload["guidance_sales_range_low"] = metrics["guidance_sales_range_low"]
    if metrics.get("guidance_sales_range_high") is not None:
        payload["guidance_sales_range_high"] = metrics["guidance_sales_range_high"]


def _sync_row_from_payload(row: RetailerFinancials, payload: dict[str, Any]) -> None:
    """Keep in-memory row in sync after append so later logic sees filled fields."""
    for key, value in payload.items():
        if hasattr(row, key):
            setattr(row, key, value)


def _append_guidance_row(
    db: Session,
    ctx: IngestionContext,
    row: RetailerFinancials,
    metrics: dict[str, Any],
    target_id: Optional[int],
) -> None:
    if row.retailer_id == target_id:
        payload = target_tier1._row_to_financials_payload(row)
        _merge_guidance_fields(payload, metrics)
        target_tier1._validate_target_payload(ctx, payload)
        target_tier1._append_retailer_financials(db, ctx, row.retailer_id, payload)
    else:
        payload = {
            field: getattr(row, field)
            for field in walmart_tier1.RETAILER_FINANCIALS_UPDATE_FIELDS
        }
        _merge_guidance_fields(payload, metrics)
        walmart_tier1._validate_walmart_payload(ctx, payload, prior_row=row)
        walmart_tier1._append_retailer_financials(db, ctx, row.retailer_id, payload)
    _sync_row_from_payload(row, payload)


def run_guidance_backfill(db: Session, ctx: IngestionContext) -> dict[str, int]:
    ids = _get_retailer_ids(db)
    target_id = ids.get("target")
    walmart_id = ids.get("walmart")
    retailer_ids = [rid for rid in (target_id, walmart_id) if rid is not None]
    if not retailer_ids:
        return {
            "candidates": 0,
            "appended": 0,
            "no_section": 0,
            "skipped_no_url": 0,
            "fetch_failed": 0,
        }

    guidance_filters = [RetailerFinancials.guidance_sales_direction.is_(None)]
    if walmart_id is not None:
        guidance_filters.append(
            and_(
                RetailerFinancials.retailer_id == walmart_id,
                RetailerFinancials.guidance_sales_direction == "not_provided",
            )
        )

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id.in_(retailer_ids),
            RetailerFinancials.is_latest.is_(True),
            or_(*guidance_filters),
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
        "no_section": 0,
        "skipped_no_url": 0,
        "fetch_failed": 0,
    }

    if not rows:
        logger.info("No retailer_financials rows with NULL guidance_sales_direction")
        return stats

    logger.info(
        "Processing %d quarter(s) with NULL guidance (newest first)",
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

        metrics = _extract_guidance_for_retailer(
            row.retailer_id,
            target_id,
            walmart_id,
            text,
        )
        if not metrics:
            stats["no_section"] += 1
            logger.info(
                "%s: no guidance section found — leaving guidance_sales_direction NULL",
                label,
            )
            continue

        _append_guidance_row(db, ctx, row, metrics, target_id)
        db.commit()
        stats["appended"] += 1
        logger.info(
            "%s: appended guidance_sales_direction=%s range=%s-%s",
            label,
            metrics.get("guidance_sales_direction"),
            metrics.get("guidance_sales_range_low"),
            metrics.get("guidance_sales_range_high"),
        )

    logger.info(
        "Guidance backfill complete — candidates=%d appended=%d no_section=%d "
        "skipped_no_url=%d fetch_failed=%d",
        stats["candidates"],
        stats["appended"],
        stats["no_section"],
        stats["skipped_no_url"],
        stats["fetch_failed"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill guidance_sales_direction and guidance sales range fields "
            "on Target and Walmart retailer_financials rows from 8-K URLs"
        )
    )
    parser.parse_args()

    db = SessionLocal()
    try:
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=target_tier1._SUBMISSIONS_URL,
            db=db,
        ) as ctx:
            run_guidance_backfill(db, ctx)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""
Walmart Inc Tier 1 ingestion — structured quarterly financials from SEC EDGAR.

Sources: XBRL company facts, 10-Q revenue disaggregation (inline XBRL),
8-K EX-99.1 earnings release, 8-K EX-99.2 earnings presentation.
Writes to retailer_financials (append-only by retailer_id + fiscal_year + fiscal_quarter).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Union

import requests
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal, assert_fk_exists, mark_latest
from database.ingestion_context import IngestionContext
from database.constants import (
    SAMS_CLUB_MODEL_NOTE,
    WALMART_INVENTORY_THRESHOLDS,
    WALMART_US_MODEL_NOTE,
)
from database.models.retail import MajorRetailers, RetailerFinancials
from database.validation.ingestion_validators import (
    validate_and_log,
    validate_gross_margin,
    validate_retailer_revenue,
    validate_sams_club_apparel,
    validate_walmart_general_merch,
)

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

WALMART_NAME = "Walmart Inc"
SCRIPT_VERSION = "2.0.0"
SOURCE_NAME = "walmart_sec_edgar_tier1"
SOURCE_SYSTEM = "walmart_sec_edgar"
WALMART_CIK = "0000104169"
CIK_NUM = "104169"
SEC_USER_AGENT = "Artemis/1.0 supply-chain-intelligence@artemis.com"
SEC_RATE_LIMIT_SECONDS = 0.1
REQUEST_TIMEOUT = 60
QUARTER_FETCH_COUNT = 5

REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
COGS_CONCEPTS = [
    "CostOfRevenue",
    "CostOfGoodsSoldAndServicesSold",
    "CostOfGoodsSold",
]
GROSS_PROFIT_CONCEPTS = ["GrossProfit"]
SGA_CONCEPTS = ["SellingGeneralAndAdministrativeExpense"]
OPERATING_INCOME_CONCEPTS = ["OperatingIncomeLoss"]
INVENTORY_CONCEPTS = ["InventoryNet"]
STORE_COUNT_CONCEPTS = ["NumberOfStores", "NumberOfOperatedStores"]

_COMPANYFACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{WALMART_CIK}.json"
_SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{WALMART_CIK}.json"
_SUBMISSIONS_FILE_BASE = "https://data.sec.gov/submissions/"
_FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
_FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

_QUARTER_FRAME_RE = re.compile(r"^CY(\d{4})Q([1-4])")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_CONTEXT_BLOCK_RE = re.compile(
    r'<xbrli:context id="([^"]+)"[^>]*>(.*?)</xbrli:context>',
    re.S,
)
_IX_NONFRACTION_RE = re.compile(
    r"<ix:nonFraction([^>]*)>([^<]+)</ix:nonFraction>",
    re.S,
)

RELEASE_EXHIBIT_PATTERNS = [
    "earningsrelease",
    "ex-99.1",
    "ex991",
    "ex99.1",
]
PRESENTATION_EXHIBIT_PATTERNS = [
    "earningspresentation",
    "ex-99.2",
    "ex992",
    "ex99.2",
    "presentation",
    "slides",
]

# Inline XBRL segment member names changed around FY2025; accept both conventions.
_DISAGG_MEMBER_RULES: list[tuple[str, tuple[frozenset[str], ...]]] = [
    (
        "walmart_us_general_merch_usd",
        (
            frozenset({"GeneralMerchandiseMember", "WalmartUSMember"}),
            frozenset({"GeneralMerchandiseMember", "WalmartUSSegmentMember"}),
        ),
    ),
    (
        "walmart_us_ecommerce_usd",
        (
            frozenset({"ECommerceMember", "WalmartUSMember"}),
            frozenset({"ECommerceMember", "WalmartUSSegmentMember"}),
        ),
    ),
    (
        "sams_club_home_apparel_usd",
        (
            frozenset({"GeneralMerchandiseMember", "SamsClubUSMember"}),
            frozenset({"HomeandapparelMember", "SamsClubSegmentMember"}),
        ),
    ),
    (
        "sams_club_total_usd",
        (
            frozenset({"SamsClubUSMember"}),
            frozenset({"SamsClubSegmentMember"}),
        ),
    ),
    (
        "sams_club_ecommerce_usd",
        (
            frozenset({"ECommerceMember", "SamsClubUSMember"}),
            frozenset({"ECommerceMember", "SamsClubSegmentMember"}),
        ),
    ),
]

_WALMART_US_SEGMENT_MEMBERS = frozenset({"WalmartUSMember", "WalmartUSSegmentMember"})
_SAMS_CLUB_SEGMENT_MEMBERS = frozenset({"SamsClubUSMember", "SamsClubSegmentMember"})
_INTERNATIONAL_SEGMENT_MEMBERS = frozenset(
    {"WalmartInternationalMember", "WalmartInternationalSegmentMember"}
)

_RELEASE_METRIC_FIELDS = (
    "comparable_sales_growth_pct",
    "digital_comp_sales_pct",
    "gross_margin_pct",
    "gross_margin_change_bps",
    "guidance_sales_direction",
    "guidance_sales_range_low",
    "guidance_sales_range_high",
    "guidance_eps_low",
    "guidance_eps_high",
    "sams_club_comp_sales_ex_fuel_pct",
    "store_count_total",
    "walmart_us_store_count",
    "sams_club_count",
    "walmart_plus_membership_growth_pct",
    "walmart_plus_member_count",
    "transaction_count_growth_pct",
    "average_transaction_value_change_pct",
    "sams_club_member_count",
    "sams_club_membership_fee_revenue_usd",
    "inventory_change_narrative",
)

RETAILER_FINANCIALS_UPDATE_FIELDS = (
    "fiscal_year",
    "fiscal_quarter",
    "period_end_date",
    "filing_date",
    "apparel_revenue_usd",
    "apparel_revenue_pct_total",
    "apparel_yoy_growth_pct",
    "total_net_sales_usd",
    "comparable_sales_growth_pct",
    "digital_comp_sales_pct",
    "gross_margin_pct",
    "gross_margin_change_bps",
    "sga_rate_pct",
    "operating_margin_pct",
    "inventory_usd",
    "inventory_days",
    "store_count_total",
    "store_count_net_change",
    "ecommerce_penetration_pct",
    "guidance_sales_direction",
    "guidance_sales_range_low",
    "guidance_sales_range_high",
    "guidance_eps_low",
    "guidance_eps_high",
    "source_10q_url",
    "source_8k_url",
    "source_8k_presentation_url",
    "walmart_us_general_merch_usd",
    "walmart_us_general_merch_pct",
    "walmart_us_general_merch_yoy_pct",
    "walmart_us_ecommerce_usd",
    "walmart_us_ecommerce_pct_of_total",
    "walmart_us_ecommerce_yoy_growth_pct",
    "sams_club_home_apparel_usd",
    "sams_club_home_apparel_pct",
    "sams_club_home_apparel_yoy_pct",
    "sams_club_total_usd",
    "sams_club_ecommerce_usd",
    "sams_club_comp_sales_ex_fuel_pct",
    "walmart_us_store_count",
    "sams_club_count",
    "walmart_us_model_note",
    "sams_club_model_note",
    "walmart_us_inventory_usd",
    "sams_club_inventory_usd",
    "walmart_international_inventory_usd",
    "walmart_us_inventory_yoy_change_pct",
    "sams_club_inventory_yoy_change_pct",
    "walmart_us_inventory_days",
    "sams_club_inventory_days",
    "walmart_us_inventory_to_sales_ratio",
    "general_merch_inventory_proxy_signal",
    "inventory_positioning_language",
    "inventory_change_narrative",
    "transaction_count_growth_pct",
    "average_transaction_value_change_pct",
    "ticket_vs_traffic_split",
    "walmart_plus_member_count",
    "walmart_plus_membership_growth_pct",
    "sams_club_member_count",
    "sams_club_membership_fee_revenue_usd",
    "sams_club_member_count_yoy_pct",
    "private_brand_mix_change_bps",
    "xbrl_extracted",
)


def _sec_get(url: str) -> Optional[Union[dict[str, Any], str]]:
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        time.sleep(SEC_RATE_LIMIT_SECONDS)
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


def _strip_html(text: str) -> str:
    plain = _HTML_TAG_RE.sub(" ", text)
    plain = (
        plain.replace("&amp;", "&")
        .replace("&#160;", " ")
        .replace("&nbsp;", " ")
        .replace("&#8217;", "'")
        .replace("&#8216;", "'")
    )
    return _WHITESPACE_RE.sub(" ", plain).strip()


def _normalize_walmart_earnings_text(text: str) -> str:
    """Normalize HTML entities and whitespace before earnings-release regex matching."""
    normalized = (
        text.replace("&#32;", " ")
        .replace("&#46;", ".")
        .replace("&#37;", "%")
        .replace("&#8217;", "'")
        .replace("\xa0", " ")
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_decimal(raw: str) -> Optional[Decimal]:
    cleaned = raw.replace(",", "").replace("$", "").strip()
    if not cleaned or cleaned in (".", "-"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _fp_to_int(fp: str) -> Optional[int]:
    if fp and fp.startswith("Q") and fp[1:].isdigit():
        return int(fp[1])
    return None


def _safe_div(numerator: Decimal, denominator: Decimal) -> Optional[Decimal]:
    if denominator == 0:
        return None
    return numerator / denominator


def _accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


def _filing_doc_url(accession: str, document: str) -> str:
    return _FILING_DOC_URL.format(
        cik=CIK_NUM,
        accession=_accession_nodash(accession),
        document=document,
    )


def _fiscal_key(fy: int, fp: str) -> tuple[int, int]:
    return fy, _fp_to_int(fp) or 0


def _collect_us_gaap_entries(
    us_gaap: dict[str, Any],
    concept_names: list[str],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for concept_name in concept_names:
        concept = us_gaap.get(concept_name)
        if not concept:
            continue
        for unit_values in concept.get("units", {}).values():
            if isinstance(unit_values, list):
                for entry in unit_values:
                    row = dict(entry)
                    row["concept"] = concept_name
                    entries.append(row)
    return entries


def _extract_fiscal_quarter_maps(
    us_gaap: dict[str, Any],
) -> tuple[
    dict[tuple[int, int], dict[str, Any]],
    dict[tuple[int, int], Decimal],
    dict[tuple[int, int], Decimal],
    dict[tuple[int, int], Decimal],
    dict[tuple[int, int], Decimal],
    dict[tuple[int, int], Decimal],
    dict[tuple[int, int], Decimal],
    dict[tuple[int, int], Decimal],
]:
    def duration_map(concepts: list[str]) -> dict[tuple[int, int], Decimal]:
        candidates: dict[tuple[int, int], dict[str, Any]] = {}
        for entry in _collect_us_gaap_entries(us_gaap, concepts):
            fp = entry.get("fp")
            fy = entry.get("fy")
            end = entry.get("end")
            if fp not in ("Q1", "Q2", "Q3", "Q4") or fy is None or not end:
                continue
            frame = entry.get("frame") or ""
            if frame.endswith("I"):
                continue
            if entry.get("val") is None:
                continue
            key = _fiscal_key(int(fy), fp)
            end_date = date.fromisoformat(str(end))
            start_date = (
                date.fromisoformat(str(entry["start"]))
                if entry.get("start")
                else None
            )
            duration_days = (end_date - start_date).days if start_date else 999
            has_frame = bool(frame and _QUARTER_FRAME_RE.match(frame.rstrip("I")))
            current = candidates.get(key)
            if current is None:
                candidates[key] = {
                    "val": Decimal(str(entry["val"])),
                    "end": end_date,
                    "has_frame": has_frame,
                    "duration_days": duration_days,
                }
                continue
            if end_date > current["end"]:
                replace = True
            elif end_date < current["end"]:
                replace = False
            elif has_frame and not current["has_frame"]:
                replace = True
            elif current["has_frame"] and not has_frame:
                replace = False
            elif duration_days < current["duration_days"]:
                replace = True
            else:
                replace = False
            if replace:
                candidates[key] = {
                    "val": Decimal(str(entry["val"])),
                    "end": end_date,
                    "has_frame": has_frame,
                    "duration_days": duration_days,
                }
        return {key: data["val"] for key, data in candidates.items()}

    def instant_map(concepts: list[str]) -> dict[tuple[int, int], Decimal]:
        result: dict[tuple[int, int], Decimal] = {}
        for entry in _collect_us_gaap_entries(us_gaap, concepts):
            fp = entry.get("fp")
            fy = entry.get("fy")
            end = entry.get("end")
            if fy is None or not end or entry.get("val") is None:
                continue
            frame = entry.get("frame") or ""
            key: Optional[tuple[int, int]] = None
            frame_match = _QUARTER_FRAME_RE.match(frame.rstrip("I"))
            if frame_match:
                key = (int(frame_match.group(1)), int(frame_match.group(2)))
            if key is None and fp in ("Q1", "Q2", "Q3", "Q4"):
                key = _fiscal_key(int(fy), fp)
            if key is None:
                continue
            val = Decimal(str(entry["val"]))
            if key not in result or frame.endswith("I"):
                result[key] = val
        return result

    revenue = duration_map(REVENUE_CONCEPTS)
    cogs = duration_map(COGS_CONCEPTS)
    gross_profit = duration_map(GROSS_PROFIT_CONCEPTS)
    sga = duration_map(SGA_CONCEPTS)
    operating = duration_map(OPERATING_INCOME_CONCEPTS)
    inventory = instant_map(INVENTORY_CONCEPTS)
    store_count = instant_map(STORE_COUNT_CONCEPTS)

    meta: dict[tuple[int, int], dict[str, Any]] = {}
    for entry in _collect_us_gaap_entries(us_gaap, REVENUE_CONCEPTS):
        fp = entry.get("fp")
        fy = entry.get("fy")
        if fp not in ("Q1", "Q2", "Q3", "Q4") or fy is None or not entry.get("end"):
            continue
        key = _fiscal_key(int(fy), fp)
        if key not in revenue:
            continue
        end_date = date.fromisoformat(str(entry["end"]))
        start_date = (
            date.fromisoformat(str(entry["start"])) if entry.get("start") else None
        )
        duration_days = (end_date - start_date).days if start_date else 999
        frame = entry.get("frame") or ""
        has_frame = bool(frame and _QUARTER_FRAME_RE.match(frame.rstrip("I")))
        current = meta.get(key)
        if current is None:
            meta[key] = {
                "fiscal_year": int(fy),
                "fiscal_quarter": _fp_to_int(fp),
                "period_end_date": end_date,
                "filing_date": (
                    date.fromisoformat(str(entry["filed"]))
                    if entry.get("filed")
                    else None
                ),
                "accession": entry.get("accn"),
                "duration_days": duration_days,
                "has_frame": has_frame,
            }
            continue
        if end_date > current["period_end_date"]:
            replace = True
        elif end_date < current["period_end_date"]:
            replace = False
        elif has_frame and not current["has_frame"]:
            replace = True
        elif current["has_frame"] and not has_frame:
            replace = False
        elif duration_days < current["duration_days"]:
            replace = True
        else:
            replace = False
        if replace:
            meta[key] = {
                "fiscal_year": int(fy),
                "fiscal_quarter": _fp_to_int(fp),
                "period_end_date": end_date,
                "filing_date": (
                    date.fromisoformat(str(entry["filed"]))
                    if entry.get("filed")
                    else None
                ),
                "accession": entry.get("accn"),
                "duration_days": duration_days,
                "has_frame": has_frame,
            }

    for key, rev in revenue.items():
        if key in gross_profit:
            continue
        if key in cogs:
            gross_profit[key] = rev - cogs[key]

    return meta, revenue, cogs, gross_profit, sga, operating, inventory, store_count


def _select_output_fiscal_keys(
    meta: dict[tuple[int, int], dict[str, Any]],
) -> list[tuple[int, int]]:
    return sorted(
        meta.keys(),
        key=lambda k: meta[k]["period_end_date"],
        reverse=True,
    )[:QUARTER_FETCH_COUNT]


def _prior_year_key(key: tuple[int, int]) -> tuple[int, int]:
    return key[0] - 1, key[1]


def _trailing_four_quarter_cogs(
    key: tuple[int, int],
    cogs: dict[tuple[int, int], Decimal],
    ordered_keys: list[tuple[int, int]],
) -> Optional[Decimal]:
    if key not in ordered_keys:
        return None
    idx = ordered_keys.index(key)
    window = ordered_keys[max(0, idx - 3) : idx + 1]
    total = Decimal("0")
    for qkey in window:
        if qkey not in cogs:
            return None
        total += cogs[qkey]
    if len(window) < 4:
        total = total * Decimal("4") / Decimal(str(len(window)))
    return total


def _fetch_filing_index(accession: str) -> Optional[dict[str, Any]]:
    url = _FILING_INDEX_URL.format(cik=CIK_NUM, accession=_accession_nodash(accession))
    payload = _sec_get(url)
    return payload if isinstance(payload, dict) else None


def _fetch_filing_html(accession: str, document: str) -> Optional[str]:
    url = _filing_doc_url(accession, document)
    body = _sec_get(url)
    return body if isinstance(body, str) else None


def _disagg_field_for_members(members: set[str]) -> Optional[str]:
    member_key = frozenset(members)
    for field_name, aliases in _DISAGG_MEMBER_RULES:
        if member_key in aliases:
            return field_name
    return None


def _single_segment_field(members: set[str], aliases: frozenset[str]) -> bool:
    return len(members) == 1 and bool(members & aliases)


def _find_primary_htm(
    index_payload: dict[str, Any],
    period_end: Optional[date] = None,
) -> Optional[str]:
    items = index_payload.get("directory", {}).get("item", [])
    candidates = [
        item["name"]
        for item in items
        if isinstance(item, dict)
        and item.get("name", "").endswith(".htm")
        and "exhibit" not in item["name"].lower()
        and "index" not in item["name"].lower()
        and item["name"].startswith("wmt-")
    ]
    if not candidates:
        return None
    if period_end is not None:
        dated_doc = f"wmt-{period_end.strftime('%Y%m%d')}.htm"
        if dated_doc in candidates:
            return dated_doc
    return sorted(candidates, key=len)[0]


def _find_exhibit_by_patterns(
    index_payload: dict[str, Any],
    patterns: list[str],
) -> Optional[str]:
    items = index_payload.get("directory", {}).get("item", [])
    candidates: list[tuple[int, str]] = []
    for item in items:
        name = item.get("name", "")
        lower = name.lower()
        if not lower.endswith(".htm"):
            continue
        for idx, pattern in enumerate(patterns):
            if pattern in lower:
                candidates.append((idx, name))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0], len(pair[1])))
    return candidates[0][1]


def _context_end_date(block: str) -> Optional[date]:
    match = re.search(r"<xbrli:endDate>(\d{4}-\d{2}-\d{2})</xbrli:endDate>", block)
    if not match:
        return None
    return date.fromisoformat(match.group(1))


def _context_duration_days(block: str) -> Optional[int]:
    start_match = re.search(r"<xbrli:startDate>(\d{4}-\d{2}-\d{2})</xbrli:startDate>", block)
    end_match = re.search(r"<xbrli:endDate>(\d{4}-\d{2}-\d{2})</xbrli:endDate>", block)
    if not start_match or not end_match:
        return None
    start_date = date.fromisoformat(start_match.group(1))
    end_date = date.fromisoformat(end_match.group(1))
    return (end_date - start_date).days


def _context_members(block: str) -> set[str]:
    return set(re.findall(r"wmt:([A-Za-z]+Member)", block))


def _scaled_usd_value(raw_value: str, attrs: str) -> Optional[Decimal]:
    value = _parse_decimal(raw_value)
    if value is None:
        return None
    scale_match = re.search(r'scale="(\d+)"', attrs)
    scale = int(scale_match.group(1)) if scale_match else 0
    return value * (Decimal(10) ** scale)


def _parse_walmart_disaggregation(
    html: str,
    period_end: date,
) -> dict[str, Optional[Decimal]]:
    contexts: dict[str, dict[str, Any]] = {}
    for context_id, block in _CONTEXT_BLOCK_RE.findall(html):
        end_date = _context_end_date(block)
        duration_days = _context_duration_days(block)
        if end_date is None:
            continue
        contexts[context_id] = {
            "end": end_date,
            "duration_days": duration_days,
            "members": _context_members(block),
        }

    results: dict[str, Optional[Decimal]] = {
        "walmart_us_general_merch_usd": None,
        "walmart_us_ecommerce_usd": None,
        "sams_club_home_apparel_usd": None,
        "sams_club_total_usd": None,
        "sams_club_ecommerce_usd": None,
    }

    for attrs, raw_value in _IX_NONFRACTION_RE.findall(html):
        if "RevenueFromContractWithCustomerExcludingAssessedTax" not in attrs:
            continue
        context_match = re.search(r'contextRef="([^"]+)"', attrs)
        if not context_match:
            continue
        context = contexts.get(context_match.group(1))
        if not context:
            continue
        if context["end"] != period_end:
            continue
        duration_days = context["duration_days"]
        if duration_days is not None and not (75 <= duration_days <= 110):
            continue
        field_name = _disagg_field_for_members(context["members"])
        if field_name is None:
            continue
        amount = _scaled_usd_value(raw_value, attrs)
        if amount is None:
            continue
        results[field_name] = amount

    return results


def _parse_walmart_10q_segment_data(
    html: str,
    period_end: date,
) -> dict[str, Optional[Decimal]]:
    contexts: dict[str, dict[str, Any]] = {}
    for context_id, block in _CONTEXT_BLOCK_RE.findall(html):
        end_date = _context_end_date(block)
        duration_days = _context_duration_days(block)
        if end_date is None:
            continue
        contexts[context_id] = {
            "end": end_date,
            "duration_days": duration_days,
            "members": _context_members(block),
        }

    results: dict[str, Optional[Decimal]] = {
        "walmart_us_inventory_usd": None,
        "sams_club_inventory_usd": None,
        "walmart_international_inventory_usd": None,
        "walmart_us_cogs_usd": None,
        "sams_club_cogs_usd": None,
        "walmart_us_segment_revenue_usd": None,
    }

    for attrs, raw_value in _IX_NONFRACTION_RE.findall(html):
        context_match = re.search(r'contextRef="([^"]+)"', attrs)
        if not context_match:
            continue
        context = contexts.get(context_match.group(1))
        if not context or context["end"] != period_end:
            continue
        members = context["members"]
        amount = _scaled_usd_value(raw_value, attrs)
        if amount is None:
            continue

        if "InventoryNet" in attrs:
            duration_days = context["duration_days"]
            if duration_days is not None:
                continue
            if _single_segment_field(members, _WALMART_US_SEGMENT_MEMBERS):
                results["walmart_us_inventory_usd"] = amount
            elif _single_segment_field(members, _SAMS_CLUB_SEGMENT_MEMBERS):
                results["sams_club_inventory_usd"] = amount
            elif _single_segment_field(members, _INTERNATIONAL_SEGMENT_MEMBERS):
                results["walmart_international_inventory_usd"] = amount

        if any(concept in attrs for concept in COGS_CONCEPTS):
            duration_days = context["duration_days"]
            if duration_days is None or not (75 <= duration_days <= 110):
                continue
            if _single_segment_field(members, _WALMART_US_SEGMENT_MEMBERS):
                results["walmart_us_cogs_usd"] = amount
            elif _single_segment_field(members, _SAMS_CLUB_SEGMENT_MEMBERS):
                results["sams_club_cogs_usd"] = amount

        if "RevenueFromContractWithCustomerExcludingAssessedTax" in attrs:
            duration_days = context["duration_days"]
            if duration_days is None or not (75 <= duration_days <= 110):
                continue
            if _single_segment_field(members, _WALMART_US_SEGMENT_MEMBERS):
                results["walmart_us_segment_revenue_usd"] = amount

    return results


def _segment_inventory_days(
    inventory_usd: Optional[Decimal],
    segment_cogs_quarterly: Optional[Decimal],
) -> Optional[Decimal]:
    if inventory_usd is None or segment_cogs_quarterly is None:
        return None
    if segment_cogs_quarterly == 0:
        return None
    annualised_cogs = segment_cogs_quarterly * Decimal("4")
    return _safe_div(inventory_usd, annualised_cogs / Decimal("365"))


def _inventory_proxy_signal(
    inventory_days: Optional[Decimal],
    entity: str,
) -> str:
    if inventory_days is None:
        return "unknown"
    thresholds = WALMART_INVENTORY_THRESHOLDS[entity]
    if inventory_days < Decimal(str(thresholds["lean"])):
        return "lean"
    if inventory_days > Decimal(str(thresholds["elevated"])):
        return "high"
    if inventory_days > Decimal(str(thresholds["normal_high"])):
        return "elevated"
    return "normal"


def _derive_ticket_vs_traffic_split(
    transaction_growth: Optional[Decimal],
    atv_change: Optional[Decimal],
) -> Optional[str]:
    if transaction_growth is None and atv_change is None:
        return None
    txn = transaction_growth or Decimal("0")
    atv = atv_change or Decimal("0")
    if txn < 0 and atv < 0:
        return "declining_both"
    if txn > atv and txn > Decimal("0.5"):
        return "traffic_led"
    if atv > txn and atv > Decimal("0.5"):
        return "ticket_led"
    if abs(txn) <= Decimal("0.5") and abs(atv) <= Decimal("0.5"):
        return "balanced"
    if txn >= atv:
        return "traffic_led"
    return "ticket_led"


def _extract_inventory_narrative(text: str, max_len: int = 500) -> Optional[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    keywords = (
        "inventory",
        "softlines",
        "general merchandise",
        "apparel",
        "in-stock",
        "in stock",
    )
    matches = [
        sentence.strip()
        for sentence in sentences
        if any(keyword in sentence.lower() for keyword in keywords)
        and "inventory" in sentence.lower()
    ]
    if not matches:
        return None
    combined = " ".join(matches[:3])
    return combined[:max_len]


def _calc_yoy_pct(
    current: Optional[Decimal],
    prior: Optional[Decimal],
) -> Optional[Decimal]:
    if current is None or prior is None or prior == 0:
        return None
    return (current / prior - Decimal("1")) * Decimal("100")


def _prior_year_financials_row(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
) -> Optional[RetailerFinancials]:
    return (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.fiscal_year == fiscal_year - 1,
            RetailerFinancials.fiscal_quarter == fiscal_quarter,
            RetailerFinancials.is_latest.is_(True),
        )
        .first()
    )


def _find_latest_earnings_8k(
    submissions: dict[str, Any],
) -> Optional[dict[str, str]]:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])

    for form, accn, filed in zip(forms, accessions, filing_dates):
        if form != "8-K":
            continue
        index_payload = _fetch_filing_index(accn)
        if not index_payload:
            continue
        release = _find_exhibit_by_patterns(index_payload, RELEASE_EXHIBIT_PATTERNS)
        if release:
            return {
                "accession": accn,
                "filing_date": filed,
                "release_document": release,
            }
    return None


def _parse_filing_date(raw: str) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _iter_submission_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent")
    if recent is None and submissions.get("form"):
        recent = submissions
    if not recent:
        return []
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    rows: list[dict[str, Any]] = []
    for form, accn, filed in zip(forms, accessions, filing_dates):
        filed_date = _parse_filing_date(filed)
        if filed_date is None:
            continue
        rows.append(
            {
                "form": form,
                "accession": accn,
                "filing_date": filed_date,
            }
        )
    return rows


def _walmart_fiscal_year_period_end(fiscal_year: int) -> date:
    """Walmart fiscal year N ends January 31 of calendar year N."""
    return date(fiscal_year, 1, 31)


def _find_walmart_10k_doc_url(
    fiscal_year: int,
    filing_rows: Optional[list[dict[str, Any]]] = None,
) -> Optional[str]:
    """Resolve the primary 10-K document URL for a Walmart fiscal year."""
    period_end = _walmart_fiscal_year_period_end(fiscal_year)
    expected_doc = f"wmt-{period_end.strftime('%Y%m%d')}.htm"

    if filing_rows is None:
        submissions = _sec_get(_SUBMISSIONS_URL)
        if not isinstance(submissions, dict):
            return None
        filing_rows = _load_all_submission_filings(submissions)

    for filing in filing_rows:
        if filing["form"] != "10-K":
            continue
        index_payload = _fetch_filing_index(filing["accession"])
        if not index_payload:
            continue
        primary_doc = _find_primary_htm(index_payload, period_end)
        if primary_doc == expected_doc:
            return _filing_doc_url(filing["accession"], primary_doc)
    return None


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


def _find_quarter_earnings_8k(
    submissions: dict[str, Any],
    period_end: date,
    filing_rows: Optional[list[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    rows = filing_rows if filing_rows is not None else _load_all_submission_filings(submissions)
    window_start = period_end + timedelta(days=15)
    window_end = period_end + timedelta(days=75)
    for filing in rows:
        if filing["form"] != "8-K":
            continue
        filed_date = filing["filing_date"]
        if filed_date < window_start or filed_date > window_end:
            continue
        index_payload = _fetch_filing_index(filing["accession"])
        if not index_payload:
            continue
        release = _find_exhibit_by_patterns(index_payload, RELEASE_EXHIBIT_PATTERNS)
        if release:
            return {
                "accession": filing["accession"],
                "filing_date": filed_date,
                "index_payload": index_payload,
                "release_document": release,
            }
    return None


def _fetch_quarter_earnings_metrics(
    submissions: dict[str, Any],
    period_end: date,
    fiscal_quarter: Optional[int] = None,
    filing_rows: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], dict[str, Any], Optional[str], Optional[str]]:
    release_metrics: dict[str, Any] = {}
    presentation_metrics: dict[str, Any] = {}
    source_8k_url: Optional[str] = None
    source_8k_presentation_url: Optional[str] = None

    earnings_8k = _find_quarter_earnings_8k(
        submissions, period_end, filing_rows=filing_rows
    )
    if not earnings_8k:
        return release_metrics, presentation_metrics, source_8k_url, source_8k_presentation_url

    accession = earnings_8k["accession"]
    release_doc = earnings_8k["release_document"]
    release_html = _fetch_filing_html(accession, release_doc)
    if release_html:
        source_8k_url = _filing_doc_url(accession, release_doc)
        release_metrics = _parse_earnings_release(
            release_html, fiscal_quarter=fiscal_quarter
        )

    presentation_doc = _find_exhibit_by_patterns(
        earnings_8k["index_payload"], PRESENTATION_EXHIBIT_PATTERNS
    )
    if presentation_doc:
        presentation_html = _fetch_filing_html(accession, presentation_doc)
        if presentation_html:
            source_8k_presentation_url = _filing_doc_url(accession, presentation_doc)
            presentation_metrics = _parse_earnings_presentation(
                presentation_html, fiscal_quarter=fiscal_quarter
            )
            if release_metrics.get("comparable_sales_growth_pct") is None:
                comp = presentation_metrics.get("comparable_sales_growth_pct")
                if comp is not None:
                    release_metrics["comparable_sales_growth_pct"] = comp

    return release_metrics, presentation_metrics, source_8k_url, source_8k_presentation_url


def _extract_walmart_us_comp_sales(
    text: str,
    fiscal_quarter: Optional[int] = None,
) -> Optional[Decimal]:
    normalized = re.sub(
        r"\s+",
        " ",
        text.replace("&#32;", " ").replace("&#59;", "").replace("&#8217;", "'"),
    )

    if fiscal_quarter is not None:
        quarter_patterns = [
            rf"Q{fiscal_quarter}\s+comp sales(?:\s+\d+)?\s+grew\s+([\d.]+)\s*%",
            rf"Q{fiscal_quarter}[^%]{{0,200}}Walmart U\.S\.[^%]{{0,120}}comp sales[^%\d]{{0,40}}([\d.]+)\s*%",
            rf"Q{fiscal_quarter}[^%]{{0,200}}Comp sales \(ex\. fuel\)[^%\d]{{0,40}}([\d.]+)\s*%",
        ]
        for pattern in quarter_patterns:
            match = re.search(pattern, normalized, re.I | re.S)
            if match:
                value = _parse_decimal(match.group(1))
                if value is not None and Decimal("0") <= value <= Decimal("20"):
                    return value

    patterns = [
        r"Walmart U\.S\.\s+comp(?:arable)?\s+sales(?:\s+\d+)?\s+"
        r"(?:grew|increased|up|rose|were|of)?\s*([\d.]+)\s*%",
        r"Walmart U\.S\.[^%]{0,250}?comp(?:arable)?\s+sales[^%\d]{0,80}([\d.]+)\s*%",
        r"Walmart U\.S\.[^S]{0,500}?Comp sales \(ex\. fuel\)[^%\d]{0,40}([\d.]+)\s*%",
        r"Walmart U\.S\.[^S]{0,500}?Comp sales\s+\(ex\.\s*fuel\)[^%\d]{0,40}([\d.]+)\s*%",
        r"Walmart U\.S\.[^%]{0,300}?comparable sales[^%\d]{0,60}([\d.]+)\s*%",
        r"Walmart U\.S\.[^%]{0,300}?comparable sales grew\s+([\d.]+)\s*%",
        r"Walmart U\.S\.[^%]{0,300}?comp sales grew\s+([\d.]+)\s*%",
        r"U\.S\. comp sales(?:\s+\d+)?\s+(?:grew|increased|up|rose)\s+([\d.]+)\s*%",
        r"U\.S\.[^%]{0,400}?Comp sales \(ex\. fuel\)\s+([\d.]+)\s*%",
        r"U\.S\.[^%]{0,400}?Comp sales\s+\(ex\.\s*fuel\)\s+([\d.]+)\s*%",
        r"Comp sales \(ex\. fuel\)[^%\d]{0,40}([\d.]+)\s*%",
        r"Comp sales\s+\(ex\.\s*fuel\)[^%\d]{0,40}([\d.]+)\s*%",
        r"comparable sales grew\s+([\d.]+)\s*%",
        r"comp sales grew\s+([\d.]+)\s*%",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.I | re.S)
        if not match:
            continue
        value = _parse_decimal(match.group(1))
        if value is not None and Decimal("0") <= value <= Decimal("20"):
            return value
    return None


def _extract_walmart_us_comp_sales_presentation(text: str) -> Optional[Decimal]:
    during_match = re.search(
        r"Walmart U\.S\.\s+comp(?:arable)?\s+sales(?:\s+\d+)?\s+During[^%]{0,250}?"
        r"([\d.]+)\s*%",
        text,
        re.I | re.S,
    )
    if during_match:
        value = _parse_decimal(during_match.group(1))
        if value is not None:
            return value

    chart_match = re.search(
        r"Walmart U\.S\.\s+(?:revenues|comp(?:arable)?\s+sales\d*)"
        r"((?:\s+[\d.]+\s*%)+)\s*(?:Q\d\s+FY)+",
        text,
        re.I | re.S,
    )
    if chart_match:
        percentages = re.findall(r"([\d.]+)\s*%", chart_match.group(1))
        if percentages:
            return _parse_decimal(percentages[-1])
    return None


def _parse_store_count_int(raw: str) -> Optional[int]:
    value = _parse_decimal(raw)
    if value is not None:
        return int(value)
    return None


def _extract_walmart_store_count_metrics(
    text: str,
    *,
    is_annual: bool = False,
) -> dict[str, Any]:
    """
    Parse Walmart store counts from 8-K earnings releases or 10-K unit tables.

    8-K (is_annual=False): global rounded store_count_total from boilerplate.
    10-K (is_annual=True): precise Total Company, Walmart U.S. Total, Sam's Club U.S.
    """
    metrics: dict[str, Any] = {}

    if is_annual:
        unit_table = re.search(
            r"(?:Total )?Retail Unit Count.*?(?:Owned and Leased|Total Company\s+[\d,]+)",
            text,
            re.I | re.S,
        )
        block = unit_table.group(0) if unit_table else text

        total_company = re.search(r"Total Company\s+([\d,]+)", block, re.I)
        if total_company:
            count = _parse_store_count_int(total_company.group(1))
            if count is not None:
                metrics["store_count_total"] = count

        wmt_us = re.search(r"Walmart U\.S\. Total\s+([\d,]+)", block, re.I)
        if wmt_us:
            count = _parse_store_count_int(wmt_us.group(1))
            if count is not None:
                metrics["walmart_us_store_count"] = count

        for sams in re.finditer(
            r"Sam(?:'s|&#8217;s) Club U\.S\.\s+([\d,]+)",
            block,
            re.I,
        ):
            count = _parse_store_count_int(sams.group(1))
            if count is None or count > 2000:
                continue
            tail = block[sams.end() : sams.end() + 200]
            if re.search(r"U\.S\. Total", tail, re.I):
                metrics["sams_club_count"] = count
                break
    else:
        normalized = _normalize_walmart_earnings_text(text)
        for pattern in (
            r"at\s+([\d,]+)\s+retail units under",
            r"at more than\s+([\d,]+)\s+retail units",
            r"more than\s+([\d,]+)\s+retail units",
            r"more than ([\d,]+)\s*(?:&#32;)?\s*stores under",
            r"more than ([\d,]+)\s+stores",
            r"visit our more than ([\d,]+) stores under",
            r"visit more than ([\d,]+) stores",
            r"visit approximately ([\d,]+) stores",
            r"visit (?:our|more than|approximately) ([\d,]+) stores under",
        ):
            global_match = re.search(pattern, normalized, re.I)
            if global_match:
                count = _parse_store_count_int(global_match.group(1))
                if count is not None:
                    metrics["store_count_total"] = count
                    break

    return metrics


def _legacy_xbrl_store_count(
    key: tuple[int, int],
    store_count_xbrl: dict[tuple[int, int], Decimal],
) -> Optional[int]:
    """XBRL NumberOfStores only filed through FY2020; legacy fallback for FY2017-2019 Q4."""
    if key[0] > 2019:
        return None
    value = store_count_xbrl.get(key)
    if value is not None:
        return int(value)
    return None


def _apply_walmart_store_count_fields(
    payload: dict[str, Any],
    key: tuple[int, int],
    store_count_xbrl: dict[tuple[int, int], Decimal],
    annual_store_metrics: dict[str, Any],
) -> None:
    """Resolve store_count_total (10-K > 8-K > legacy XBRL) and Q4 segment counts."""
    if annual_store_metrics.get("store_count_total") is not None:
        payload["store_count_total"] = annual_store_metrics["store_count_total"]
    elif payload.get("store_count_total") is None:
        legacy = _legacy_xbrl_store_count(key, store_count_xbrl)
        if legacy is not None:
            payload["store_count_total"] = legacy

    if annual_store_metrics.get("walmart_us_store_count") is not None:
        payload["walmart_us_store_count"] = annual_store_metrics[
            "walmart_us_store_count"
        ]
    if annual_store_metrics.get("sams_club_count") is not None:
        payload["sams_club_count"] = annual_store_metrics["sams_club_count"]


def _merge_quarter_earnings_metrics(
    payload: dict[str, Any],
    release_metrics: dict[str, Any],
    presentation_metrics: dict[str, Any],
    prior_row: Optional[RetailerFinancials],
) -> None:
    for field in _RELEASE_METRIC_FIELDS:
        if release_metrics.get(field) is not None:
            payload[field] = release_metrics[field]

    if payload.get("comparable_sales_growth_pct") is None:
        comp = presentation_metrics.get("comparable_sales_growth_pct")
        if comp is not None:
            payload["comparable_sales_growth_pct"] = comp

    if presentation_metrics.get("private_brand_mix_change_bps") is not None:
        payload["private_brand_mix_change_bps"] = presentation_metrics[
            "private_brand_mix_change_bps"
        ]
    if presentation_metrics.get("inventory_positioning_language") is not None:
        payload["inventory_positioning_language"] = presentation_metrics[
            "inventory_positioning_language"
        ]

    split = _derive_ticket_vs_traffic_split(
        payload.get("transaction_count_growth_pct"),
        payload.get("average_transaction_value_change_pct"),
    )
    if split is not None:
        payload["ticket_vs_traffic_split"] = split
    if payload.get("sams_club_member_count") is not None and prior_row is not None:
        payload["sams_club_member_count_yoy_pct"] = _calc_yoy_pct(
            payload["sams_club_member_count"],
            prior_row.sams_club_member_count,
        )


def _parse_earnings_release(
    html: str,
    fiscal_quarter: Optional[int] = None,
) -> dict[str, Any]:
    text = _strip_html(html)
    metrics: dict[str, Any] = {}

    wmt_comp = _extract_walmart_us_comp_sales(text, fiscal_quarter=fiscal_quarter)
    if wmt_comp is not None:
        metrics["comparable_sales_growth_pct"] = wmt_comp

    if metrics.get("comparable_sales_growth_pct") is None:
        norm = _normalize_walmart_earnings_text(text)

        if re.search(
            r"Walmart U\.S\. comp sales were flat",
            norm,
            re.I,
        ):
            metrics["comparable_sales_growth_pct"] = Decimal("0")
        else:
            narr_match = re.search(
                r"Walmart U\.S\. comp sales\s+"
                r"(increased|decreased|grew|declined)\s+([\d.]+)\s*percent",
                norm,
                re.I,
            )
            if narr_match:
                verb = narr_match.group(1).lower()
                value = _parse_decimal(narr_match.group(2))
                if value is not None:
                    if verb in ("decreased", "declined"):
                        value = -value
                    metrics["comparable_sales_growth_pct"] = value

        if metrics.get("comparable_sales_growth_pct") is None:
            alt_narr = re.search(
                r"Comp sales at Walmart U\.S\.\s+"
                r"(increased|decreased|grew|declined)\s+([\d.]+)\s*(?:%|percent)",
                norm,
                re.I,
            )
            if alt_narr:
                verb = alt_narr.group(1).lower()
                value = _parse_decimal(alt_narr.group(2))
                if value is not None:
                    if verb in ("decreased", "declined"):
                        value = -value
                    metrics["comparable_sales_growth_pct"] = value

        if metrics.get("comparable_sales_growth_pct") is None:
            table_match = re.search(
                r"comparable store sales results.{0,1200}?"
                r"Walmart U\.S\.\s+([\d.]+)%\s+([\d.]+)%",
                norm,
                re.I | re.S,
            )
            if table_match:
                metrics["comparable_sales_growth_pct"] = Decimal(table_match.group(1))

        if metrics.get("comparable_sales_growth_pct") is None:
            total_match = re.search(r"Total U\.S\.\s+([\d.]+)%", norm, re.I)
            if total_match:
                metrics["comparable_sales_growth_pct"] = Decimal(total_match.group(1))

        if metrics.get("comparable_sales_growth_pct") is None:
            for table_pattern in (
                r"Walmart U\.S\..{0,600}?Comp sales \(ex\. fuel\)[^%\d]{0,40}([\d.]+)\s*%",
                r"Walmart U\.S\..{0,600}?Comp sales\s+\(ex\.\s*fuel\)[^%\d]{0,40}([\d.]+)\s*%",
                r"U\.S\.[^%]{0,500}?Comp sales \(ex\. fuel\)\s+([\d.]+)\s*%",
                r"U\.S\.[^%]{0,500}?Comp sales\s+\(ex\.\s*fuel\)\s+([\d.]+)\s*%",
                r"Walmart U\.S\.[^%]{0,300}?comparable sales grew\s+([\d.]+)\s*%",
                r"Walmart U\.S\.[^%]{0,300}?comp sales grew\s+([\d.]+)\s*%",
            ):
                legacy_match = re.search(table_pattern, norm, re.I | re.S)
                if legacy_match:
                    metrics["comparable_sales_growth_pct"] = Decimal(
                        legacy_match.group(1)
                    )
                    break

        if metrics.get("comparable_sales_growth_pct") is None:
            rose_match = re.search(
                r"Walmart U\.S\. comparable store sales (?:for[^,]+)?\s*rose\s+"
                r"([\d.]+)\s*percent",
                norm,
                re.I,
            )
            if rose_match:
                value = _parse_decimal(rose_match.group(1))
                if value is not None:
                    metrics["comparable_sales_growth_pct"] = value

        if metrics.get("comparable_sales_growth_pct") is None:
            declined_match = re.search(
                r"Walmart U\.S\. comparable store sales (?:for[^,]+)?\s*declined\s+"
                r"([\d.]+)\s*percent",
                norm,
                re.I,
            )
            if declined_match:
                value = _parse_decimal(declined_match.group(1))
                if value is not None:
                    metrics["comparable_sales_growth_pct"] = -value

        if metrics.get("comparable_sales_growth_pct") is None:
            inc_dec_match = re.search(
                r"Walmart U\.S\. comparable store sales (?:for[^,]+)?\s*"
                r"(increased|decreased)\s+([\d.]+)\s*percent",
                norm,
                re.I,
            )
            if inc_dec_match:
                verb = inc_dec_match.group(1).lower()
                value = _parse_decimal(inc_dec_match.group(2))
                if value is not None:
                    if verb == "decreased":
                        value = -value
                    metrics["comparable_sales_growth_pct"] = value

        if metrics.get("comparable_sales_growth_pct") is None:
            dex_table = re.search(
                r"(?:Thirteen Weeks Ended|13-[Ww]eek|comparable store sales).{0,2000}?"
                r"Walmart U\.S\.\s+([-]?[\d.]+)\s*%",
                norm,
                re.I | re.S,
            )
            if dex_table:
                raw = dex_table.group(1).strip()
                value = _parse_decimal(raw.lstrip("-"))
                if value is not None:
                    if raw.startswith("-"):
                        value = -value
                    metrics["comparable_sales_growth_pct"] = value

    sams_comp = re.search(
        r"Sam(?:'s|&#8217;s) Club U\.S\..{0,300}?Without Fuel[^\d]{0,40}([\d.]+)\s*%",
        text,
        re.I | re.S,
    )
    if sams_comp:
        metrics["sams_club_comp_sales_ex_fuel_pct"] = Decimal(sams_comp.group(1))

    digital = re.search(
        r"(?:Global )?eCommerce(?: sales)?(?: grew| up| increased)?\s*([\d.]+)\s*%",
        text,
        re.I,
    )
    if digital:
        metrics["digital_comp_sales_pct"] = Decimal(digital.group(1))

    gm_rate = re.search(
        r"gross margin rate\s*(?:was|of)?\s*([\d.]+)\s*(?:%|percent)",
        text,
        re.I,
    )
    if gm_rate:
        metrics["gross_margin_pct"] = Decimal(gm_rate.group(1))

    gm_change = re.search(
        r"gross margin rate\s*(?:up|down|increased|decreased)\s*([\d.]+)\s*bps",
        text,
        re.I,
    )
    if gm_change:
        metrics["gross_margin_change_bps"] = Decimal(gm_change.group(1))

    guidance_block = text[text.find("Guidance") : text.find("Guidance") + 2000] if "Guidance" in text else text
    if re.search(r"net sales guidance|grow net sales|sales growth", guidance_block, re.I):
        if re.search(r"decline|lower|decrease", guidance_block, re.I):
            metrics["guidance_sales_direction"] = "decline"
        elif re.search(r"flat|stable|unchanged", guidance_block, re.I):
            metrics["guidance_sales_direction"] = "flat"
        else:
            metrics["guidance_sales_direction"] = "growth"
    else:
        metrics["guidance_sales_direction"] = "not_provided"

    range_match = re.search(
        r"([\d.]+)\s*(?:%|percent)\s*(?:to|-)\s*([\d.]+)\s*(?:%|percent)",
        guidance_block,
        re.I,
    )
    if range_match:
        metrics["guidance_sales_range_low"] = Decimal(range_match.group(1))
        metrics["guidance_sales_range_high"] = Decimal(range_match.group(2))
    else:
        single_range = re.search(
            r"(?:range around|growth of|grow)\s*([\d.]+)\s*(?:%|percent)",
            guidance_block,
            re.I,
        )
        if single_range:
            midpoint = Decimal(single_range.group(1))
            metrics["guidance_sales_range_low"] = midpoint
            metrics["guidance_sales_range_high"] = midpoint

    eps_match = re.search(
        r"(?:GAAP|Adjusted)?\s*EPS.*?\$\s*([\d.]+)\s*(?:to|-)\s*\$\s*([\d.]+)",
        guidance_block,
        re.I,
    )
    if eps_match:
        metrics["guidance_eps_low"] = _parse_decimal(eps_match.group(1))
        metrics["guidance_eps_high"] = _parse_decimal(eps_match.group(2))

    metrics.update(_extract_walmart_store_count_metrics(text))

    wmt_plus = re.search(
        r"Walmart\+.{0,60}?([\d.]+)\s*%",
        text,
        re.I | re.S,
    )
    if wmt_plus:
        metrics["walmart_plus_membership_growth_pct"] = Decimal(wmt_plus.group(1))

    wmt_plus_count = re.search(
        r"Walmart\+.{0,100}?(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*(?:million\s+)?members",
        text,
        re.I | re.S,
    )
    if wmt_plus_count:
        raw_count = _parse_decimal(wmt_plus_count.group(1))
        if raw_count is not None:
            if "million" in wmt_plus_count.group(0).lower():
                raw_count *= Decimal("1000000")
            metrics["walmart_plus_member_count"] = int(raw_count)

    txn_match = re.search(
        r"transaction(?:s)?(?: count)?[^\d]{0,60}"
        r"(?:up|grew|increased|growth of|rose)\s*([\d.]+)\s*%",
        text,
        re.I,
    )
    if txn_match:
        metrics["transaction_count_growth_pct"] = Decimal(txn_match.group(1))

    atv_match = re.search(
        r"(?:average ticket|average transaction|basket size|ATV)"
        r"[^\d]{0,60}(?:up|down|grew|increased|decreased|changed)\s*([\d.]+)\s*%",
        text,
        re.I,
    )
    if atv_match:
        metrics["average_transaction_value_change_pct"] = Decimal(atv_match.group(1))

    sams_members = re.search(
        r"Sam(?:'s|&#8217;s) Club[^\d]{0,160}?(\d+(?:\.\d+)?)\s*million\s+members",
        text,
        re.I | re.S,
    )
    if sams_members:
        millions = Decimal(sams_members.group(1))
        metrics["sams_club_member_count"] = int(millions * Decimal("1000000"))

    membership_fee = re.search(
        r"membership fee(?: income| revenue)?[^\$]{0,60}"
        r"\$?\s*([\d,.]+)\s*(billion|million|B|M)?",
        text,
        re.I,
    )
    if membership_fee:
        fee = _parse_decimal(membership_fee.group(1))
        unit = (membership_fee.group(2) or "").lower()
        if fee is not None:
            if unit in {"billion", "b"}:
                fee *= Decimal("1000000000")
            elif unit in {"million", "m"}:
                fee *= Decimal("1000000")
            metrics["sams_club_membership_fee_revenue_usd"] = fee

    inventory_narrative = _extract_inventory_narrative(text, max_len=200)
    if inventory_narrative:
        metrics["inventory_change_narrative"] = inventory_narrative

    return metrics


def _parse_earnings_presentation(
    html: str,
    fiscal_quarter: Optional[int] = None,
) -> dict[str, Any]:
    text = _strip_html(html)
    metrics: dict[str, Any] = {}

    private_brand = re.search(
        r"private brand(?: penetration| mix)?[^\d]{0,40}(?:up|increased|grew)\s*~?\s*([\d.]+)\s*bps",
        text,
        re.I,
    )
    if private_brand:
        metrics["private_brand_mix_change_bps"] = Decimal(private_brand.group(1))

    if "private_brand_mix_change_bps" not in metrics:
        alt = re.search(
            r"private brand[^\d]{0,60}([\d.]+)\s*bps",
            text,
            re.I,
        )
        if alt:
            metrics["private_brand_mix_change_bps"] = Decimal(alt.group(1))

    inventory_language = _extract_inventory_narrative(text, max_len=500)
    if inventory_language:
        metrics["inventory_positioning_language"] = inventory_language

    comp_sales = _extract_walmart_us_comp_sales_presentation(text)
    if comp_sales is None:
        comp_sales = _extract_walmart_us_comp_sales(
            text, fiscal_quarter=fiscal_quarter
        )
    if comp_sales is not None:
        metrics["comparable_sales_growth_pct"] = comp_sales

    return metrics


def _build_walmart_supplemental_fields(
    segment_data: dict[str, Optional[Decimal]],
    prior_row: Optional[RetailerFinancials],
    release_metrics: Optional[dict[str, Any]] = None,
    presentation_metrics: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    release_metrics = release_metrics or {}
    presentation_metrics = presentation_metrics or {}

    wmt_inv = segment_data.get("walmart_us_inventory_usd")
    sams_inv = segment_data.get("sams_club_inventory_usd")
    wmt_cogs = segment_data.get("walmart_us_cogs_usd")
    sams_cogs = segment_data.get("sams_club_cogs_usd")
    wmt_seg_rev = segment_data.get("walmart_us_segment_revenue_usd")

    wmt_inv_yoy = None
    sams_inv_yoy = None
    if prior_row is not None:
        wmt_inv_yoy = _calc_yoy_pct(wmt_inv, prior_row.walmart_us_inventory_usd)
        sams_inv_yoy = _calc_yoy_pct(sams_inv, prior_row.sams_club_inventory_usd)

    wmt_inv_days = _segment_inventory_days(wmt_inv, wmt_cogs)
    sams_inv_days = _segment_inventory_days(sams_inv, sams_cogs)

    inv_to_sales = None
    if wmt_inv is not None and wmt_seg_rev is not None and wmt_seg_rev != 0:
        inv_to_sales = _safe_div(wmt_inv, wmt_seg_rev)

    txn_growth = release_metrics.get("transaction_count_growth_pct")
    atv_change = release_metrics.get("average_transaction_value_change_pct")

    sams_member_count = release_metrics.get("sams_club_member_count")
    sams_member_yoy = None
    if prior_row is not None and sams_member_count is not None:
        sams_member_yoy = _calc_yoy_pct(
            sams_member_count, prior_row.sams_club_member_count
        )

    return {
        "walmart_us_model_note": WALMART_US_MODEL_NOTE,
        "sams_club_model_note": SAMS_CLUB_MODEL_NOTE,
        "walmart_us_inventory_usd": wmt_inv,
        "sams_club_inventory_usd": sams_inv,
        "walmart_international_inventory_usd": segment_data.get(
            "walmart_international_inventory_usd"
        ),
        "walmart_us_inventory_yoy_change_pct": wmt_inv_yoy,
        "sams_club_inventory_yoy_change_pct": sams_inv_yoy,
        "walmart_us_inventory_days": wmt_inv_days,
        "sams_club_inventory_days": sams_inv_days,
        "walmart_us_inventory_to_sales_ratio": inv_to_sales,
        "general_merch_inventory_proxy_signal": _inventory_proxy_signal(
            wmt_inv_days, "walmart_us"
        ),
        "inventory_positioning_language": presentation_metrics.get(
            "inventory_positioning_language"
        ),
        "inventory_change_narrative": release_metrics.get("inventory_change_narrative"),
        "transaction_count_growth_pct": txn_growth,
        "average_transaction_value_change_pct": atv_change,
        "ticket_vs_traffic_split": _derive_ticket_vs_traffic_split(txn_growth, atv_change),
        "walmart_plus_member_count": release_metrics.get("walmart_plus_member_count"),
        "sams_club_member_count": sams_member_count,
        "sams_club_membership_fee_revenue_usd": release_metrics.get(
            "sams_club_membership_fee_revenue_usd"
        ),
        "sams_club_member_count_yoy_pct": sams_member_yoy,
    }


def _get_walmart_retailer_id(db: Session) -> Optional[int]:
    retailer = (
        db.query(MajorRetailers).filter(MajorRetailers.name == WALMART_NAME).first()
    )
    if retailer is None:
        logger.error("Walmart Inc not found in major_retailers")
        return None
    return retailer.retailer_id


def _validate_walmart_payload(
    ctx: IngestionContext,
    payload: dict[str, Any],
    prior_row: Optional[RetailerFinancials],
) -> None:
    if payload.get("total_net_sales_usd") is not None:
        payload["total_net_sales_usd"] = validate_and_log(
            payload["total_net_sales_usd"],
            lambda v: validate_retailer_revenue(v, WALMART_NAME),
            ctx,
        )

    if payload.get("gross_margin_pct") is not None:
        payload["gross_margin_pct"] = validate_and_log(
            payload["gross_margin_pct"],
            lambda v: validate_gross_margin(v, WALMART_NAME),
            ctx,
        )

    if payload.get("walmart_us_general_merch_usd") is not None:
        payload["walmart_us_general_merch_usd"] = validate_and_log(
            payload["walmart_us_general_merch_usd"],
            validate_walmart_general_merch,
            ctx,
        )

    if payload.get("sams_club_home_apparel_usd") is not None:
        payload["sams_club_home_apparel_usd"] = validate_and_log(
            payload["sams_club_home_apparel_usd"],
            validate_sams_club_apparel,
            ctx,
        )


def _append_retailer_financials(
    db: Session,
    ctx: IngestionContext,
    retailer_id: int,
    payload: dict[str, Any],
) -> RetailerFinancials:
    missing_fields = [
        field for field in RETAILER_FINANCIALS_UPDATE_FIELDS if field not in payload
    ]
    if missing_fields:
        raise ValueError(
            f"Append payload missing required fields: {', '.join(missing_fields)}"
        )

    if not assert_fk_exists(db, MajorRetailers, "retailer_id", retailer_id):
        ctx.rejected(f"retailer_id {retailer_id} not found in major_retailers")
        raise ValueError(f"Invalid retailer_id: {retailer_id}")

    mark_latest(
        db,
        RetailerFinancials,
        {
            "retailer_id": retailer_id,
            "fiscal_year": payload["fiscal_year"],
            "fiscal_quarter": payload["fiscal_quarter"],
        },
    )

    data_source_url = (
        payload.get("source_8k_url")
        or payload.get("source_10q_url")
        or _COMPANYFACTS_URL
    )
    row = RetailerFinancials(
        retailer_id=retailer_id,
        source=SOURCE_SYSTEM,
        data_source_url=data_source_url,
        pulled_at=datetime.now(timezone.utc),
        is_latest=True,
        manually_verified=False,
    )
    for field in RETAILER_FINANCIALS_UPDATE_FIELDS:
        setattr(row, field, payload[field])

    db.add(row)
    db.flush()
    ctx.inserted()
    return row


def run_walmart_tier1_ingestion(
    db: Session,
    ctx: IngestionContext,
) -> list[dict[str, Any]]:
    retailer_id = _get_walmart_retailer_id(db)
    if retailer_id is None:
        return []

    companyfacts = _sec_get(_COMPANYFACTS_URL)
    submissions = _sec_get(_SUBMISSIONS_URL)
    if not isinstance(companyfacts, dict):
        logger.error("Failed to fetch Walmart company facts")
        return []
    if not isinstance(submissions, dict):
        logger.warning("Failed to fetch Walmart submissions — 8-K URLs may be missing")
        submissions = {}

    filing_rows = _load_all_submission_filings(submissions) if submissions else []
    if filing_rows:
        logger.info("Loaded %d SEC filing row(s) for historical 8-K lookup", len(filing_rows))

    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        logger.error("No us-gaap facts for Walmart")
        return []

    (
        meta,
        revenue,
        cogs,
        gross_profit,
        sga,
        operating,
        inventory,
        store_count_xbrl,
    ) = _extract_fiscal_quarter_maps(us_gaap)

    quarter_keys = _select_output_fiscal_keys(meta)
    if not quarter_keys:
        logger.error("No fiscal quarters found in Walmart XBRL data")
        return []

    ordered_keys = sorted(
        meta.keys(),
        key=lambda k: meta[k]["period_end_date"],
    )

    submissions = submissions if isinstance(submissions, dict) else {}

    summaries: list[dict[str, Any]] = []

    for key in reversed(quarter_keys):
        qmeta = meta[key]
        period_end: date = qmeta["period_end_date"]
        fiscal_year = qmeta["fiscal_year"]
        fiscal_quarter = qmeta["fiscal_quarter"]

        total_sales = revenue.get(key)
        gp = gross_profit.get(key)
        cogs_q = cogs.get(key)
        sga_q = sga.get(key)
        op_q = operating.get(key)
        inv = inventory.get(key)

        gross_margin_pct = None
        if gp is not None and total_sales is not None:
            gross_margin_pct = _safe_div(gp, total_sales)
            if gross_margin_pct is not None:
                gross_margin_pct *= Decimal("100")

        prior_key = _prior_year_key(key)
        prior_gp = gross_profit.get(prior_key)
        prior_sales = revenue.get(prior_key)
        gross_margin_change_bps = None
        if gross_margin_pct is not None and prior_gp is not None and prior_sales:
            prior_margin = _safe_div(prior_gp, prior_sales)
            if prior_margin is not None:
                gross_margin_change_bps = (
                    gross_margin_pct - prior_margin * Decimal("100")
                ) * Decimal("100")

        sga_rate_pct = None
        if sga_q is not None and total_sales is not None:
            rate = _safe_div(sga_q, total_sales)
            if rate is not None:
                sga_rate_pct = rate * Decimal("100")

        operating_margin_pct = None
        if op_q is not None and total_sales is not None:
            rate = _safe_div(op_q, total_sales)
            if rate is not None:
                operating_margin_pct = rate * Decimal("100")

        cogs_annual = _trailing_four_quarter_cogs(key, cogs, ordered_keys)
        inventory_days = None
        if inv is not None and cogs_annual is not None and cogs_annual > 0:
            inventory_days = _safe_div(inv, cogs_annual / Decimal("365"))

        source_10q_url: Optional[str] = None
        disagg: dict[str, Optional[Decimal]] = {}
        segment_data: dict[str, Optional[Decimal]] = {}
        source_8k_url: Optional[str] = None
        source_8k_presentation_url: Optional[str] = None
        release_metrics: dict[str, Any] = {}
        presentation_metrics: dict[str, Any] = {}
        annual_store_metrics: dict[str, Any] = {}

        if submissions:
            (
                release_metrics,
                presentation_metrics,
                source_8k_url,
                source_8k_presentation_url,
            ) = _fetch_quarter_earnings_metrics(
                submissions,
                period_end,
                fiscal_quarter=fiscal_quarter,
                filing_rows=filing_rows,
            )
            if release_metrics.get("comparable_sales_growth_pct") is not None:
                logger.info(
                    "Parsed Walmart U.S. comp sales %.2f%% for FY%s Q%s from 8-K",
                    release_metrics["comparable_sales_growth_pct"],
                    fiscal_year,
                    fiscal_quarter,
                )

        accession = qmeta.get("accession")
        if accession:
            index_payload = _fetch_filing_index(accession)
            if index_payload:
                primary_doc = _find_primary_htm(index_payload, period_end)
                if primary_doc:
                    source_10q_url = _filing_doc_url(accession, primary_doc)
                    html = _fetch_filing_html(accession, primary_doc)
                    if html:
                        disagg = _parse_walmart_disaggregation(html, period_end)
                        segment_data = _parse_walmart_10q_segment_data(html, period_end)
                        if fiscal_quarter == 4:
                            annual_store_metrics = _extract_walmart_store_count_metrics(
                                _strip_html(html),
                                is_annual=True,
                            )

        prior_row = _prior_year_financials_row(
            db, retailer_id, fiscal_year, fiscal_quarter
        )

        general_merch = disagg.get("walmart_us_general_merch_usd")
        ecommerce = disagg.get("walmart_us_ecommerce_usd")
        sams_apparel = disagg.get("sams_club_home_apparel_usd")
        sams_total = disagg.get("sams_club_total_usd")

        general_merch_pct = None
        if general_merch is not None and total_sales:
            rate = _safe_div(general_merch, total_sales)
            if rate is not None:
                general_merch_pct = rate * Decimal("100")

        ecommerce_pct = None
        if ecommerce is not None and total_sales:
            rate = _safe_div(ecommerce, total_sales)
            if rate is not None:
                ecommerce_pct = rate * Decimal("100")

        sams_apparel_pct = None
        if sams_apparel is not None and sams_total:
            rate = _safe_div(sams_apparel, sams_total)
            if rate is not None:
                sams_apparel_pct = rate * Decimal("100")

        general_merch_yoy = None
        ecommerce_yoy = None
        sams_apparel_yoy = None
        if prior_row is not None:
            general_merch_yoy = _calc_yoy_pct(
                general_merch, prior_row.walmart_us_general_merch_usd
            )
            ecommerce_yoy = _calc_yoy_pct(
                ecommerce, prior_row.walmart_us_ecommerce_usd
            )
            sams_apparel_yoy = _calc_yoy_pct(
                sams_apparel, prior_row.sams_club_home_apparel_usd
            )

        payload: dict[str, Any] = {
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
            "period_end_date": period_end,
            "filing_date": qmeta.get("filing_date"),
            "apparel_revenue_usd": general_merch,
            "apparel_revenue_pct_total": general_merch_pct,
            "apparel_yoy_growth_pct": general_merch_yoy,
            "total_net_sales_usd": total_sales,
            "comparable_sales_growth_pct": None,
            "digital_comp_sales_pct": None,
            "gross_margin_pct": gross_margin_pct,
            "gross_margin_change_bps": gross_margin_change_bps,
            "sga_rate_pct": sga_rate_pct,
            "operating_margin_pct": operating_margin_pct,
            "inventory_usd": inv,
            "inventory_days": inventory_days,
            "store_count_total": None,
            "store_count_net_change": None,
            "ecommerce_penetration_pct": ecommerce_pct,
            "guidance_sales_direction": None,
            "guidance_sales_range_low": None,
            "guidance_sales_range_high": None,
            "guidance_eps_low": None,
            "guidance_eps_high": None,
            "source_10q_url": source_10q_url,
            "source_8k_url": source_8k_url,
            "source_8k_presentation_url": source_8k_presentation_url,
            "walmart_us_general_merch_usd": general_merch,
            "walmart_us_general_merch_pct": general_merch_pct,
            "walmart_us_general_merch_yoy_pct": general_merch_yoy,
            "walmart_us_ecommerce_usd": ecommerce,
            "walmart_us_ecommerce_pct_of_total": ecommerce_pct,
            "walmart_us_ecommerce_yoy_growth_pct": ecommerce_yoy,
            "sams_club_home_apparel_usd": sams_apparel,
            "sams_club_home_apparel_pct": sams_apparel_pct,
            "sams_club_home_apparel_yoy_pct": sams_apparel_yoy,
            "sams_club_total_usd": sams_total,
            "sams_club_ecommerce_usd": disagg.get("sams_club_ecommerce_usd"),
            "sams_club_comp_sales_ex_fuel_pct": None,
            "walmart_us_store_count": None,
            "sams_club_count": None,
            "walmart_plus_membership_growth_pct": None,
            "private_brand_mix_change_bps": None,
            "xbrl_extracted": total_sales is not None,
        }
        payload.update(
            _build_walmart_supplemental_fields(
                segment_data,
                prior_row,
                release_metrics,
                presentation_metrics,
            )
        )
        _merge_quarter_earnings_metrics(
            payload, release_metrics, presentation_metrics, prior_row
        )
        _apply_walmart_store_count_fields(
            payload, key, store_count_xbrl, annual_store_metrics
        )
        _validate_walmart_payload(ctx, payload, prior_row)
        _append_retailer_financials(db, ctx, retailer_id, payload)

        summaries.append(
            {
                "label": f"FY{fiscal_year} Q{fiscal_quarter}",
                "total_net_sales": total_sales,
                "general_merch": general_merch,
                "general_merch_pct": general_merch_pct,
                "sams_apparel": sams_apparel,
                "ecommerce": ecommerce,
                "comp_sales": payload["comparable_sales_growth_pct"],
                "gross_margin": payload["gross_margin_pct"],
            }
        )

    logger.info("Appended %d Walmart quarter(s) to retailer_financials", len(summaries))
    return summaries


def _format_billions(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    return f"${value / Decimal('1000000000'):.1f}B"


def _format_pct(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def print_summary(summaries: list[dict[str, Any]]) -> None:
    header = (
        f"{'Quarter':<12} | {'Net Sales':<12} | {'Gen Merch':<12} | "
        f"{'Gen Merch %':<11} | {'Sam Apparel':<12} | {'eComm $':<12} | "
        f"{'Comp Sales':<10} | {'GM%'}"
    )
    print(header)
    print("-" * len(header))
    for row in summaries:
        print(
            f"{row['label']:<12} | "
            f"{_format_billions(row['total_net_sales']):<12} | "
            f"{_format_billions(row['general_merch']):<12} | "
            f"{_format_pct(row['general_merch_pct']):<11} | "
            f"{_format_billions(row['sams_apparel']):<12} | "
            f"{_format_billions(row['ecommerce']):<12} | "
            f"{_format_pct(row['comp_sales']):<10} | "
            f"{_format_pct(row['gross_margin'])}"
        )


def main() -> int:
    db = SessionLocal()
    try:
        logger.info("Starting Walmart Tier 1 SEC EDGAR ingestion")
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=_COMPANYFACTS_URL,
            db=db,
        ) as ctx:
            summaries = run_walmart_tier1_ingestion(db, ctx)
            if not summaries:
                logger.error("No Walmart retailer_financials rows written")
                ctx.set_failed("No quarters written")
                return 1
            print_summary(summaries)
            logger.info(
                "Walmart Tier 1 ingestion complete — %d quarter(s)",
                len(summaries),
            )
            return 0
    except Exception as exc:
        logger.exception("Walmart Tier 1 ingestion failed: %s", exc)
        try:
            with IngestionContext(
                source_name=SOURCE_NAME,
                script_version=SCRIPT_VERSION,
                data_source_url=_COMPANYFACTS_URL,
                db=db,
            ) as fail_ctx:
                fail_ctx.set_failed(exc)
        except Exception:
            logger.exception("Failed to write ingestion_log for Walmart Tier 1 failure")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

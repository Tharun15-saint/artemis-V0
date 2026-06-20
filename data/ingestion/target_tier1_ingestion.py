"""
Target Corporation Tier 1 ingestion — structured quarterly financials from SEC EDGAR.
Appends to retailer_financials (append-only via mark_latest + is_latest).
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
from database.models.retail import MajorRetailers, RetailerFinancials
from database.validation.ingestion_validators import (
    validate_and_log,
    validate_gross_margin,
    validate_retailer_revenue,
)

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TARGET_NAME = "Target Corporation"
SCRIPT_VERSION = "target-tier1-v2.0"
SOURCE_NAME = "target_sec_edgar_tier1"
SOURCE_SYSTEM = "target_sec_edgar"
TARGET_CIK = "0000027419"
CIK_NUM = "27419"
SEC_USER_AGENT = "Artemis/1.0 supply-chain-intelligence@artemis.com"
SEC_RATE_LIMIT_SECONDS = 0.1
REQUEST_TIMEOUT = 30
QUARTER_FETCH_COUNT = 5
QUARTER_OUTPUT_COUNT = 4
MILLIONS = Decimal("1000000")

# Net-sales-first, MERCHANDISE-first — must mirror the SEC reconciliation gate's TGT chain
# (retail_financials_reconcile.RETAILER_PROFILES["TGT"]["revenue"]). Target's demand figure is
# merchandise SalesRevenueGoodsNet; the ASC 606 RevenueFromContract... (total revenue) is only a
# late fallback, used for the post-2018 quarters where the merchandise concepts no longer appear.
# Keeping this identical to the gate means what we ingest is what the gate verifies, by construction.
REVENUE_CONCEPTS = [
    "SalesRevenueGoodsNet",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
]
COGS_CONCEPTS = [
    "CostOfGoodsAndServicesSold",
    "CostOfRevenue",
    "CostOfGoodsSold",
]
GROSS_PROFIT_CONCEPTS = ["GrossProfit"]
SGA_CONCEPTS = ["SellingGeneralAndAdministrativeExpense"]
OPERATING_INCOME_CONCEPTS = ["OperatingIncomeLoss"]
INVENTORY_CONCEPTS = ["InventoryNet"]
STORE_COUNT_CONCEPTS = ["NumberOfStores", "NumberOfOperatedStores"]

_COMPANYFACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{TARGET_CIK}.json"
_SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{TARGET_CIK}.json"
_SUBMISSIONS_FILE_BASE = "https://data.sec.gov/submissions/"

EARNINGS_8K_EXHIBIT_PATTERNS = [
    "ex-99.1",
    "ex99.1",
    "ex-99",
    "ex99",
]

_QUARTER_LABELS = {
    1: "first quarter",
    2: "second quarter",
    3: "third quarter",
    4: "fourth quarter",
}
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
    r'<ix:nonFraction([^>]*)>([^<]+)</ix:nonFraction>',
    re.S,
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
        .replace("&#8226;", " ")
        .replace("&#8212;", "—")
        .replace("&#8211;", "–")
    )
    return _WHITESPACE_RE.sub(" ", plain).strip()


def _first_count_after_label(text: str, label: str) -> Optional[int]:
    """Return the first integer count immediately following a table row label."""
    pattern = rf"{label}\s*((?:\([\d,]+\)|[\d,]+|—|–|-|\s)+)"
    match = re.search(pattern, text, re.I)
    if not match:
        return None
    for token in re.finditer(r"\(?([\d,]+)\)?", match.group(1)):
        value = _parse_decimal(token.group(1))
        if value is not None:
            return int(value)
    return None


def _extract_target_store_count_metrics(text: str) -> dict[str, Any]:
    """
    Parse Target store count from 10-Q/10-K activity tables or 8-K
    Number of Stores and Retail Square Feet summary tables.
    """
    metrics: dict[str, Any] = {}

    ending = _first_count_after_label(text, "Ending store count")
    if ending is None:
        total_match = re.search(
            r"Number of Stores and Retail Square Feet.*?Total\s+([\d,]+)",
            text,
            re.I | re.S,
        )
        if total_match:
            ending_value = _parse_decimal(total_match.group(1))
            if ending_value is not None:
                ending = int(ending_value)

    if ending is not None:
        metrics["store_count_total"] = ending

    beginning = _first_count_after_label(text, "Beginning store count")
    if beginning is not None and ending is not None:
        metrics["store_count_net_change"] = ending - beginning
        return metrics

    opened = _first_count_after_label(text, "Opened")
    closed_match = re.search(
        r"Closed\s*((?:\([\d,]+\)|[\d,]+|—|–|-|\s)+)",
        text,
        re.I,
    )
    closed: Optional[int] = None
    if closed_match:
        for token in re.finditer(r"\(?([\d,]+)\)?", closed_match.group(1)):
            value = _parse_decimal(token.group(1))
            if value is not None:
                closed = int(value)
                break
    if opened is not None and closed is not None:
        metrics["store_count_net_change"] = opened - closed
    elif opened is not None:
        metrics["store_count_net_change"] = opened

    if metrics.get("store_count_total") is None:
        for pattern in (
            r"serves guests at ([\d,]+) stores",
            r"at ([\d,]+) stores across the United States",
        ):
            boilerplate_match = re.search(pattern, text, re.I)
            if boilerplate_match:
                count = _parse_decimal(boilerplate_match.group(1))
                if count is not None:
                    metrics["store_count_total"] = int(count)
                    break

    return metrics


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


def _parse_quarter_frame(frame: str) -> Optional[tuple[int, int]]:
    match = _QUARTER_FRAME_RE.match(frame.rstrip("I"))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _fiscal_key(fy: int, fp: str) -> tuple[int, int]:
    return fy, _fp_to_int(fp) or 0


def _fiscal_quarter_from_end(end_date: date) -> Optional[tuple[int, int]]:
    """Derive (fiscal_year, fiscal_quarter) from a period-END date for Target's 4-4-5 calendar
    (fiscal-year-end the Saturday nearest Jan 31, so quarter-ends drift across late-Jan/early-
    Feb, late-Apr/early-May, etc.) — DETERMINISTICALLY, never SEC's `fy`/`fp` tags.

    SEC's tags drift: a prior-year comparative is re-tagged with the filer's current fy, and the
    fiscal-year-focus lagged a year in some eras — e.g. SEC tags the 2022-10-29 quarter fy=2023,
    colliding it with the real FY2023 Q3 and silently dropping FY2022 Q3. Anchoring on the end
    date is collision-free and matches Target's own labelling for every era.

    Target names a fiscal year for the calendar year it mostly spans: FY_N runs ~Feb_N …
    Jan/early-Feb_{N+1} (the year ending Feb 3 2018 is Target 'fiscal 2017'). Quarter by end
    month: Mar-May=Q1, Jun-Aug=Q2, Sep-Nov=Q3, Dec-Feb=Q4; fy = end.year for Q1-Q3 (and a Dec
    Q4), else end.year-1 for a Jan/Feb Q4."""
    m = end_date.month
    if m in (3, 4, 5):
        return end_date.year, 1
    if m in (6, 7, 8):
        return end_date.year, 2
    if m in (9, 10, 11):
        return end_date.year, 3
    if m == 12:
        return end_date.year, 4
    if m in (1, 2):
        return end_date.year - 1, 4
    return None


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
                    entry = dict(entry)
                    entry["concept"] = concept_name
                    entries.append(entry)
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
    dict[tuple[int, int], Decimal],
]:
    """Build fiscal-quarter keyed maps from XBRL company facts."""

    def duration_map(concepts: list[str]) -> dict[tuple[int, int], Decimal]:
        # Key by (fiscal_year, fiscal_quarter) derived from the period-END date, never SEC's
        # drifting `fy`/`fp` tags. Only a discrete ~quarter duration (80-100 days, incl. a
        # 14-week quarter in 53-week years) qualifies, so a YTD cumulative can't masquerade as a
        # quarter. Value selection MIRRORS the reconciliation gate's duration_facts_by_end
        # EXACTLY — concept priority (list order) wins, then the later-filed value supersedes a
        # restatement (no `has_frame` preference, which would pick a re-presented variant) — so
        # what we ingest is what the SEC-truth gate verifies, by construction.
        out: dict[tuple[int, int], Decimal] = {}
        chosen: dict[tuple[int, int], tuple[int, str]] = {}
        for rank, concept in enumerate(concepts):
            node = us_gaap.get(concept)
            if not node:
                continue
            for unit_vals in node.get("units", {}).values():
                if not isinstance(unit_vals, list):
                    continue
                for fact in unit_vals:
                    start, end, val = fact.get("start"), fact.get("end"), fact.get("val")
                    if not start or not end or val is None:
                        continue
                    end_date = date.fromisoformat(str(end))
                    if not (80 <= (end_date - date.fromisoformat(str(start))).days <= 100):
                        continue
                    key = _fiscal_quarter_from_end(end_date)
                    if key is None:
                        continue
                    filed = str(fact.get("filed") or "")
                    prior = chosen.get(key)
                    if prior is None or rank < prior[0] or (rank == prior[0] and filed > prior[1]):
                        out[key] = Decimal(str(val))
                        chosen[key] = (rank, filed)
        return out

    def instant_map(concepts: list[str]) -> dict[tuple[int, int], Decimal]:
        # Key balance-sheet instants by (fiscal_year, fiscal_quarter) derived from the instant's
        # OWN date (_fiscal_quarter_from_end) — never the calendar XBRL frame (which shifts a
        # year for retailers whose fiscal year is offset from the calendar) and never SEC's
        # `fy`/`fp` tags. On a restatement (same date, refiled), the later-filed value wins.
        result: dict[tuple[int, int], Decimal] = {}
        chosen_filed: dict[tuple[int, int], str] = {}
        for entry in _collect_us_gaap_entries(us_gaap, concepts):
            end = entry.get("end")
            if not end or entry.get("val") is None:
                continue
            key = _fiscal_quarter_from_end(date.fromisoformat(str(end)))
            if key is None:
                continue
            filed = str(entry.get("filed") or "")
            if key not in result or filed > chosen_filed.get(key, ""):
                result[key] = Decimal(str(entry["val"]))
                chosen_filed[key] = filed
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
        end = entry.get("end")
        start = entry.get("start")
        if not end or not start:
            continue
        end_date = date.fromisoformat(str(end))
        duration_days = (end_date - date.fromisoformat(str(start))).days
        if not (80 <= duration_days <= 100):           # discrete quarter only
            continue
        key = _fiscal_quarter_from_end(end_date)
        if key is None or key not in revenue:
            continue
        frame = entry.get("frame") or ""
        has_frame = bool(frame and _parse_quarter_frame(frame))
        record = {
            "fiscal_year": key[0],
            "fiscal_quarter": key[1],
            "period_end_date": end_date,
            "filing_date": (
                date.fromisoformat(str(entry["filed"])) if entry.get("filed") else None
            ),
            "accession": entry.get("accn"),
            "duration_days": duration_days,
            "has_frame": has_frame,
        }
        current = meta.get(key)
        if current is None:
            meta[key] = record
            continue
        # all entries for a key share one end date; prefer a framed quarterly tag, then the
        # shortest duration.
        if has_frame and not current["has_frame"]:
            replace = True
        elif current["has_frame"] and not has_frame:
            replace = False
        else:
            replace = duration_days < current["duration_days"]
        if replace:
            meta[key] = record

    for key in list(revenue.keys()):
        if key not in meta:
            logger.warning("Missing revenue metadata for fiscal key %s", key)

    for key, rev in revenue.items():
        if key in gross_profit:
            continue
        if key in cogs:
            gross_profit[key] = rev - cogs[key]

    _synthesize_fiscal_q4_from_10k(
        us_gaap,
        meta,
        revenue,
        cogs,
        gross_profit,
        sga,
        operating,
    )

    return meta, revenue, cogs, gross_profit, sga, operating, inventory, store_count


def _fy_annual_10k_entry(
    us_gaap: dict[str, Any],
    concepts: list[str],
    fiscal_year: int,
    period_end: date,
) -> Optional[dict[str, Any]]:
    for entry in _collect_us_gaap_entries(us_gaap, concepts):
        if entry.get("fp") != "FY" or entry.get("form") != "10-K":
            continue
        if entry.get("fy") != fiscal_year or entry.get("val") is None:
            continue
        if not entry.get("end") or date.fromisoformat(str(entry["end"])) != period_end:
            continue
        if not entry.get("start"):
            continue
        start_date = date.fromisoformat(str(entry["start"]))
        if (period_end - start_date).days < 300:
            continue
        return entry
    return None


def _synthesize_fiscal_q4_from_10k(
    us_gaap: dict[str, Any],
    meta: dict[tuple[int, int], dict[str, Any]],
    revenue: dict[tuple[int, int], Decimal],
    cogs: dict[tuple[int, int], Decimal],
    gross_profit: dict[tuple[int, int], Decimal],
    sga: dict[tuple[int, int], Decimal],
    operating: dict[tuple[int, int], Decimal],
) -> None:
    """
    Target tags fiscal Q4 in 10-K filings as fp=FY annual.
    Derive single-quarter Q4 as FY annual minus Q1+Q2+Q3.
    """
    fy_entries_by_year: dict[int, dict[str, Any]] = {}
    for entry in _collect_us_gaap_entries(us_gaap, REVENUE_CONCEPTS):
        if entry.get("fp") != "FY" or entry.get("form") != "10-K":
            continue
        if entry.get("val") is None or not entry.get("end") or not entry.get("start"):
            continue
        end_date = date.fromisoformat(str(entry["end"]))
        start_date = date.fromisoformat(str(entry["start"]))
        if end_date.month not in (1, 2) or (end_date - start_date).days < 300:
            continue
        fiscal_year = int(entry["fy"])
        current = fy_entries_by_year.get(fiscal_year)
        if current is None or end_date > date.fromisoformat(str(current["end"])):
            fy_entries_by_year[fiscal_year] = entry

    if not fy_entries_by_year:
        logger.warning("No 10-K FY revenue entry found — cannot synthesize fiscal Q4")
        return

    for fiscal_year, fy_revenue_entry in fy_entries_by_year.items():
        period_end = date.fromisoformat(str(fy_revenue_entry["end"]))
        q4_key = (fiscal_year, 4)
        if q4_key in revenue:
            continue

        prior_quarters = [(fiscal_year, 1), (fiscal_year, 2), (fiscal_year, 3)]
        if not all(key in revenue for key in prior_quarters):
            logger.warning(
                "Cannot synthesize FY%s Q4 — missing one or more of Q1-Q3 in XBRL",
                fiscal_year,
            )
            continue

        fy_revenue = Decimal(str(fy_revenue_entry["val"]))
        revenue[q4_key] = fy_revenue - sum(revenue[key] for key in prior_quarters)

        metric_specs = (
            (cogs, COGS_CONCEPTS),
            (sga, SGA_CONCEPTS),
            (operating, OPERATING_INCOME_CONCEPTS),
            (gross_profit, GROSS_PROFIT_CONCEPTS),
        )
        for metric_map, concepts in metric_specs:
            fy_entry = _fy_annual_10k_entry(us_gaap, concepts, fiscal_year, period_end)
            if fy_entry is None:
                if metric_map is gross_profit and q4_key in revenue and q4_key in cogs:
                    metric_map[q4_key] = revenue[q4_key] - cogs[q4_key]
                continue
            fy_val = Decimal(str(fy_entry["val"]))
            if all(key in metric_map for key in prior_quarters):
                metric_map[q4_key] = fy_val - sum(
                    metric_map[key] for key in prior_quarters
                )
            elif metric_map is gross_profit and q4_key in revenue and q4_key in cogs:
                metric_map[q4_key] = revenue[q4_key] - cogs[q4_key]

        meta[q4_key] = {
            "fiscal_year": fiscal_year,
            "fiscal_quarter": 4,
            "period_end_date": period_end,
            "filing_date": (
                date.fromisoformat(str(fy_revenue_entry["filed"]))
                if fy_revenue_entry.get("filed")
                else None
            ),
            "accession": fy_revenue_entry.get("accn"),
            "duration_days": (
                period_end - date.fromisoformat(str(fy_revenue_entry["start"]))
            ).days,
            "has_frame": bool(fy_revenue_entry.get("frame")),
            "synthesized_q4": True,
        }
        logger.info(
            "Synthesized FY%s Q4 from 10-K annual data (period_end=%s, revenue=%s)",
            fiscal_year,
            period_end,
            revenue[q4_key],
        )


def _select_output_fiscal_keys(
    meta: dict[tuple[int, int], dict[str, Any]],
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    fetched = sorted(
        meta.keys(),
        key=lambda k: meta[k]["period_end_date"],
        reverse=True,
    )[:QUARTER_FETCH_COUNT]
    output = fetched[:QUARTER_OUTPUT_COUNT]
    if len(fetched) > len(output):
        dropped_labels = [
            f"FY{meta[k]['fiscal_year']} Q{meta[k]['fiscal_quarter']}"
            for k in fetched[QUARTER_OUTPUT_COUNT:]
        ]
        logger.info(
            "Fetched %d quarters; upserting all fetched rows; summary shows %d most recent. "
            "Older fetched quarter(s): %s",
            len(fetched),
            len(output),
            ", ".join(dropped_labels),
        )
    return output, fetched


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
    if len(window) < 4:
        logger.warning(
            "Fewer than 4 COGS quarters for %s — annualizing %d quarter(s)",
            key,
            len(window),
        )
    total = Decimal("0")
    for qkey in window:
        if qkey not in cogs:
            logger.warning("Missing COGS for %s when computing inventory_days", qkey)
            return None
        total += cogs[qkey]
    if len(window) < 4:
        total = total * Decimal("4") / Decimal(str(len(window)))
    return total


def _find_primary_htm(index_payload: dict[str, Any], form_hint: str = "10-Q") -> Optional[str]:
    items = index_payload.get("directory", {}).get("item", [])
    candidates = [
        item["name"]
        for item in items
        if isinstance(item, dict)
        and item.get("name", "").endswith(".htm")
        and "exhibit" not in item["name"].lower()
        and "index" not in item["name"].lower()
        and item["name"].startswith("tgt-")
    ]
    if not candidates:
        return None
    return sorted(candidates, key=len)[0]


def _find_ex99_document(index_payload: dict[str, Any]) -> Optional[str]:
    return _find_exhibit_by_patterns(index_payload, EARNINGS_8K_EXHIBIT_PATTERNS)


def _find_exhibit_by_patterns(
    index_payload: dict[str, Any],
    patterns: list[str],
) -> Optional[str]:
    items = index_payload.get("directory", {}).get("item", [])
    candidates: list[tuple[int, str]] = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name", "")
        else:
            name = str(item)
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


def _looks_like_target_earnings_release(text: str) -> bool:
    return bool(
        re.search(
            r"comparable sales|same-store sales|same store sales|Target Corporation|Earnings",
            text,
            re.I,
        )
    )


def _find_quarter_earnings_8k(
    filing_rows: list[dict[str, Any]],
    period_end: date,
) -> Optional[dict[str, Any]]:
    window_start = period_end + timedelta(days=15)
    window_end = period_end + timedelta(days=75)
    for filing in filing_rows:
        if filing["form"] != "8-K":
            continue
        filed_date = filing["filing_date"]
        if filed_date < window_start or filed_date > window_end:
            continue
        index_payload = _fetch_filing_index(filing["accession"])
        if not index_payload:
            continue
        exhibit = _find_exhibit_by_patterns(index_payload, EARNINGS_8K_EXHIBIT_PATTERNS)
        if not exhibit:
            continue
        html = _fetch_filing_html(filing["accession"], exhibit)
        if not html:
            continue
        text = _strip_html(html)
        if not _looks_like_target_earnings_release(text):
            continue
        return {
            "accession": filing["accession"],
            "document": exhibit,
            "filing_date": filed_date,
            "url": _filing_doc_url(filing["accession"], exhibit),
            "text": text,
        }
    return None


def fetch_quarter_earnings_metrics(
    filing_rows: list[dict[str, Any]],
    period_end: date,
    fiscal_quarter: int,
) -> tuple[dict[str, Any], Optional[str]]:
    earnings_8k = _find_quarter_earnings_8k(filing_rows, period_end)
    if earnings_8k is None:
        return {}, None
    metrics = _parse_8k_guidance(earnings_8k["text"], fiscal_quarter=fiscal_quarter)
    return metrics, earnings_8k["url"]


def _row_to_financials_payload(row: RetailerFinancials) -> dict[str, Any]:
    return {field: getattr(row, field) for field in RETAILER_FINANCIALS_UPDATE_FIELDS}


def _fetch_filing_index(accession: str) -> Optional[dict[str, Any]]:
    url = _FILING_INDEX_URL.format(cik=CIK_NUM, accession=_accession_nodash(accession))
    payload = _sec_get(url)
    return payload if isinstance(payload, dict) else None


def _fetch_filing_html(accession: str, document: str) -> Optional[str]:
    url = _filing_doc_url(accession, document)
    body = _sec_get(url)
    return body if isinstance(body, str) else None


def _context_duration_days(block: str) -> Optional[int]:
    start_match = re.search(
        r"<xbrli:startDate>(\d{4}-\d{2}-\d{2})</xbrli:startDate>", block
    )
    end_match = re.search(
        r"<xbrli:endDate>(\d{4}-\d{2}-\d{2})</xbrli:endDate>", block
    )
    if not start_match or not end_match:
        return None
    start_date = date.fromisoformat(start_match.group(1))
    end_date = date.fromisoformat(end_match.group(1))
    return (end_date - start_date).days


def _parse_inline_apparel_revenue(
    html: str,
    period_end: date,
    *,
    duration_min: int = 75,
    duration_max: int = 110,
) -> Optional[Decimal]:
    """Parse apparel segment revenue; default matches single-quarter XBRL contexts."""
    apparel_contexts: set[str] = set()
    for ctx_id, block in _CONTEXT_BLOCK_RE.findall(html):
        if "ApparelAndAccessoriesMember" not in block:
            continue
        end_match = re.search(r"<xbrli:endDate>(\d{4}-\d{2}-\d{2})</xbrli:endDate>", block)
        if not end_match or date.fromisoformat(end_match.group(1)) != period_end:
            continue
        duration_days = _context_duration_days(block)
        if duration_days is None or not (duration_min <= duration_days <= duration_max):
            continue
        apparel_contexts.add(ctx_id)

    if not apparel_contexts:
        return None

    for attrs, raw_value in _IX_NONFRACTION_RE.findall(html):
        if "RevenueFromContractWithCustomerExcludingAssessedTax" not in attrs:
            continue
        ctx_match = re.search(r'contextRef="([^"]+)"', attrs)
        if not ctx_match or ctx_match.group(1) not in apparel_contexts:
            continue
        value = _parse_decimal(raw_value)
        if value is None:
            continue
        return value * MILLIONS

    text = _strip_html(html)
    pattern = (
        r"Apparel\s*&\s*accessories\s*\(a\)\s*\$\s*([\d,]+)"
        r"(?:\s*\$\s*([\d,]+))?"
    )
    match = re.search(pattern, text, re.I)
    if match:
        current = _parse_decimal(match.group(1))
        if current is not None:
            return current * MILLIONS
    return None


def _parse_inline_apparel_revenue_annual(
    html: str,
    period_end: date,
) -> Optional[Decimal]:
    """Full fiscal-year apparel segment revenue from 10-K inline XBRL."""
    return _parse_inline_apparel_revenue(
        html,
        period_end,
        duration_min=300,
        duration_max=400,
    )


def _derive_q4_apparel_revenue(
    html: str,
    period_end: date,
    fiscal_year: int,
    meta: dict[tuple[int, int], dict[str, Any]],
) -> Optional[Decimal]:
    """
    Target 10-K reports apparel at FY duration with the same end date as Q4.
    Derive Q4 apparel as FY annual minus Q1+Q2+Q3 quarterly apparel.
    """
    annual_apparel = _parse_inline_apparel_revenue_annual(html, period_end)
    if annual_apparel is None:
        return None

    prior_quarters = [(fiscal_year, 1), (fiscal_year, 2), (fiscal_year, 3)]
    prior_apparel: list[Decimal] = []
    for pq_key in prior_quarters:
        pq_meta = meta.get(pq_key)
        if not pq_meta or not pq_meta.get("accession"):
            logger.warning(
                "Cannot derive FY%s Q4 apparel — missing Q%s metadata",
                fiscal_year,
                pq_key[1],
            )
            return None
        pq_index = _fetch_filing_index(pq_meta["accession"])
        pq_doc = _find_primary_htm(pq_index) if pq_index else None
        if not pq_doc:
            logger.warning(
                "Cannot derive FY%s Q4 apparel — missing Q%s 10-Q document",
                fiscal_year,
                pq_key[1],
            )
            return None
        pq_html = _fetch_filing_html(pq_meta["accession"], pq_doc)
        if not pq_html:
            return None
        pq_apparel = _parse_inline_apparel_revenue(
            pq_html,
            pq_meta["period_end_date"],
        )
        if pq_apparel is None:
            logger.warning(
                "Cannot derive FY%s Q4 apparel — missing Q%s apparel revenue",
                fiscal_year,
                pq_key[1],
            )
            return None
        prior_apparel.append(pq_apparel)

    return annual_apparel - sum(prior_apparel, Decimal("0"))


def _normalize_earnings_text(text: str) -> str:
    plain = text.replace("\xa0", " ").replace("&#32;", " ")
    return re.sub(r"\s+", " ", plain).strip()


def _parse_signed_percent_token(raw: str) -> Optional[Decimal]:
    cleaned = raw.strip().replace(" ", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        value = _parse_decimal(cleaned[1:-1])
        return -value if value is not None else None
    return _parse_decimal(cleaned)


def _extract_target_comparable_sales_growth_pct(
    text: str,
    fiscal_quarter: Optional[int] = None,
) -> Optional[Decimal]:
    """
    Extract quarterly comparable / same-store sales growth from earnings text.
    Handles headline, narrative, and table formats across historical terminology.
    """
    normalized = _normalize_earnings_text(text)
    quarter_label = _QUARTER_LABELS.get(fiscal_quarter or 0, "")

    if quarter_label:
        quarter_patterns = [
            rf"{quarter_label}\s+comparable sales(?:\s+growth)?\s+of\s+([\d.]+)\s*(?:percent|%)",
            rf"{quarter_label}\s+comparable sales grew\s+([\d.]+)\s*(?:percent|%)",
            rf"comparable sales increased\s+([\d.]+)\s*percent in the {quarter_label}",
            rf"total comparable sales increased\s+([\d.]+)\s*percent in the {quarter_label}",
            rf"{quarter_label}[^.]{0,160}comparable sales[^.]{0,120}"
            rf"(?:increase|growth|grew|rose|up)\s+(?:of\s+)?([\d.]+)\s*(?:percent|%)",
        ]
        for pattern in quarter_patterns:
            match = re.search(pattern, normalized, re.I)
            if match:
                value = _parse_signed_percent_token(match.group(1))
                if value is not None:
                    return value

    headline_match = re.search(
        r"Comparable Sales (Increase|Decrease) of\s+([\d.]+)\s*Percent",
        normalized,
        re.I,
    )
    if headline_match:
        value = Decimal(headline_match.group(2))
        if headline_match.group(1).lower() == "decrease":
            value = -value
        return value

    table_match = re.search(
        r"Three Months Ended.*?(?:Comparable sales change|Comparable Sales Change)\s+"
        r"(\(?[\d.]+\s*\)?|\([\d.]+\))\s*%",
        normalized,
        re.I | re.S,
    )
    if table_match:
        value = _parse_signed_percent_token(table_match.group(1))
        if value is not None:
            return value

    narrative_patterns: list[tuple[str, int]] = [
        (r"comparable sales increase of\s+([\d.]+)\s*percent", 1),
        (r"comparable sales decrease of\s+([\d.]+)\s*percent", -1),
        (r"comparable sales (?:up|grew|increased|rose)\s+(?:a[^.\d]{0,60})?([\d.]+)\s*percent", 1),
        (r"comparable sales (?:decreased|declined|fell)\s+(?:a[^.\d]{0,60})?([\d.]+)\s*percent", -1),
        (r"(?<!Digital )Comparable sales grew\s+([\d.]+)\s*percent", 1),
        (r"U\.S\. comparable sales decreased\s+\(([\d.]+)\)\s*%", -1),
        (r"same-store sales (?:increased|grew|up)\s+([\d.]+)\s*percent", 1),
        (r"same-store sales (?:decreased|declined|fell)\s+([\d.]+)\s*percent", -1),
        (r"same store sales (?:increased|grew|up)\s+([\d.]+)\s*percent", 1),
        (r"same store sales (?:decreased|declined|fell)\s+([\d.]+)\s*percent", -1),
    ]
    for pattern, sign in narrative_patterns:
        for match in re.finditer(pattern, normalized, re.I):
            prefix = normalized[max(0, match.start() - 60) : match.start()]
            if re.search(
                r"full[\-\s]?year|fiscal year|nine months|six months|full-year",
                prefix,
                re.I,
            ):
                continue
            value = _parse_signed_percent_token(match.group(1))
            if value is None:
                continue
            if sign < 0 and value > 0:
                value = -value
            return value

    return None


_APPAREL_REVENUE_DOLLAR_RE = re.compile(
    r"Apparel\s*(?:&amp;|&#38;|&|and)\s*accessories"
    r"(?:\s*\([a-z]\))*"
    r"\s+\$\s*([\d,]+)",
    re.I,
)
_APPAREL_MIX_PCT_RE = re.compile(
    r"Sales by Product Category.{0,900}?"
    r"Apparel\s*(?:&amp;|&#38;|&|and)\s*accessories(?:\s*\([a-z]\))?\s+([\d]+)\s*(?:%|\s+[\d])",
    re.I | re.S,
)
_REVENUE_SECTION_HEADER_RE = re.compile(r"\bRevenues\b|\bNet Sales\b", re.I)
_BLOCKED_APPAREL_SECTION_RE = re.compile(
    r"Sales by Product Category|Percentage of Sales",
    re.I,
)
_THREE_MONTHS_ENDED_RE = re.compile(r"Three Months Ended", re.I)
_ANNUAL_REVENUES_TABLE_RE = re.compile(r"Revenues\s*\(\s*millions\s*\)", re.I)

_APPAREL_MILLIONS_MIN = Decimal("1000")
_APPAREL_MILLIONS_MAX = Decimal("10000")
_APPAREL_MIX_PCT_MIN = Decimal("0.05")
_APPAREL_MIX_PCT_MAX = Decimal("0.50")


def _apparel_millions_in_valid_range(millions: Decimal) -> bool:
    return _APPAREL_MILLIONS_MIN <= millions <= _APPAREL_MILLIONS_MAX


def _apparel_row_in_quarterly_revenue_context(text: str, match_start: int) -> bool:
    prefix = text[max(0, match_start - 2000) : match_start]
    if not _REVENUE_SECTION_HEADER_RE.search(prefix):
        return False

    markers: list[tuple[int, str]] = []
    for match in _REVENUE_SECTION_HEADER_RE.finditer(prefix):
        markers.append((match.start(), "revenue"))
    for match in _BLOCKED_APPAREL_SECTION_RE.finditer(prefix):
        markers.append((match.start(), "blocked"))
    if not markers:
        return False

    markers.sort()
    return markers[-1][1] == "revenue"


def _parse_apparel_revenue_usd_from_text(text: str) -> Optional[Decimal]:
    """Quarterly apparel revenue from explicit $ amounts in revenue tables."""
    for match in _APPAREL_REVENUE_DOLLAR_RE.finditer(text):
        if not _apparel_row_in_quarterly_revenue_context(text, match.start()):
            continue

        local_prefix = text[max(0, match.start() - 500) : match.start()]
        if not (
            _THREE_MONTHS_ENDED_RE.search(local_prefix)
            or "$" in match.group(0)
        ):
            continue

        millions = _parse_decimal(match.group(1))
        if millions is None or not _apparel_millions_in_valid_range(millions):
            continue
        return millions * MILLIONS
    return None


def _parse_annual_apparel_revenue_usd_from_text(text: str) -> Optional[Decimal]:
    """Full fiscal-year apparel revenue from 10-K Revenues (millions) table."""
    for match in _APPAREL_REVENUE_DOLLAR_RE.finditer(text):
        prefix = text[max(0, match.start() - 2000) : match.start()]
        if not _ANNUAL_REVENUES_TABLE_RE.search(prefix):
            continue
        if _THREE_MONTHS_ENDED_RE.search(text[max(0, match.start() - 500) : match.start()]):
            continue
        if _BLOCKED_APPAREL_SECTION_RE.search(
            prefix[max(0, len(prefix) - 800) :]
        ) and not _ANNUAL_REVENUES_TABLE_RE.search(prefix):
            continue

        millions = _parse_decimal(match.group(1))
        if millions is None or millions < _APPAREL_MILLIONS_MIN:
            continue
        return millions * MILLIONS
    return None


def _parse_apparel_mix_pct_total_from_text(text: str) -> Optional[Decimal]:
    """QTD mix % from Sales by Product Category (stored as fraction)."""
    mix_fractions: list[Decimal] = []
    for match in _APPAREL_MIX_PCT_RE.finditer(text):
        pct = _parse_decimal(match.group(1))
        if pct is None:
            continue
        fraction = pct / Decimal("100")
        if _APPAREL_MIX_PCT_MIN <= fraction <= _APPAREL_MIX_PCT_MAX:
            mix_fractions.append(fraction)

    if not mix_fractions:
        return None
    # U.S. table appears first; Canada duplicate uses higher mix (e.g. 26% vs 19%).
    return mix_fractions[0] if len(mix_fractions) == 1 else min(mix_fractions)


_TARGET_GROSS_MARGIN_MIN = Decimal("20")
_TARGET_GROSS_MARGIN_MAX = Decimal("40")
_GROSS_MARGIN_RATE_TABLE_RE = re.compile(
    r"Gross margin rate\s+([\d.]+)\s*%",
    re.I,
)
_GROSS_MARGIN_RATE_NARRATIVE_RE = re.compile(
    r"gross margin rate was\s+([\d.]+)\s*percent",
    re.I,
)
_GROSS_MARGIN_RATE_NARRATIVE_PRIOR_RE = re.compile(
    r"gross margin rate was\s+([\d.]+)\s*percent,\s*compared with\s*"
    r"([\d.]+)\s*percent",
    re.I,
)


def _target_gross_margin_in_valid_range(value: Decimal) -> bool:
    return _TARGET_GROSS_MARGIN_MIN <= value <= _TARGET_GROSS_MARGIN_MAX


def _parse_gross_margin_pct_from_10q_text(
    text: str,
) -> tuple[Optional[Decimal], Optional[str], Optional[Decimal]]:
    """
    Extract quarterly gross margin % from 10-Q stripped text.

    Returns (gross_margin_pct, pattern_name, gross_margin_change_bps).
    """
    table_match = _GROSS_MARGIN_RATE_TABLE_RE.search(text)
    if table_match:
        current = _parse_decimal(table_match.group(1))
        if current is not None and _target_gross_margin_in_valid_range(current):
            return current, "rate_analysis_table", None

    narrative_match = _GROSS_MARGIN_RATE_NARRATIVE_PRIOR_RE.search(text)
    if narrative_match:
        current = _parse_decimal(narrative_match.group(1))
        prior = _parse_decimal(narrative_match.group(2))
        if current is not None and _target_gross_margin_in_valid_range(current):
            change_bps = None
            if prior is not None:
                change_bps = (current - prior) * Decimal("100")
            return current, "mda_narrative", change_bps

    narrative_only = _GROSS_MARGIN_RATE_NARRATIVE_RE.search(text)
    if narrative_only:
        current = _parse_decimal(narrative_only.group(1))
        if current is not None and _target_gross_margin_in_valid_range(current):
            return current, "mda_narrative", None

    return None, None, None


_TARGET_COMP_SALES_MIN = Decimal("-30")
_TARGET_COMP_SALES_MAX = Decimal("40")
_COMP_SALES_CHANGE_TABLE_NEG_RE = re.compile(
    r"Comparable sales change\s+\(\s*([\d.]+)\s*\)\s*%",
    re.I,
)
_COMP_SALES_CHANGE_TABLE_POS_RE = re.compile(
    r"Comparable sales change\s+([\d.]+(?:\.\d+)?)\s*%",
    re.I,
)
_COMP_SALES_NARRATIVE_RE = re.compile(
    r"[Cc]omparable sales "
    r"(increased|decreased|grew|declined|change of|increase of|decrease of)\s+"
    r"([\d.]+)\s*percent",
    re.I,
)
_COMP_STORE_SALES_CHANGE_TABLE_NEG_RE = re.compile(
    r"Comparable-store sales change\s+\(\s*([\d.]+)\s*\)\s*%",
    re.I,
)
_COMP_STORE_SALES_CHANGE_TABLE_POS_RE = re.compile(
    r"Comparable-store sales change\s+([\d.]+(?:\.\d+)?)\s*%",
    re.I,
)
_COMP_STORE_SALES_LEGACY_NARRATIVE_RE = re.compile(
    r"([\d.]+)\s*percent comparable-store "
    r"(?:increase|decrease|sales increase|sales decrease)",
    re.I,
)
_COMP_STORE_SALES_ROW_NEG_RE = re.compile(
    r"Comparable-store sales\s+\(\s*([\d.]+)\s*\)\s*%",
    re.I,
)
_COMP_STORE_SALES_ROW_POS_RE = re.compile(
    r"Comparable-store sales\s+([\d.]+(?:\.\d+)?)\s*%",
    re.I,
)
_COMP_SALES_NEGATIVE_VERBS = frozenset({"decreased", "declined", "decrease of"})
_COMP_STORE_SALES_NEGATIVE_PHRASES = frozenset({"decrease", "sales decrease"})


def _target_comp_sales_in_valid_range(value: Decimal) -> bool:
    return _TARGET_COMP_SALES_MIN <= value <= _TARGET_COMP_SALES_MAX


def _parse_comparable_sales_growth_pct_from_10q_text(
    text: str,
) -> tuple[Optional[Decimal], Optional[str]]:
    """
    Extract quarterly comparable sales growth from 10-Q stripped text.

    Returns (comparable_sales_growth_pct, pattern_name).
    """
    normalized = _normalize_earnings_text(text)

    neg_match = _COMP_SALES_CHANGE_TABLE_NEG_RE.search(normalized)
    if neg_match:
        value = _parse_decimal(neg_match.group(1))
        if value is not None:
            value = -value
            if _target_comp_sales_in_valid_range(value):
                return value, "comparable_sales_table"

    pos_match = _COMP_SALES_CHANGE_TABLE_POS_RE.search(normalized)
    if pos_match:
        value = _parse_decimal(pos_match.group(1))
        if value is not None and _target_comp_sales_in_valid_range(value):
            return value, "comparable_sales_table"

    for match in _COMP_SALES_NARRATIVE_RE.finditer(normalized):
        verb = match.group(1).lower()
        value = _parse_decimal(match.group(2))
        if value is None:
            continue
        if verb in _COMP_SALES_NEGATIVE_VERBS:
            value = -value
        if _target_comp_sales_in_valid_range(value):
            return value, "mda_narrative"

    neg_store_match = _COMP_STORE_SALES_CHANGE_TABLE_NEG_RE.search(normalized)
    if neg_store_match:
        value = _parse_decimal(neg_store_match.group(1))
        if value is not None:
            value = -value
            if _target_comp_sales_in_valid_range(value):
                return value, "comparable_sales_table"

    pos_store_match = _COMP_STORE_SALES_CHANGE_TABLE_POS_RE.search(normalized)
    if pos_store_match:
        value = _parse_decimal(pos_store_match.group(1))
        if value is not None and _target_comp_sales_in_valid_range(value):
            return value, "comparable_sales_table"

    legacy_store_match = _COMP_STORE_SALES_LEGACY_NARRATIVE_RE.search(normalized)
    if legacy_store_match:
        value = _parse_decimal(legacy_store_match.group(1))
        phrase = legacy_store_match.group(0).lower()
        if value is not None:
            if any(neg in phrase for neg in _COMP_STORE_SALES_NEGATIVE_PHRASES):
                value = -value
            if _target_comp_sales_in_valid_range(value):
                return value, "mda_narrative"

    neg_row_match = _COMP_STORE_SALES_ROW_NEG_RE.search(normalized)
    if neg_row_match:
        value = _parse_decimal(neg_row_match.group(1))
        if value is not None:
            value = -value
            if _target_comp_sales_in_valid_range(value):
                return value, "comparable_sales_table"

    pos_row_match = _COMP_STORE_SALES_ROW_POS_RE.search(normalized)
    if pos_row_match:
        value = _parse_decimal(pos_row_match.group(1))
        if value is not None and _target_comp_sales_in_valid_range(value):
            return value, "comparable_sales_table"

    return None, None


_TARGET_OPERATING_MARGIN_MIN = Decimal("-5")
_TARGET_OPERATING_MARGIN_MAX = Decimal("15")
_OPERATING_MARGIN_RATE_TABLE_NEG_RE = re.compile(
    r"Operating income margin rate\s+(?:\([a-z]\)\s*)?\(([\d.]+)\)\s*%",
    re.I,
)
_OPERATING_MARGIN_RATE_TABLE_POS_RE = re.compile(
    r"Operating income margin rate\s+(?:\([a-z]\)\s*)?([\d.]+)\s*%?",
    re.I,
)
_EBIT_MARGIN_RATE_RE = re.compile(
    r"EBIT margin rate\s+(?:\([a-z]\)\s*)?([\d.]+)\s*%?",
    re.I,
)
_10Q_OPERATING_INCOME_RE = re.compile(
    r"Three Months Ended.{0,5000}?Operating income\s+(?:\(\s*([\d,]+)\s*\)|([\d,]+))",
    re.I | re.S,
)
_QUARTERLY_SALES_ROW_RE = re.compile(r"Sales\s+[^A-Z]{0,2500}", re.I)
_QUARTERLY_EBIT_ROW_RE = re.compile(
    r"(?:Operating income|Earnings before interest expense and income taxes)\s+"
    r"[^A-Z]{0,2500}",
    re.I,
)


def _target_operating_margin_in_valid_range(value: Decimal) -> bool:
    return _TARGET_OPERATING_MARGIN_MIN <= value <= _TARGET_OPERATING_MARGIN_MAX


def _parse_operating_margin_pct_from_10q_text(
    text: str,
) -> tuple[Optional[Decimal], Optional[str]]:
    """
    Extract quarterly operating margin % from 10-Q Rate Analysis table.

    Returns (operating_margin_pct, pattern_name).
    """
    normalized = _normalize_earnings_text(text)

    neg_match = _OPERATING_MARGIN_RATE_TABLE_NEG_RE.search(normalized)
    if neg_match:
        value = _parse_decimal(neg_match.group(1))
        if value is not None:
            value = -value
            if _target_operating_margin_in_valid_range(value):
                return value, "rate_analysis_table"

    pos_match = _OPERATING_MARGIN_RATE_TABLE_POS_RE.search(normalized)
    if pos_match:
        value = _parse_decimal(pos_match.group(1))
        if value is not None and _target_operating_margin_in_valid_range(value):
            return value, "rate_analysis_table"

    quarterly_ebit: Optional[Decimal] = None
    annual_ebit: Optional[Decimal] = None
    for ebit_match in _EBIT_MARGIN_RATE_RE.finditer(normalized):
        value = _parse_decimal(ebit_match.group(1))
        if value is None or not _target_operating_margin_in_valid_range(value):
            continue
        prefix = normalized[max(0, ebit_match.start() - 2000) : ebit_match.start()]
        if _THREE_MONTHS_ENDED_RE.search(prefix):
            if quarterly_ebit is None:
                quarterly_ebit = value
        elif annual_ebit is None:
            annual_ebit = value

    if quarterly_ebit is not None:
        return quarterly_ebit, "ebit_rate_analysis_table"
    if annual_ebit is not None:
        return annual_ebit, "ebit_rate_analysis_table"

    return None, None


def _derive_operating_margin_pct_from_income_statement(
    text: str,
    total_net_sales_usd: Decimal,
) -> tuple[Optional[Decimal], Optional[str]]:
    """
    Derive operating margin as (operating income / total revenue) × 100.

    Uses total_net_sales_usd from retailer_financials; operating income is
    parsed from the filing income statement because operating_income_usd is
    not stored on retailer_financials.
    """
    if total_net_sales_usd <= 0:
        return None, None

    normalized = _normalize_earnings_text(text)
    sales_m = total_net_sales_usd / MILLIONS

    op_match = _10Q_OPERATING_INCOME_RE.search(normalized)
    if op_match:
        raw = op_match.group(1) or op_match.group(2)
        operating_income_m = _parse_decimal(raw)
        if operating_income_m is not None:
            margin = (operating_income_m * MILLIONS / total_net_sales_usd) * Decimal(
                "100"
            )
            if _target_operating_margin_in_valid_range(margin):
                return margin, "income_statement_derivation"

    for delta in (Decimal("0"), Decimal("1"), Decimal("-1"), Decimal("2"), Decimal("-2")):
        target_m = int(sales_m + delta)
        target_plain = str(target_m)
        for sales_row in _QUARTERLY_SALES_ROW_RE.finditer(normalized):
            chunk = sales_row.group(0)
            numbers = re.findall(r"[\d,]+", chunk)
            column_index: Optional[int] = None
            for idx, token in enumerate(numbers):
                if token.replace(",", "") != target_plain:
                    continue
                if idx > 7:
                    continue
                column_index = idx
                break
            if column_index is None:
                continue

            for ebit_row in _QUARTERLY_EBIT_ROW_RE.finditer(normalized):
                ebit_numbers = re.findall(r"[\d,]+", ebit_row.group(0))
                if len(ebit_numbers) <= column_index:
                    continue
                operating_income_m = _parse_decimal(ebit_numbers[column_index])
                if operating_income_m is None:
                    continue
                margin = (
                    operating_income_m * MILLIONS / total_net_sales_usd
                ) * Decimal("100")
                if _target_operating_margin_in_valid_range(margin):
                    return margin, "income_statement_derivation"

    return None, None


def _parse_10q_metrics(html: str, fiscal_quarter: Optional[int] = None) -> dict[str, Any]:
    text = _strip_html(html)
    metrics: dict[str, Any] = {}

    gross_margin_pct, gm_pattern, gm_change_bps = _parse_gross_margin_pct_from_10q_text(
        text
    )
    if gross_margin_pct is not None:
        metrics["gross_margin_pct"] = gross_margin_pct
        metrics["gross_margin_pattern"] = gm_pattern
    if gm_change_bps is not None:
        metrics["gross_margin_change_bps"] = gm_change_bps

    apparel_revenue_usd = _parse_apparel_revenue_usd_from_text(text)
    if apparel_revenue_usd is not None:
        metrics["apparel_revenue_usd"] = apparel_revenue_usd
        apparel_mix_pct = _parse_apparel_mix_pct_total_from_text(text)
        if apparel_mix_pct is not None:
            metrics["apparel_revenue_pct_total"] = apparel_mix_pct

    comp_value, comp_pattern = _parse_comparable_sales_growth_pct_from_10q_text(text)
    if comp_value is not None:
        metrics["comparable_sales_growth_pct"] = comp_value
        metrics["comparable_sales_pattern"] = comp_pattern

    op_margin, op_pattern = _parse_operating_margin_pct_from_10q_text(text)
    if op_margin is not None:
        metrics["operating_margin_pct"] = op_margin
        metrics["operating_margin_pattern"] = op_pattern

    digital_match = re.search(
        r"comparable digital sales growth of\s*([\d.]+)\s*percent",
        text,
        re.I,
    )
    if digital_match:
        metrics["digital_comp_sales_pct"] = Decimal(digital_match.group(1))

    ecommerce_match = re.search(
        r"(?:digital|online|e-?commerce)[^%]{0,80}(\d+(?:\.\d+)?)\s*percent of (?:total )?(?:net )?sales",
        text,
        re.I,
    )
    if ecommerce_match:
        metrics["ecommerce_penetration_pct"] = Decimal(ecommerce_match.group(1))

    metrics.update(_extract_target_store_count_metrics(text))

    return metrics


def _find_latest_8k_ex99(submissions: dict[str, Any]) -> Optional[dict[str, str]]:
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
        ex99 = _find_ex99_document(index_payload)
        if ex99:
            return {
                "accession": accn,
                "document": ex99,
                "filing_date": filed,
                "url": _filing_doc_url(accn, ex99),
            }
    return None


def _parse_8k_guidance(html: str, fiscal_quarter: Optional[int] = None) -> dict[str, Any]:
    text = _strip_html(html)
    result: dict[str, Any] = {}

    margin_match = re.search(
        r"(?:first quarter )?gross margin rate was\s*([\d.]+)\s*percent,\s*compared with\s*"
        r"([\d.]+)\s*percent",
        text,
        re.I,
    )
    if margin_match:
        current = Decimal(margin_match.group(1))
        prior = Decimal(margin_match.group(2))
        result["gross_margin_pct"] = current
        result["gross_margin_change_bps"] = (current - prior) * Decimal("100")

    guidance_block = text[text.find("Guidance") : text.find("Guidance") + 1500] if "Guidance" in text else text

    if re.search(r"net sales growth|grow net sales", guidance_block, re.I):
        if re.search(r"flat|stable|unchanged", guidance_block, re.I):
            result["guidance_sales_direction"] = "flat"
        else:
            result["guidance_sales_direction"] = "growth"
    elif re.search(r"decline|lower|decrease", guidance_block, re.I):
        result["guidance_sales_direction"] = "decline"
    else:
        result["guidance_sales_direction"] = "not_provided"

    range_match = re.search(
        r"(?:net sales growth in a range around|growth in a range around|growth of)\s*"
        r"([\d.]+)\s*percent",
        guidance_block,
        re.I,
    )
    if range_match:
        midpoint = Decimal(range_match.group(1))
        result["guidance_sales_range_low"] = midpoint
        result["guidance_sales_range_high"] = midpoint
    else:
        band_match = re.search(
            r"([\d.]+)\s*(?:to|-)\s*([\d.]+)\s*percent",
            guidance_block,
            re.I,
        )
        if band_match:
            result["guidance_sales_range_low"] = Decimal(band_match.group(1))
            result["guidance_sales_range_high"] = Decimal(band_match.group(2))

    eps_match = re.search(
        r"(?:GAAP and Adjusted EPS|Adjusted EPS|EPS).*?\$\s*([\d.]+)\s*(?:to|-)\s*\$\s*([\d.]+)",
        guidance_block,
        re.I,
    )
    if eps_match:
        eps_low = _parse_decimal(eps_match.group(1))
        eps_high = _parse_decimal(eps_match.group(2))
        if eps_low is not None:
            result["guidance_eps_low"] = eps_low
        if eps_high is not None:
            result["guidance_eps_high"] = eps_high
    else:
        eps_near = re.search(
            r"EPS near the high end of the prior guidance range of\s*"
            r"\$\s*([\d.]+)\s*to\s*\$\s*([\d.]+)",
            guidance_block,
            re.I,
        )
        if eps_near:
            eps_low = _parse_decimal(eps_near.group(1))
            eps_high = _parse_decimal(eps_near.group(2))
            if eps_low is not None:
                result["guidance_eps_low"] = eps_low
            if eps_high is not None:
                result["guidance_eps_high"] = eps_high

    comp_value = _extract_target_comparable_sales_growth_pct(text, fiscal_quarter)
    if comp_value is not None:
        result["comparable_sales_growth_pct"] = comp_value

    digital_match = re.search(
        r"comparable digital sales growth of\s*([\d.]+)\s*percent",
        text,
        re.I,
    )
    if digital_match:
        result["digital_comp_sales_pct"] = Decimal(digital_match.group(1))

    apparel_revenue_usd = _parse_apparel_revenue_usd_from_text(text)
    if apparel_revenue_usd is not None:
        result["apparel_revenue_usd"] = apparel_revenue_usd

    result.update(_extract_target_store_count_metrics(text))

    return result


def _get_target_retailer_id(db: Session) -> Optional[int]:
    retailer = (
        db.query(MajorRetailers).filter(MajorRetailers.name == TARGET_NAME).first()
    )
    if retailer is None:
        logger.error("Target Corporation not found in major_retailers")
        return None
    return retailer.retailer_id


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
    "xbrl_extracted",
)


def _validate_target_payload(
    ctx: IngestionContext,
    payload: dict[str, Any],
) -> None:
    if payload.get("total_net_sales_usd") is not None:
        payload["total_net_sales_usd"] = validate_and_log(
            payload["total_net_sales_usd"],
            lambda v: validate_retailer_revenue(v, TARGET_NAME),
            ctx,
        )
    if payload.get("gross_margin_pct") is not None:
        payload["gross_margin_pct"] = validate_and_log(
            payload["gross_margin_pct"],
            lambda v: validate_gross_margin(v, TARGET_NAME),
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


def run_target_tier1_ingestion(
    db: Session,
    ctx: IngestionContext,
) -> list[dict[str, Any]]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return []

    companyfacts = _sec_get(_COMPANYFACTS_URL)
    submissions = _sec_get(_SUBMISSIONS_URL)
    if not isinstance(companyfacts, dict):
        logger.error("Failed to fetch Target company facts")
        return []
    if not isinstance(submissions, dict):
        logger.warning("Failed to fetch Target submissions — guidance URLs may be missing")
        submissions = {}

    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        logger.error("No us-gaap facts for Target")
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

    quarter_keys, fetched_keys = _select_output_fiscal_keys(meta)
    if not quarter_keys:
        logger.error("No fiscal quarters found in Target XBRL data")
        return []

    fetched_labels = [
        f"FY{meta[k]['fiscal_year']} Q{meta[k]['fiscal_quarter']}"
        for k in fetched_keys
    ]
    logger.info("Quarter window fetched: %s", ", ".join(fetched_labels))

    ordered_keys = sorted(
        meta.keys(),
        key=lambda k: meta[k]["period_end_date"],
    )

    ex99_info = _find_latest_8k_ex99(submissions) if submissions else None
    ex99_metrics: dict[str, Any] = {}
    source_8k_url: Optional[str] = None
    if ex99_info:
        source_8k_url = ex99_info["url"]
        ex99_html = _fetch_filing_html(ex99_info["accession"], ex99_info["document"])
        if ex99_html:
            ex99_metrics = _parse_8k_guidance(ex99_html)
            logger.info("Parsed 8-K EX-99 guidance from %s", source_8k_url)
        else:
            logger.warning("Failed to fetch 8-K EX-99 document")

    filing_rows = _load_all_submission_filings(submissions) if submissions else []
    if filing_rows:
        logger.info("Loaded %d SEC filing row(s) for historical 8-K lookup", len(filing_rows))

    summaries: list[dict[str, Any]] = []
    summary_keys = set(quarter_keys)
    latest_key = quarter_keys[0]

    for key in reversed(fetched_keys):
        qmeta = meta[key]
        period_end: date = qmeta["period_end_date"]
        fiscal_year = qmeta["fiscal_year"]
        fiscal_quarter = qmeta["fiscal_quarter"]
        field_sources: dict[str, str] = {}

        total_sales = revenue.get(key)
        if total_sales is not None:
            field_sources["total_net_sales_usd"] = "XBRL"

        gp = gross_profit.get(key)
        cogs_q = cogs.get(key)
        sga_q = sga.get(key)
        op_q = operating.get(key)
        inv = inventory.get(key)
        stores_xbrl = store_count_xbrl.get(key)

        if gp is not None:
            field_sources["gross_profit"] = "XBRL" if key in gross_profit else "XBRL-derived"
        if cogs_q is not None:
            field_sources["cogs"] = "XBRL"
        if sga_q is not None:
            field_sources["sga"] = "XBRL"
        if op_q is not None:
            field_sources["operating_income"] = "XBRL"
        if inv is not None:
            field_sources["inventory_usd"] = "XBRL"
        if stores_xbrl is not None:
            field_sources["store_count_total"] = "XBRL"

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
                gross_margin_change_bps = (gross_margin_pct - prior_margin * Decimal("100")) * Decimal("100")

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
        apparel_revenue_usd: Optional[Decimal] = None
        html_metrics: dict[str, Any] = {}

        accession = qmeta.get("accession")
        if accession:
            index_payload = _fetch_filing_index(accession)
            if index_payload:
                primary_doc = _find_primary_htm(index_payload)
                if primary_doc:
                    source_10q_url = _filing_doc_url(accession, primary_doc)
                    html = _fetch_filing_html(accession, primary_doc)
                    if html:
                        apparel_revenue_usd = _parse_inline_apparel_revenue(html, period_end)
                        if apparel_revenue_usd is not None:
                            field_sources["apparel_revenue_usd"] = "HTML-inline-XBRL"
                        elif qmeta.get("synthesized_q4"):
                            apparel_revenue_usd = _derive_q4_apparel_revenue(
                                html,
                                period_end,
                                fiscal_year,
                                meta,
                            )
                            if apparel_revenue_usd is not None:
                                field_sources["apparel_revenue_usd"] = "HTML-derived-Q4"
                        html_metrics = _parse_10q_metrics(html, fiscal_quarter=fiscal_quarter)
                        for field in html_metrics:
                            field_sources[field] = "HTML"

        quarter_8k_metrics, quarter_8k_url = fetch_quarter_earnings_metrics(
            filing_rows,
            period_end,
            fiscal_quarter,
        )
        if quarter_8k_metrics.get("comparable_sales_growth_pct") is not None:
            html_metrics["comparable_sales_growth_pct"] = quarter_8k_metrics[
                "comparable_sales_growth_pct"
            ]
            field_sources["comparable_sales_growth_pct"] = "HTML-8K"
        if quarter_8k_metrics.get("digital_comp_sales_pct") is not None:
            html_metrics["digital_comp_sales_pct"] = quarter_8k_metrics[
                "digital_comp_sales_pct"
            ]
            field_sources["digital_comp_sales_pct"] = "HTML-8K"
        for store_field in ("store_count_total", "store_count_net_change"):
            if (
                html_metrics.get(store_field) is None
                and quarter_8k_metrics.get(store_field) is not None
            ):
                html_metrics[store_field] = quarter_8k_metrics[store_field]
                field_sources[store_field] = "HTML-8K"
        if quarter_8k_url:
            source_8k_url = quarter_8k_url

        apparel_revenue_pct_total = None
        if apparel_revenue_usd is not None and total_sales is not None:
            apparel_revenue_pct_total = _safe_div(apparel_revenue_usd, total_sales)

        apparel_yoy_growth_pct = None
        if apparel_revenue_usd is not None:
            prior_meta = meta.get(prior_key)
            if prior_meta and prior_meta.get("accession"):
                prior_index = _fetch_filing_index(prior_meta["accession"])
                prior_doc = (
                    _find_primary_htm(prior_index) if prior_index else None
                )
                if prior_doc:
                    prior_html = _fetch_filing_html(prior_meta["accession"], prior_doc)
                    if prior_html:
                        prior_apparel = _parse_inline_apparel_revenue(
                            prior_html,
                            prior_meta["period_end_date"],
                        )
                        if prior_apparel and prior_apparel > 0:
                            apparel_yoy_growth_pct = (
                                apparel_revenue_usd / prior_apparel - Decimal("1")
                            ) * Decimal("100")
                            field_sources["apparel_yoy_growth_pct"] = "HTML-derived"

        payload: dict[str, Any] = {
            "fiscal_year": fiscal_year,
            "fiscal_quarter": fiscal_quarter,
            "period_end_date": period_end,
            "filing_date": qmeta.get("filing_date"),
            "apparel_revenue_usd": apparel_revenue_usd,
            "apparel_revenue_pct_total": apparel_revenue_pct_total,
            "apparel_yoy_growth_pct": apparel_yoy_growth_pct,
            "total_net_sales_usd": total_sales,
            "comparable_sales_growth_pct": html_metrics.get("comparable_sales_growth_pct"),
            "digital_comp_sales_pct": html_metrics.get("digital_comp_sales_pct"),
            "gross_margin_pct": gross_margin_pct,
            "gross_margin_change_bps": gross_margin_change_bps,
            "sga_rate_pct": sga_rate_pct,
            "operating_margin_pct": operating_margin_pct,
            "inventory_usd": inv,
            "inventory_days": inventory_days,
            "store_count_total": html_metrics.get("store_count_total"),
            "store_count_net_change": html_metrics.get("store_count_net_change"),
            "ecommerce_penetration_pct": html_metrics.get("ecommerce_penetration_pct"),
            "guidance_sales_direction": None,
            "guidance_sales_range_low": None,
            "guidance_sales_range_high": None,
            "guidance_eps_low": None,
            "guidance_eps_high": None,
            "source_10q_url": source_10q_url,
            "source_8k_url": source_8k_url,
            "xbrl_extracted": total_sales is not None,
        }

        if payload["store_count_total"] is None and stores_xbrl is not None:
            payload["store_count_total"] = int(stores_xbrl)

        if key == latest_key and ex99_metrics:
            for field in (
                "gross_margin_pct",
                "gross_margin_change_bps",
                "guidance_sales_direction",
                "guidance_sales_range_low",
                "guidance_sales_range_high",
                "guidance_eps_low",
                "guidance_eps_high",
                "comparable_sales_growth_pct",
                "digital_comp_sales_pct",
            ):
                if ex99_metrics.get(field) is not None:
                    payload[field] = ex99_metrics[field]
                    field_sources[field] = "HTML-8K"

        _validate_target_payload(ctx, payload)
        _append_retailer_financials(db, ctx, retailer_id, payload)

        logger.info(
            "FY%s Q%s field sources: %s",
            fiscal_year,
            fiscal_quarter,
            field_sources,
        )

        if key in summary_keys:
            summaries.append(
                {
                    "label": f"FY{fiscal_year} Q{fiscal_quarter}",
                    "apparel_rev": apparel_revenue_usd,
                    "apparel_pct": apparel_revenue_pct_total,
                    "total_sales": total_sales,
                    "gross_margin": payload["gross_margin_pct"],
                    "comp_sales": payload["comparable_sales_growth_pct"],
                    "store_count": payload["store_count_total"],
                }
            )

    logger.info("Appended %d quarter(s) to retailer_financials", len(summaries))
    return summaries


def _format_money(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    billions = value / Decimal("1000000000")
    return f"${billions:.2f}B"


def _format_pct(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _format_fraction(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f}"


def print_summary(summaries: list[dict[str, Any]]) -> None:
    header = (
        f"{'Quarter':<12} | {'Apparel Rev':<12} | {'Apparel %':<10} | "
        f"{'Total Sales':<12} | {'Gross Margin':<12} | {'Comp Sales':<10} | "
        f"{'Store Count'}"
    )
    print(header)
    print("-" * len(header))
    for row in summaries:
        print(
            f"{row['label']:<12} | "
            f"{_format_money(row['apparel_rev']):<12} | "
            f"{_format_fraction(row['apparel_pct']):<10} | "
            f"{_format_money(row['total_sales']):<12} | "
            f"{_format_pct(row['gross_margin']):<12} | "
            f"{_format_pct(row['comp_sales']):<10} | "
            f"{row['store_count'] or 'N/A'}"
        )


def main() -> int:
    db = SessionLocal()
    try:
        logger.info("Starting Target Tier 1 SEC EDGAR ingestion")
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=_COMPANYFACTS_URL,
            db=db,
        ) as ctx:
            summaries = run_target_tier1_ingestion(db, ctx)
            if not summaries:
                logger.error("No Target retailer_financials rows written")
                ctx.set_failed("No quarters written")
                return 1
            print_summary(summaries)
            logger.info(
                "Target Tier 1 ingestion complete — %d quarter(s) in summary",
                len(summaries),
            )
            return 0
    except Exception as exc:
        logger.exception("Target Tier 1 ingestion failed: %s", exc)
        try:
            with IngestionContext(
                source_name=SOURCE_NAME,
                script_version=SCRIPT_VERSION,
                data_source_url=_COMPANYFACTS_URL,
                db=db,
            ) as fail_ctx:
                fail_ctx.set_failed(exc)
        except Exception:
            logger.exception("Failed to write ingestion_log for Target Tier 1 failure")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""
Walmart Inc Tier 2 ingestion — narrative intelligence grounded in Tier 1 financials.

SEC sources per earnings event: EX-99.2 presentation (primary), EX-99.1 release,
10-Q MD&A. Walmart does not file call transcripts on EDGAR — optional Motley Fool
fetch via fetch_walmart_transcript().

Three-pass Claude extraction with consolidation and evidence chains.
Requires Tier 1 retailer_financials row for each quarter before processing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional, Union

import requests
from anthropic import Anthropic
from sqlalchemy import desc
from sqlalchemy.orm import Session

import data.ingestion.target_tier2_ingestion as engine
from data.ingestion import walmart_tier1_ingestion as walmart_tier1
from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.models.retail import (
    MajorRetailers,
    RetailerFinancials,
    RetailerIntelligenceExtract,
)

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

WALMART_NAME = "Walmart Inc"
WALMART_CIK = "0000104169"
CIK_NUM = "104169"
SEC_USER_AGENT = "Artemis/1.0 supply-chain-intelligence@artemis.com"
SEC_RATE_LIMIT_SECONDS = 0.1
CLAUDE_RATE_LIMIT_SECONDS = 0.5
REQUEST_TIMEOUT = 60
EXTRACTION_MODEL = "claude-sonnet-4-6"
EXTRACTION_PROMPT_VER = "wmt-v2.1"
MAX_DOCUMENT_WORDS = 4000
MAX_PRESENTATION_WORDS = 6000

_SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{WALMART_CIK}.json"
_FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
_FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

_MDA_START_RE = re.compile(r"Management['\u2019]?s Discussion", re.I)
_MDA_END_RE = re.compile(r"Quantitative and Qualitative Disclosures", re.I)
_QA_SPLIT_RE = re.compile(
    r"Question[-\s]and[-\s]Answer(?:\s+Session)?|Q\s*&\s*A\s+Session",
    re.I,
)
_FOOL_TRANSCRIPTS_URL = "https://www.fool.com/earnings/call-transcripts/"
_FOOL_BASE_URL = "https://www.fool.com"
_FOOL_RATE_LIMIT_SECONDS = 0.5
_FOOL_LINK_RE = re.compile(r'href="(/earnings/call-transcripts/[^"]+)"', re.I)

WALMART_DOCUMENT_PRIORITY = {
    "8K_earnings_presentation": 1,
    "8K_earnings_release": 2,
    "10Q_mda": 3,
    "external_transcript_qa": 4,
    "external_transcript_remarks": 5,
}

WALMART_SIGNAL_CATEGORIES = (
    "walmart_us_general_merch_performance",
    "walmart_us_apparel_fashion_specific",
    "sams_club_home_apparel_performance",
    "sams_club_membership_signals",
    "private_brand_penetration",
    "rollback_pricing_pressure",
    "ecommerce_channel_shift_walmart_us",
    "ecommerce_channel_shift_sams_club",
    "inventory_positioning_walmart_us",
    "inventory_positioning_sams_club",
    "consumer_traffic_and_basket",
    "consumer_value_behavior",
    "forward_guidance_walmart_us",
    "forward_guidance_sams_club",
    "tariff_and_sourcing_geography",
    "supplier_vendor_rollback_commentary",
)

WALMART_SIGNAL_CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "rollback_pricing_pressure": (
        "Every mention of Rollback (capital R) — Walmart's proprietary programme "
        "for supplier-funded price reductions. Classify ALL Rollback mentions here, "
        "NOT under consumer_value_behavior, even when Rollbacks drive traffic or "
        "value perception."
    ),
    "tariff_and_sourcing_geography": (
        "Any mention of tariffs, import costs, China sourcing, country of origin "
        "diversification, IEEPA, Section 301, or supplier cost impacts from trade policy"
    ),
    "consumer_value_behavior": (
        "Value-seeking shopper behaviour (EDLP, saving money, trading down) — "
        "NOT Rollback programme mentions (those are rollback_pricing_pressure)"
    ),
}

PASS1_SYSTEM_PROMPT = """
You are extracting facts from Walmart Inc earnings documents.
Walmart Inc operates two distinct analytical entities that must never be conflated:

Walmart U.S. — Mass market retail. Apparel sits inside General Merchandise
alongside home, auto care, and seasonal. Value-seeking consumer, FOB price
sensitive. Rollbacks and General Merchandise comps are the primary apparel proxy.

Sam's Club — Membership warehouse club. Home and Apparel is a named category —
the only explicit apparel line in the Walmart enterprise. Bulk buying consumer;
membership growth predicts volume. Sam's Club signals are not Walmart U.S. signals.

Classic Fashion Apparel supplies both entities with different FOB structures,
pack configurations, and lead times. Tag every extraction to the correct entity.

ROLLBACK CLASSIFICATION RULE:
The word Rollback (capital R) is Walmart's proprietary programme for
supplier-funded price reductions. Every mention of Rollback is a
rollback_pricing_pressure signal regardless of context — including when
Rollbacks drive traffic, improve value perception, or appear alongside
EDLP language. Never classify Rollback as consumer_value_behavior.

TARIFF AND TRADE POLICY RULE:
Tariff, import cost, China sourcing, country-of-origin diversification,
IEEPA, Section 301, and supplier cost impacts from trade policy are
tariff_and_sourcing_geography signals. Extract each explicit statement
separately — do not merge multiple tariff comments into one object.

When you see mentions of 'fashion', 'apparel', 'clothing', 'softlines',
'kids clothing', 'private brands', 'Rollback', 'general merchandise',
'Home and Apparel', 'membership', 'tariff', 'import costs', or 'sourcing'
— these are high-priority signals.

Extract only statements explicitly made in the document.
Return ONLY a valid JSON array with no preamble, no explanation, no markdown.
""".strip()

PASS2_ENTITY_CONTEXT = """
ENTITY CONTEXT:
Walmart U.S. — Mass market retail. 4,600+ stores. 240 million weekly
shoppers. Apparel sits inside General Merchandise alongside home,
auto care, and seasonal. The apparel customer buys on price and value.
Walmart U.S. buyers negotiate FOB aggressively because every cent
passes through to volume. When General Merchandise comps are negative
or flat, Walmart U.S. buyers reduce forward apparel commitments and
press for FOB reductions.

Sam's Club — Membership warehouse club. 600+ locations. 17 million
paying members who pay $50-$110 annually. Home and Apparel is a
named category — the only explicit apparel line in the Walmart
enterprise. Sam's Club members buy in bulk with intent. Membership
growth is a leading indicator of sustained buying volume.
Sam's Club apparel is bundled differently — multi-packs, seasonal
value sets, basics in quantity. When Sam's Club comp sales are
positive and membership is growing, bulk apparel programs are
lower cancellation risk than Walmart U.S. fashion programs.

CLASSIC FASHION CONTEXT:
Classic Fashion Apparel supplies approximately 40 percent of
Walmart's clothing category through direct manufacturing in Jordan
and third-party sourcing from Bangladesh, India, and Vietnam.
Jordan manufacturing is duty-free under the US-Jordan FTA.
Bangladesh manufacturing carries 16.5% NTR duty.
Classic Fashion runs both Walmart U.S. programs and potentially
Sam's Club programs — these require different FOB structures,
pack configurations, and lead times.
Every signal derived must specify whether it affects Walmart U.S.
programs, Sam's Club programs, or both — be very precise.

CONSUMPTION PATTERN CONTEXT:
Traffic-led comparable sales growth (more transactions, smaller basket)
indicates a value-seeking consumer who shops more frequently for basics
and replenishment items. This sustains apparel basics volume.
Classic Fashion's Bangladesh and Vietnam basics programs are more
resilient in traffic-led growth environments.

Ticket-led comparable sales growth (fewer transactions, larger basket)
indicates consumers are consolidating trips but spending more per visit.
This signals price-sensitive behaviour — customers are not choosing to
shop at Walmart more, they are spending more per trip when they do.
In this environment Walmart buyers face pressure to keep apparel prices
flat or declining, which means FOB pressure on Classic Fashion.

Sam's Club member growth is the strongest forward indicator for bulk
apparel demand. Each new paying member represents a committed buyer
who is more likely to purchase bundled apparel sets. When Sam's Club
membership grows more than 5% YoY, Classic Fashion's Sam's Club
channel programs should be at full committed volume.
When membership growth slows below 2% YoY, reduce Sam's Club forward
commitments by 10-15% until trend stabilises.
""".strip()

PASS2_SYSTEM_PROMPT_TEMPLATE = (
    PASS2_ENTITY_CONTEXT
    + """

You advise Classic Fashion Apparel — a Jordan-based company that supplies
approximately 40 percent of Walmart's clothing category.

Every signal you derive must answer: what does this mean for Classic Fashion
specifically — their current programs, their next season commitments,
their FOB negotiations, their corridor selection, and their risk exposure.
Every implication must state which entity is affected: walmart_us, sams_club, or both.

TIER 1 NUMERICAL CONTEXT FOR THIS QUARTER:

WALMART U.S. ENTITY:
  Business model: {walmart_us_model_note}
  General Merchandise revenue: {walmart_us_general_merch_usd}
    YoY change: {walmart_us_general_merch_yoy_pct}
  eCommerce: {walmart_us_ecommerce_usd}
    YoY growth: {walmart_us_ecommerce_yoy_growth_pct}
  Comparable sales: {comparable_sales_growth_pct}
  Inventory: {walmart_us_inventory_usd}
    YoY change: {walmart_us_inventory_yoy_change_pct}
    Inventory days: {walmart_us_inventory_days}
    Inventory-to-sales ratio: {walmart_us_inventory_to_sales_ratio}
    Proxy signal: {general_merch_inventory_proxy_signal}
  Inventory narrative (10-Q/8-K): {inventory_change_narrative}

SAM'S CLUB ENTITY:
  Business model: {sams_club_model_note}
  Home and Apparel revenue: {sams_club_home_apparel_usd}
    YoY change: {sams_club_home_apparel_yoy_pct}
  Comp sales ex fuel: {sams_club_comp_sales_ex_fuel_pct}
  Inventory: {sams_club_inventory_usd}
    YoY change: {sams_club_inventory_yoy_change_pct}
    Inventory days: {sams_club_inventory_days}
  Member count: {sams_club_member_count}
    YoY growth: {sams_club_member_count_yoy_pct}
  Membership fee revenue: {sams_club_membership_fee_revenue_usd}

CONSUMPTION PATTERNS:
  Transaction count growth: {transaction_count_growth_pct}
  Average transaction value change: {average_transaction_value_change_pct}
  Traffic vs ticket split: {ticket_vs_traffic_split}
  Walmart+ member count: {walmart_plus_member_count}
  Walmart+ membership growth: {walmart_plus_membership_growth_pct}

ENTERPRISE METRICS:
  Gross margin: {gross_margin_pct}
    Change vs prior year: {gross_margin_change_bps} bps
  Private brand mix change: {private_brand_mix_change_bps} bps
  Inventory positioning language: {inventory_positioning_language}
  Forward guidance: {guidance_sales_direction} {guidance_sales_range_low}%
    to {guidance_sales_range_high}%

Use these numbers to ground every implication. When management says
'General Merchandise was strong' — reference the actual dollar figure.
When they say 'private brand penetration increased' — reference the
basis points number. When they say 'Rollbacks are driving traffic' —
connect it to whether gross margin compressed and which entity is affected.

A signal without a number is an opinion.
A number without a signal is noise.
The combination is intelligence.

Return ONLY a valid JSON array. No preamble. No explanation. No markdown.
"""
).strip()

PASS3_SYSTEM_PROMPT = """
You are a pattern recognition analyst for Artemis. You compare current
Walmart retailer signals against historical signals to identify whether the
same language patterns have appeared before and whether outcomes that followed
can inform current predictions.

Return ONLY a valid JSON array. No preamble. No explanation. No markdown.
""".strip()


@dataclass
class QuarterContext:
    fiscal_year: int
    fiscal_quarter: int
    period_end_date: date
    filing_date: Optional[date]
    source_10q_url: Optional[str]


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
    except Exception as exc:
        logger.warning("SEC request failed for %s: %s", url, exc)
        return None


def _accession_nodash(accession: str) -> str:
    return accession.replace("-", "")


def _filing_doc_url(accession: str, document: str) -> str:
    return _FILING_DOC_URL.format(
        cik=CIK_NUM,
        accession=_accession_nodash(accession),
        document=document,
    )


def _fetch_filing_index(accession: str) -> Optional[dict[str, Any]]:
    url = _FILING_INDEX_URL.format(cik=CIK_NUM, accession=_accession_nodash(accession))
    payload = _sec_get(url)
    return payload if isinstance(payload, dict) else None


def _fetch_filing_html(accession: str, document: str) -> Optional[str]:
    return _sec_get(_filing_doc_url(accession, document))  # type: ignore[return-value]


def _parse_filing_date(raw: str) -> Optional[date]:
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _iter_recent_filings(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    rows: list[dict[str, Any]] = []
    for form, accn, filed, primary in zip(forms, accessions, filing_dates, primary_docs):
        filed_date = _parse_filing_date(filed)
        if filed_date is None:
            continue
        rows.append(
            {
                "form": form,
                "accession": accn,
                "filing_date": filed_date,
                "primary_document": primary,
            }
        )
    return rows


def _truncate_words(text: str, max_words: int = MAX_DOCUMENT_WORDS) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _document_word_limit(document_type: str) -> int:
    if document_type == "8K_earnings_presentation":
        return MAX_PRESENTATION_WORDS
    return MAX_DOCUMENT_WORDS


def _truncate_document_text(text: str, document_type: str) -> str:
    max_words = _document_word_limit(document_type)
    words = text.split()
    if len(words) <= max_words:
        return text

    head = " ".join(words[:max_words])
    if document_type != "8K_earnings_presentation":
        return head

    tail = " ".join(words[max_words:])
    priority_pattern = re.compile(
        r"[^.!?]*(?:Rollback|tariff|tariffs|IEEPA|Section 301|import cost|"
        r"China sourcing|country of origin|trade policy|sourcing geography)"
        r"[^.!?]*[.!?]",
        re.I,
    )
    excerpts = [
        match.group(0).strip()
        for match in priority_pattern.finditer(tail)
        if len(match.group(0).strip()) > 20
    ]
    if not excerpts:
        return head

    seen: set[str] = set()
    unique_excerpts: list[str] = []
    for excerpt in excerpts:
        if excerpt in seen:
            continue
        seen.add(excerpt)
        unique_excerpts.append(excerpt)

    excerpt_block = " ".join(unique_excerpts[:12])
    excerpt_words = excerpt_block.split()
    if len(excerpt_words) > 800:
        excerpt_block = " ".join(excerpt_words[:800])

    return (
        f"{head}\n\n"
        "[PRIORITY EXCERPTS FROM LATER IN PRESENTATION — Rollback/tariff/trade policy]\n"
        f"{excerpt_block}"
    )


def _format_pass1_signal_categories() -> str:
    lines: list[str] = []
    for category in WALMART_SIGNAL_CATEGORIES:
        description = WALMART_SIGNAL_CATEGORY_DESCRIPTIONS.get(category)
        if description:
            lines.append(f"  {category} — {description}")
        else:
            lines.append(f"  {category}")
    return "\n".join(lines)


def _split_transcript(text: str) -> tuple[str, str]:
    match = _QA_SPLIT_RE.search(text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), text[match.start() :].strip()


def _http_get_text(url: str, *, rate_limit: float = _FOOL_RATE_LIMIT_SECONDS) -> Optional[str]:
    headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "gzip, deflate",
    }
    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        time.sleep(rate_limit)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        logger.warning("HTTP request failed for %s: %s", url, exc)
        return None


def _fool_transcript_link(
    html: str,
    fiscal_quarter: int,
    fiscal_year: int,
) -> Optional[str]:
    quarter_token = f"q{fiscal_quarter}"
    year_short = str(fiscal_year)[-2:]
    candidates: list[str] = []
    for href in _FOOL_LINK_RE.findall(html):
        lower = href.lower()
        if "walmart" not in lower and "wmt" not in lower:
            continue
        if quarter_token not in lower:
            continue
        if str(fiscal_year) not in lower and f"fy{year_short}" not in lower:
            continue
        if "transcript" not in lower:
            continue
        candidates.append(href)
    if not candidates:
        return None
    return sorted(candidates, key=len, reverse=True)[0]


def _extract_fool_transcript_text(html: str) -> Optional[str]:
    article_match = re.search(
        r'<div[^>]+class="[^"]*article-body[^"]*"[^>]*>(.*)</div>\s*<div[^>]+class="[^"]*article-footer',
        html,
        re.S | re.I,
    )
    if article_match:
        return walmart_tier1._strip_html(article_match.group(1))
    article_match = re.search(
        r"<article[^>]*>(.*?)</article>",
        html,
        re.S | re.I,
    )
    if article_match:
        return walmart_tier1._strip_html(article_match.group(1))
    return walmart_tier1._strip_html(html)


def fetch_walmart_transcript(
    quarter: int,
    year: int,
) -> list[engine.DocumentSection]:
    """
    Attempt to fetch Walmart earnings call transcript from Motley Fool.
    Returns external_transcript_remarks and/or external_transcript_qa sections.
    Never raises — returns empty list if unavailable.
    """
    documents: list[engine.DocumentSection] = []
    try:
        index_html = _http_get_text(_FOOL_TRANSCRIPTS_URL)
        transcript_path: Optional[str] = None
        if index_html:
            transcript_path = _fool_transcript_link(index_html, quarter, year)

        if transcript_path is None:
            search_url = (
                "https://www.fool.com/search/?q="
                f"Walmart+Q{quarter}+FY{year}+earnings+call+transcript"
            )
            search_html = _http_get_text(search_url)
            if search_html:
                transcript_path = _fool_transcript_link(search_html, quarter, year)

        if transcript_path is None:
            logger.warning(
                "Motley Fool transcript not found for Walmart FY%s Q%s",
                year,
                quarter,
            )
            return documents

        transcript_url = (
            transcript_path
            if transcript_path.startswith("http")
            else f"{_FOOL_BASE_URL}{transcript_path}"
        )
        transcript_html = _http_get_text(transcript_url)
        if not transcript_html:
            logger.warning(
                "Failed to fetch Motley Fool transcript page for FY%s Q%s: %s",
                year,
                quarter,
                transcript_url,
            )
            return documents

        transcript_text = _extract_fool_transcript_text(transcript_html)
        if not transcript_text or len(transcript_text) < 500:
            logger.warning(
                "Motley Fool transcript content too short for FY%s Q%s",
                year,
                quarter,
            )
            return documents

        remarks, qa = _split_transcript(transcript_text)
        if remarks.strip():
            documents.append(
                engine.DocumentSection(
                    document_type="external_transcript_remarks",
                    document_section="prepared_remarks",
                    text=remarks,
                    source_url=transcript_url,
                    filing_date=None,
                )
            )
        if qa.strip():
            documents.append(
                engine.DocumentSection(
                    document_type="external_transcript_qa",
                    document_section="qa_session",
                    text=qa,
                    source_url=transcript_url,
                    filing_date=None,
                )
            )

        if documents:
            logger.info(
                "Fetched Motley Fool transcript for Walmart FY%s Q%s from %s",
                year,
                quarter,
                transcript_url,
            )
    except Exception as exc:
        logger.warning(
            "Motley Fool transcript fetch failed for Walmart FY%s Q%s: %s",
            year,
            quarter,
            exc,
        )
    return documents


def _extract_mda_section(html: str) -> Optional[str]:
    text = walmart_tier1._strip_html(html)
    if not text:
        return None
    start_match = _MDA_START_RE.search(text)
    end_match = _MDA_END_RE.search(text)
    if start_match and end_match and end_match.start() > start_match.start():
        return text[start_match.start() : end_match.start()].strip()
    if start_match:
        return text[start_match.start() :].strip()
    return text


def _find_quarter_8k(
    submissions: dict[str, Any],
    quarter: QuarterContext,
) -> Optional[dict[str, Any]]:
    window_start = quarter.period_end_date + timedelta(days=15)
    window_end = quarter.period_end_date + timedelta(days=75)
    for filing in _iter_recent_filings(submissions):
        if filing["form"] != "8-K":
            continue
        filed_date = filing["filing_date"]
        if filed_date < window_start or filed_date > window_end:
            continue
        index_payload = _fetch_filing_index(filing["accession"])
        if not index_payload:
            continue
        release = walmart_tier1._find_exhibit_by_patterns(
            index_payload, walmart_tier1.RELEASE_EXHIBIT_PATTERNS
        )
        if release:
            return {
                "accession": filing["accession"],
                "filing_date": filed_date,
                "index_payload": index_payload,
                "release_document": release,
            }
    return None


def _collect_quarter_documents(
    submissions: dict[str, Any],
    quarter: QuarterContext,
) -> list[engine.DocumentSection]:
    documents: list[engine.DocumentSection] = []
    earnings_8k = _find_quarter_8k(submissions, quarter)
    if not earnings_8k:
        logger.warning(
            "No Walmart 8-K found for FY%s Q%s",
            quarter.fiscal_year,
            quarter.fiscal_quarter,
        )
        return documents

    accession = earnings_8k["accession"]
    filed_date = earnings_8k["filing_date"]
    index_payload = earnings_8k["index_payload"]

    presentation_doc = walmart_tier1._find_exhibit_by_patterns(
        index_payload, walmart_tier1.PRESENTATION_EXHIBIT_PATTERNS
    )
    if presentation_doc:
        html = _fetch_filing_html(accession, presentation_doc)
        if html:
            documents.append(
                engine.DocumentSection(
                    document_type="8K_earnings_presentation",
                    document_section="category_commentary",
                    text=walmart_tier1._strip_html(html),
                    source_url=_filing_doc_url(accession, presentation_doc),
                    filing_date=filed_date,
                )
            )
    else:
        logger.warning(
            "EX-99.2 presentation not found for FY%s Q%s — falling back to release",
            quarter.fiscal_year,
            quarter.fiscal_quarter,
        )

    release_doc = earnings_8k["release_document"]
    release_html = _fetch_filing_html(accession, release_doc)
    if release_html:
        documents.append(
            engine.DocumentSection(
                document_type="8K_earnings_release",
                document_section="earnings_release",
                text=walmart_tier1._strip_html(release_html),
                source_url=_filing_doc_url(accession, release_doc),
                filing_date=filed_date,
            )
        )

    if quarter.source_10q_url:
        accession_match = re.search(r"/data/\d+/(\d+)/", quarter.source_10q_url)
        doc_match = quarter.source_10q_url.rstrip("/").split("/")[-1]
        if accession_match and doc_match:
            digits = accession_match.group(1)
            if len(digits) >= 18:
                accession = f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"
                html = _fetch_filing_html(accession, doc_match)
                if html:
                    mda = _extract_mda_section(html)
                    if mda:
                        documents.append(
                            engine.DocumentSection(
                                document_type="10Q_mda",
                                document_section="MD&A",
                                text=mda,
                                source_url=quarter.source_10q_url,
                                filing_date=quarter.filing_date,
                            )
                        )

    documents.extend(
        fetch_walmart_transcript(quarter.fiscal_quarter, quarter.fiscal_year)
    )
    return documents


def _get_walmart_retailer_id(db: Session) -> Optional[int]:
    retailer = (
        db.query(MajorRetailers).filter(MajorRetailers.name == WALMART_NAME).first()
    )
    if retailer is None:
        logger.error("Walmart Inc not found in major_retailers")
        return None
    return retailer.retailer_id


def _load_tier1_row(
    db: Session,
    retailer_id: int,
    quarter: QuarterContext,
) -> Optional[RetailerFinancials]:
    return (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.fiscal_year == quarter.fiscal_year,
            RetailerFinancials.fiscal_quarter == quarter.fiscal_quarter,
            RetailerFinancials.is_latest.is_(True),
        )
        .first()
    )


def _format_money_b(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    return f"${value / Decimal('1000000000'):.1f}B"


def _format_pct(value: Optional[Decimal]) -> str:
    if value is None:
        return "N/A"
    return f"{value}%"


def _format_text(value: Optional[str]) -> str:
    if not value:
        return "N/A"
    return value[:200] + ("..." if len(value) > 200 else "")


def _build_tier1_context_block(row: RetailerFinancials) -> str:
    return PASS2_SYSTEM_PROMPT_TEMPLATE.format(
        walmart_us_model_note=row.walmart_us_model_note or "N/A",
        sams_club_model_note=row.sams_club_model_note or "N/A",
        walmart_us_general_merch_usd=_format_money_b(row.walmart_us_general_merch_usd),
        walmart_us_general_merch_yoy_pct=_format_pct(row.walmart_us_general_merch_yoy_pct),
        sams_club_home_apparel_usd=_format_money_b(row.sams_club_home_apparel_usd),
        sams_club_home_apparel_yoy_pct=_format_pct(row.sams_club_home_apparel_yoy_pct),
        walmart_us_ecommerce_usd=_format_money_b(row.walmart_us_ecommerce_usd),
        walmart_us_ecommerce_yoy_growth_pct=_format_pct(
            row.walmart_us_ecommerce_yoy_growth_pct
        ),
        comparable_sales_growth_pct=_format_pct(row.comparable_sales_growth_pct),
        sams_club_comp_sales_ex_fuel_pct=_format_pct(row.sams_club_comp_sales_ex_fuel_pct),
        walmart_us_inventory_usd=_format_money_b(row.walmart_us_inventory_usd),
        walmart_us_inventory_yoy_change_pct=_format_pct(row.walmart_us_inventory_yoy_change_pct),
        walmart_us_inventory_days=row.walmart_us_inventory_days or "N/A",
        walmart_us_inventory_to_sales_ratio=row.walmart_us_inventory_to_sales_ratio or "N/A",
        general_merch_inventory_proxy_signal=row.general_merch_inventory_proxy_signal or "unknown",
        inventory_change_narrative=_format_text(row.inventory_change_narrative),
        sams_club_inventory_usd=_format_money_b(row.sams_club_inventory_usd),
        sams_club_inventory_yoy_change_pct=_format_pct(row.sams_club_inventory_yoy_change_pct),
        sams_club_inventory_days=row.sams_club_inventory_days or "N/A",
        sams_club_member_count=row.sams_club_member_count or "N/A",
        sams_club_member_count_yoy_pct=_format_pct(row.sams_club_member_count_yoy_pct),
        sams_club_membership_fee_revenue_usd=_format_money_b(
            row.sams_club_membership_fee_revenue_usd
        ),
        transaction_count_growth_pct=_format_pct(row.transaction_count_growth_pct),
        average_transaction_value_change_pct=_format_pct(
            row.average_transaction_value_change_pct
        ),
        ticket_vs_traffic_split=row.ticket_vs_traffic_split or "N/A",
        walmart_plus_member_count=row.walmart_plus_member_count or "N/A",
        walmart_plus_membership_growth_pct=_format_pct(row.walmart_plus_membership_growth_pct),
        gross_margin_pct=_format_pct(row.gross_margin_pct),
        gross_margin_change_bps=row.gross_margin_change_bps or "N/A",
        private_brand_mix_change_bps=row.private_brand_mix_change_bps or "N/A",
        inventory_positioning_language=_format_text(row.inventory_positioning_language),
        guidance_sales_direction=row.guidance_sales_direction or "N/A",
        guidance_sales_range_low=row.guidance_sales_range_low or "N/A",
        guidance_sales_range_high=row.guidance_sales_range_high or "N/A",
    )


def _build_pass1_prompt(
    section: engine.DocumentSection,
    quarter: QuarterContext,
    tier1_row: RetailerFinancials,
) -> str:
    truncated = _truncate_document_text(section.text, section.document_type)
    word_limit = _document_word_limit(section.document_type)
    tier1_block = _build_tier1_context_block(tier1_row)
    return f"""
Retailer: Walmart Inc
Document type: {section.document_type}
Fiscal period: Q{quarter.fiscal_quarter} FY{quarter.fiscal_year} ending {quarter.period_end_date}
Document word limit: {word_limit} words

TIER 1 NUMERICAL CONTEXT (ground all extractions against these figures):
{tier1_block}

DOCUMENT TEXT:
{truncated}

Extract every explicit statement related to these categories.
For each statement found, return a JSON object:
  {{
    "signal_category": one of the categories below — use the exact snake_case name,
    "raw_text_passage": exact quote from document max 400 characters,
    "stated_fact": one sentence capturing exactly what was said max 150 chars,
    "speaker": "management" or "analyst" or "unknown",
    "is_forward_looking": true or false,
    "contains_number": true or false,
    "number_mentioned": the specific number if any or null,
    "time_period_referenced": "current_quarter" "next_quarter" "full_year"
      "next_season" "multi_year" or "unspecified"
  }}

SIGNAL CATEGORIES (exact names required):
{_format_pass1_signal_categories()}

ROLLBACK REMINDER: Every mention of Rollback (capital R) must use
signal_category rollback_pricing_pressure — never consumer_value_behavior.

Omit categories not present. Do not invent. Only extract explicit statements.
""".strip()


def _build_pass2_prompt(
    section: engine.DocumentSection,
    quarter: QuarterContext,
    pass1_facts: list[dict[str, Any]],
    tier1_row: RetailerFinancials,
) -> str:
    tier1_block = _build_tier1_context_block(tier1_row)
    return f"""
Retailer: Walmart Inc
Fiscal period: Q{quarter.fiscal_quarter} FY{quarter.fiscal_year}
Document type: {section.document_type}

{tier1_block}

EXTRACTED FACTS FROM THIS DOCUMENT:
{json.dumps(pass1_facts, indent=2)}

For each extracted fact derive its supply chain implication for Classic Fashion.
Every artemis_implication must begin with the affected entity: [Walmart U.S.],
[Sam's Club], or [Both entities].
Return a JSON array where each object contains:
  {{
    "original_signal_category": category from Pass 1,
    "raw_text_passage": same passage from Pass 1,
    "stated_fact": same fact from Pass 1,
    "signal_sentiment": "positive" "negative" "neutral" or "mixed",
    "signal_strength": "strong" "moderate" or "weak",
    "artemis_implication": specific implication referencing a Tier 1 number,
      naming corridor/season and decision type. Max 250 characters.,
    "affected_decision": one of "commit_timing" "fob_negotiation"
      "volume_expectation" "factory_booking" "hedge_decision"
      "program_risk" "corridor_selection" or "no_direct_impact",
    "time_horizon": "immediate" "next_quarter" "next_season" or "multi_season",
    "confidence_score": 0.00 to 1.00
  }}
""".strip()


def _build_pass3_prompt(
    quarter: QuarterContext,
    pass2_results: list[dict[str, Any]],
    prior_signals: list[Any],
    tier1_row: RetailerFinancials,
) -> str:
    prior_summary = [
        {
            "fiscal_year": row.fiscal_year,
            "fiscal_quarter": row.fiscal_quarter,
            "signal_category": row.signal_category,
            "extracted_signal": row.extracted_signal,
            "signal_sentiment": row.signal_sentiment,
            "artemis_implication": row.artemis_implication_full or row.artemis_implication,
        }
        for row in prior_signals
    ]
    tier1_block = _build_tier1_context_block(tier1_row)
    return f"""
CURRENT SIGNALS (Q{quarter.fiscal_quarter} FY{quarter.fiscal_year}):
{json.dumps(pass2_results, indent=2)}

TIER 1 OUTCOMES CONTEXT:
{tier1_block}

PRIOR SIGNALS FROM WALMART (last 30 entries):
{json.dumps(prior_summary, indent=2)}

For each current signal determine if a similar pattern exists in prior quarters.
Return a JSON array:
  {{
    "signal_category": from current signal,
    "raw_text_passage": from current signal,
    "historical_pattern_found": true or false,
    "similar_prior_quarter": "Q2 FY2024" or null,
    "similar_prior_language": brief description or null,
    "observed_outcome": what followed in the next quarter or null,
    "pattern_confidence": 0.00 to 1.00 or null
  }}
""".strip()


def _call_claude(
    client: Anthropic,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> Optional[str]:
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is not set")
        return None
    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        time.sleep(CLAUDE_RATE_LIMIT_SECONDS)
        if not response.content:
            return None
        return response.content[0].text
    except Exception as exc:
        logger.error("Claude API call failed: %s", exc)
        return None


def _load_quarter_contexts(
    db: Session,
    retailer_id: int,
    quarter_count: int,
) -> list[QuarterContext]:
    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
        )
        .order_by(
            RetailerFinancials.period_end_date.desc(),
            RetailerFinancials.fiscal_year.desc(),
            RetailerFinancials.fiscal_quarter.desc(),
        )
        .limit(quarter_count)
        .all()
    )
    return [
        QuarterContext(
            fiscal_year=row.fiscal_year,
            fiscal_quarter=row.fiscal_quarter,
            period_end_date=row.period_end_date,
            filing_date=row.filing_date,
            source_10q_url=row.source_10q_url,
        )
        for row in rows
    ]


def _process_document_section(
    db: Session,
    client: Anthropic,
    retailer_id: int,
    quarter: QuarterContext,
    section: engine.DocumentSection,
    tier1_row: RetailerFinancials,
) -> tuple[engine.DocumentRunSummary, list[engine.PendingSignal]]:
    quarter_label = f"FY{quarter.fiscal_year} Q{quarter.fiscal_quarter}"
    summary = engine.DocumentRunSummary(
        document=section.document_type,
        quarter=quarter_label,
    )
    if not section.text.strip():
        summary.notes = "empty document text"
        return summary, []

    pass1_prompt = _build_pass1_prompt(section, quarter, tier1_row)
    pass1_raw = _call_claude(client, PASS1_SYSTEM_PROMPT, pass1_prompt, max_tokens=4000)
    if pass1_raw is None:
        summary.pass1_status = "failed"
        summary.notes = "Pass 1 Claude call failed"
        return summary, []

    try:
        pass1_facts = engine._parse_json_array(pass1_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        summary.pass1_status = "failed"
        summary.notes = f"Pass 1 JSON parse failed: {exc}"
        return summary, []

    summary.pass1_status = "ok"
    summary.pass1_signals = len(pass1_facts)
    if not pass1_facts:
        summary.notes = "Pass 1 returned no signals"
        return summary, []

    pass2_prompt = _build_pass2_prompt(section, quarter, pass1_facts, tier1_row)
    pass2_raw = _call_claude(
        client,
        _build_tier1_context_block(tier1_row),
        pass2_prompt,
        max_tokens=4000,
    )
    pass2_results: list[dict[str, Any]] = []
    pass2_ok = False
    if pass2_raw is None:
        summary.pass2_status = "failed"
        logger.error("Pass 2 failed for %s %s", section.document_type, quarter_label)
    else:
        try:
            pass2_results = engine._parse_json_array(pass2_raw)
            pass2_ok = True
            summary.pass2_status = "ok"
            summary.pass2_implications = len(pass2_results)
        except (json.JSONDecodeError, ValueError) as exc:
            summary.pass2_status = "failed"
            logger.error("Pass 2 parse failed for %s %s: %s", section.document_type, quarter_label, exc)

    prior_signals = (
        db.query(RetailerIntelligenceExtract)
        .filter(
            RetailerIntelligenceExtract.retailer_id == retailer_id,
            (
                (RetailerIntelligenceExtract.fiscal_year < quarter.fiscal_year)
                | (
                    (RetailerIntelligenceExtract.fiscal_year == quarter.fiscal_year)
                    & (
                        RetailerIntelligenceExtract.fiscal_quarter
                        < quarter.fiscal_quarter
                    )
                )
            ),
        )
        .order_by(desc(RetailerIntelligenceExtract.filing_date))
        .limit(30)
        .all()
    )

    pass3_results: list[dict[str, Any]] = []
    pass3_ok = False
    if not prior_signals:
        summary.pass3_status = "skipped"
        summary.notes = "no prior signals"
    else:
        pass3_prompt = _build_pass3_prompt(
            quarter, pass2_results or pass1_facts, prior_signals, tier1_row
        )
        pass3_raw = _call_claude(client, PASS3_SYSTEM_PROMPT, pass3_prompt, max_tokens=2000)
        if pass3_raw is None:
            summary.pass3_status = "failed"
        else:
            try:
                pass3_results = engine._parse_json_array(pass3_raw)
                pass3_ok = True
                summary.pass3_status = "ok"
                summary.pass3_patterns = sum(
                    1
                    for item in pass3_results
                    if engine._to_bool(item.get("historical_pattern_found"))
                )
            except (json.JSONDecodeError, ValueError) as exc:
                summary.pass3_status = "failed"
                logger.error("Pass 3 parse failed for %s %s: %s", section.document_type, quarter_label, exc)

    pending: list[engine.PendingSignal] = []
    for index, fact in enumerate(pass1_facts):
        pass2 = engine._match_pass2_fact(fact, pass2_results, index) if pass2_ok else {}
        pass3 = (
            engine._match_pass3_fact(fact, pass3_results, index)
            if pass3_ok
            else {"historical_pattern_found": False}
        )
        pending.append(
            engine.PendingSignal(
                section=section,
                fact=fact,
                pass2=pass2,
                pass3=pass3,
                pass2_ok=pass2_ok,
                pass3_ok=pass3_ok,
            )
        )
    return summary, pending


def run_walmart_tier2_ingestion(
    db: Session,
    quarter_count: int = 1,
) -> list[engine.DocumentRunSummary]:
    retailer_id = _get_walmart_retailer_id(db)
    if retailer_id is None:
        return []

    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is not set")
        return []

    submissions = _sec_get(_SUBMISSIONS_URL)
    if not isinstance(submissions, dict):
        logger.error("Failed to fetch Walmart SEC submissions")
        return []

    quarters = _load_quarter_contexts(db, retailer_id, quarter_count)
    if not quarters:
        logger.error("No retailer_financials rows for Walmart — run walmart_tier1 first")
        return []

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    summaries: list[engine.DocumentRunSummary] = []
    original_priority = engine.DOCUMENT_PRIORITY
    engine.DOCUMENT_PRIORITY = WALMART_DOCUMENT_PRIORITY

    try:
        for quarter in quarters:
            quarter_label = f"FY{quarter.fiscal_year} Q{quarter.fiscal_quarter}"
            tier1_row = _load_tier1_row(db, retailer_id, quarter)
            if tier1_row is None:
                logger.warning(
                    "Skipping Tier 2 for %s — no Tier 1 retailer_financials row",
                    quarter_label,
                )
                continue

            logger.info(
                "Processing Walmart Tier 2 %s with Tier 1 context "
                "(Gen Merch=%s, Sam Apparel=%s)",
                quarter_label,
                _format_money_b(tier1_row.walmart_us_general_merch_usd),
                _format_money_b(tier1_row.sams_club_home_apparel_usd),
            )

            documents = _collect_quarter_documents(submissions, quarter)
            if not documents:
                logger.warning("No documents found for %s", quarter_label)
                continue

            quarter_pending: list[engine.PendingSignal] = []
            for section in documents:
                summary, pending = _process_document_section(
                    db,
                    client,
                    retailer_id,
                    quarter,
                    section,
                    tier1_row,
                )
                summaries.append(summary)
                quarter_pending.extend(pending)

            if quarter_pending:
                quarter_ctx = engine.QuarterContext(
                    fiscal_year=quarter.fiscal_year,
                    fiscal_quarter=quarter.fiscal_quarter,
                    period_end_date=quarter.period_end_date,
                    filing_date=quarter.filing_date,
                    source_10q_url=quarter.source_10q_url,
                )
                masters_written, deferred = engine._consolidate_quarter_signals(
                    db,
                    retailer_id,
                    quarter_ctx,
                    quarter_pending,
                )
                db.commit()
                logger.info(
                    "Consolidated %d pending signals into %d masters for %s",
                    len(quarter_pending),
                    masters_written,
                    quarter_label,
                )
                if deferred:
                    logger.warning("Deferred fields for %s: %s", quarter_label, deferred)
    finally:
        engine.DOCUMENT_PRIORITY = original_priority

    return summaries


def print_summary_table(summaries: list[engine.DocumentRunSummary]) -> None:
    header = (
        f"{'Document':<28} | {'Quarter':<12} | {'Pass1':<6} | "
        f"{'Pass2':<6} | {'Pass3':<6} | {'Status'}"
    )
    print(header)
    print("-" * len(header))
    for row in summaries:
        status = f"P1:{row.pass1_status} P2:{row.pass2_status} P3:{row.pass3_status}"
        if row.notes:
            status = f"{status} ({row.notes})"
        print(
            f"{row.document:<28} | {row.quarter:<12} | "
            f"{row.pass1_signals:<6} | {row.pass2_implications:<6} | "
            f"{row.pass3_patterns:<6} | {status}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walmart Tier 2 narrative intelligence (Tier 1 grounded)"
    )
    parser.add_argument(
        "--quarters",
        type=int,
        default=1,
        help="Number of most recent fiscal quarters to process (default: 1)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.quarters < 1:
        logger.error("--quarters must be >= 1")
        return 1

    db = SessionLocal()
    try:
        summaries = run_walmart_tier2_ingestion(db, quarter_count=args.quarters)
        if not summaries:
            logger.error("No Walmart Tier 2 documents processed")
            return 1
        print_summary_table(summaries)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

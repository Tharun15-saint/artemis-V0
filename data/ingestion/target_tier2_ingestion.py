"""
Target Corporation Tier 2 ingestion — narrative intelligence from earnings documents.

Three-pass Claude API extraction (facts → implications → historical patterns).
After all documents in a quarter are extracted, signals are consolidated into
master records in retailer_intelligence_extract with full evidence chains in
retailer_signal_evidence.

Requires ANTHROPIC_API_KEY in environment. Schema migration required for
retailer_intelligence_extract consolidation fields and retailer_signal_evidence.
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

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
from sqlalchemy import desc, inspect
from sqlalchemy.orm import Session

from database.base import SessionLocal
from database.models.retail import (
    MajorRetailers,
    RetailerFinancials,
    RetailerIntelligenceExtract,
    RetailerSignalEvidence,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TARGET_NAME = "Target Corporation"
TARGET_CIK = "0000027419"
CIK_NUM = "27419"
SEC_USER_AGENT = "Artemis/1.0 supply-chain-intelligence@artemis.com"
SEC_RATE_LIMIT_SECONDS = 0.1
CLAUDE_RATE_LIMIT_SECONDS = 0.5
REQUEST_TIMEOUT = 60
EXTRACTION_MODEL = "claude-sonnet-4-6"
EXTRACTION_PROMPT_VER = "v1.0"
MAX_DOCUMENT_WORDS = 4000

_SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{TARGET_CIK}.json"
_FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
_FILING_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_QA_SPLIT_RE = re.compile(r"Question\s+and\s+Answer", re.I)
_MDA_START_RE = re.compile(r"Management['\u2019]?s Discussion", re.I)
_MDA_END_RE = re.compile(r"Quantitative and Qualitative Disclosures", re.I)

PASS1_SYSTEM_PROMPT = """
You are a precise data extraction system for Artemis, an apparel supply chain
intelligence platform. Extract only statements that are explicitly made in
the document. Do not interpret, infer, or add context. Only extract facts.
Return ONLY a valid JSON array with no preamble, no explanation, no markdown.
""".strip()

PASS2_SYSTEM_PROMPT = """
You are a senior supply chain intelligence analyst at Artemis. You advise
large apparel importers like Classic Fashion Apparel, a Jordan-based company
that supplies approximately 40 percent of Walmart clothing through direct
manufacturing and third-party sourcing from Bangladesh, India, and Vietnam.

Your job is to take factual statements extracted from retailer earnings
documents and derive their specific implications for an apparel importer's
sourcing decisions. Think about: commit timing, factory capacity booking,
FOB price negotiation leverage, order volume expectations, and program risk.

Return ONLY a valid JSON array. No preamble. No explanation. No markdown.
""".strip()

PASS3_SYSTEM_PROMPT = """
You are a pattern recognition analyst for Artemis. You compare current
retailer signals against historical signals to identify whether the same
language patterns have appeared before and whether outcomes that followed
can inform current predictions.

Return ONLY a valid JSON array. No preamble. No explanation. No markdown.
""".strip()

SOURCING_CONTEXT = """
- Classic Fashion sources from: Bangladesh (Gazipur, Dhaka), India (Tirupur),
  Jordan (duty-free under US-Jordan FTA), Vietnam
- Primary product: knit apparel — t-shirts, sweatshirts, hoodies, basics
- Primary buyer: Walmart (Target is a secondary market signal)
- Current season: SS27 commit window is open (June-August 2026)
- FW26 programs are in production or committed
- Bangladesh factory financing rates: 12-15 percent annually
- Ocean freight lead time: 28-35 days Bangladesh to US East Coast
""".strip()

RETAILER_INTELLIGENCE_EXTRACT_UPDATE_FIELDS = (
    "retailer_id",
    "fiscal_year",
    "fiscal_quarter",
    "period_end_date",
    "filing_date",
    "document_type",
    "document_section",
    "source_url",
    "signal_category",
    "raw_text_passage",
    "extracted_signal",
    "speaker",
    "is_forward_looking",
    "contains_number",
    "number_mentioned",
    "time_period_referenced",
    "signal_sentiment",
    "signal_strength",
    "artemis_implication",
    "affected_decision",
    "time_horizon",
    "confidence_score",
    "historical_pattern_found",
    "similar_prior_quarter",
    "similar_prior_language",
    "observed_outcome",
    "pattern_confidence",
    "extraction_model",
    "extraction_prompt_ver",
    "human_verified",
    "evidence_count",
    "corroboration_score",
    "has_contradiction",
    "primary_document_type",
    "primary_speaker",
)

DOCUMENT_PRIORITY = {
    "8K_transcript_qa": 1,
    "8K_transcript_remarks": 2,
    "10Q_mda": 3,
    "8K_earnings_release": 4,
}

_WORD_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass
class DocumentSection:
    document_type: str
    document_section: str
    text: str
    source_url: str
    filing_date: Optional[date]


@dataclass
class DocumentRunSummary:
    document: str
    quarter: str
    pass1_signals: int = 0
    pass2_implications: int = 0
    pass3_patterns: int = 0
    pass1_status: str = "skipped"
    pass2_status: str = "skipped"
    pass3_status: str = "skipped"
    notes: str = ""


@dataclass
class QuarterContext:
    fiscal_year: int
    fiscal_quarter: int
    period_end_date: date
    filing_date: Optional[date]
    source_10q_url: Optional[str]


@dataclass
class PendingSignal:
    section: DocumentSection
    fact: dict[str, Any]
    pass2: dict[str, Any]
    pass3: dict[str, Any]
    pass2_ok: bool
    pass3_ok: bool


def _model_column_names() -> set[str]:
    return {c.key for c in inspect(RetailerIntelligenceExtract).mapper.column_attrs}


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
    )
    return _WHITESPACE_RE.sub(" ", plain).strip()


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
    url = _filing_doc_url(accession, document)
    body = _sec_get(url)
    return body if isinstance(body, str) else None


def _find_exhibit_document(
    index_payload: dict[str, Any],
    preferred_patterns: list[str],
    fallback_patterns: Optional[list[str]] = None,
) -> Optional[str]:
    items = index_payload.get("directory", {}).get("item", [])
    candidates: list[tuple[int, str]] = []
    all_patterns = preferred_patterns + (fallback_patterns or [])
    for item in items:
        name = item.get("name", "")
        lower = name.lower()
        if not lower.endswith(".htm"):
            continue
        for idx, pattern in enumerate(all_patterns):
            if pattern in lower:
                candidates.append((idx, name))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0], len(pair[1])))
    return candidates[0][1]


def _parse_filing_date(raw: str) -> Optional[date]:
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _truncate_words(text: str, max_words: int = MAX_DOCUMENT_WORDS) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _split_transcript(text: str) -> tuple[str, str]:
    match = _QA_SPLIT_RE.search(text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), text[match.start() :].strip()


def _extract_mda_section(html: str) -> Optional[str]:
    text = _strip_html(html)
    if not text:
        return None
    start_match = _MDA_START_RE.search(text)
    end_match = _MDA_END_RE.search(text)
    if (
        start_match
        and end_match
        and end_match.start() > start_match.start()
    ):
        return text[start_match.start() : end_match.start()].strip()
    if start_match:
        logger.warning(
            "MD&A end boundary not found in 10-Q — using text from start marker onward"
        )
        return text[start_match.start() :].strip()
    logger.warning(
        "MD&A section boundaries not found in 10-Q — falling back to full document"
    )
    return text


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


def _looks_like_earnings_release(text: str) -> bool:
    lower = text.lower()
    markers = (
        "first quarter",
        "second quarter",
        "third quarter",
        "fourth quarter",
        "financial results",
        "earnings release",
        "reported net earnings",
        "total revenue",
        "comparable sales",
    )
    return any(marker in lower for marker in markers)


def _find_earnings_release_8k(
    submissions: dict[str, Any],
    quarter: QuarterContext,
) -> Optional[DocumentSection]:
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

        exhibit = _find_exhibit_document(
            index_payload,
            preferred_patterns=["ex-99.1", "ex99.1"],
            fallback_patterns=["ex-99", "ex99"],
        )
        if not exhibit:
            continue

        html = _fetch_filing_html(filing["accession"], exhibit)
        if not html:
            continue
        text = _strip_html(html)
        if not _looks_like_earnings_release(text):
            continue

        return DocumentSection(
            document_type="8K_earnings_release",
            document_section="full",
            text=text,
            source_url=_filing_doc_url(filing["accession"], exhibit),
            filing_date=filed_date,
        )

    logger.warning(
        "No 8-K earnings release found for FY%s Q%s (period_end=%s)",
        quarter.fiscal_year,
        quarter.fiscal_quarter,
        quarter.period_end_date,
    )
    return None


def _find_transcript_sections(
    submissions: dict[str, Any],
    earnings: DocumentSection,
) -> Optional[tuple[DocumentSection, DocumentSection]]:
    accession = _accession_from_url(earnings.source_url)
    earnings_doc = _document_from_url(earnings.source_url)
    if not accession or not earnings.filing_date:
        return None

    def _transcript_from_filing(
        filing_accession: str,
        exhibit: str,
        filed_date: date,
    ) -> Optional[tuple[DocumentSection, DocumentSection]]:
        html = _fetch_filing_html(filing_accession, exhibit)
        if not html:
            return None
        text = _strip_html(html)
        if "question" not in text.lower() and "operator" not in text.lower():
            return None
        remarks, qa = _split_transcript(text)
        if not remarks:
            return None
        source_url = _filing_doc_url(filing_accession, exhibit)
        return (
            DocumentSection(
                document_type="8K_transcript_remarks",
                document_section="prepared_remarks",
                text=remarks,
                source_url=source_url,
                filing_date=filed_date,
            ),
            DocumentSection(
                document_type="8K_transcript_qa",
                document_section="qa_session",
                text=qa or "",
                source_url=source_url,
                filing_date=filed_date,
            ),
        )

    index_payload = _fetch_filing_index(accession)
    if index_payload:
        exhibit = _find_exhibit_document(
            index_payload,
            preferred_patterns=["ex-99.2", "ex99.2", "transcript"],
            fallback_patterns=["ex-99", "ex99"],
        )
        if exhibit and exhibit != earnings_doc:
            same_filing = _transcript_from_filing(
                accession,
                exhibit,
                earnings.filing_date,
            )
            if same_filing:
                return same_filing

    window_start = earnings.filing_date - timedelta(days=1)
    window_end = earnings.filing_date + timedelta(days=7)
    for filing in _iter_recent_filings(submissions):
        if filing["form"] != "8-K":
            continue
        if filing["accession"] == accession:
            continue
        filed_date = filing["filing_date"]
        if filed_date < window_start or filed_date > window_end:
            continue
        index_payload = _fetch_filing_index(filing["accession"])
        if not index_payload:
            continue
        exhibit = _find_exhibit_document(
            index_payload,
            preferred_patterns=["ex-99.2", "ex99.2", "transcript"],
            fallback_patterns=["ex-99", "ex99"],
        )
        if not exhibit:
            continue
        transcript_pair = _transcript_from_filing(
            filing["accession"],
            exhibit,
            filed_date,
        )
        if transcript_pair:
            return transcript_pair

    logger.warning(
        "No earnings call transcript found within 7 days of %s",
        earnings.filing_date,
    )
    return None


def _accession_from_url(url: str) -> Optional[str]:
    match = re.search(r"/data/\d+/(\d+)/", url)
    if not match:
        return None
    digits = match.group(1)
    if len(digits) < 18:
        return None
    return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"


def _document_from_url(url: str) -> Optional[str]:
    return url.rstrip("/").split("/")[-1] if url else None


def _find_10q_mda(quarter: QuarterContext) -> Optional[DocumentSection]:
    if not quarter.source_10q_url:
        logger.warning(
            "No source_10q_url for FY%s Q%s — cannot fetch MD&A",
            quarter.fiscal_year,
            quarter.fiscal_quarter,
        )
        return None

    accession = _accession_from_url(quarter.source_10q_url)
    document = _document_from_url(quarter.source_10q_url)
    if not accession or not document:
        logger.warning("Could not parse 10-Q URL: %s", quarter.source_10q_url)
        return None

    html = _fetch_filing_html(accession, document)
    if not html:
        return None

    mda_text = _extract_mda_section(html)
    if not mda_text:
        return None

    return DocumentSection(
        document_type="10Q_mda",
        document_section="mda",
        text=mda_text,
        source_url=quarter.source_10q_url,
        filing_date=quarter.filing_date,
    )


def _get_target_retailer_id(db: Session) -> Optional[int]:
    retailer = (
        db.query(MajorRetailers).filter(MajorRetailers.name == TARGET_NAME).first()
    )
    if retailer is None:
        logger.error("Target Corporation not found in major_retailers")
        return None
    return retailer.retailer_id


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


def parse_claude_json(text: str) -> Any:
    # Strip any markdown code fences if present
    text = re.sub(r"^```json\s*", "", text.strip())
    text = re.sub(r"^```\s*", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find a JSON array in the text
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Try to recover partial JSON by finding complete objects
        objects = []
        for obj_match in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
            try:
                objects.append(json.loads(obj_match.group()))
            except json.JSONDecodeError:
                continue
        if objects:
            return objects
        raise


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    data = parse_claude_json(raw)
    if not isinstance(data, list):
        raise ValueError("Expected JSON array from Claude response")
    return data


def _call_claude(
    client: Anthropic,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
) -> Optional[str]:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
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


def _build_pass1_prompt(section: DocumentSection, quarter: QuarterContext) -> str:
    truncated = _truncate_words(section.text)
    return f"""
Retailer: Target Corporation
Document type: {section.document_type}
Fiscal period: Q{quarter.fiscal_quarter} FY{quarter.fiscal_year} ending {quarter.period_end_date}

DOCUMENT TEXT:
{truncated}

Extract every explicit statement related to these categories.
For each statement found, return a JSON object:
  {{
    "signal_category": one of the categories below,
    "raw_text_passage": exact quote from document max 400 characters,
    "stated_fact": one sentence capturing exactly what was said max 150 chars,
    "speaker": "management" or "analyst" or "unknown",
    "is_forward_looking": true or false,
    "contains_number": true or false,
    "number_mentioned": the specific number if any (e.g. "-3.8%" or "$4.1B") or null,
    "time_period_referenced": "current_quarter" "next_quarter" "full_year"
      "next_season" "multi_year" or "unspecified"
  }}

SIGNAL CATEGORIES:
  apparel_sales_performance
  apparel_markdown_and_inventory
  category_mix_shift
  owned_vs_national_brand
  digital_vs_store_channel
  seasonal_sellthrough
  consumer_behavior_language
  forward_guidance
  tariff_and_sourcing_geography
  vendor_and_supply_chain
  analyst_concern (use only for QA section — what analysts pressed on)
  management_deflection (use only for QA — when management avoided a question)

Omit categories not present. Do not invent. Only extract explicit statements.
""".strip()


def _build_pass2_prompt(
    section: DocumentSection,
    quarter: QuarterContext,
    pass1_facts: list[dict[str, Any]],
) -> str:
    return f"""
Retailer: Target Corporation
Fiscal period: Q{quarter.fiscal_quarter} FY{quarter.fiscal_year}
Document type: {section.document_type}

EXTRACTED FACTS FROM THIS DOCUMENT:
{json.dumps(pass1_facts, indent=2)}

SOURCING CONTEXT:
{SOURCING_CONTEXT}

For each extracted fact derive its supply chain implication.
Return a JSON array where each object contains:
  {{
    "original_signal_category": category from Pass 1,
    "raw_text_passage": same passage from Pass 1,
    "stated_fact": same fact from Pass 1,
    "signal_sentiment": "positive" "negative" "neutral" or "mixed",
    "signal_strength": "strong" "moderate" or "weak",
    "artemis_implication": specific one-sentence implication for Classic Fashion
      sourcing decisions — be precise about which corridor, which season,
      which product type where relevant. Max 250 characters.,
    "affected_decision": one of "commit_timing" "fob_negotiation"
      "volume_expectation" "factory_booking" "hedge_decision"
      "program_risk" "corridor_selection" or "no_direct_impact",
    "time_horizon": "immediate" "next_quarter" "next_season" or "multi_season",
    "confidence_score": 0.00 to 1.00 based on how directly stated vs inferred
  }}
""".strip()


def _build_pass3_prompt(
    quarter: QuarterContext,
    pass2_results: list[dict[str, Any]],
    prior_signals: list[RetailerIntelligenceExtract],
) -> str:
    prior_summary = [
        {
            "fiscal_year": row.fiscal_year,
            "fiscal_quarter": row.fiscal_quarter,
            "filing_date": row.filing_date.isoformat() if row.filing_date else None,
            "document_type": row.document_type,
            "document_section": row.document_section,
            "signal_category": row.signal_category,
            "extracted_signal": row.extracted_signal,
            "raw_text_passage": (row.raw_text_passage or "")[:200],
            "signal_sentiment": row.signal_sentiment,
            "artemis_implication": row.artemis_implication_full or row.artemis_implication,
        }
        for row in prior_signals
    ]
    return f"""
CURRENT SIGNALS (Q{quarter.fiscal_quarter} FY{quarter.fiscal_year}):
{json.dumps(pass2_results, indent=2)}

PRIOR SIGNALS FROM SAME RETAILER (last 30 entries):
{json.dumps(prior_summary, indent=2)}

For each current signal determine if a similar pattern exists in prior quarters.
Return a JSON array:
  {{
    "signal_category": from current signal,
    "raw_text_passage": from current signal,
    "historical_pattern_found": true or false,
    "similar_prior_quarter": "Q2 FY2024" or null,
    "similar_prior_language": brief description of what was said then or null,
    "observed_outcome": what followed in the next quarter based on financials
      or null if unknown,
    "pattern_confidence": 0.00 to 1.00 or null
  }}
""".strip()


def _match_pass2_fact(
    fact: dict[str, Any],
    pass2_results: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    passage = fact.get("raw_text_passage", "")
    category = fact.get("signal_category")
    for item in pass2_results:
        if item.get("raw_text_passage") == passage:
            return item
        if (
            item.get("original_signal_category") == category
            and item.get("stated_fact") == fact.get("stated_fact")
        ):
            return item
    if index < len(pass2_results):
        return pass2_results[index]
    return {}


def _match_pass3_fact(
    fact: dict[str, Any],
    pass3_results: list[dict[str, Any]],
    index: int,
) -> dict[str, Any]:
    passage = fact.get("raw_text_passage", "")
    category = fact.get("signal_category")
    for item in pass3_results:
        if item.get("raw_text_passage") == passage:
            return item
        if item.get("signal_category") == category and passage[:80] in str(
            item.get("raw_text_passage", "")
        ):
            return item
    if index < len(pass3_results):
        return pass3_results[index]
    return {"historical_pattern_found": False}


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _tokenize_words(text: str) -> set[str]:
    return set(_WORD_TOKEN_RE.findall(text.lower()))


def _extracted_signal_text(pending: PendingSignal) -> str:
    if pending.pass2_ok:
        stated = pending.pass2.get("stated_fact")
        if stated:
            return str(stated)
    return str(pending.fact.get("stated_fact") or "")


def _word_overlap_ratio(text_a: str, text_b: str) -> float:
    words_a = _tokenize_words(text_a)
    words_b = _tokenize_words(text_b)
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def _document_priority(document_type: str) -> int:
    return DOCUMENT_PRIORITY.get(document_type, 99)


def _corroboration_score(evidence_count: int) -> Decimal:
    if evidence_count >= 4:
        return Decimal("1.00")
    if evidence_count == 3:
        return Decimal("0.75")
    if evidence_count == 2:
        return Decimal("0.50")
    return Decimal("0.25")


def _normalize_sentiment(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _sentiment_value(pending: PendingSignal) -> Optional[str]:
    if not pending.pass2_ok:
        return None
    return _normalize_sentiment(pending.pass2.get("signal_sentiment"))


def _sentiments_opposite(left: Optional[str], right: Optional[str]) -> bool:
    if not left or not right:
        return False
    positive = {"positive"}
    negative = {"negative"}
    return (left in positive and right in negative) or (
        left in negative and right in positive
    )


def _cluster_by_similarity(instances: list[PendingSignal]) -> list[list[PendingSignal]]:
    if not instances:
        return []

    parent = list(range(len(instances)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left_idx in range(len(instances)):
        left_text = _extracted_signal_text(instances[left_idx])
        for right_idx in range(left_idx + 1, len(instances)):
            right_text = _extracted_signal_text(instances[right_idx])
            if _word_overlap_ratio(left_text, right_text) > 0.65:
                union(left_idx, right_idx)

    grouped: dict[int, list[PendingSignal]] = {}
    for index, instance in enumerate(instances):
        root = find(index)
        grouped.setdefault(root, []).append(instance)
    return list(grouped.values())


def _select_master_instance(group: list[PendingSignal]) -> PendingSignal:
    def sort_key(pending: PendingSignal) -> tuple[int, int]:
        priority = _document_priority(pending.section.document_type)
        passage_len = len(pending.fact.get("raw_text_passage") or "")
        return (priority, -passage_len)

    return min(group, key=sort_key)


def _group_has_contradiction(group: list[PendingSignal]) -> bool:
    sentiments = {_sentiment_value(item) for item in group}
    sentiments.discard(None)
    return "positive" in sentiments and "negative" in sentiments


def _partition_by_sentiment_polarity(
    group: list[PendingSignal],
) -> list[list[PendingSignal]]:
    positive = [item for item in group if _sentiment_value(item) == "positive"]
    negative = [item for item in group if _sentiment_value(item) == "negative"]
    others = [
        item
        for item in group
        if _sentiment_value(item) not in ("positive", "negative")
    ]
    if positive and negative:
        partitions: list[list[PendingSignal]] = []
        if positive:
            partitions.append(positive)
        if negative:
            partitions.append(negative)
        for item in others:
            partitions.append([item])
        return partitions
    return [group]


def _adjust_implication_for_qa(
    implication: Optional[str],
    group: list[PendingSignal],
) -> Optional[str]:
    if not implication:
        return implication

    qa_management = any(
        pending.section.document_type == "8K_transcript_qa"
        and pending.fact.get("speaker") == "management"
        for pending in group
    )
    if not qa_management:
        return implication

    lowered = implication.lower()
    if "q&a" in lowered or "analyst q" in lowered:
        return implication

    replacements = (
        (
            "management stated in earnings release that",
            "Management confirmed in analyst Q&A that",
        ),
        ("management stated that", "Management confirmed in analyst Q&A that"),
        (
            "management said in prepared remarks that",
            "Management confirmed in analyst Q&A that",
        ),
        ("management said that", "Management confirmed in analyst Q&A that"),
    )
    for old, new in replacements:
        start = lowered.find(old)
        if start != -1:
            return implication[:start] + new + implication[start + len(old) :]

    stripped = implication.rstrip(".")
    if stripped and stripped[0].isupper():
        remainder = stripped[0].lower() + stripped[1:]
    else:
        remainder = stripped
    return f"Management confirmed in analyst Q&A that {remainder}"


def _build_master_payload(
    retailer_id: int,
    quarter: QuarterContext,
    master: PendingSignal,
    group: list[PendingSignal],
    *,
    has_contradiction: bool,
) -> dict[str, Any]:
    filing_date = master.section.filing_date or quarter.filing_date
    evidence_count = len(group)
    implication = (
        master.pass2.get("artemis_implication") if master.pass2_ok else None
    )
    implication = _adjust_implication_for_qa(implication, group)

    return {
        "retailer_id": retailer_id,
        "fiscal_year": quarter.fiscal_year,
        "fiscal_quarter": quarter.fiscal_quarter,
        "period_end_date": quarter.period_end_date,
        "filing_date": filing_date,
        "document_type": master.section.document_type,
        "document_section": master.section.document_section,
        "source_url": master.section.source_url,
        "signal_category": master.fact.get("signal_category"),
        "raw_text_passage": master.fact.get("raw_text_passage"),
        "speaker": master.fact.get("speaker"),
        "is_forward_looking": _to_bool(master.fact.get("is_forward_looking")),
        "contains_number": _to_bool(master.fact.get("contains_number")),
        "number_mentioned": master.fact.get("number_mentioned"),
        "time_period_referenced": master.fact.get("time_period_referenced"),
        "extracted_signal": (
            master.pass2.get("stated_fact") or master.fact.get("stated_fact")
            if master.pass2_ok
            else master.fact.get("stated_fact")
        ),
        "signal_sentiment": master.pass2.get("signal_sentiment")
        if master.pass2_ok
        else None,
        "signal_strength": master.pass2.get("signal_strength")
        if master.pass2_ok
        else None,
        "artemis_implication": implication,
        "affected_decision": master.pass2.get("affected_decision")
        if master.pass2_ok
        else None,
        "time_horizon": master.pass2.get("time_horizon") if master.pass2_ok else None,
        "confidence_score": _to_decimal(master.pass2.get("confidence_score"))
        if master.pass2_ok
        else None,
        "historical_pattern_found": _to_bool(
            master.pass3.get("historical_pattern_found", False)
        ),
        "similar_prior_quarter": master.pass3.get("similar_prior_quarter"),
        "similar_prior_language": master.pass3.get("similar_prior_language"),
        "observed_outcome": master.pass3.get("observed_outcome"),
        "pattern_confidence": _to_decimal(master.pass3.get("pattern_confidence")),
        "extraction_model": EXTRACTION_MODEL,
        "extraction_prompt_ver": EXTRACTION_PROMPT_VER,
        "human_verified": False,
        "evidence_count": evidence_count,
        "corroboration_score": _corroboration_score(evidence_count),
        "has_contradiction": has_contradiction,
        "primary_document_type": master.section.document_type,
        "primary_speaker": master.fact.get("speaker"),
    }


def _write_master_extract(
    db: Session,
    retailer_id: int,
    payload: dict[str, Any],
) -> tuple[RetailerIntelligenceExtract, list[str]]:
    row = RetailerIntelligenceExtract(retailer_id=retailer_id)
    db.add(row)

    model_columns = _model_column_names()
    deferred_fields: list[str] = []
    for field_name in RETAILER_INTELLIGENCE_EXTRACT_UPDATE_FIELDS:
        value = payload.get(field_name)
        if field_name in model_columns:
            setattr(row, field_name, value)
        else:
            deferred_fields.append(field_name)

    row.human_verified = False
    db.flush()
    return row, deferred_fields


def _source_is_sec_filing(document_type: str) -> bool:
    return not document_type.startswith("external_")


def _corroborates_master(
    master_sentiment: Optional[str],
    instance_sentiment: Optional[str],
) -> bool:
    if _sentiments_opposite(master_sentiment, instance_sentiment):
        return False
    if master_sentiment and instance_sentiment:
        return master_sentiment == instance_sentiment
    return True


def _write_evidence_row(
    db: Session,
    extract_id: int,
    retailer_id: int,
    quarter: QuarterContext,
    pending: PendingSignal,
    master_sentiment: Optional[str],
) -> None:
    instance_sentiment = _sentiment_value(pending)
    contradicts = _sentiments_opposite(master_sentiment, instance_sentiment)
    corroborates = _corroborates_master(master_sentiment, instance_sentiment)

    evidence = RetailerSignalEvidence(
        extract_id=extract_id,
        retailer_id=retailer_id,
        fiscal_year=quarter.fiscal_year,
        fiscal_quarter=quarter.fiscal_quarter,
        document_type=pending.section.document_type,
        document_section=pending.section.document_section,
        source_url=pending.section.source_url,
        speaker=pending.fact.get("speaker"),
        raw_text_passage=pending.fact.get("raw_text_passage"),
        is_forward_looking=_to_bool(pending.fact.get("is_forward_looking")),
        contains_number=_to_bool(pending.fact.get("contains_number")),
        number_mentioned=pending.fact.get("number_mentioned"),
        time_period_referenced=pending.fact.get("time_period_referenced"),
        extraction_confidence=_to_decimal(pending.pass2.get("confidence_score"))
        if pending.pass2_ok
        else None,
        document_priority=_document_priority(pending.section.document_type),
        corroborates_master=corroborates,
        contradicts_master=contradicts,
        is_analyst_pressure=pending.fact.get("speaker") == "analyst",
        source_is_sec_filing=_source_is_sec_filing(pending.section.document_type),
    )
    db.add(evidence)


def _clear_quarter_signals(
    db: Session,
    retailer_id: int,
    quarter: QuarterContext,
) -> None:
    extract_ids = [
        row.extract_id
        for row in db.query(RetailerIntelligenceExtract.extract_id)
        .filter(
            RetailerIntelligenceExtract.retailer_id == retailer_id,
            RetailerIntelligenceExtract.fiscal_year == quarter.fiscal_year,
            RetailerIntelligenceExtract.fiscal_quarter == quarter.fiscal_quarter,
        )
        .all()
    ]
    if extract_ids:
        db.query(RetailerSignalEvidence).filter(
            RetailerSignalEvidence.extract_id.in_(extract_ids)
        ).delete(synchronize_session=False)
    db.query(RetailerIntelligenceExtract).filter(
        RetailerIntelligenceExtract.retailer_id == retailer_id,
        RetailerIntelligenceExtract.fiscal_year == quarter.fiscal_year,
        RetailerIntelligenceExtract.fiscal_quarter == quarter.fiscal_quarter,
    ).delete(synchronize_session=False)


def _consolidate_quarter_signals(
    db: Session,
    retailer_id: int,
    quarter: QuarterContext,
    pending_signals: list[PendingSignal],
) -> tuple[int, list[str]]:
    if not pending_signals:
        return 0, []

    _clear_quarter_signals(db, retailer_id, quarter)

    by_category: dict[str, list[PendingSignal]] = {}
    for pending in pending_signals:
        category = pending.fact.get("signal_category")
        if not category:
            continue
        by_category.setdefault(str(category), []).append(pending)

    masters_written = 0
    all_deferred: set[str] = set()

    for instances in by_category.values():
        clusters = _cluster_by_similarity(instances)
        for cluster in clusters:
            has_contradiction = _group_has_contradiction(cluster)
            partitions = (
                _partition_by_sentiment_polarity(cluster)
                if has_contradiction
                else [cluster]
            )
            for partition in partitions:
                master = _select_master_instance(partition)
                payload = _build_master_payload(
                    retailer_id,
                    quarter,
                    master,
                    partition,
                    has_contradiction=has_contradiction,
                )
                row, deferred = _write_master_extract(db, retailer_id, payload)
                all_deferred.update(deferred)
                master_sentiment = _normalize_sentiment(payload.get("signal_sentiment"))
                for pending in partition:
                    _write_evidence_row(
                        db,
                        row.extract_id,
                        retailer_id,
                        quarter,
                        pending,
                        master_sentiment,
                    )
                masters_written += 1

    if all_deferred:
        logger.warning(
            "Deferred schema fields (migration required): %s",
            ", ".join(sorted(all_deferred)),
        )
    return masters_written, sorted(all_deferred)


def _serialize_extract(row: RetailerIntelligenceExtract) -> dict[str, Any]:
    return {
        "extract_id": row.extract_id,
        "retailer_id": row.retailer_id,
        "fiscal_year": row.fiscal_year,
        "fiscal_quarter": row.fiscal_quarter,
        "period_end_date": row.period_end_date.isoformat()
        if row.period_end_date
        else None,
        "filing_date": row.filing_date.isoformat() if row.filing_date else None,
        "document_type": row.document_type,
        "document_section": row.document_section,
        "source_url": row.source_url,
        "signal_category": row.signal_category,
        "raw_text_passage": row.raw_text_passage,
        "extracted_signal": row.extracted_signal,
        "signal_sentiment": row.signal_sentiment,
        "signal_strength": row.signal_strength,
        "artemis_implication": row.artemis_implication_full or row.artemis_implication,
        "confidence_score": str(row.confidence_score)
        if row.confidence_score is not None
        else None,
        "speaker": row.speaker,
        "is_forward_looking": row.is_forward_looking,
        "contains_number": row.contains_number,
        "number_mentioned": row.number_mentioned,
        "time_period_referenced": row.time_period_referenced,
        "affected_decision": row.affected_decision,
        "time_horizon": row.time_horizon,
        "historical_pattern_found": row.historical_pattern_found,
        "similar_prior_quarter": row.similar_prior_quarter,
        "similar_prior_language": row.similar_prior_language,
        "observed_outcome": row.observed_outcome,
        "pattern_confidence": str(row.pattern_confidence)
        if row.pattern_confidence is not None
        else None,
        "evidence_count": row.evidence_count,
        "corroboration_score": str(row.corroboration_score)
        if row.corroboration_score is not None
        else None,
        "has_contradiction": row.has_contradiction,
        "primary_document_type": row.primary_document_type,
        "primary_speaker": row.primary_speaker,
    }


def _serialize_evidence(row: RetailerSignalEvidence) -> dict[str, Any]:
    return {
        "evidence_id": row.evidence_id,
        "extract_id": row.extract_id,
        "document_type": row.document_type,
        "document_section": row.document_section,
        "source_url": row.source_url,
        "speaker": row.speaker,
        "raw_text_passage": row.raw_text_passage,
        "is_forward_looking": row.is_forward_looking,
        "contains_number": row.contains_number,
        "number_mentioned": row.number_mentioned,
        "time_period_referenced": row.time_period_referenced,
        "extraction_confidence": str(row.extraction_confidence)
        if row.extraction_confidence is not None
        else None,
        "document_priority": row.document_priority,
        "corroborates_master": row.corroborates_master,
        "contradicts_master": row.contradicts_master,
        "is_analyst_pressure": row.is_analyst_pressure,
        "source_is_sec_filing": row.source_is_sec_filing,
    }


def get_signal_evidence(
    extract_id: int,
    db: Session,
) -> Optional[dict[str, Any]]:
    master = (
        db.query(RetailerIntelligenceExtract)
        .filter(RetailerIntelligenceExtract.extract_id == extract_id)
        .first()
    )
    if master is None:
        return None

    evidence_rows = (
        db.query(RetailerSignalEvidence)
        .filter(RetailerSignalEvidence.extract_id == extract_id)
        .order_by(
            RetailerSignalEvidence.document_priority,
            RetailerSignalEvidence.evidence_id,
        )
        .all()
    )
    return {
        "master_signal": _serialize_extract(master),
        "evidence_chain": [_serialize_evidence(row) for row in evidence_rows],
        "evidence_count": master.evidence_count,
        "corroboration_score": str(master.corroboration_score)
        if master.corroboration_score is not None
        else None,
        "has_contradiction": master.has_contradiction,
    }


def _query_prior_signals(
    db: Session,
    retailer_id: int,
    quarter: QuarterContext,
) -> list[RetailerIntelligenceExtract]:
    return (
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


def _process_document_section(
    db: Session,
    client: Anthropic,
    retailer_id: int,
    quarter: QuarterContext,
    section: DocumentSection,
) -> tuple[DocumentRunSummary, list[PendingSignal]]:
    quarter_label = f"FY{quarter.fiscal_year} Q{quarter.fiscal_quarter}"
    summary = DocumentRunSummary(
        document=section.document_type,
        quarter=quarter_label,
    )

    if not section.text.strip():
        summary.notes = "empty document text"
        return summary, []

    pass1_prompt = _build_pass1_prompt(section, quarter)
    pass1_raw = _call_claude(client, PASS1_SYSTEM_PROMPT, pass1_prompt, max_tokens=4000)
    if pass1_raw is None:
        summary.pass1_status = "failed"
        summary.notes = "Pass 1 Claude call failed"
        logger.error(
            "Pass 1 failed for %s %s — skipping document",
            section.document_type,
            quarter_label,
        )
        return summary, []

    try:
        pass1_facts = _parse_json_array(pass1_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        summary.pass1_status = "failed"
        summary.notes = f"Pass 1 JSON parse failed: {exc}"
        logger.error(
            "Pass 1 parse failed for %s %s — skipping document",
            section.document_type,
            quarter_label,
        )
        return summary, []

    summary.pass1_status = "ok"
    summary.pass1_signals = len(pass1_facts)
    if not pass1_facts:
        summary.notes = "Pass 1 returned no signals"
        return summary, []

    pass2_prompt = _build_pass2_prompt(section, quarter, pass1_facts)
    pass2_raw = _call_claude(client, PASS2_SYSTEM_PROMPT, pass2_prompt, max_tokens=4000)
    pass2_results: list[dict[str, Any]] = []
    pass2_ok = False
    if pass2_raw is None:
        summary.pass2_status = "failed"
        logger.error("Pass 2 failed for %s %s — storing Pass 1 only", section.document_type, quarter_label)
    else:
        try:
            pass2_results = _parse_json_array(pass2_raw)
            pass2_ok = True
            summary.pass2_status = "ok"
            summary.pass2_implications = len(pass2_results)
        except (json.JSONDecodeError, ValueError) as exc:
            summary.pass2_status = "failed"
            logger.error(
                "Pass 2 parse failed for %s %s — storing Pass 1 only: %s",
                section.document_type,
                quarter_label,
                exc,
            )

    prior_signals = _query_prior_signals(db, retailer_id, quarter)
    pass3_results: list[dict[str, Any]] = []
    pass3_ok = False
    if not prior_signals:
        summary.pass3_status = "skipped"
        summary.notes = (summary.notes + "; " if summary.notes else "") + "no prior signals"
        logger.info(
            "Pass 3 skipped for %s %s — no prior retailer_intelligence_extract rows",
            section.document_type,
            quarter_label,
        )
    else:
        pass3_prompt = _build_pass3_prompt(quarter, pass2_results or pass1_facts, prior_signals)
        pass3_raw = _call_claude(client, PASS3_SYSTEM_PROMPT, pass3_prompt, max_tokens=2000)
        if pass3_raw is None:
            summary.pass3_status = "failed"
            logger.error(
                "Pass 3 failed for %s %s — storing without historical fields",
                section.document_type,
                quarter_label,
            )
        else:
            try:
                pass3_results = _parse_json_array(pass3_raw)
                pass3_ok = True
                summary.pass3_status = "ok"
                summary.pass3_patterns = sum(
                    1
                    for item in pass3_results
                    if _to_bool(item.get("historical_pattern_found"))
                )
            except (json.JSONDecodeError, ValueError) as exc:
                summary.pass3_status = "failed"
                logger.error(
                    "Pass 3 parse failed for %s %s — storing without historical fields: %s",
                    section.document_type,
                    quarter_label,
                    exc,
                )

    pending_signals: list[PendingSignal] = []
    for index, fact in enumerate(pass1_facts):
        pass2 = _match_pass2_fact(fact, pass2_results, index) if pass2_ok else {}
        pass3 = (
            _match_pass3_fact(fact, pass3_results, index)
            if pass3_ok
            else {"historical_pattern_found": False}
        )
        pending_signals.append(
            PendingSignal(
                section=section,
                fact=fact,
                pass2=pass2,
                pass3=pass3,
                pass2_ok=pass2_ok,
                pass3_ok=pass3_ok,
            )
        )

    logger.info(
        "Extracted %d pending signal(s) for %s %s (Pass1=%s Pass2=%s Pass3=%s)",
        len(pending_signals),
        section.document_type,
        quarter_label,
        summary.pass1_status,
        summary.pass2_status,
        summary.pass3_status,
    )
    return summary, pending_signals


def _collect_quarter_documents(
    submissions: dict[str, Any],
    quarter: QuarterContext,
) -> list[DocumentSection]:
    documents: list[DocumentSection] = []

    earnings = _find_earnings_release_8k(submissions, quarter)
    if earnings:
        documents.append(earnings)
        transcript_pair = _find_transcript_sections(submissions, earnings)
        if transcript_pair:
            remarks, qa = transcript_pair
            if remarks.text.strip():
                documents.append(remarks)
            if qa.text.strip():
                documents.append(qa)

    mda = _find_10q_mda(quarter)
    if mda:
        documents.append(mda)

    return documents


def run_target_tier2_ingestion(
    db: Session,
    quarter_count: int = 1,
) -> list[DocumentRunSummary]:
    retailer_id = _get_target_retailer_id(db)
    if retailer_id is None:
        return []

    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is not set — cannot run Tier 2 extraction")
        return []

    submissions = _sec_get(_SUBMISSIONS_URL)
    if not isinstance(submissions, dict):
        logger.error("Failed to fetch Target SEC submissions")
        return []

    quarters = _load_quarter_contexts(db, retailer_id, quarter_count)
    if not quarters:
        logger.error(
            "No retailer_financials rows for Target — run target_tier1_ingestion.py first"
        )
        return []

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    summaries: list[DocumentRunSummary] = []

    for quarter in quarters:
        quarter_label = f"FY{quarter.fiscal_year} Q{quarter.fiscal_quarter}"
        logger.info(
            "Processing %s (period_end=%s)",
            quarter_label,
            quarter.period_end_date,
        )
        documents = _collect_quarter_documents(submissions, quarter)
        if not documents:
            logger.warning("No documents found for %s", quarter_label)
            continue

        quarter_pending: list[PendingSignal] = []
        for section in documents:
            summary, pending = _process_document_section(
                db,
                client,
                retailer_id,
                quarter,
                section,
            )
            summaries.append(summary)
            quarter_pending.extend(pending)

        if quarter_pending:
            masters_written, deferred = _consolidate_quarter_signals(
                db,
                retailer_id,
                quarter,
                quarter_pending,
            )
            db.commit()
            logger.info(
                "Consolidated %d pending signal(s) into %d master record(s) for %s",
                len(quarter_pending),
                masters_written,
                quarter_label,
            )
            if deferred:
                logger.warning(
                    "Quarter %s deferred schema fields: %s",
                    quarter_label,
                    ", ".join(deferred),
                )
        else:
            logger.warning(
                "No pending signals to consolidate for %s",
                quarter_label,
            )

    return summaries


def print_summary_table(summaries: list[DocumentRunSummary]) -> None:
    header = (
        f"{'Document':<24} | {'Quarter':<12} | {'Pass1':<6} | "
        f"{'Pass2':<6} | {'Pass3':<6} | {'Status'}"
    )
    print(header)
    print("-" * len(header))
    for row in summaries:
        status = f"P1:{row.pass1_status} P2:{row.pass2_status} P3:{row.pass3_status}"
        if row.notes:
            status = f"{status} ({row.notes})"
        print(
            f"{row.document:<24} | {row.quarter:<12} | "
            f"{row.pass1_signals:<6} | {row.pass2_implications:<6} | "
            f"{row.pass3_patterns:<6} | {status}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Target Tier 2 narrative intelligence extraction via Claude API"
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
        logger.info(
            "Starting Target Tier 2 ingestion for %d quarter(s)",
            args.quarters,
        )
        summaries = run_target_tier2_ingestion(db, quarter_count=args.quarters)
        if not summaries:
            logger.error("No documents processed")
            return 1
        print_summary_table(summaries)
        logger.info(
            "Target Tier 2 ingestion complete — %d document section(s) processed",
            len(summaries),
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""
Earnings call transcript ingestion for Target and Walmart.

Parses prepared remarks and Q&A, extracts supply-chain intelligence signals via
Claude API, and writes append-only rows to retailer_intelligence_extract and
retailer_signal_evidence.

Requires ANTHROPIC_API_KEY in environment.
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
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional, Union

from anthropic import Anthropic
from sqlalchemy.orm import Session

from database.base import SessionLocal, mark_latest
from database.ingestion_context import IngestionContext
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

SOURCE_NAME = "earnings_transcript_ingestion"
SCRIPT_VERSION = "1.0.0"
DOCUMENT_TYPE = "earnings_call_transcript"
EXTRACTION_MODEL = "claude-sonnet-4-6"
EXTRACTION_PROMPT_VER = "v2.0"
CLAUDE_RATE_LIMIT_SECONDS = 0.5
MIN_PASSAGE_SENTENCES = 2
MAX_PASSAGE_SENTENCES = 4

ALLOWED_RETAILER_IDS = {1, 2}  # Target Corporation, Walmart Inc

SIGNAL_CATEGORIES = frozenset({
    "apparel_sales_performance",
    "inventory_positioning",
    "forward_guidance",
    "tariff_and_sourcing_geography",
    "margin_pressure",
    "consumer_demand",
    "channel_mix",
    "vendor_supply_chain",
    "analyst_pressure",
    "retailer_strategy",
    "store_expansion",
    "fulfillment_requirements",
    "pricing_pressure",
    "program_risk",
})

SIGNAL_SENTIMENTS = frozenset({"positive", "negative", "neutral", "mixed"})
SIGNAL_STRENGTHS = frozenset({"strong", "moderate", "weak"})
AFFECTED_DECISIONS = frozenset({
    "commodity_hedge_timing",
    "yarn_procurement",
    "factory_allocation",
    "capacity_planning",
    "freight_booking",
    "fx_hedge",
    "program_acceptance",
    "pricing_negotiation",
    "inventory_risk",
    "vendor_qualification",
    "compliance_posture",
    "store_program_sizing",
})

SYSTEM_PROMPT = (
    "You are the retail intelligence layer of Artemis — the operating system "
    "that apparel operators use to orchestrate their entire supply chain from "
    "cotton to consumer. Your reader is the CEO and supply chain leadership "
    "of a large apparel manufacturer like Classic Fashion, which produces "
    "hundreds of millions of garments annually for retailers like Walmart and "
    "Target. Artemis gives these operators complete visibility across every "
    "layer of their supply chain — commodity, yarn, fabric, factory, freight, "
    "compliance, and retail — and the ability to take action on that visibility. "
    "The retail intelligence layer is the top of this chain. What a retailer "
    "says on an earnings call flows downstream through every layer, affecting "
    "program volumes, factory capacity, yarn procurement timing, and raw "
    "material hedge decisions. "
    "\n\n"
    "Your job is to read one passage from a retailer earnings call and extract "
    "any signal that gives the apparel operator deeper intelligence about their "
    "retail partner. The financial statements show the numbers. Your job is to "
    "extract the color, context, strategy, and real-world texture that only "
    "the transcript provides — the things that change what an operator should "
    "do before the numbers confirm it. "
    "\n\n"
    "EXTRACT a signal if the passage contains any of the following: "
    "\n\n"
    "RETAILER PERFORMANCE IN FASHION AND APPAREL: "
    "Any mention of apparel, accessories, clothing, softlines, fashion, or "
    "active and athletic wear performance — sales growth or decline, "
    "sell-through rates, comparable sales in the category, market share, "
    "customer engagement with fashion, seasonal performance, or category "
    "momentum. This is the primary signal. Extract every apparel and fashion "
    "reference without exception. "
    "\n\n"
    "CUSTOMER BEHAVIOR AS IT AFFECTS FASHION BUYING: "
    "How the retailer's customers are shopping — trade-down or trade-up "
    "behavior, basket composition shifts, frequency of fashion purchases, "
    "response to promotions in apparel, value-seeking behavior affecting "
    "discretionary categories, or any signal about whether consumers are "
    "buying more or less fashion. Extract consumer behavior signals only "
    "when they directly affect fashion and apparel category spending. "
    "\n\n"
    "INVENTORY HEALTH AND SELL-THROUGH: "
    "Retailer inventory levels overall and specifically in apparel and fashion "
    "— days on hand, excess stock, lean positioning, markdown rates, clearance "
    "activity, sell-through rates by category, inventory improvement or "
    "deterioration. A retailer destocking apparel is a cancellation risk "
    "signal that flows through every downstream layer. A retailer with lean "
    "apparel inventory is a replenishment opportunity. "
    "\n\n"
    "STORE EXPANSION AND RETAIL FOOTPRINT: "
    "New store openings, closures, remodels, new format launches, or square "
    "footage changes — especially when connected to apparel floor space or "
    "fashion assortment depth. More stores means more programs. Fewer stores "
    "means less volume. This affects factory capacity allocation directly. "
    "\n\n"
    "RETAIL STRATEGY AND TECHNOLOGY IN FASHION: "
    "New strategies the retailer is implementing — AI-driven personalization, "
    "algorithmic buying, trend forecasting technology, digital fashion "
    "experiences, virtual try-on, or any use of technology that changes how "
    "the retailer buys, positions, or sells fashion. These signals indicate "
    "where the retailer is heading and what they will demand from suppliers "
    "in terms of speed, flexibility, data, and product responsiveness. "
    "\n\n"
    "FORWARD GUIDANCE ON FASHION AND VOLUME: "
    "Management statements about future buying plans, expected fashion category "
    "performance, vendor strategy, or any forward-looking language about "
    "apparel investment, program expansion, or volume expectations. Forward "
    "signals are the most valuable — they create the longest window for "
    "operators to act on the downstream supply chain implications. "
    "\n\n"
    "ANALYST PRESSURE ON FASHION PERFORMANCE: "
    "Any analyst question challenging management on apparel performance, "
    "fashion category strategy, inventory management in apparel, or vendor "
    "relationships. These questions reveal what sophisticated investors are "
    "worried about that management is not volunteering — often the earliest "
    "signal of problems that will flow downstream to supplier programs. "
    "\n\n"
    "SOURCING AND VENDOR SIGNALS: "
    "Any mention of supplier relationships, vendor base changes, sourcing "
    "geography shifts, direct sourcing expansion, private label versus national "
    "brand strategy in apparel, lead time expectations, or compliance "
    "requirements affecting apparel suppliers. "
    "\n\n"
    "TARIFF AND TRADE POLICY: "
    "Any mention of tariffs, trade policy changes, country of origin "
    "requirements, Section 301, de minimis rules, or nearshoring that affects "
    "apparel imports. These signals change corridor economics and compliance "
    "requirements across the entire supply chain. "
    "\n\n"
    "MARGIN AND PRICING SIGNALS: "
    "Gross margin trends, promotional intensity in apparel, markdown depth in "
    "fashion categories, or pricing strategy changes that signal what FOB "
    "prices the retailer will accept and how much cost pressure they will "
    "transmit to apparel suppliers. "
    "\n\n"
    "DO NOT EXTRACT if the passage is primarily about: "
    "Food, grocery, beverage, or pharmacy category performance with no "
    "connection to apparel or discretionary spending impact. "
    "Beauty, cosmetics, skincare, or personal care — unless explicitly stated "
    "to be displacing apparel floor space or reducing apparel open-to-buy. "
    "Home furnishings, furniture, or home decor — unless explicitly displacing "
    "apparel investment or floor space. "
    "Electronics, hardlines, or toys with no apparel connection. "
    "Store construction, parking, or real estate details unrelated to apparel "
    "floor space or fashion assortment. "
    "Executive compensation, board governance, or shareholder return language. "
    "Generic motivational statements with no operational content — excitement "
    "about momentum, pride in teams, or aspirational language with no "
    "specific operational signal. "
    "Legal boilerplate, safe harbor disclaimers, or document section headers. "
    "Third-party partnerships in non-apparel categories — beauty shop-in-shops, "
    "electronics partnerships, food service integrations — unless they "
    "explicitly state that apparel floor space is being reduced or "
    "open-to-buy is being shifted away from apparel. "
    "\n\n"
    "RELEVANCE TEST: Before extracting, ask — does this passage give Classic "
    "Fashion's CEO a more sophisticated understanding of how Walmart or Target "
    "is performing in fashion and apparel, how their customers are behaving "
    "toward fashion, how their apparel inventory is positioned, where they are "
    "opening stores, or where their retail strategy is heading in ways that "
    "affect apparel programs? If yes, extract. If no — return null. "
    "\n\n"
    "IMPLICATION STANDARD: The artemis_implication must trace the full "
    "downstream consequence chain from this retail signal through every "
    "affected layer of the supply chain. Start at the retail layer — what "
    "does this signal mean for program volumes or cancellation risk. Then "
    "move to the factory layer — what does that mean for capacity utilization "
    "and production scheduling. Then move to the material layer — what does "
    "that mean for yarn procurement timing or fabric commitment decisions. "
    "Then move to the commodity layer — what does that mean for cotton hedge "
    "positioning or polyester exposure. Not every signal will touch all four "
    "layers — but the implication must go as deep as the signal warrants. "
    "Generic statements like 'signals continued demand for apparel' are not "
    "acceptable. Every implication must be a chain of consequences, not a "
    "single observation. "
    "Example of unacceptable: 'Target apparel demand signals stable buying.' "
    "Example of acceptable: 'Target apparel comp sales decline of 3.9% signals "
    "open-to-buy compression in Q3 — Classic Fashion should flag programs "
    "scheduled for H2 delivery as at risk, hold yarn commitments above "
    "confirmed PO coverage, and reduce cotton hedge exposure until the next "
    "quarter confirms recovery or further decline.' "
    "\n\n"
    "Return JSON with exactly these fields: "
    "signal_category (one of: apparel_sales_performance, inventory_positioning, "
    "forward_guidance, tariff_and_sourcing_geography, margin_pressure, "
    "consumer_demand, channel_mix, vendor_supply_chain, analyst_pressure, "
    "retailer_strategy, store_expansion, fulfillment_requirements, "
    "pricing_pressure, program_risk), "
    "signal_sentiment (positive/negative/neutral/mixed), "
    "signal_strength (strong/moderate/weak), "
    "confidence_score (0.0-1.0 — use below 0.5 only when the signal is "
    "genuinely ambiguous or indirect), "
    "is_forward_looking (true/false), "
    "is_analyst_pressure (true/false — true only if an analyst is explicitly "
    "challenging management on a specific risk or performance gap), "
    "affected_decision (one of: commodity_hedge_timing, yarn_procurement, "
    "factory_allocation, capacity_planning, freight_booking, fx_hedge, "
    "program_acceptance, pricing_negotiation, inventory_risk, "
    "vendor_qualification, compliance_posture, store_program_sizing), "
    "artemis_implication (one to two sentences — a chain of consequences "
    "tracing from this retail signal downstream through program risk, factory "
    "capacity, material procurement, and commodity exposure as far as the "
    "signal warrants — specific, operational, actionable), "
    "extracted_signal (one sentence: what this passage actually says in plain "
    "language, no interpretation). "
    "Return ONLY valid JSON with no markdown fences or preamble. "
    "If the passage fails the relevance test, return "
    '{"signal_category": null}.'
)

_QA_SPLIT_RE = re.compile(
    r"(?:Question[\-\s]and[\-\s]Answer(?:\s+Session)?|Q\s*&\s*A\s+Session)",
    re.I,
)
_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+")
_NUMBER_RE = re.compile(r"\d|(?:%|\$|billion|million|\bB\b|\bM\b)", re.I)

_SPEAKER_WITH_TITLE_RE = re.compile(
    r"^([A-Z][\w\.\'\-]+(?:\s+[A-Z][\w\.\'\-]+)*)\s*[-–—,]\s*(.+)$"
)
_SPEAKER_WITH_FIRM_RE = re.compile(
    r"^([A-Z][\w\.\'\-]+(?:\s+[A-Z][\w\.\'\-]+)*)\s+(?:with|from|at)\s+(.+)$",
    re.I,
)
_OPERATOR_RE = re.compile(r"^operator\.?$", re.I)


@dataclass
class TranscriptPassage:
    section: str  # prepared_remarks | qa
    speaker_name: str
    speaker_role: str  # CEO, CFO, analyst, operator, management
    text: str
    is_analyst_pressure: bool = False


@dataclass
class IngestionStats:
    passages_processed: int = 0
    signals_extracted: int = 0
    analyst_pressure_signals: int = 0
    contradictions_found: int = 0


@dataclass
class QuarterDates:
    period_end_date: Optional[date] = None
    filing_date: Optional[date] = None


def _truncate(value: Optional[str], max_len: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


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


def parse_claude_json(text: str) -> Any:
    text = re.sub(r"^```json\s*", "", text.strip())
    text = re.sub(r"^```\s*", "", text.strip())
    text = re.sub(r"```$", "", text.strip())
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


def _call_claude(
    client: Anthropic,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 1024,
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


def _classify_role(name: str, title: Optional[str], section: str) -> str:
    if _OPERATOR_RE.match(name.strip()):
        return "operator"
    combined = f"{name} {title or ''}".lower()
    if "chief executive" in combined or re.search(r"\bceo\b", combined):
        return "CEO"
    if "chief financial" in combined or re.search(r"\bcfo\b", combined):
        return "CFO"
    if "analyst" in combined or (section == "qa" and title and "analyst" in title.lower()):
        return "analyst"
    if section == "qa" and title and not any(
        token in title.lower()
        for token in ("chief", "president", "officer", "director", "executive")
    ):
        return "analyst"
    return "management"


def _parse_speaker_header(line: str) -> Optional[tuple[str, Optional[str]]]:
    stripped = line.strip()
    if not stripped:
        return None
    if _OPERATOR_RE.match(stripped):
        return ("Operator", "Operator")
    match = _SPEAKER_WITH_TITLE_RE.match(stripped)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    match = _SPEAKER_WITH_FIRM_RE.match(stripped)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    if len(stripped.split()) <= 5 and stripped[0].isupper() and stripped.endswith(":"):
        return stripped.rstrip(":").strip(), None
    return None


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.findall(text) if part.strip()]


def _chunk_passages(text: str) -> list[str]:
    sentences = _split_sentences(text)
    if len(sentences) < MIN_PASSAGE_SENTENCES:
        return []
    if len(sentences) <= 6:
        return [" ".join(sentences)]
    passages: list[str] = []
    index = 0
    while index < len(sentences):
        remaining = len(sentences) - index
        if remaining < MIN_PASSAGE_SENTENCES:
            if passages:
                passages[-1] = f"{passages[-1]} {' '.join(sentences[index:])}"
            break
        chunk_size = min(MAX_PASSAGE_SENTENCES, remaining)
        if remaining - chunk_size == 1:
            chunk_size = remaining
        passages.append(" ".join(sentences[index : index + chunk_size]))
        index += chunk_size
    return passages


def _split_transcript(text: str) -> tuple[str, str]:
    match = _QA_SPLIT_RE.search(text)
    if not match:
        return text.strip(), ""
    return text[: match.start()].strip(), text[match.end() :].strip()


def _parse_section(section_name: str, text: str) -> list[TranscriptPassage]:
    if not text.strip():
        return []

    lines = text.splitlines()
    passages: list[TranscriptPassage] = []
    current_name = "Unknown"
    current_title: Optional[str] = None
    current_role = "management"
    buffer: list[str] = []

    def flush_buffer() -> None:
        nonlocal buffer
        body = " ".join(part.strip() for part in buffer if part.strip())
        buffer = []
        if not body:
            return
        if current_role == "operator":
            return
        for chunk in _chunk_passages(body):
            is_pressure = current_role == "analyst"
            passages.append(
                TranscriptPassage(
                    section=section_name,
                    speaker_name=current_name,
                    speaker_role=current_role,
                    text=chunk,
                    is_analyst_pressure=is_pressure,
                )
            )

    for line in lines:
        header = _parse_speaker_header(line)
        if header and len(line.strip()) < 120:
            flush_buffer()
            current_name, current_title = header
            current_role = _classify_role(current_name, current_title, section_name)
            continue
        buffer.append(line)

    flush_buffer()
    return passages


def parse_transcript(text: str) -> list[TranscriptPassage]:
    remarks, qa = _split_transcript(text)
    prep_passages = _parse_section("prepared_remarks", remarks)
    qa_passages = _parse_section("qa", qa)
    passages = prep_passages
    passages.extend(qa_passages)
    return passages


def _contains_number(text: str) -> bool:
    return bool(_NUMBER_RE.search(text))


def _normalize_category(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in SIGNAL_CATEGORIES else None


def _normalize_sentiment(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in SIGNAL_SENTIMENTS else None


def _normalize_strength(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in SIGNAL_STRENGTHS else None


def _normalize_affected_decision(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in AFFECTED_DECISIONS else None


def _sentiments_opposite(left: Optional[str], right: Optional[str]) -> bool:
    if not left or not right:
        return False
    positive = {"positive"}
    negative = {"negative"}
    return (left in positive and right in negative) or (
        left in negative and right in positive
    )


def _resolve_is_analyst_pressure(
    passage: TranscriptPassage,
    signal: dict[str, Any],
) -> bool:
    """
    Resolve analyst-pressure consistently for stats and evidence writes.

    Passage role and analyst_pressure category take precedence over Claude's
    is_analyst_pressure when Claude returns false for an analyst Q&A passage.
    """
    if passage.is_analyst_pressure:
        return True
    if signal.get("signal_category") == "analyst_pressure":
        return True
    return bool(_to_bool(signal.get("is_analyst_pressure")))


def extract_signal_from_passage(
    client: Anthropic,
    passage: TranscriptPassage,
) -> Optional[dict[str, Any]]:
    user_prompt = (
        f"Speaker: {passage.speaker_name} ({passage.speaker_role})\n"
        f"Section: {passage.section}\n"
        f"Passage:\n{passage.text}"
    )
    raw = _call_claude(client, SYSTEM_PROMPT, user_prompt)
    if not raw:
        return None
    try:
        payload = parse_claude_json(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse Claude JSON for passage: %s", passage.text[:80])
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("signal_category") is None:
        return None
    category = _normalize_category(payload.get("signal_category"))
    if not category:
        return None
    payload["signal_category"] = category
    payload["signal_sentiment"] = _normalize_sentiment(payload.get("signal_sentiment"))
    payload["signal_strength"] = _normalize_strength(payload.get("signal_strength"))
    payload["affected_decision"] = _normalize_affected_decision(
        payload.get("affected_decision")
    )
    return payload


def _load_quarter_dates(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
) -> QuarterDates:
    row = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.fiscal_year == fiscal_year,
            RetailerFinancials.fiscal_quarter == fiscal_quarter,
            RetailerFinancials.is_latest.is_(True),
        )
        .first()
    )
    if not row:
        return QuarterDates()
    return QuarterDates(
        period_end_date=row.period_end_date,
        filing_date=row.filing_date,
    )


def _demote_prior_transcript_rows(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
) -> None:
    """Demote prior transcript rows for this quarter before inserting new ones."""
    extract_filter = {
        "retailer_id": retailer_id,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "document_type": DOCUMENT_TYPE,
    }
    mark_latest(db, RetailerIntelligenceExtract, extract_filter)
    db.flush()
    mark_latest(db, RetailerSignalEvidence, extract_filter)
    db.flush()


def _detect_contradictions(
    rows: list[tuple[str, Optional[str]]],
) -> tuple[int, set[int]]:
    contradictions = 0
    flagged: set[int] = set()
    by_category: dict[str, list[tuple[int, Optional[str]]]] = {}
    for index, (category, sentiment) in enumerate(rows):
        by_category.setdefault(category, []).append((index, sentiment))
    for items in by_category.values():
        for left_idx in range(len(items)):
            for right_idx in range(left_idx + 1, len(items)):
                left_sentiment = items[left_idx][1]
                right_sentiment = items[right_idx][1]
                if _sentiments_opposite(left_sentiment, right_sentiment):
                    contradictions += 1
                    flagged.add(items[left_idx][0])
                    flagged.add(items[right_idx][0])
    return contradictions, flagged


def _write_extract_and_evidence(
    db: Session,
    *,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
    quarter_dates: QuarterDates,
    passage: TranscriptPassage,
    signal: dict[str, Any],
    has_contradiction: bool,
    source_url: Optional[str] = None,
) -> None:
    is_forward_looking = _to_bool(signal.get("is_forward_looking"))
    is_analyst_pressure = _resolve_is_analyst_pressure(passage, signal)

    implication_raw = signal.get("artemis_implication")
    implication_full = (
        str(implication_raw).strip() if implication_raw is not None else None
    )

    extract = RetailerIntelligenceExtract(
        retailer_id=retailer_id,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        period_end_date=quarter_dates.period_end_date,
        filing_date=quarter_dates.filing_date,
        document_type=DOCUMENT_TYPE,
        document_section=passage.section,
        source_url=source_url,
        signal_category=signal["signal_category"],
        raw_text_passage=passage.text,
        extracted_signal=_truncate(signal.get("extracted_signal"), 500),
        signal_sentiment=_truncate(signal.get("signal_sentiment"), 20),
        signal_strength=_truncate(signal.get("signal_strength"), 20),
        artemis_implication=_truncate(implication_raw, 500),
        artemis_implication_full=implication_full,
        affected_decision=_truncate(signal.get("affected_decision"), 50),
        confidence_score=_to_decimal(signal.get("confidence_score")),
        speaker=_truncate(passage.speaker_name, 20),
        is_forward_looking=is_forward_looking,
        contains_number=_contains_number(passage.text),
        extraction_model=EXTRACTION_MODEL,
        extraction_prompt_ver=EXTRACTION_PROMPT_VER,
        human_verified=False,
        evidence_count=1,
        has_contradiction=has_contradiction,
        primary_document_type=DOCUMENT_TYPE,
        primary_speaker=_truncate(passage.speaker_role, 20),
        is_latest=True,
        pulled_at=datetime.utcnow(),
    )
    db.add(extract)
    db.flush()

    evidence = RetailerSignalEvidence(
        extract_id=extract.extract_id,
        retailer_id=retailer_id,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        period_end_date=quarter_dates.period_end_date,
        document_type=DOCUMENT_TYPE,
        document_section=passage.section,
        source_url=source_url,
        speaker=_truncate(passage.speaker_name, 20),
        raw_text_passage=passage.text,
        is_forward_looking=is_forward_looking,
        contains_number=_contains_number(passage.text),
        extraction_confidence=_to_decimal(signal.get("confidence_score")),
        document_priority=10,
        corroborates_master=not has_contradiction,
        contradicts_master=has_contradiction,
        is_analyst_pressure=is_analyst_pressure,
        source_is_sec_filing=False,
        is_latest=True,
        pulled_at=datetime.utcnow(),
    )
    db.add(evidence)


def process_transcript(
    transcript: Union[str, os.PathLike[str]],
    *,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
    db: Optional[Session] = None,
    source_url: Optional[str] = None,
    client: Optional[Anthropic] = None,
) -> IngestionStats:
    """
    Process an earnings transcript from a raw string or .txt file path.
    """
    if isinstance(transcript, (str, os.PathLike)) and os.path.isfile(transcript):
        with open(transcript, encoding="utf-8") as handle:
            text = handle.read()
    elif isinstance(transcript, str):
        text = transcript
    else:
        raise TypeError("transcript must be a file path or raw string")

    owns_session = db is None
    db = db or SessionLocal()
    stats = IngestionStats()
    claude_client = client or Anthropic()

    try:
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=source_url,
            db=db,
        ) as ctx:
            if retailer_id not in ALLOWED_RETAILER_IDS:
                ctx.set_failed(
                    f"retailer_id {retailer_id} is not supported; "
                    "use Target (1) or Walmart (2)"
                )
                raise ValueError(
                    f"retailer_id {retailer_id} is not supported; "
                    "use Target (1) or Walmart (2)"
                )

            retailer = (
                db.query(MajorRetailers)
                .filter(MajorRetailers.retailer_id == retailer_id)
                .first()
            )
            if not retailer:
                ctx.set_failed(f"retailer_id {retailer_id} not found in major_retailers")
                raise ValueError(
                    f"retailer_id {retailer_id} not found in major_retailers"
                )

            passages = parse_transcript(text)
            stats.passages_processed = len(passages)
            if not passages:
                logger.warning("No qualifying passages found in transcript")
                return stats

            quarter_dates = _load_quarter_dates(
                db, retailer_id, fiscal_year, fiscal_quarter
            )
            ctx.set_as_of_date(quarter_dates.period_end_date)

            pending_rows: list[tuple[TranscriptPassage, dict[str, Any]]] = []
            category_sentiments: list[tuple[str, Optional[str]]] = []

            for passage in passages:
                signal = extract_signal_from_passage(claude_client, passage)
                if not signal:
                    continue
                pending_rows.append((passage, signal))
                category_sentiments.append(
                    (signal["signal_category"], signal.get("signal_sentiment"))
                )

            stats.signals_extracted = len(pending_rows)
            stats.analyst_pressure_signals = sum(
                1
                for passage, signal in pending_rows
                if _resolve_is_analyst_pressure(passage, signal)
            )

            contradictions, flagged = _detect_contradictions(category_sentiments)
            stats.contradictions_found = contradictions

            _demote_prior_transcript_rows(
                db, retailer_id, fiscal_year, fiscal_quarter
            )
            for index, (passage, signal) in enumerate(pending_rows):
                try:
                    _write_extract_and_evidence(
                        db,
                        retailer_id=retailer_id,
                        fiscal_year=fiscal_year,
                        fiscal_quarter=fiscal_quarter,
                        quarter_dates=quarter_dates,
                        passage=passage,
                        signal=signal,
                        has_contradiction=index in flagged,
                        source_url=source_url,
                    )
                    ctx.increment_inserted()
                except Exception as exc:
                    logger.error("Failed to write signal: %s", exc)
                    ctx.increment_rejected(str(exc))

            db.commit()

        logger.info(
            "Transcript ingestion complete | retailer_id=%s FY%s Q%s | "
            "passages=%s signals=%s analyst_pressure=%s contradictions=%s",
            retailer_id,
            fiscal_year,
            fiscal_quarter,
            stats.passages_processed,
            stats.signals_extracted,
            stats.analyst_pressure_signals,
            stats.contradictions_found,
        )
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        if owns_session:
            db.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest Target or Walmart earnings call transcripts into "
            "retailer_intelligence_extract and retailer_signal_evidence."
        )
    )
    parser.add_argument("--retailer-id", type=int, required=True)
    parser.add_argument("--fiscal-year", type=int, required=True)
    parser.add_argument("--fiscal-quarter", type=int, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--transcript-file",
        help="Path to a .txt file containing the earnings call transcript.",
    )
    group.add_argument(
        "--transcript-text",
        help="Raw transcript text (alternative to --transcript-file).",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    transcript_input = args.transcript_file or args.transcript_text
    try:
        stats = process_transcript(
            transcript_input,
            retailer_id=args.retailer_id,
            fiscal_year=args.fiscal_year,
            fiscal_quarter=args.fiscal_quarter,
        )
    except Exception as exc:
        logger.exception("Earnings transcript ingestion failed: %s", exc)
        return 1
    print(
        f"passages_processed={stats.passages_processed} "
        f"signals_extracted={stats.signals_extracted} "
        f"analyst_pressure_signals={stats.analyst_pressure_signals} "
        f"contradictions_found={stats.contradictions_found}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

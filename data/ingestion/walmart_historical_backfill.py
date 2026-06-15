"""
Walmart Inc historical backfill — SEC EDGAR Tier 1 (XBRL) and Tier 2 (LLM).

Tier 1: retailer_financials from 2009 Q1 through 2025 Q1.
Tier 2: retailer_intelligence_extract from 2015 Q1 through 2025 Q1.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterator, Optional

from anthropic import Anthropic
from sqlalchemy.orm import Session

import data.ingestion.target_tier2_ingestion as engine
from data.ingestion import walmart_tier1_ingestion as tier1
from data.ingestion import walmart_tier2_ingestion as tier2
from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.ingestion_context import IngestionContext
from database.models.retail import RetailerFinancials, RetailerIntelligenceExtract

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

TIER1_START = (2009, 1)
TIER1_END = (2025, 1)
TIER2_DEFAULT_START = (2015, 1)
TIER2_END = (2025, 1)
LIVE_DATA_START = (2025, 2)

CLAUDE_COST_PER_CALL = Decimal("0.15")
CLAUDE_SECONDS_PER_CALL = 1.5
TIER2_CALLS_PER_QUARTER = 2
_SUBMISSIONS_FILE_BASE = "https://data.sec.gov/submissions/"


def _iter_fiscal_quarters(
    start: tuple[int, int],
    end: tuple[int, int],
) -> Iterator[tuple[int, int]]:
    fiscal_year, fiscal_quarter = start
    end_year, end_quarter = end
    while (fiscal_year, fiscal_quarter) <= (end_year, end_quarter):
        yield fiscal_year, fiscal_quarter
        fiscal_quarter += 1
        if fiscal_quarter > 4:
            fiscal_quarter = 1
            fiscal_year += 1


def _financials_row_exists(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
) -> bool:
    return (
        db.query(RetailerFinancials.retailer_financials_id)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.fiscal_year == fiscal_year,
            RetailerFinancials.fiscal_quarter == fiscal_quarter,
            RetailerFinancials.is_latest.is_(True),
        )
        .first()
        is not None
    )


def _intelligence_quarter_exists(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
) -> bool:
    return (
        db.query(RetailerIntelligenceExtract.extract_id)
        .filter(
            RetailerIntelligenceExtract.retailer_id == retailer_id,
            RetailerIntelligenceExtract.fiscal_year == fiscal_year,
            RetailerIntelligenceExtract.fiscal_quarter == fiscal_quarter,
        )
        .first()
        is not None
    )


def _parse_filing_columnar(recent: dict[str, Any]) -> list[dict[str, Any]]:
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    filing_dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    rows: list[dict[str, Any]] = []
    for form, accn, filed, primary in zip(
        forms, accessions, filing_dates, primary_docs
    ):
        filed_date = tier2._parse_filing_date(filed)
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


def _load_all_filing_rows(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _parse_filing_columnar(submissions.get("filings", {}).get("recent", {}))
    for file_info in submissions.get("filings", {}).get("files", []) or []:
        name = file_info.get("name")
        if not name:
            continue
        payload = tier2._sec_get(f"{_SUBMISSIONS_FILE_BASE}{name}")
        if not isinstance(payload, dict):
            continue
        rows.extend(_parse_filing_columnar(payload))
    rows.sort(key=lambda row: row["filing_date"], reverse=True)
    return rows


def _find_historical_8k_document(
    filing_rows: list[dict[str, Any]],
    quarter: tier2.QuarterContext,
    *,
    prefer_presentation: bool,
) -> Optional[engine.DocumentSection]:
    window_start = quarter.period_end_date + timedelta(days=15)
    window_end = quarter.period_end_date + timedelta(days=75)

    for filing in filing_rows:
        if filing["form"] != "8-K":
            continue
        filed_date = filing["filing_date"]
        if filed_date < window_start or filed_date > window_end:
            continue

        index_payload = tier2._fetch_filing_index(filing["accession"])
        if not index_payload:
            continue

        doc_name = None
        document_type = None
        document_section = None

        if prefer_presentation:
            doc_name = tier1._find_exhibit_by_patterns(
                index_payload, tier1.PRESENTATION_EXHIBIT_PATTERNS
            )
            if doc_name:
                document_type = "8K_earnings_presentation"
                document_section = "category_commentary"

        if doc_name is None:
            doc_name = tier1._find_exhibit_by_patterns(
                index_payload, tier1.RELEASE_EXHIBIT_PATTERNS
            )
            if doc_name:
                document_type = "8K_earnings_release"
                document_section = "earnings_release"
                if prefer_presentation:
                    logger.warning(
                        "EX-99.2 not found for FY%s Q%s — using EX-99.1 release",
                        quarter.fiscal_year,
                        quarter.fiscal_quarter,
                    )

        if not doc_name or not document_type:
            continue

        html = tier2._fetch_filing_html(filing["accession"], doc_name)
        if not html:
            continue
        text = tier1._strip_html(html)
        if not text.strip():
            continue

        return engine.DocumentSection(
            document_type=document_type,
            document_section=document_section,
            text=text,
            source_url=tier2._filing_doc_url(filing["accession"], doc_name),
            filing_date=filed_date,
        )

    logger.warning(
        "No Walmart 8-K document found for FY%s Q%s",
        quarter.fiscal_year,
        quarter.fiscal_quarter,
    )
    return None


def _build_tier1_payload(
    db: Session,
    retailer_id: int,
    key: tuple[int, int],
    meta: dict[tuple[int, int], dict[str, Any]],
    revenue: dict[tuple[int, int], Decimal],
    cogs: dict[tuple[int, int], Decimal],
    gross_profit: dict[tuple[int, int], Decimal],
    sga: dict[tuple[int, int], Decimal],
    operating: dict[tuple[int, int], Decimal],
    inventory: dict[tuple[int, int], Decimal],
    store_count_xbrl: dict[tuple[int, int], Decimal],
    ordered_keys: list[tuple[int, int]],
    submissions: Optional[dict[str, Any]] = None,
    filing_rows: Optional[list[dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    if key not in meta or key not in revenue:
        return None

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
    stores_xbrl = store_count_xbrl.get(key)

    gross_margin_pct = None
    if gp is not None and total_sales is not None:
        gross_margin_pct = tier1._safe_div(gp, total_sales)
        if gross_margin_pct is not None:
            gross_margin_pct *= Decimal("100")

    prior_key = tier1._prior_year_key(key)
    prior_gp = gross_profit.get(prior_key)
    prior_sales = revenue.get(prior_key)
    gross_margin_change_bps = None
    if gross_margin_pct is not None and prior_gp is not None and prior_sales:
        prior_margin = tier1._safe_div(prior_gp, prior_sales)
        if prior_margin is not None:
            gross_margin_change_bps = (
                gross_margin_pct - prior_margin * Decimal("100")
            ) * Decimal("100")

    sga_rate_pct = None
    if sga_q is not None and total_sales is not None:
        rate = tier1._safe_div(sga_q, total_sales)
        if rate is not None:
            sga_rate_pct = rate * Decimal("100")

    operating_margin_pct = None
    if op_q is not None and total_sales is not None:
        rate = tier1._safe_div(op_q, total_sales)
        if rate is not None:
            operating_margin_pct = rate * Decimal("100")

    cogs_annual = tier1._trailing_four_quarter_cogs(key, cogs, ordered_keys)
    inventory_days = None
    if inv is not None and cogs_annual is not None and cogs_annual > 0:
        inventory_days = tier1._safe_div(inv, cogs_annual / Decimal("365"))

    source_10q_url: Optional[str] = None
    disagg: dict[str, Optional[Decimal]] = {}
    segment_data: dict[str, Optional[Decimal]] = {}
    accession = qmeta.get("accession")
    if accession:
        index_payload = tier1._fetch_filing_index(accession)
        if index_payload:
            primary_doc = tier1._find_primary_htm(index_payload, period_end)
            if primary_doc:
                source_10q_url = tier1._filing_doc_url(accession, primary_doc)
                html = tier1._fetch_filing_html(accession, primary_doc)
                if html:
                    disagg = tier1._parse_walmart_disaggregation(html, period_end)
                    segment_data = tier1._parse_walmart_10q_segment_data(html, period_end)

    prior_row = tier1._prior_year_financials_row(
        db, retailer_id, fiscal_year, fiscal_quarter
    )

    release_metrics: dict[str, Any] = {}
    presentation_metrics: dict[str, Any] = {}
    source_8k_url: Optional[str] = None
    source_8k_presentation_url: Optional[str] = None
    if submissions is not None:
        (
            release_metrics,
            presentation_metrics,
            source_8k_url,
            source_8k_presentation_url,
        ) = tier1._fetch_quarter_earnings_metrics(
            submissions,
            period_end,
            fiscal_quarter=fiscal_quarter,
            filing_rows=filing_rows,
        )

    general_merch = disagg.get("walmart_us_general_merch_usd")
    ecommerce = disagg.get("walmart_us_ecommerce_usd")
    sams_apparel = disagg.get("sams_club_home_apparel_usd")
    sams_total = disagg.get("sams_club_total_usd")

    general_merch_pct = None
    if general_merch is not None and total_sales:
        rate = tier1._safe_div(general_merch, total_sales)
        if rate is not None:
            general_merch_pct = rate * Decimal("100")

    ecommerce_pct = None
    if ecommerce is not None and total_sales:
        rate = tier1._safe_div(ecommerce, total_sales)
        if rate is not None:
            ecommerce_pct = rate * Decimal("100")

    sams_apparel_pct = None
    if sams_apparel is not None and sams_total:
        rate = tier1._safe_div(sams_apparel, sams_total)
        if rate is not None:
            sams_apparel_pct = rate * Decimal("100")

    general_merch_yoy = None
    ecommerce_yoy = None
    sams_apparel_yoy = None
    if prior_row is not None:
        general_merch_yoy = tier1._calc_yoy_pct(
            general_merch, prior_row.walmart_us_general_merch_usd
        )
        ecommerce_yoy = tier1._calc_yoy_pct(
            ecommerce, prior_row.walmart_us_ecommerce_usd
        )
        sams_apparel_yoy = tier1._calc_yoy_pct(
            sams_apparel, prior_row.sams_club_home_apparel_usd
        )

    payload = {
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "period_end_date": period_end,
        "filing_date": qmeta.get("filing_date"),
        "apparel_revenue_usd": general_merch,
        "apparel_revenue_pct_total": general_merch_pct,
        "apparel_yoy_growth_pct": general_merch_yoy,
        "total_net_sales_usd": total_sales,
        "comparable_sales_growth_pct": release_metrics.get("comparable_sales_growth_pct"),
        "digital_comp_sales_pct": release_metrics.get("digital_comp_sales_pct"),
        "gross_margin_pct": gross_margin_pct,
        "gross_margin_change_bps": gross_margin_change_bps,
        "sga_rate_pct": sga_rate_pct,
        "operating_margin_pct": operating_margin_pct,
        "inventory_usd": inv,
        "inventory_days": inventory_days,
        "store_count_total": int(stores_xbrl) if stores_xbrl is not None else None,
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
        tier1._build_walmart_supplemental_fields(
            segment_data,
            prior_row,
            release_metrics=release_metrics,
            presentation_metrics=presentation_metrics,
        )
    )
    return payload


def _row_to_financials_payload(row: RetailerFinancials) -> dict[str, Any]:
    return {field: getattr(row, field) for field in tier1.RETAILER_FINANCIALS_UPDATE_FIELDS}


def run_comp_sales_reextract(db: Session) -> tuple[int, int, int]:
    retailer_id = tier1._get_walmart_retailer_id(db)
    if retailer_id is None:
        return 0, 0, 0

    submissions = tier1._sec_get(tier1._SUBMISSIONS_URL)
    if not isinstance(submissions, dict):
        logger.error("Failed to fetch Walmart SEC submissions")
        return 0, 0, 0

    filing_rows = tier1._load_all_submission_filings(submissions)
    logger.info("Loaded %d SEC filing row(s) for comp sales re-extract", len(filing_rows))

    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.comparable_sales_growth_pct.is_(None),
        )
        .order_by(RetailerFinancials.fiscal_year, RetailerFinancials.fiscal_quarter)
        .all()
    )

    updated = 0
    still_null = 0
    with IngestionContext(
        source_name="walmart_comp_sales_reextract",
        script_version="1.0.0",
        data_source_url=tier1._SUBMISSIONS_URL,
        db=db,
    ) as ctx:
        for row in rows:
            if row.period_end_date is None:
                still_null += 1
                continue
            release_metrics, presentation_metrics, source_8k_url, source_8k_presentation_url = (
                tier1._fetch_quarter_earnings_metrics(
                    submissions,
                    row.period_end_date,
                    fiscal_quarter=row.fiscal_quarter,
                    filing_rows=filing_rows,
                )
            )
            comp = release_metrics.get("comparable_sales_growth_pct")
            if comp is None and presentation_metrics:
                comp = presentation_metrics.get("comparable_sales_growth_pct")
            if comp is None:
                still_null += 1
                logger.warning(
                    "FY%s Q%s — no comparable sales found in 8-K",
                    row.fiscal_year,
                    row.fiscal_quarter,
                )
                continue

            payload = _row_to_financials_payload(row)
            payload["comparable_sales_growth_pct"] = comp
            if release_metrics.get("digital_comp_sales_pct") is not None:
                payload["digital_comp_sales_pct"] = release_metrics["digital_comp_sales_pct"]
            if source_8k_url:
                payload["source_8k_url"] = source_8k_url
            if source_8k_presentation_url:
                payload["source_8k_presentation_url"] = source_8k_presentation_url

            tier1._append_retailer_financials(db, ctx, retailer_id, payload)
            db.commit()
            updated += 1
            logger.info(
                "Updated FY%s Q%s comparable_sales_growth_pct=%s",
                row.fiscal_year,
                row.fiscal_quarter,
                comp,
            )

    logger.info(
        "Walmart comp sales re-extract — updated %d, still null %d (of %d candidates)",
        updated,
        still_null,
        len(rows),
    )
    return updated, len(rows) - updated - still_null, still_null


def run_tier1_backfill(db: Session) -> tuple[int, int, int]:
    retailer_id = tier1._get_walmart_retailer_id(db)
    if retailer_id is None:
        return 0, 0, 0

    companyfacts = tier1._sec_get(tier1._COMPANYFACTS_URL)
    if not isinstance(companyfacts, dict):
        logger.error("Failed to fetch Walmart company facts")
        return 0, 0, 0

    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        logger.error("No us-gaap facts for Walmart")
        return 0, 0, 0

    (
        meta,
        revenue,
        cogs,
        gross_profit,
        sga,
        operating,
        inventory,
        store_count_xbrl,
    ) = tier1._extract_fiscal_quarter_maps(us_gaap)

    ordered_keys = sorted(meta.keys(), key=lambda k: meta[k]["period_end_date"])
    written = skipped = missing = 0

    with IngestionContext(
        source_name="walmart_sec_edgar_tier1_backfill",
        script_version="1.0.0",
        data_source_url=tier1._COMPANYFACTS_URL,
        db=db,
    ) as ctx:
        for fiscal_year, fiscal_quarter in _iter_fiscal_quarters(TIER1_START, TIER1_END):
            if (fiscal_year, fiscal_quarter) >= LIVE_DATA_START:
                continue
            label = f"FY{fiscal_year} Q{fiscal_quarter}"
            if _financials_row_exists(db, retailer_id, fiscal_year, fiscal_quarter):
                skipped += 1
                continue
            key = (fiscal_year, fiscal_quarter)
            if key not in meta:
                missing += 1
                logger.warning("No XBRL data for %s", label)
                continue
            payload = _build_tier1_payload(
                db,
                retailer_id,
                key,
                meta,
                revenue,
                cogs,
                gross_profit,
                sga,
                operating,
                inventory,
                store_count_xbrl,
                ordered_keys,
            )
            if payload is None:
                missing += 1
                continue
            logger.info("Writing %s...", label)
            tier1._validate_walmart_payload(ctx, payload, prior_row=None)
            tier1._append_retailer_financials(db, ctx, retailer_id, payload)
            db.commit()
            written += 1

    logger.info(
        "Walmart Tier 1 backfill — wrote %d, skipped %d, missing %d",
        written,
        skipped,
        missing,
    )
    return written, skipped, missing


def _tier2_quarters_to_process(
    db: Session,
    retailer_id: int,
    start: tuple[int, int],
) -> list[tuple[int, int]]:
    quarters: list[tuple[int, int]] = []
    for fiscal_year, fiscal_quarter in _iter_fiscal_quarters(start, TIER2_END):
        if (fiscal_year, fiscal_quarter) >= LIVE_DATA_START:
            continue
        if _intelligence_quarter_exists(db, retailer_id, fiscal_year, fiscal_quarter):
            continue
        if not _financials_row_exists(db, retailer_id, fiscal_year, fiscal_quarter):
            continue
        quarters.append((fiscal_year, fiscal_quarter))
    return quarters


def _confirm_tier2_cost(quarter_count: int) -> bool:
    estimated_calls = quarter_count * TIER2_CALLS_PER_QUARTER
    estimated_cost = estimated_calls * CLAUDE_COST_PER_CALL
    estimated_minutes = estimated_calls * CLAUDE_SECONDS_PER_CALL / 60
    print(f"Estimated API calls: {estimated_calls}")
    print(f"Estimated cost: ${estimated_cost:.2f}")
    print(f"Estimated time: {estimated_minutes:.0f} minutes")
    answer = input("Proceed? (y/n) ").strip().lower()
    return answer in ("y", "yes")


def _process_historical_pass12(
    client: Anthropic,
    quarter: tier2.QuarterContext,
    section: engine.DocumentSection,
    tier1_row: RetailerFinancials,
) -> tuple[list[engine.PendingSignal], int]:
    api_calls = 0
    if not section.text.strip():
        return [], api_calls

    pass1_prompt = tier2._build_pass1_prompt(section, quarter, tier1_row)
    pass1_raw = tier2._call_claude(
        client, tier2.PASS1_SYSTEM_PROMPT, pass1_prompt, max_tokens=4000
    )
    api_calls += 1
    if pass1_raw is None:
        return [], api_calls

    try:
        pass1_facts = engine._parse_json_array(pass1_raw)
    except (json.JSONDecodeError, ValueError):
        return [], api_calls
    if not pass1_facts:
        return [], api_calls

    pass2_prompt = tier2._build_pass2_prompt(section, quarter, pass1_facts, tier1_row)
    pass2_raw = tier2._call_claude(
        client,
        tier2._build_tier1_context_block(tier1_row),
        pass2_prompt,
        max_tokens=4000,
    )
    api_calls += 1
    pass2_results: list[dict[str, Any]] = []
    pass2_ok = False
    if pass2_raw is not None:
        try:
            pass2_results = engine._parse_json_array(pass2_raw)
            pass2_ok = True
        except (json.JSONDecodeError, ValueError):
            pass2_ok = False

    pending: list[engine.PendingSignal] = []
    for index, fact in enumerate(pass1_facts):
        pass2 = engine._match_pass2_fact(fact, pass2_results, index) if pass2_ok else {}
        pending.append(
            engine.PendingSignal(
                section=section,
                fact=fact,
                pass2=pass2,
                pass3={"historical_pattern_found": False},
                pass2_ok=pass2_ok,
                pass3_ok=False,
            )
        )
    return pending, api_calls


def run_tier2_backfill(
    db: Session,
    start: tuple[int, int],
    *,
    confirm_cost: bool = True,
) -> tuple[int, int, int]:
    retailer_id = tier1._get_walmart_retailer_id(db)
    if retailer_id is None:
        return 0, 0, 0
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is not set")
        return 0, 0, 0

    quarters = _tier2_quarters_to_process(db, retailer_id, start)
    if not quarters:
        logger.info("No Walmart Tier 2 quarters to backfill")
        return 0, 0, 0
    if confirm_cost and not _confirm_tier2_cost(len(quarters)):
        logger.info("Walmart Tier 2 backfill cancelled")
        return 0, 0, 0

    submissions = tier2._sec_get(tier2._SUBMISSIONS_URL)
    if not isinstance(submissions, dict):
        logger.error("Failed to fetch Walmart submissions")
        return 0, 0, 0

    filing_rows = _load_all_filing_rows(submissions)
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    original_priority = engine.DOCUMENT_PRIORITY
    engine.DOCUMENT_PRIORITY = tier2.WALMART_DOCUMENT_PRIORITY

    processed = skipped = 0
    total_api_calls = 0

    try:
        for fiscal_year, fiscal_quarter in _iter_fiscal_quarters(start, TIER2_END):
            if (fiscal_year, fiscal_quarter) >= LIVE_DATA_START:
                continue
            label = f"FY{fiscal_year} Q{fiscal_quarter}"
            if _intelligence_quarter_exists(
                db, retailer_id, fiscal_year, fiscal_quarter
            ):
                skipped += 1
                continue

            tier1_row = (
                db.query(RetailerFinancials)
                .filter(
                    RetailerFinancials.retailer_id == retailer_id,
                    RetailerFinancials.fiscal_year == fiscal_year,
                    RetailerFinancials.fiscal_quarter == fiscal_quarter,
                    RetailerFinancials.is_latest.is_(True),
                )
                .first()
            )
            if tier1_row is None:
                logger.warning(
                    "Skipping %s Tier 2 — no Tier 1 row (run Tier 1 backfill first)",
                    label,
                )
                skipped += 1
                continue

            quarter = tier2.QuarterContext(
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                period_end_date=tier1_row.period_end_date,
                filing_date=tier1_row.filing_date,
                source_10q_url=tier1_row.source_10q_url,
            )
            section = _find_historical_8k_document(
                filing_rows, quarter, prefer_presentation=True
            )
            if section is None:
                skipped += 1
                continue

            logger.info("Processing Walmart Tier 2 %s (%s)...", label, section.document_type)
            pending, api_calls = _process_historical_pass12(
                client, quarter, section, tier1_row
            )
            total_api_calls += api_calls

            if pending:
                quarter_ctx = engine.QuarterContext(
                    fiscal_year=quarter.fiscal_year,
                    fiscal_quarter=quarter.fiscal_quarter,
                    period_end_date=quarter.period_end_date,
                    filing_date=quarter.filing_date,
                    source_10q_url=quarter.source_10q_url,
                )
                masters_written, _ = engine._consolidate_quarter_signals(
                    db, retailer_id, quarter_ctx, pending
                )
                db.commit()
                logger.info("Consolidated %s into %d master(s)", label, masters_written)

            running_cost = total_api_calls * CLAUDE_COST_PER_CALL
            print(
                f"Running API total after {label}: "
                f"{total_api_calls} calls, ${running_cost:.2f}"
            )
            processed += 1
    finally:
        engine.DOCUMENT_PRIORITY = original_priority

    logger.info(
        "Walmart Tier 2 backfill — processed %d, skipped %d, %d API calls",
        processed,
        skipped,
        total_api_calls,
    )
    return processed, skipped, total_api_calls


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Walmart historical SEC EDGAR backfill")
    parser.add_argument(
        "--reextract-comp-sales",
        action="store_true",
        help="Re-fetch comparable_sales_growth_pct from 8-K for null rows only",
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--tier1-only", action="store_true")
    group.add_argument("--tier2-from", type=int, metavar="YEAR")
    group.add_argument("--all", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        if args.reextract_comp_sales:
            run_comp_sales_reextract(db)
            return 0

        if args.tier1_only:
            run_tier1_backfill(db)
            return 0
        if args.tier2_from is not None:
            if args.tier2_from < TIER2_DEFAULT_START[0]:
                logger.error("--tier2-from must be >= %s", TIER2_DEFAULT_START[0])
                return 1
            run_tier2_backfill(db, (args.tier2_from, 1))
            return 0
        if args.all:
            run_tier1_backfill(db)
            run_tier2_backfill(db, TIER2_DEFAULT_START)
            return 0
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

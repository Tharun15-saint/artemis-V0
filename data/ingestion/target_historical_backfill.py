"""
Target Corporation historical backfill — SEC EDGAR Tier 1 (XBRL) and Tier 2 (LLM).

Tier 1: retailer_financials from 2009 Q1 through 2025 Q1 (before live data at 2025 Q2).
Tier 2: retailer_intelligence_extract from 2015 Q1 through 2025 Q1 (8-K earnings only).

Do not run in CI — requires SEC network access and (for Tier 2) ANTHROPIC_API_KEY.
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

from data.ingestion import target_tier1_ingestion as tier1
from data.ingestion import target_tier2_ingestion as tier2
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
    """Merge filings.recent with paginated submissions files (1000+ filing history)."""
    rows = _parse_filing_columnar(submissions.get("filings", {}).get("recent", {}))
    for file_info in submissions.get("filings", {}).get("files", []) or []:
        name = file_info.get("name")
        if not name:
            continue
        payload = tier2._sec_get(f"{_SUBMISSIONS_FILE_BASE}{name}")
        if not isinstance(payload, dict):
            logger.warning("Failed to fetch submissions file %s", name)
            continue
        rows.extend(_parse_filing_columnar(payload))
    rows.sort(key=lambda row: row["filing_date"], reverse=True)
    return rows


def _find_historical_earnings_release_8k(
    filing_rows: list[dict[str, Any]],
    quarter: tier2.QuarterContext,
) -> Optional[tier2.DocumentSection]:
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

        exhibit = tier2._find_exhibit_document(
            index_payload,
            preferred_patterns=["ex-99.1", "ex99.1"],
            fallback_patterns=["ex-99", "ex99"],
        )
        if not exhibit:
            continue

        html = tier2._fetch_filing_html(filing["accession"], exhibit)
        if not html:
            continue
        text = tier2._strip_html(html)
        if not tier2._looks_like_earnings_release(text):
            continue

        return tier2.DocumentSection(
            document_type="8K_earnings_release",
            document_section="full",
            text=text,
            source_url=tier2._filing_doc_url(filing["accession"], exhibit),
            filing_date=filed_date,
        )

    logger.warning(
        "No 8-K earnings release found for FY%s Q%s (period_end=%s)",
        quarter.fiscal_year,
        quarter.fiscal_quarter,
        quarter.period_end_date,
    )
    return None


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


def _load_quarter_context(
    db: Session,
    retailer_id: int,
    fiscal_year: int,
    fiscal_quarter: int,
) -> Optional[tier2.QuarterContext]:
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
    if row is None:
        return None
    return tier2.QuarterContext(
        fiscal_year=row.fiscal_year,
        fiscal_quarter=row.fiscal_quarter,
        period_end_date=row.period_end_date,
        filing_date=row.filing_date,
        source_10q_url=row.source_10q_url,
    )


def _build_tier1_payload(
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
    apparel_revenue_usd: Optional[Decimal] = None
    html_metrics: dict[str, Any] = {}

    accession = qmeta.get("accession")
    if accession:
        index_payload = tier1._fetch_filing_index(accession)
        if index_payload:
            primary_doc = tier1._find_primary_htm(index_payload)
            if primary_doc:
                source_10q_url = tier1._filing_doc_url(accession, primary_doc)
                html = tier1._fetch_filing_html(accession, primary_doc)
                if html:
                    apparel_revenue_usd = tier1._parse_inline_apparel_revenue(
                        html, period_end
                    )
                    if apparel_revenue_usd is None and qmeta.get("synthesized_q4"):
                        apparel_revenue_usd = tier1._derive_q4_apparel_revenue(
                            html,
                            period_end,
                            fiscal_year,
                            meta,
                        )
                    html_metrics = tier1._parse_10q_metrics(
                        html, fiscal_quarter=fiscal_quarter
                    )

    source_8k_url: Optional[str] = None
    if filing_rows and period_end is not None:
        quarter_8k_metrics, source_8k_url = tier1.fetch_quarter_earnings_metrics(
            filing_rows,
            period_end,
            fiscal_quarter,
        )
        if quarter_8k_metrics.get("comparable_sales_growth_pct") is not None:
            html_metrics["comparable_sales_growth_pct"] = quarter_8k_metrics[
                "comparable_sales_growth_pct"
            ]
        if quarter_8k_metrics.get("digital_comp_sales_pct") is not None:
            html_metrics["digital_comp_sales_pct"] = quarter_8k_metrics[
                "digital_comp_sales_pct"
            ]
        for store_field in ("store_count_total", "store_count_net_change"):
            if (
                html_metrics.get(store_field) is None
                and quarter_8k_metrics.get(store_field) is not None
            ):
                html_metrics[store_field] = quarter_8k_metrics[store_field]

    apparel_revenue_pct_total = None
    if apparel_revenue_usd is not None and total_sales is not None:
        apparel_revenue_pct_total = tier1._safe_div(
            apparel_revenue_usd, total_sales
        )

    apparel_yoy_growth_pct = None
    if apparel_revenue_usd is not None:
        prior_meta = meta.get(prior_key)
        if prior_meta and prior_meta.get("accession"):
            prior_index = tier1._fetch_filing_index(prior_meta["accession"])
            prior_doc = tier1._find_primary_htm(prior_index) if prior_index else None
            if prior_doc:
                prior_html = tier1._fetch_filing_html(
                    prior_meta["accession"], prior_doc
                )
                if prior_html:
                    prior_apparel = tier1._parse_inline_apparel_revenue(
                        prior_html,
                        prior_meta["period_end_date"],
                    )
                    if prior_apparel and prior_apparel > 0:
                        apparel_yoy_growth_pct = (
                            apparel_revenue_usd / prior_apparel - Decimal("1")
                        ) * Decimal("100")

    payload: dict[str, Any] = {
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "period_end_date": period_end,
        "filing_date": qmeta.get("filing_date"),
        "apparel_revenue_usd": apparel_revenue_usd,
        "apparel_revenue_pct_total": apparel_revenue_pct_total,
        "apparel_yoy_growth_pct": apparel_yoy_growth_pct,
        "total_net_sales_usd": total_sales,
        "comparable_sales_growth_pct": html_metrics.get(
            "comparable_sales_growth_pct"
        ),
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

    return payload


def run_comp_sales_reextract(db: Session) -> tuple[int, int, int]:
    """Re-fetch comparable_sales_growth_pct from 8-K for rows where it is null."""
    retailer_id = tier1._get_target_retailer_id(db)
    if retailer_id is None:
        return 0, 0, 0

    submissions = tier1._sec_get(tier1._SUBMISSIONS_URL)
    if not isinstance(submissions, dict):
        logger.error("Failed to fetch Target SEC submissions")
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
        source_name="target_comp_sales_reextract",
        script_version="1.0.0",
        data_source_url=tier1._SUBMISSIONS_URL,
        db=db,
    ) as ctx:
        for row in rows:
            if row.period_end_date is None:
                still_null += 1
                continue
            metrics, source_8k_url = tier1.fetch_quarter_earnings_metrics(
                filing_rows,
                row.period_end_date,
                row.fiscal_quarter,
            )
            comp = metrics.get("comparable_sales_growth_pct")
            if comp is None:
                still_null += 1
                logger.warning(
                    "FY%s Q%s — no comparable sales found in 8-K",
                    row.fiscal_year,
                    row.fiscal_quarter,
                )
                continue

            payload = tier1._row_to_financials_payload(row)
            payload["comparable_sales_growth_pct"] = comp
            if metrics.get("digital_comp_sales_pct") is not None:
                payload["digital_comp_sales_pct"] = metrics["digital_comp_sales_pct"]
            if source_8k_url:
                payload["source_8k_url"] = source_8k_url

            tier1._validate_target_payload(ctx, payload)
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
        "Target comp sales re-extract — updated %d, still null %d (of %d candidates)",
        updated,
        still_null,
        len(rows),
    )
    return updated, len(rows) - updated - still_null, still_null


def run_tier1_backfill(db: Session) -> tuple[int, int, int]:
    retailer_id = tier1._get_target_retailer_id(db)
    if retailer_id is None:
        return 0, 0, 0

    companyfacts = tier1._sec_get(tier1._COMPANYFACTS_URL)
    submissions = tier1._sec_get(tier1._SUBMISSIONS_URL)
    if not isinstance(companyfacts, dict):
        logger.error("Failed to fetch Target company facts")
        return 0, 0, 0
    if not isinstance(submissions, dict):
        logger.warning("Failed to fetch Target submissions — 8-K comp sales may be missing")
        submissions = {}

    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    if not us_gaap:
        logger.error("No us-gaap facts for Target")
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

    ordered_keys = sorted(
        meta.keys(),
        key=lambda k: meta[k]["period_end_date"],
    )

    filing_rows = tier1._load_all_submission_filings(submissions)
    logger.info("Loaded %d SEC filing row(s) for Tier 1 8-K lookup", len(filing_rows))

    written = 0
    skipped = 0
    missing = 0

    with IngestionContext(
        source_name="target_sec_edgar_tier1_backfill",
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
                logger.debug("Skipping %s — row already exists", label)
                continue

            key = (fiscal_year, fiscal_quarter)
            if key not in meta:
                missing += 1
                logger.warning("No XBRL data for %s — skipping", label)
                continue

            payload = _build_tier1_payload(
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
                filing_rows=filing_rows,
            )
            if payload is None:
                missing += 1
                logger.warning("Could not build payload for %s — skipping", label)
                continue

            logger.info("Writing %s...", label)
            tier1._validate_target_payload(ctx, payload)
            tier1._append_retailer_financials(db, ctx, retailer_id, payload)
            db.commit()
            written += 1

    logger.info(
        "Tier 1 backfill complete — wrote %d, skipped %d existing, missing %d",
        written,
        skipped,
        missing,
    )
    return written, skipped, missing


def _process_earnings_pass12_only(
    client: Anthropic,
    quarter: tier2.QuarterContext,
    section: tier2.DocumentSection,
) -> tuple[list[tier2.PendingSignal], int]:
    """Pass 1 + Pass 2 only; returns pending signals and API call count."""
    api_calls = 0
    if not section.text.strip():
        return [], api_calls

    pass1_prompt = tier2._build_pass1_prompt(section, quarter)
    pass1_raw = tier2._call_claude(
        client,
        tier2.PASS1_SYSTEM_PROMPT,
        pass1_prompt,
        max_tokens=4000,
    )
    api_calls += 1
    if pass1_raw is None:
        logger.error(
            "Pass 1 failed for %s FY%s Q%s",
            section.document_type,
            quarter.fiscal_year,
            quarter.fiscal_quarter,
        )
        return [], api_calls

    try:
        pass1_facts = tier2._parse_json_array(pass1_raw)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.error(
            "Pass 1 parse failed for %s FY%s Q%s: %s",
            section.document_type,
            quarter.fiscal_year,
            quarter.fiscal_quarter,
            exc,
        )
        return [], api_calls

    if not pass1_facts:
        return [], api_calls

    pass2_prompt = tier2._build_pass2_prompt(section, quarter, pass1_facts)
    pass2_raw = tier2._call_claude(
        client,
        tier2.PASS2_SYSTEM_PROMPT,
        pass2_prompt,
        max_tokens=4000,
    )
    api_calls += 1
    pass2_results: list[dict[str, Any]] = []
    pass2_ok = False
    if pass2_raw is None:
        logger.error(
            "Pass 2 failed for %s FY%s Q%s — storing Pass 1 fields only",
            section.document_type,
            quarter.fiscal_year,
            quarter.fiscal_quarter,
        )
    else:
        try:
            pass2_results = tier2._parse_json_array(pass2_raw)
            pass2_ok = True
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error(
                "Pass 2 parse failed for %s FY%s Q%s: %s",
                section.document_type,
                quarter.fiscal_year,
                quarter.fiscal_quarter,
                exc,
            )

    pending: list[tier2.PendingSignal] = []
    for index, fact in enumerate(pass1_facts):
        pass2 = (
            tier2._match_pass2_fact(fact, pass2_results, index) if pass2_ok else {}
        )
        pending.append(
            tier2.PendingSignal(
                section=section,
                fact=fact,
                pass2=pass2,
                pass3={"historical_pattern_found": False},
                pass2_ok=pass2_ok,
                pass3_ok=False,
            )
        )
    return pending, api_calls


def _tier2_quarters_to_process(
    db: Session,
    retailer_id: int,
    start: tuple[int, int],
) -> list[tuple[int, int]]:
    quarters: list[tuple[int, int]] = []
    for fiscal_year, fiscal_quarter in _iter_fiscal_quarters(start, TIER2_END):
        if (fiscal_year, fiscal_quarter) >= LIVE_DATA_START:
            continue
        if _intelligence_quarter_exists(
            db, retailer_id, fiscal_year, fiscal_quarter
        ):
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


def run_tier2_backfill(
    db: Session,
    start: tuple[int, int],
    *,
    confirm_cost: bool = True,
) -> tuple[int, int, int]:
    retailer_id = tier2._get_target_retailer_id(db)
    if retailer_id is None:
        return 0, 0, 0

    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY is not set — cannot run Tier 2 backfill")
        return 0, 0, 0

    quarters = _tier2_quarters_to_process(db, retailer_id, start)
    if not quarters:
        logger.info("No Tier 2 quarters to backfill")
        return 0, 0, 0

    if confirm_cost and not _confirm_tier2_cost(len(quarters)):
        logger.info("Tier 2 backfill cancelled by user")
        return 0, 0, 0

    submissions = tier2._sec_get(tier2._SUBMISSIONS_URL)
    if not isinstance(submissions, dict):
        logger.error("Failed to fetch Target SEC submissions")
        return 0, 0, 0

    filing_rows = _load_all_filing_rows(submissions)
    logger.info("Loaded %d SEC filing row(s) for historical 8-K lookup", len(filing_rows))

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    quarters_processed = 0
    quarters_skipped = 0
    total_api_calls = 0

    for fiscal_year, fiscal_quarter in _iter_fiscal_quarters(start, TIER2_END):
        if (fiscal_year, fiscal_quarter) >= LIVE_DATA_START:
            continue

        label = f"FY{fiscal_year} Q{fiscal_quarter}"
        if _intelligence_quarter_exists(
            db, retailer_id, fiscal_year, fiscal_quarter
        ):
            quarters_skipped += 1
            continue

        quarter = _load_quarter_context(
            db, retailer_id, fiscal_year, fiscal_quarter
        )
        if quarter is None:
            logger.warning(
                "Skipping %s Tier 2 — no retailer_financials row (run Tier 1 first)",
                label,
            )
            quarters_skipped += 1
            continue

        earnings = _find_historical_earnings_release_8k(filing_rows, quarter)
        if earnings is None:
            logger.warning("Skipping %s Tier 2 — no 8-K earnings release found", label)
            quarters_skipped += 1
            continue

        logger.info("Processing Tier 2 %s (8-K earnings release only)...", label)
        pending, api_calls = _process_earnings_pass12_only(
            client, quarter, earnings
        )
        total_api_calls += api_calls

        if pending:
            masters_written, deferred = tier2._consolidate_quarter_signals(
                db,
                retailer_id,
                quarter,
                pending,
            )
            db.commit()
            logger.info(
                "Consolidated %s into %d master signal(s)",
                label,
                masters_written,
            )
            if deferred:
                logger.warning(
                    "%s deferred schema fields: %s",
                    label,
                    ", ".join(deferred),
                )
        else:
            logger.warning("%s — no signals extracted", label)

        running_cost = total_api_calls * CLAUDE_COST_PER_CALL
        logger.info(
            "Running API total: %d calls, $%.2f",
            total_api_calls,
            running_cost,
        )
        print(
            f"Running API total after {label}: "
            f"{total_api_calls} calls, ${running_cost:.2f}"
        )
        quarters_processed += 1

    logger.info(
        "Tier 2 backfill complete — processed %d quarter(s), "
        "skipped %d, %d API calls ($%.2f)",
        quarters_processed,
        quarters_skipped,
        total_api_calls,
        total_api_calls * CLAUDE_COST_PER_CALL,
    )
    return quarters_processed, quarters_skipped, total_api_calls


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Target Corporation historical SEC EDGAR backfill (Tier 1 XBRL + Tier 2 LLM)"
    )
    parser.add_argument(
        "--reextract-comp-sales",
        action="store_true",
        help="Re-fetch comparable_sales_growth_pct from 8-K for null rows only",
    )
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(
        "--tier1-only",
        action="store_true",
        help="Run Tier 1 XBRL backfill only (no Claude API cost)",
    )
    group.add_argument(
        "--tier2-from",
        type=int,
        metavar="YEAR",
        help="Run Tier 2 from YEAR Q1 through 2025 Q1 (e.g. 2020)",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Tier 1 from 2009 Q1 and Tier 2 from 2015 Q1",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    db = SessionLocal()
    try:
        if args.reextract_comp_sales:
            updated, _, still_null = run_comp_sales_reextract(db)
            return 0 if updated > 0 or still_null == 0 else 1

        if args.tier1_only:
            written, skipped, missing = run_tier1_backfill(db)
            if written == 0 and skipped == 0 and missing > 0:
                return 1
            return 0

        if args.tier2_from is not None:
            if args.tier2_from < TIER2_DEFAULT_START[0]:
                logger.error(
                    "--tier2-from must be >= %s (Tier 2 starts 2015 Q1)",
                    TIER2_DEFAULT_START[0],
                )
                return 1
            start = (args.tier2_from, 1)
            processed, _, _ = run_tier2_backfill(db, start)
            return 0 if processed >= 0 else 1

        if args.all:
            logger.info("Starting Tier 1 historical backfill (2009 Q1 – 2025 Q1)")
            run_tier1_backfill(db)
            logger.info("Starting Tier 2 historical backfill (2015 Q1 – 2025 Q1)")
            run_tier2_backfill(db, TIER2_DEFAULT_START)
            return 0

        logger.error("Specify --reextract-comp-sales, --tier1-only, --tier2-from YEAR, or --all")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

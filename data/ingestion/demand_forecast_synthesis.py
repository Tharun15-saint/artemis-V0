"""
Derive retailer_demand_forecast and demand_signals from retailer_intelligence_extract
and retailer_financials — replaces pre-generated forecasts that ignore Tier 2 signals.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.base import SessionLocal
from database.constants import INTELLIGENCE_MODEL_VERSION
from database.models.outputs import RetailerDemandForecast
from database.models.prediction import PredictionLog
from database.models.retail import (
    DemandSignals,
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

MODEL_VERSION = f"demand_forecast_synthesis_{INTELLIGENCE_MODEL_VERSION}"
NO_EXTRACT_CONFIDENCE = Decimal("0.30")
SIGNAL_BALANCE_THRESHOLD = Decimal("0.40")
DEFAULT_EXTRACT_CONFIDENCE = Decimal("0.50")
INVENTORY_DAYS_TREND_QUARTERS = 6

_BUYING_SIGNAL_SCORE: dict[str, Decimal] = {
    "increasing": Decimal("1.0000"),
    "stable": Decimal("0.0000"),
    "declining": Decimal("-1.0000"),
    "unknown": Decimal("0.0000"),
}


def _quarter_key(fiscal_year: int, fiscal_quarter: int) -> tuple[int, int]:
    return fiscal_year, fiscal_quarter


def _recent_two_quarters(
    db: Session,
    retailer_id: int,
) -> list[tuple[int, int]]:
    rows = (
        db.query(
            RetailerIntelligenceExtract.fiscal_year,
            RetailerIntelligenceExtract.fiscal_quarter,
        )
        .filter(
            RetailerIntelligenceExtract.retailer_id == retailer_id,
            RetailerIntelligenceExtract.is_latest.is_(True),
            RetailerIntelligenceExtract.excluded_reason.is_(None),
        )
        .distinct()
        .all()
    )
    unique = sorted({_quarter_key(fy, fq) for fy, fq in rows}, reverse=True)
    return unique[:2]


def _fetch_extracts_for_quarters(
    db: Session,
    retailer_id: int,
    quarters: list[tuple[int, int]],
) -> list[RetailerIntelligenceExtract]:
    if not quarters:
        return []

    extracts: list[RetailerIntelligenceExtract] = []
    for fiscal_year, fiscal_quarter in quarters:
        rows = (
            db.query(RetailerIntelligenceExtract)
            .filter(
                RetailerIntelligenceExtract.retailer_id == retailer_id,
                RetailerIntelligenceExtract.is_latest.is_(True),
                RetailerIntelligenceExtract.excluded_reason.is_(None),
                RetailerIntelligenceExtract.fiscal_year == fiscal_year,
                RetailerIntelligenceExtract.fiscal_quarter == fiscal_quarter,
            )
            .all()
        )
        extracts.extend(rows)
    return extracts


def _extract_confidence(extract: RetailerIntelligenceExtract) -> Decimal:
    if extract.confidence_score is not None:
        return Decimal(str(extract.confidence_score))
    return DEFAULT_EXTRACT_CONFIDENCE


def _signal_age_weight(
    signal_fiscal_year: int,
    signal_fiscal_quarter: int,
    current_fiscal_year: int,
    current_fiscal_quarter: int,
) -> float:
    age_quarters = (current_fiscal_year * 4 + current_fiscal_quarter) - (
        signal_fiscal_year * 4 + signal_fiscal_quarter
    )
    if age_quarters <= 4:
        return 1.0
    if age_quarters <= 8:
        return 0.75
    if age_quarters <= 16:
        return 0.5
    return 0.25


def _summarize_extracts(
    extracts: list[RetailerIntelligenceExtract],
    *,
    current_fiscal_year: Optional[int] = None,
    current_fiscal_quarter: Optional[int] = None,
) -> dict[str, Any]:
    positive_count = Decimal("0")
    negative_count = Decimal("0")
    neutral_count = 0
    weighted_positive = Decimal("0")
    weighted_negative = Decimal("0")
    confidence_total = Decimal("0")

    category_counts: Counter[str] = Counter()

    for extract in extracts:
        sentiment = (extract.signal_sentiment or "").strip().lower()
        confidence_weight = _extract_confidence(extract)
        age_weight = Decimal("1")
        if (
            current_fiscal_year is not None
            and current_fiscal_quarter is not None
            and extract.fiscal_year is not None
            and extract.fiscal_quarter is not None
        ):
            age_weight = Decimal(
                str(
                    _signal_age_weight(
                        extract.fiscal_year,
                        extract.fiscal_quarter,
                        current_fiscal_year,
                        current_fiscal_quarter,
                    )
                )
            )
        signal_weight = confidence_weight * age_weight
        confidence_total += confidence_weight

        if extract.signal_category:
            category_counts[extract.signal_category] += 1

        if sentiment == "positive":
            positive_count += age_weight
            weighted_positive += signal_weight
        elif sentiment == "negative":
            negative_count += age_weight
            weighted_negative += signal_weight
        else:
            neutral_count += 1

    extract_count = len(extracts)
    avg_confidence = (
        (confidence_total / Decimal(extract_count)).quantize(Decimal("0.01"))
        if extract_count
        else None
    )

    return {
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "weighted_positive": weighted_positive,
        "weighted_negative": weighted_negative,
        "average_confidence": avg_confidence,
        "category_counts": dict(category_counts),
        "extract_count": extract_count,
    }


def _derive_buying_volume_signal(summary: dict[str, Any]) -> str:
    if summary["extract_count"] == 0:
        return "unknown"

    wp = summary["weighted_positive"]
    wn = summary["weighted_negative"]
    denominator = wp + wn
    if denominator == 0:
        return "stable"

    balance = (wp - wn) / denominator
    if balance > SIGNAL_BALANCE_THRESHOLD:
        return "increasing"
    if balance < -SIGNAL_BALANCE_THRESHOLD:
        return "declining"
    return "stable"


def _derive_confidence_score(
    summary: dict[str, Any],
    has_extracts: bool,
) -> Decimal:
    if not has_extracts:
        return NO_EXTRACT_CONFIDENCE
    if summary["average_confidence"] is not None:
        return summary["average_confidence"]
    return NO_EXTRACT_CONFIDENCE


def _latest_financials(
    db: Session,
    retailer_id: int,
) -> Optional[RetailerFinancials]:
    quarters = _recent_two_financial_quarters(db, retailer_id)
    return quarters[0] if quarters else None


def _recent_financial_quarters(
    db: Session,
    retailer_id: int,
    *,
    limit: int = 2,
) -> list[RetailerFinancials]:
    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
        )
        .order_by(
            RetailerFinancials.fiscal_year.desc(),
            RetailerFinancials.fiscal_quarter.desc(),
        )
        .all()
    )
    seen: set[tuple[int, int]] = set()
    unique: list[RetailerFinancials] = []
    for row in rows:
        key = _quarter_key(row.fiscal_year, row.fiscal_quarter)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
        if len(unique) == limit:
            break
    return unique


def _recent_two_financial_quarters(
    db: Session,
    retailer_id: int,
) -> list[RetailerFinancials]:
    return _recent_financial_quarters(db, retailer_id, limit=2)


def _inventory_days_slope(
    quarters: list[RetailerFinancials],
) -> Optional[Decimal]:
    """OLS slope of inventory_days over equally spaced quarter indices."""
    points: list[tuple[int, Decimal]] = []
    for index, row in enumerate(reversed(quarters)):
        if row.inventory_days is None:
            continue
        points.append((index, Decimal(str(row.inventory_days))))
    if len(points) < 2:
        return None

    n = len(points)
    x_mean = Decimal(n - 1) / Decimal("2")
    y_mean = sum(y for _, y in points) / Decimal(n)
    numerator = sum(
        (Decimal(x) - x_mean) * (y - y_mean) for x, y in points
    )
    denominator = sum((Decimal(x) - x_mean) ** 2 for x, _ in points)
    if denominator == 0:
        return Decimal("0")
    return (numerator / denominator).quantize(Decimal("0.01"))


def _derive_inventory_improving(
    financial_quarters: list[RetailerFinancials],
) -> tuple[str, Optional[str]]:
    trend_quarters = [
        row
        for row in financial_quarters[:INVENTORY_DAYS_TREND_QUARTERS]
        if row.inventory_days is not None
    ]
    if len(trend_quarters) < 2:
        return "stable", None

    slope = _inventory_days_slope(trend_quarters)
    if slope is None:
        return "stable", None

    source = "retailer_financials.inventory_days_6q_trend"
    if slope == 0:
        return "stable", source
    if slope < 0:
        return "improving", source
    return "deteriorating", source


def _derive_margin_compression(
    financial_quarters: list[RetailerFinancials],
) -> tuple[str, Optional[str]]:
    if len(financial_quarters) < 2:
        return "stable", None

    recent, prior = financial_quarters[0], financial_quarters[1]
    recent_margin = recent.gross_margin_pct
    prior_margin = prior.gross_margin_pct
    if recent_margin is None or prior_margin is None:
        return "stable", None

    recent_val = Decimal(str(recent_margin))
    prior_val = Decimal(str(prior_margin))
    if recent_val < prior_val:
        return "compressing", "retailer_financials.gross_margin_pct_qoq"
    if recent_val > prior_val:
        return "expanding", "retailer_financials.gross_margin_pct_qoq"
    return "stable", "retailer_financials.gross_margin_pct_qoq"


def _derive_unit_growth_pct(
    financials: Optional[RetailerFinancials],
) -> tuple[Optional[Decimal], Optional[str]]:
    if financials is None:
        return None, None

    if financials.apparel_yoy_growth_pct is not None:
        value = Decimal(str(financials.apparel_yoy_growth_pct)).quantize(Decimal("0.01"))
        return value, "retailer_financials.apparel_yoy_growth_pct"

    if financials.comparable_sales_growth_pct is not None:
        value = Decimal(str(financials.comparable_sales_growth_pct)).quantize(Decimal("0.01"))
        return value, "retailer_financials.comparable_sales_growth_pct"

    return None, None


def _derive_store_count_trend(
    financials: Optional[RetailerFinancials],
) -> tuple[str, Optional[str]]:
    if financials is None or financials.store_count_net_change is None:
        return "stable", None

    net_change = financials.store_count_net_change
    if net_change > 0:
        return "expanding", "retailer_financials.store_count_net_change"
    if net_change < 0:
        return "contracting", "retailer_financials.store_count_net_change"
    return "stable", "retailer_financials.store_count_net_change"


def _format_quarters(quarters: list[tuple[int, int]]) -> str:
    if not quarters:
        return "none"
    return ", ".join(f"{fy}Q{fq}" for fy, fq in sorted(quarters))


def _format_category_counts(category_counts: dict[str, int]) -> str:
    if not category_counts:
        return "none"
    return ", ".join(f"{name}={count}" for name, count in sorted(category_counts.items()))


def _build_data_sources(
    quarters: list[tuple[int, int]],
    financial_quarters: list[RetailerFinancials],
    unit_growth_source: Optional[str],
    store_trend_source: Optional[str],
    inventory_source: Optional[str],
    margin_source: Optional[str],
    has_extracts: bool,
) -> str:
    parts: list[str] = []
    if has_extracts:
        parts.append(f"retailer_intelligence_extract({_format_quarters(quarters)})")
    if financial_quarters:
        fin_q = _format_quarters(
            [_quarter_key(r.fiscal_year, r.fiscal_quarter) for r in financial_quarters]
        )
        parts.append(f"retailer_financials({fin_q})")
    for source in (
        unit_growth_source,
        store_trend_source,
        inventory_source,
        margin_source,
    ):
        if source and source not in parts:
            parts.append(source)
    return "+".join(parts) if parts else "none"


def _next_demand_signal_id(db: Session) -> int:
    current_max = db.query(func.max(DemandSignals.demand_signal_id)).scalar()
    return (current_max or 0) + 1


def _update_demand_signals(
    db: Session,
    retailer_id: int,
    buying_volume_signal: str,
    store_count_trend: str,
    inventory_improving: str,
    margin_compression: str,
) -> DemandSignals:
    existing = (
        db.query(DemandSignals)
        .filter(DemandSignals.retailer_id == retailer_id)
        .first()
    )
    if existing is None:
        existing = DemandSignals(
            demand_signal_id=_next_demand_signal_id(db),
            retailer_id=retailer_id,
        )
        db.add(existing)
        db.flush()

    existing.buying_volume_signal = buying_volume_signal
    existing.store_expansion = store_count_trend
    existing.inventory_improving = inventory_improving
    existing.margin_compression = margin_compression
    existing.status = "LIVE"
    return existing


def _derive_data_freshness_metadata(
    db: Session,
    retailer_id: int,
    extract_quarters: list[tuple[int, int]],
    financial_quarters: list[RetailerFinancials],
    as_of: date,
) -> dict[str, object]:
    """Days from as_of to the most recent period_end_date used in synthesis."""
    period_ends: list[date] = []

    for row in financial_quarters:
        if row.period_end_date is not None:
            period_ends.append(row.period_end_date)

    for fiscal_year, fiscal_quarter in extract_quarters:
        extract_end = (
            db.query(RetailerIntelligenceExtract.period_end_date)
            .filter(
                RetailerIntelligenceExtract.retailer_id == retailer_id,
                RetailerIntelligenceExtract.is_latest.is_(True),
                RetailerIntelligenceExtract.excluded_reason.is_(None),
                RetailerIntelligenceExtract.fiscal_year == fiscal_year,
                RetailerIntelligenceExtract.fiscal_quarter == fiscal_quarter,
                RetailerIntelligenceExtract.period_end_date.isnot(None),
            )
            .limit(1)
            .scalar()
        )
        if extract_end is not None:
            period_ends.append(extract_end)

    if not period_ends:
        return {
            "data_freshness_days": None,
            "earliest_data_date": None,
            "latest_data_date": None,
        }

    earliest = min(period_ends)
    latest = max(period_ends)
    return {
        "data_freshness_days": (as_of - latest).days,
        "earliest_data_date": earliest.isoformat(),
        "latest_data_date": latest.isoformat(),
    }


def _append_retailer_demand_forecast(
    db: Session,
    retailer_id: int,
    payload: dict[str, Any],
    as_of: date,
    freshness_metadata: dict[str, object],
) -> RetailerDemandForecast:
    """
    Append-only insert for retailer_demand_forecast (schema has no is_latest column).
    Latest forecast per retailer is the row with the greatest as_of_date / output_id.
    """
    metadata = {
        "retailer_id": retailer_id,
        "prediction_type": "retailer_demand",
        "retailer_type": payload.get("retailer_type"),
        "retailer_sub_type": payload.get("retailer_sub_type"),
        **freshness_metadata,
    }
    record = RetailerDemandForecast(
        retailer_id=retailer_id,
        buying_volume_signal=payload["buying_volume_signal"],
        store_count_trend=payload["store_count_trend"],
        unit_growth_pct=payload.get("unit_growth_pct"),
        category_focus=payload.get("category_focus"),
        confidence_score=payload["confidence_score"],
        as_of_date=as_of,
        fiscal_year_latest=payload.get("fiscal_year_latest"),
        fiscal_quarter_latest=payload.get("fiscal_quarter_latest"),
        model_version=MODEL_VERSION,
        metadata_json=json.dumps(metadata),
    )
    db.add(record)
    return record


def _append_prediction_log(
    db: Session,
    retailer_id: int,
    buying_volume_signal: str,
    unit_growth_pct: Optional[Decimal],
    as_of: date,
    freshness_metadata: dict[str, object],
) -> None:
    predicted = _BUYING_SIGNAL_SCORE.get(buying_volume_signal, Decimal("0.0000"))
    unit_growth = unit_growth_pct if unit_growth_pct is not None else Decimal("0.00")
    snapshot_id = f"retailer_demand_{retailer_id}_{as_of.isoformat()}"

    metadata = {
        "retailer_id": retailer_id,
        "prediction_type": "retailer_demand",
        **freshness_metadata,
    }

    existing = (
        db.query(PredictionLog)
        .filter(PredictionLog.data_snapshot_id == snapshot_id)
        .first()
    )
    payload = {
        "program_id": None,
        "spec_id": None,
        "prediction_type": "retailer_demand",
        "corridor": None,
        "predicted_value": predicted,
        "p10": unit_growth,
        "p50": unit_growth,
        "p90": unit_growth,
        "target_date": as_of,
        "model_version": MODEL_VERSION,
        "data_snapshot_id": snapshot_id,
        "metadata_json": json.dumps(metadata),
    }
    if existing is None:
        db.add(PredictionLog(**payload))
    else:
        for key, value in payload.items():
            setattr(existing, key, value)


def synthesize_retailer_demand_forecast(
    db: Session,
    as_of: Optional[date] = None,
) -> dict[str, int]:
    as_of = as_of or date.today()
    summary_counts = {"retailers": 0, "forecasts_written": 0, "demand_signals_updated": 0}

    retailers = db.query(MajorRetailers).order_by(MajorRetailers.retailer_id).all()
    for retailer in retailers:
        summary_counts["retailers"] += 1
        quarters = _recent_two_quarters(db, retailer.retailer_id)
        financial_quarters = _recent_two_financial_quarters(db, retailer.retailer_id)
        if quarters:
            current_fiscal_year, current_fiscal_quarter = quarters[0]
        elif financial_quarters:
            current_fiscal_year = financial_quarters[0].fiscal_year
            current_fiscal_quarter = financial_quarters[0].fiscal_quarter
        else:
            current_fiscal_year = None
            current_fiscal_quarter = None

        extracts = _fetch_extracts_for_quarters(db, retailer.retailer_id, quarters)
        extract_summary = _summarize_extracts(
            extracts,
            current_fiscal_year=current_fiscal_year,
            current_fiscal_quarter=current_fiscal_quarter,
        )
        has_extracts = extract_summary["extract_count"] > 0

        buying_volume_signal = _derive_buying_volume_signal(extract_summary)
        if not has_extracts:
            buying_volume_signal = "unknown"

        confidence_score = _derive_confidence_score(extract_summary, has_extracts)
        inventory_quarters = _recent_financial_quarters(
            db,
            retailer.retailer_id,
            limit=INVENTORY_DAYS_TREND_QUARTERS,
        )
        financials = financial_quarters[0] if financial_quarters else None
        unit_growth_pct, unit_growth_source = _derive_unit_growth_pct(financials)
        store_count_trend, store_trend_source = _derive_store_count_trend(financials)
        inventory_improving, inventory_source = _derive_inventory_improving(
            inventory_quarters
        )
        margin_compression, margin_source = _derive_margin_compression(financial_quarters)
        data_sources = _build_data_sources(
            quarters,
            financial_quarters,
            unit_growth_source,
            store_trend_source,
            inventory_source,
            margin_source,
            has_extracts,
        )

        fin_q_label = _format_quarters(
            [_quarter_key(r.fiscal_year, r.fiscal_quarter) for r in financial_quarters]
        )
        logger.info(
            "%s | extract_quarters=%s | positive=%d negative=%d neutral=%d | "
            "categories={%s} | signal=%s | confidence=%s | unit_growth=%s | "
            "store_trend=%s | inventory_improving=%s | margin_compression=%s | "
            "financial_quarters=%s | sources=%s",
            retailer.name,
            _format_quarters(quarters),
            float(extract_summary["positive_count"]),
            float(extract_summary["negative_count"]),
            extract_summary["neutral_count"],
            _format_category_counts(extract_summary["category_counts"]),
            buying_volume_signal,
            confidence_score,
            unit_growth_pct,
            store_count_trend,
            inventory_improving,
            margin_compression,
            fin_q_label,
            data_sources,
        )

        freshness_metadata = _derive_data_freshness_metadata(
            db,
            retailer.retailer_id,
            quarters,
            financial_quarters,
            as_of,
        )

        if quarters:
            fiscal_year_latest, fiscal_quarter_latest = quarters[0]
        elif financial_quarters:
            fiscal_year_latest = financial_quarters[0].fiscal_year
            fiscal_quarter_latest = financial_quarters[0].fiscal_quarter
        else:
            fiscal_year_latest = as_of.year
            fiscal_quarter_latest = (as_of.month - 1) // 3 + 1

        _append_retailer_demand_forecast(
            db,
            retailer.retailer_id,
            {
                "buying_volume_signal": buying_volume_signal,
                "store_count_trend": store_count_trend,
                "unit_growth_pct": unit_growth_pct,
                "category_focus": None,
                "confidence_score": confidence_score,
                "fiscal_year_latest": fiscal_year_latest,
                "fiscal_quarter_latest": fiscal_quarter_latest,
                "retailer_type": retailer.retailer_type,
                "retailer_sub_type": retailer.retailer_sub_type,
            },
            as_of,
            freshness_metadata,
        )
        summary_counts["forecasts_written"] += 1

        _update_demand_signals(
            db,
            retailer.retailer_id,
            buying_volume_signal,
            store_count_trend,
            inventory_improving,
            margin_compression,
        )
        summary_counts["demand_signals_updated"] += 1

        _append_prediction_log(
            db,
            retailer.retailer_id,
            buying_volume_signal,
            unit_growth_pct,
            as_of,
            freshness_metadata,
        )

    db.commit()
    return summary_counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Synthesize retailer demand forecasts from intelligence extracts.",
    )
    parser.add_argument(
        "--as-of",
        dest="as_of",
        type=date.fromisoformat,
        default=None,
        help="Forecast as_of_date (ISO format, default: today)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = synthesize_retailer_demand_forecast(db, as_of=args.as_of)
        logger.info(
            "Demand forecast synthesis complete: %d retailer(s), "
            "%d forecast row(s) appended, %d demand_signals row(s) updated",
            result["retailers"],
            result["forecasts_written"],
            result["demand_signals_updated"],
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()

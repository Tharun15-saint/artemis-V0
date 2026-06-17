"""
Retail intelligence engine — connects SEC retailer signals to operator programs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from database.constants import INTELLIGENCE_MODEL_VERSION
from database.models.outputs import RetailerDemandForecast
from database.models.prediction import PredictionLog
from database.models.program import Program
from database.models.retail import (
    DemandSignals,
    MajorRetailers,
    RetailerFinancials,
    SeasonalPatterns,
)
from intelligence.retail_market_signals import generate_market_signal
from intelligence.retail_narrative import correlate_period

_BUYING_SIGNAL_GROWTH: dict[str, Decimal] = {
    "strongly_increasing": Decimal("8.00"),
    "increasing": Decimal("4.00"),
    "stable": Decimal("0.00"),
    "declining": Decimal("-4.00"),
    "strongly_declining": Decimal("-8.00"),
}

_BUYING_SIGNAL_SCORE: dict[str, Decimal] = {
    "strongly_increasing": Decimal("2.0000"),
    "increasing": Decimal("1.0000"),
    "stable": Decimal("0.0000"),
    "declining": Decimal("-1.0000"),
    "strongly_declining": Decimal("-2.0000"),
}

_URGENCY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _get_latest_financials_by_retailer(db: Session) -> dict[int, RetailerFinancials]:
    """Return the most-recent RetailerFinancials row per retailer.

    "Most recent" = highest (fiscal_year * 10 + fiscal_quarter) among is_latest=True rows.
    Using the period-key arithmetic avoids a correlated subquery and works in SQLite.
    """
    max_period_sq = (
        db.query(
            RetailerFinancials.retailer_id,
            sqlfunc.max(
                RetailerFinancials.fiscal_year * 10 + RetailerFinancials.fiscal_quarter
            ).label("max_period"),
        )
        .filter(RetailerFinancials.is_latest.is_(True))
        .group_by(RetailerFinancials.retailer_id)
        .subquery()
    )

    rows = (
        db.query(RetailerFinancials)
        .join(
            max_period_sq,
            (RetailerFinancials.retailer_id == max_period_sq.c.retailer_id)
            & (
                RetailerFinancials.fiscal_year * 10 + RetailerFinancials.fiscal_quarter
                == max_period_sq.c.max_period
            ),
        )
        .filter(RetailerFinancials.is_latest.is_(True))
        .all()
    )

    result: dict[int, RetailerFinancials] = {}
    for row in rows:
        if row.retailer_id not in result:
            result[row.retailer_id] = row
    return result


def generate_implication(
    retailer: MajorRetailers,
    signals: DemandSignals,
    seasonal: Optional[SeasonalPatterns],
    financials: Optional[RetailerFinancials] = None,
) -> str:
    """
    Translate financial signals into plain English that an operator understands.
    Not 'inventory turnover improved 8%' — that is data.
    'Target is selling through faster than last year — they will need to
    replenish SS27 inventory and the commit window opens this month' — that is intelligence.
    """
    implications: list[str] = []

    if signals.inventory_improving == "improving":
        implications.append(
            f"{retailer.name} inventory turnover is improving — "
            f"healthy sell-through signals they will maintain or increase buying volumes."
        )
    elif signals.inventory_improving == "deteriorating":
        implications.append(
            f"{retailer.name} inventory is building — "
            f"risk of order cancellations or volume reductions on open programs."
        )

    if signals.margin_compression == "compressing":
        gm = (
            float(financials.gross_margin_pct)
            if financials and financials.gross_margin_pct is not None
            else None
        )
        if gm is not None:
            implications.append(
                f"Gross margin under pressure at {gm:.1f}% — "
                f"expect FOB price negotiations to be harder this season."
            )
        else:
            implications.append(
                "Gross margin under pressure — expect tighter FOB price negotiations."
            )

    if signals.store_expansion == "expanding":
        implications.append(
            f"Store count growing — "
            f"incremental volume demand across all categories."
        )

    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        commit_window = (
            f"{seasonal.ss_factory_commit_window}"
            if seasonal and seasonal.ss_factory_commit_window
            else "current window"
        )
        implications.append(
            f"Combined signals point to volume growth — "
            f"early factory commitment recommended to secure capacity "
            f"during the {commit_window} commit window."
        )

    return " ".join(implications) if implications else "Signals are neutral — monitor next quarter."


def generate_action(
    retailer: MajorRetailers,
    signals: DemandSignals,
    seasonal: Optional[SeasonalPatterns],
    open_programs: list[Program],
) -> str:
    """
    Turn intelligence into a specific, timed action.
    Every output must answer: what should the operator do THIS WEEK?
    """
    today = date.today()

    ss_window_open = today.month in [9, 10, 11]
    fw_window_open = today.month in [3, 4, 5]
    ss27_early = today.month in [6, 7, 8]

    ss_commit_label = (
        seasonal.ss_factory_commit_window
        if seasonal and seasonal.ss_factory_commit_window
        else "Sep-Nov"
    )
    fw_commit_label = (
        seasonal.fw_factory_commit_window
        if seasonal and seasonal.fw_factory_commit_window
        else "Mar-May"
    )
    freight_lead = (
        f"{seasonal.freight_book_lead_days} days"
        if seasonal and seasonal.freight_book_lead_days
        else "30 days"
    )

    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        if ss27_early:
            return (
                f"COMMIT EARLY: {retailer.name} signals are strong and SS27 factory "
                f"commit window is opening now (June-August). Secure factory capacity "
                f"before FW27 commitments compete for the same slots in the "
                f"{fw_commit_label} window. Book freight {freight_lead} before delivery."
            )
        if fw_window_open:
            return (
                f"COMMIT NOW: {retailer.name} buying signals are positive and FW factory "
                f"commit window is open ({fw_commit_label}). Lock in capacity for "
                f"{ss_commit_label} delivery. Book freight {freight_lead} before delivery."
            )
        if ss_window_open:
            return (
                f"COMMIT NOW: {retailer.name} buying signals are positive and SS factory "
                f"commit window is open ({ss_commit_label}). Lock in capacity before "
                f"peak slot competition."
            )

    if signals.inventory_improving == "deteriorating":
        at_risk_programs = [p for p in open_programs if p.status == "IN_PRODUCTION"]
        if at_risk_programs:
            return (
                f"MONITOR CLOSELY: {retailer.name} inventory is building. "
                f"Risk of order reduction on {len(at_risk_programs)} open program(s). "
                f"Confirm delivery schedule with buyer before committing more production."
            )

    if signals.margin_compression == "compressing":
        return (
            f"PREPARE FOR PRICE PRESSURE: {retailer.name} gross margin is compressing. "
            f"Expect tighter FOB negotiation. Use Artemis corridor comparison to "
            f"identify lowest-cost sourcing option before buyer meeting."
        )

    return f"No immediate action required for {retailer.name}. Review again next quarter."


def _fundamental_direction(signals: DemandSignals) -> str:
    """Collapse the categorical demand signals into up / down / flat."""
    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        return "up"
    if signals.buying_volume_signal in ("declining", "strongly_declining"):
        return "down"
    if signals.inventory_improving == "deteriorating":
        return "down"
    return "flat"


def _synthesize_market_alignment(
    signals: DemandSignals,
    market: Optional[dict[str, Any]],
) -> str:
    """One sentence relating the market's view to the fundamental demand signal.

    Agreement reinforces conviction; divergence is flagged as the more
    interesting signal — the market often moves a quarter ahead of the print.
    """
    if not market:
        return ""
    market_signal = market.get("market_demand_signal")
    if market_signal in (None, "unknown"):
        return ""

    fundamental = _fundamental_direction(signals)
    market_dir = {"bullish": "up", "bearish": "down", "neutral": "flat"}.get(market_signal, "flat")

    if fundamental == market_dir and fundamental != "flat":
        return (
            f"Market confirms the fundamentals — equity is pricing the same "
            f"{'strength' if fundamental == 'up' else 'weakness'}, raising conviction."
        )
    if fundamental != "flat" and market_dir != "flat" and fundamental != market_dir:
        return (
            f"DIVERGENCE: fundamentals read {fundamental} but the market is pricing "
            f"{market_signal} — the Street is often a quarter ahead; weight the market view "
            f"and confirm order intent directly with the buyer."
        )
    if fundamental == "flat" and market_dir != "flat":
        return (
            f"Market is already leaning {market_signal} while the reported signals are still "
            f"neutral — an early read worth watching into next quarter."
        )
    return ""


def calculate_urgency(
    signals: DemandSignals,
    seasonal: Optional[SeasonalPatterns],
    market: Optional[dict[str, Any]] = None,
) -> str:
    """Score how urgently the operator should act on this retailer's signals.

    The market view can escalate urgency: a bearish tape on an at-risk retailer,
    or a fundamentals/market divergence, both warrant faster operator attention.
    """
    today = date.today()
    in_commit_window = today.month in [3, 4, 5, 6, 7, 8, 9, 10, 11]

    market_signal = market.get("market_demand_signal") if market else None

    if signals.inventory_improving == "deteriorating":
        return "HIGH"

    # Market pricing demand weakness on a non-growing retailer → act now.
    if market_signal == "bearish" and _fundamental_direction(signals) != "up":
        return "HIGH"

    if signals.buying_volume_signal in ("strongly_increasing", "increasing") and in_commit_window:
        return "HIGH"

    # Fundamentals/market divergence is worth a closer, sooner look.
    if (
        market_signal in ("bullish", "bearish")
        and _fundamental_direction(signals) != "flat"
        and {"bullish": "up", "bearish": "down"}[market_signal]
        != _fundamental_direction(signals)
    ):
        return "MEDIUM"

    if signals.margin_compression == "compressing":
        return "MEDIUM"

    if signals.buying_volume_signal in ("declining", "strongly_declining"):
        return "MEDIUM"

    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        return "MEDIUM"

    return "LOW"


def calculate_unit_growth(
    financials: Optional[RetailerFinancials],
    buying_volume_signal: Optional[str] = None,
) -> Decimal:
    """Map retailer signals to an estimated unit growth percentage."""
    if buying_volume_signal:
        return _BUYING_SIGNAL_GROWTH.get(buying_volume_signal, Decimal("0.00"))

    if financials is None or financials.total_net_sales_usd is None:
        return Decimal("0.00")

    if financials.inventory_days is not None:
        if financials.inventory_days <= Decimal("45"):
            return Decimal("3.00")
        if financials.inventory_days >= Decimal("90"):
            return Decimal("-3.00")

    return Decimal("0.00")


def derive_category_focus(
    retailer: MajorRetailers,
    financials: Optional[RetailerFinancials] = None,
) -> str:
    """Infer primary category focus from disclosed revenue mix or retailer type."""
    if (
        financials is not None
        and financials.apparel_revenue_usd is not None
        and financials.total_net_sales_usd is not None
        and financials.total_net_sales_usd > 0
    ):
        apparel_share = (
            financials.apparel_revenue_usd / financials.total_net_sales_usd * Decimal("100")
        )
        if apparel_share >= Decimal("80"):
            return "apparel_core"
        if apparel_share >= Decimal("50"):
            return "apparel_mixed"

    name = (retailer.name or "").lower()
    if "amazon" in name or "walmart" in name:
        return "broadline_multi_category"
    if any(token in name for token in ("tjx", "ross", "burlington")):
        return "off_price_apparel"
    if "gap" in name or "pvh" in name:
        return "branded_apparel"
    return "general_apparel"


def calculate_confidence(financials: Optional[RetailerFinancials]) -> Decimal:
    """Confidence reflects completeness of SEC-sourced retailer fundamentals."""
    if financials is None:
        return Decimal("0.55")
    present = sum(
        1
        for value in (
            financials.total_net_sales_usd,
            financials.gross_margin_pct,
            financials.inventory_days,
            financials.store_count_total,
        )
        if value is not None
    )
    return Decimal(str(min(0.95, 0.55 + present * 0.10))).quantize(Decimal("0.01"))


def derive_demand_trend(
    db: Session,
    retailer_id: int,
    lookback_quarters: int = 6,
) -> Optional[dict[str, Any]]:
    """Derive a demand-trend read from the retailer's comparable-sales trajectory.

    Comparable sales is the cleanest like-for-like demand proxy a retailer
    discloses (strips out new-store and FX noise). We read the last few quarters
    of real comp-sales figures and classify the *shape* of demand:

      - recovering    : returned to positive after a run of negative prints
      - accelerating  : positive and the latest print is the strongest
      - steady_growth : consistently positive, little change
      - decelerating  : positive but slowing
      - softening     : turned negative after positive prints
      - contracting   : persistently negative

    Returns None when there isn't enough comp-sales history to read a trend.
    """
    rows = (
        db.query(RetailerFinancials)
        .filter(
            RetailerFinancials.retailer_id == retailer_id,
            RetailerFinancials.is_latest.is_(True),
            RetailerFinancials.comparable_sales_growth_pct.isnot(None),
        )
        .order_by(
            RetailerFinancials.fiscal_year.desc(),
            RetailerFinancials.fiscal_quarter.desc(),
        )
        .limit(lookback_quarters)
        .all()
    )
    if len(rows) < 3:
        return None

    # rows are newest-first; comps[0] is the latest quarter.
    comps = [float(r.comparable_sales_growth_pct) for r in rows]
    latest = comps[0]
    prior = comps[1]
    older = comps[2:]

    positive = latest >= 0
    prior_run_negative = prior < 0 and (not older or all(c < 0 for c in older[:1]))
    is_strongest = latest >= max(comps)

    if positive and prior < 0:
        classification = "recovering"
        narrative = (
            f"Comparable sales inflected positive to {latest:+.1f}% after "
            f"{prior:+.1f}% — demand is recovering; an early read for restocking."
        )
    elif positive and is_strongest and latest - prior >= 0.5:
        classification = "accelerating"
        narrative = (
            f"Comparable sales accelerating to {latest:+.1f}% (from {prior:+.1f}%) — "
            f"demand strengthening; buying volumes likely to follow."
        )
    elif positive and abs(latest - prior) < 0.5:
        classification = "steady_growth"
        narrative = (
            f"Comparable sales holding at {latest:+.1f}% — steady, dependable "
            f"demand; plan capacity to the trend, not to surprises."
        )
    elif positive and latest < prior:
        classification = "decelerating"
        narrative = (
            f"Comparable sales positive but slowing to {latest:+.1f}% "
            f"(from {prior:+.1f}%) — demand still growing, momentum fading."
        )
    elif not positive and prior >= 0:
        classification = "softening"
        narrative = (
            f"Comparable sales turned negative to {latest:+.1f}% after "
            f"{prior:+.1f}% — demand softening; watch open programs for pullback."
        )
    else:
        classification = "contracting"
        narrative = (
            f"Comparable sales negative at {latest:+.1f}% — demand contracting; "
            f"treat new commitments with this retailer cautiously."
        )

    return {
        "classification": classification,
        "latest_comp_sales_pct": round(latest, 2),
        "trajectory_pct": [round(c, 2) for c in comps],
        "narrative": narrative,
    }


def detect_current_season_context(
    month: int,
    seasonal: Optional[SeasonalPatterns],
) -> str:
    """Describe where the operator sits in the seasonal commit calendar."""
    if month in [6, 7, 8]:
        return "SS27 early commit window opening (June-August)"

    if month in [9, 10, 11]:
        window = seasonal.ss_factory_commit_window if seasonal else "Sep-Nov"
        return f"SS factory commit window open ({window})"

    if month in [3, 4, 5]:
        window = seasonal.fw_factory_commit_window if seasonal else "Mar-May"
        return f"FW factory commit window open ({window})"

    return "Between commit windows — monitor retailer signals for next season"


def generate_commit_rationale(
    retailer: MajorRetailers,
    signals: DemandSignals,
    month: int,
) -> str:
    """Explain why this retailer should be committed now, later, or first."""
    if signals.buying_volume_signal == "strongly_increasing":
        rationale = f"Commit {retailer.name} first — strongest buying signals"
    elif signals.buying_volume_signal == "increasing":
        rationale = f"Prioritize {retailer.name} — positive volume signals"
    elif signals.margin_compression == "compressing":
        rationale = (
            f"Defer {retailer.name} price-sensitive commits until corridor "
            f"optimization is complete"
        )
    else:
        rationale = f"{retailer.name} stable — commit in standard window order"

    if month in [6, 7, 8] and signals.buying_volume_signal in (
        "strongly_increasing",
        "increasing",
    ):
        return f"{rationale}; SS27 window opening now"
    return rationale


def _upsert_retailer_forecast(
    db: Session,
    retailer: MajorRetailers,
    signals: DemandSignals,
    intel: dict[str, Any],
    as_of: date,
    financials: Optional[RetailerFinancials] = None,
) -> RetailerDemandForecast:
    existing = (
        db.query(RetailerDemandForecast)
        .filter(
            RetailerDemandForecast.retailer_id == retailer.retailer_id,
            RetailerDemandForecast.as_of_date == as_of,
        )
        .first()
    )

    payload = {
        "buying_volume_signal": intel["buying_volume_signal"],
        "store_count_trend": intel["store_trajectory"],
        "unit_growth_pct": calculate_unit_growth(financials, intel["buying_volume_signal"]),
        "category_focus": derive_category_focus(retailer, financials),
        "confidence_score": calculate_confidence(financials),
        "as_of_date": as_of,
        "model_version": INTELLIGENCE_MODEL_VERSION,
    }

    if existing is None:
        existing = RetailerDemandForecast(
            retailer_id=retailer.retailer_id,
            **payload,
        )
        db.add(existing)
    else:
        for key, value in payload.items():
            setattr(existing, key, value)

    return existing


def _write_prediction_log(
    db: Session,
    retailer: MajorRetailers,
    intel: dict[str, Any],
    as_of: date,
    financials: Optional[RetailerFinancials] = None,
) -> PredictionLog:
    predicted = _BUYING_SIGNAL_SCORE.get(
        intel["buying_volume_signal"],
        Decimal("0.0000"),
    )
    unit_growth = calculate_unit_growth(financials, intel["buying_volume_signal"])

    snapshot_id = f"retailer_{retailer.retailer_id}_{as_of.isoformat()}"
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
        "model_version": INTELLIGENCE_MODEL_VERSION,
        "data_snapshot_id": snapshot_id,
    }

    if existing is None:
        record = PredictionLog(**payload)
        db.add(record)
    else:
        for key, value in payload.items():
            setattr(existing, key, value)
        record = existing

    return record


def generate_retailer_intelligence(importer_id: int, db: Session) -> dict:
    """
    For a specific importer, combine:
    1. What each retailer's financial health signals
    2. What the operator's historical relationship with each retailer is
    3. What the seasonal commit windows say about timing
    4. What the operator's current open programs look like
    5. What action they should take RIGHT NOW

    Returns a dict with retailer-by-retailer intelligence and
    a prioritised action list.
    """
    retailers_with_signals = (
        db.query(MajorRetailers, DemandSignals)
        .join(DemandSignals, DemandSignals.retailer_id == MajorRetailers.retailer_id)
        .filter(DemandSignals.is_latest.is_(True))
        .all()
    )

    open_programs = (
        db.query(Program)
        .filter(
            Program.importer_id == importer_id,
            Program.status.in_(["PLANNING", "COMMITTED", "IN_PRODUCTION"]),
        )
        .all()
    )

    seasonal = db.query(SeasonalPatterns).first()
    financials_by_retailer = _get_latest_financials_by_retailer(db)
    as_of = date.today()

    retailer_intelligence: list[dict[str, Any]] = []
    for retailer, signals in retailers_with_signals:
        financials = financials_by_retailer.get(retailer.retailer_id)
        market = generate_market_signal(db, retailer.retailer_id)

        base_implication = generate_implication(retailer, signals, seasonal, financials)
        alignment = _synthesize_market_alignment(signals, market)
        implication = (
            f"{base_implication} {alignment}".strip() if alignment else base_implication
        )

        intel = {
            "retailer_id": retailer.retailer_id,
            "retailer_name": retailer.name,
            "buying_volume_signal": signals.buying_volume_signal,
            "inventory_status": signals.inventory_improving,
            "margin_pressure": signals.margin_compression,
            "store_trajectory": signals.store_expansion,
            "fiscal_year": signals.fiscal_year,
            "fiscal_quarter": signals.fiscal_quarter,
            "gross_margin_pct": (
                float(financials.gross_margin_pct)
                if financials and financials.gross_margin_pct is not None
                else None
            ),
            "inventory_days": (
                float(financials.inventory_days)
                if financials and financials.inventory_days is not None
                else None
            ),
            "apparel_revenue_usd": (
                float(financials.apparel_revenue_usd)
                if financials and financials.apparel_revenue_usd is not None
                else None
            ),
            "comparable_sales_growth_pct": (
                float(financials.comparable_sales_growth_pct)
                if financials and financials.comparable_sales_growth_pct is not None
                else None
            ),
            "store_count_total": (
                financials.store_count_total
                if financials and financials.store_count_total is not None
                else None
            ),
            "category_focus": derive_category_focus(retailer, financials),
            "unit_growth_outlook": float(
                calculate_unit_growth(financials, signals.buying_volume_signal)
            ),
            "demand_trend": derive_demand_trend(db, retailer.retailer_id),
            "confidence": float(calculate_confidence(financials)),
            "earnings_call_context": (
                correlate_period(
                    db,
                    retailer.retailer_id,
                    signals.fiscal_year,
                    signals.fiscal_quarter,
                    top_n=4,
                )
                if signals.fiscal_year is not None and signals.fiscal_quarter is not None
                else None
            ),
            "market": market,
            "market_demand_signal": market["market_demand_signal"] if market else None,
            "implication": implication,
            "recommended_action": generate_action(
                retailer, signals, seasonal, open_programs
            ),
            "urgency": calculate_urgency(signals, seasonal, market),
        }
        retailer_intelligence.append(intel)

        _upsert_retailer_forecast(db, retailer, signals, intel, as_of, financials)
        _write_prediction_log(db, retailer, intel, as_of, financials)

    db.commit()

    prioritised_actions = sorted(
        retailer_intelligence,
        key=lambda item: _URGENCY_RANK.get(item["urgency"], 99),
    )

    return {
        "importer_id": importer_id,
        "as_of_date": str(as_of),
        "model_version": INTELLIGENCE_MODEL_VERSION,
        "seasonal_context": detect_current_season_context(as_of.month, seasonal),
        "retailer_intelligence": retailer_intelligence,
        "prioritised_actions": [
            {
                "retailer_name": item["retailer_name"],
                "urgency": item["urgency"],
                "action": item["recommended_action"],
            }
            for item in prioritised_actions
        ],
    }


def detect_order_cancellation_risk(importer_id: int, db: Session) -> list[dict]:
    """
    Cross-reference retailer inventory signals with open programs.
    If a retailer shows inventory build risk AND the operator has
    programs IN_PRODUCTION for that season, flag the risk explicitly.

    This is one of the highest-value signals Artemis can surface —
    knowing a cancellation is coming before the buyer calls.
    """
    at_risk_retailers = (
        db.query(MajorRetailers)
        .join(DemandSignals, DemandSignals.retailer_id == MajorRetailers.retailer_id)
        .filter(
            DemandSignals.is_latest.is_(True),
            DemandSignals.inventory_improving == "deteriorating",
        )
        .all()
    )

    if not at_risk_retailers:
        return []

    active_programs = (
        db.query(Program)
        .filter(
            Program.importer_id == importer_id,
            Program.status.in_(["COMMITTED", "IN_PRODUCTION"]),
        )
        .all()
    )

    if not active_programs:
        return []

    financials_by_retailer = _get_latest_financials_by_retailer(db)
    in_production = [p for p in active_programs if p.status == "IN_PRODUCTION"]
    committed = [p for p in active_programs if p.status == "COMMITTED"]

    risks: list[dict[str, Any]] = []
    for retailer in at_risk_retailers:
        fin = financials_by_retailer.get(retailer.retailer_id)
        at_risk_in_prod = len(in_production)
        at_risk_committed = len(committed)
        urgency = "HIGH" if at_risk_in_prod > 0 else "MEDIUM"
        program_summary = (
            f"{at_risk_in_prod} IN_PRODUCTION, {at_risk_committed} COMMITTED"
        )
        risks.append(
            {
                "retailer_id": retailer.retailer_id,
                "retailer_name": retailer.name,
                "risk_type": "order_cancellation",
                "signal": "inventory_deteriorating",
                "inventory_days": float(fin.inventory_days) if fin and fin.inventory_days else None,
                "gross_margin_pct": float(fin.gross_margin_pct) if fin and fin.gross_margin_pct else None,
                "programs_at_risk": program_summary,
                "recommended_action": (
                    f"Contact {retailer.name} buyer to confirm open program "
                    f"volumes — {program_summary} programs in the pipeline. "
                    f"Inventory signal indicates potential volume reduction. "
                    f"Do not commit new CMT capacity until delivery schedule confirmed."
                ),
                "urgency": urgency,
            }
        )

    return risks


def generate_commit_timing_intelligence(importer_id: int, db: Session) -> dict:
    """
    Map retailer health signals to seasonal commit windows.
    Tell the operator exactly which retailers to commit with first,
    in what order, and why — based on their financial health and
    the current position in the seasonal calendar.
    """
    open_programs = (
        db.query(Program)
        .filter(
            Program.importer_id == importer_id,
            Program.status.in_(["PLANNING", "COMMITTED", "IN_PRODUCTION"]),
        )
        .all()
    )
    program_count = len(open_programs)
    today = date.today()
    month = today.month

    seasonal = db.query(SeasonalPatterns).first()
    retailers = (
        db.query(MajorRetailers, DemandSignals)
        .join(DemandSignals, DemandSignals.retailer_id == MajorRetailers.retailer_id)
        .filter(DemandSignals.is_latest.is_(True))
        .all()
    )

    commit_priority: list[dict[str, Any]] = []
    for retailer, signals in retailers:
        priority_score = 0
        if signals.buying_volume_signal == "strongly_increasing":
            priority_score = 5
        elif signals.buying_volume_signal == "increasing":
            priority_score = 4
        elif signals.buying_volume_signal == "stable":
            priority_score = 3
        elif signals.buying_volume_signal == "declining":
            priority_score = 2
        else:
            priority_score = 1

        if signals.margin_compression != "compressing":
            priority_score += 1

        commit_priority.append(
            {
                "retailer_name": retailer.name,
                "priority_score": priority_score,
                "signal": signals.buying_volume_signal,
                "margin_pressure": signals.margin_compression,
                "commit_rationale": generate_commit_rationale(retailer, signals, month),
            }
        )

    commit_priority.sort(key=lambda item: item["priority_score"], reverse=True)

    return {
        "importer_id": importer_id,
        "open_programs": program_count,
        "as_of_date": str(today),
        "season_context": detect_current_season_context(month, seasonal),
        "commit_priority": commit_priority,
        "top_action": commit_priority[0]["commit_rationale"] if commit_priority else None,
    }

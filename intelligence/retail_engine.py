"""
Retail intelligence engine — connects SEC retailer signals to operator programs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from database.constants import INTELLIGENCE_MODEL_VERSION
from database.models.outputs import RetailerDemandForecast
from database.models.prediction import PredictionLog
from database.models.program import Program
from database.models.retail import DemandSignals, MajorRetailers, SeasonalPatterns

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


def generate_implication(
    retailer: MajorRetailers,
    signals: DemandSignals,
    seasonal: Optional[SeasonalPatterns],
) -> str:
    """
    Translate financial signals into plain English that an operator understands.
    Not 'inventory turnover improved 8%' — that is data.
    'Target is selling through faster than last year — they will need to
    replenish SS27 inventory and the commit window opens this month' — that is intelligence.
    """
    _ = seasonal
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

    if signals.margin_compression == "compressing" and retailer.gross_margin is not None:
        implications.append(
            f"Gross margin under pressure at {float(retailer.gross_margin):.1f}% — "
            f"expect FOB price negotiations to be harder this season."
        )

    if signals.store_expansion == "expanding":
        implications.append(
            f"Store count growing — "
            f"incremental volume demand across all categories."
        )

    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        implications.append(
            f"Combined signals point to volume growth — "
            f"early factory commitment recommended to secure capacity."
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
    _ = seasonal
    today = date.today()

    ss_window_open = today.month in [9, 10, 11]
    fw_window_open = today.month in [3, 4, 5]
    ss27_early = today.month in [6, 7, 8]
    _ = ss_window_open

    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        if ss27_early:
            return (
                f"COMMIT EARLY: {retailer.name} signals are strong and SS27 factory "
                f"commit window is opening now (June-August). Secure factory capacity "
                f"before FW27 commitments compete for the same slots in March 2027."
            )
        if fw_window_open:
            return (
                f"COMMIT NOW: {retailer.name} buying signals are positive and FW factory "
                f"commit window is open. Lock in capacity for Sep-Nov delivery."
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


def calculate_urgency(
    signals: DemandSignals,
    seasonal: Optional[SeasonalPatterns],
) -> str:
    """Score how urgently the operator should act on this retailer's signals."""
    _ = seasonal
    today = date.today()
    in_commit_window = today.month in [3, 4, 5, 6, 7, 8, 9, 10, 11]

    if signals.inventory_improving == "deteriorating":
        return "HIGH"

    if signals.buying_volume_signal in ("strongly_increasing", "increasing") and in_commit_window:
        return "HIGH"

    if signals.margin_compression == "compressing":
        return "MEDIUM"

    if signals.buying_volume_signal in ("declining", "strongly_declining"):
        return "MEDIUM"

    if signals.buying_volume_signal in ("strongly_increasing", "increasing"):
        return "MEDIUM"

    return "LOW"


def calculate_unit_growth(
    retailer: MajorRetailers,
    buying_volume_signal: Optional[str] = None,
) -> Decimal:
    """Map stored retailer metrics to an estimated unit growth percentage."""
    if buying_volume_signal:
        return _BUYING_SIGNAL_GROWTH.get(buying_volume_signal, Decimal("0.00"))

    if retailer.total_sales is None:
        return Decimal("0.00")

    if retailer.inventory_turnover is not None and retailer.inventory_turnover >= Decimal("8"):
        return Decimal("3.00")
    if retailer.inventory_turnover is not None and retailer.inventory_turnover <= Decimal("4"):
        return Decimal("-3.00")

    return Decimal("0.00")


def derive_category_focus(retailer: MajorRetailers) -> str:
    """Infer primary category focus from disclosed revenue mix or retailer type."""
    if (
        retailer.apparel_revenue is not None
        and retailer.total_sales is not None
        and retailer.total_sales > 0
    ):
        apparel_share = retailer.apparel_revenue / retailer.total_sales * Decimal("100")
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


def calculate_confidence(retailer: MajorRetailers) -> Decimal:
    """Confidence reflects completeness of SEC-sourced retailer fundamentals."""
    present = sum(
        1
        for value in (
            retailer.total_sales,
            retailer.gross_margin,
            retailer.inventory_turnover,
            retailer.store_count,
        )
        if value is not None
    )
    return Decimal(str(min(0.95, 0.55 + present * 0.10))).quantize(Decimal("0.01"))


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
) -> RetailerDemandForecast:
    _ = signals
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
        "unit_growth_pct": calculate_unit_growth(
            retailer, intel["buying_volume_signal"]
        ),
        "category_focus": derive_category_focus(retailer),
        "confidence_score": calculate_confidence(retailer),
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
) -> PredictionLog:
    predicted = _BUYING_SIGNAL_SCORE.get(
        intel["buying_volume_signal"],
        Decimal("0.0000"),
    )
    unit_growth = calculate_unit_growth(retailer, intel["buying_volume_signal"])

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
    as_of = date.today()

    retailer_intelligence: list[dict[str, Any]] = []
    for retailer, signals in retailers_with_signals:
        intel = {
            "retailer_id": retailer.retailer_id,
            "retailer_name": retailer.name,
            "buying_volume_signal": signals.buying_volume_signal,
            "inventory_status": signals.inventory_improving,
            "margin_pressure": signals.margin_compression,
            "store_trajectory": signals.store_expansion,
            "gross_margin_pct": retailer.gross_margin,
            "inventory_turnover": retailer.inventory_turnover,
            "implication": generate_implication(retailer, signals, seasonal),
            "recommended_action": generate_action(
                retailer, signals, seasonal, open_programs
            ),
            "urgency": calculate_urgency(signals, seasonal),
        }
        retailer_intelligence.append(intel)

        _upsert_retailer_forecast(db, retailer, signals, intel, as_of)
        _write_prediction_log(db, retailer, intel, as_of)

    db.commit()

    prioritised_actions = sorted(
        retailer_intelligence,
        key=lambda item: _URGENCY_RANK.get(item["urgency"], 99),
    )

    return {
        "importer_id": importer_id,
        "as_of_date": str(as_of),
        "model_version": INTELLIGENCE_MODEL_VERSION,
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
        .filter(DemandSignals.inventory_improving == "deteriorating")
        .all()
    )

    active_programs = (
        db.query(Program)
        .filter(
            Program.importer_id == importer_id,
            Program.status.in_(["COMMITTED", "IN_PRODUCTION"]),
        )
        .all()
    )

    risks: list[dict[str, Any]] = []
    for program in active_programs:
        for retailer in at_risk_retailers:
            risks.append(
                {
                    "program_id": program.program_id,
                    "season": program.season,
                    "retailer_name": retailer.name,
                    "risk_type": "order_cancellation",
                    "signal": "inventory_deteriorating",
                    "inventory_turnover": retailer.inventory_turnover,
                    "gross_margin": retailer.gross_margin,
                    "recommended_action": (
                        f"Contact {retailer.name} buyer to confirm {program.season} "
                        f"program volumes before CMT start date. "
                        f"Their inventory turnover signals potential volume reduction."
                    ),
                    "urgency": "HIGH" if program.status == "IN_PRODUCTION" else "MEDIUM",
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
    _ = importer_id
    today = date.today()
    month = today.month

    seasonal = db.query(SeasonalPatterns).first()
    retailers = (
        db.query(MajorRetailers, DemandSignals)
        .join(DemandSignals, DemandSignals.retailer_id == MajorRetailers.retailer_id)
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
        "as_of_date": str(today),
        "season_context": detect_current_season_context(month, seasonal),
        "commit_priority": commit_priority,
        "top_action": commit_priority[0]["commit_rationale"] if commit_priority else None,
    }

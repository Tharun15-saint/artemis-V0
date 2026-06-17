"""Artemis synthesis engine — truth-seeking apparel market intelligence."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

import anthropic
from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from data.ingestion._env import load_project_env
from database.database import SessionLocal
from database.models import (
    CommodityFutures,
    CompanyFactoryRelationship,
    CompanyProfile,
    CostLayerPrior,
    CostVariablePrior,
    BunkerFuelPrices,
    CottonSupplyDemand,
    CrudeOil,
    FactoryFinancingCost,
    FxRates,
    GeopoliticalRiskEvent,
    GovernmentExportIncentive,
    HedgeOpportunityRecommendation,
    LearnedCoefficient,
    MarineInsurance,
    OceanFreightRates,
    PolyesterPetChips,
    Program,
    PurchaseOrderHistory,
    PxParaxylene,
    ShippingLaneRisk,
    UsImportDutyRate,
)
from intelligence.cost_reasoning.engine import CostReasoningEngine, ProgramSpec
from intelligence.crude_cost_inputs import CrudeCostInputs
from intelligence.exceptions import CrudeDataStaleError

load_project_env()

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

DOZENS_PER_CONTAINER = 5000
CIF_ASSUMPTION_USD = 13.0

CORRIDOR_FX = {
    "India": "usd_inr",
    "Bangladesh": "usd_bdt",
    "Vietnam": "usd_vnd",
    "China": "usd_cny",
    "Turkey": "usd_try",
    "Pakistan": "usd_pkr",
}

# Ocean freight is stored one row per (origin_port, destination_port) corridor
# since migration 353dd690a9d9 — not wide columns. get_freight_snapshot iterates
# over is_latest rows directly. The 40ft high-cube rate is the published WCI unit.

SYSTEM_PROMPT_MARKET_BRIEF = """
You are Artemis — an apparel supply chain intelligence system built for
large apparel importers who have relationships with major US retailers
(Target, TJX Companies, Walmart, Burlington Coat Factory, Ross Stores,
Gap, PVH Corp).

THE STAKES YOU ARE OPERATING AT:
The operators who read your output make sourcing, hedging, and capital
decisions worth hundreds of thousands to millions of dollars per program.
A single overconfident estimate, a missed signal, or a smoothed-over
data gap can cause a real, measurable financial loss to your customer.
You will be held to the same standard as a senior advisor at a
top-tier commodity or supply chain consultancy.

YOUR ABSOLUTE PRINCIPLES:

1. TRUTH ABOVE ALL
   Every claim must be grounded in the data provided.
   Never extrapolate beyond what the data supports.
   Never produce a number without its basis.
   If you are uncertain, say so explicitly.

2. CITE EVERYTHING
   Every significant claim includes: (a) what the data shows,
   (b) when it was last updated, (c) the source.

3. NAME CONTRADICTIONS EXPLICITLY
   When two signals point in different directions, do not smooth over it.

4. QUANTIFY EVERYTHING IN DOLLARS
   Market commentary without dollar impact is not intelligence.

5. CONFIDENCE IS EARNED, NOT ASSUMED
   Use HIGH CONFIDENCE, MODERATE CONFIDENCE, LOW CONFIDENCE, INSUFFICIENT DATA.

6. MISSING DATA IS INTELLIGENCE
   State gaps clearly. Never fill with generic estimates without labelling.

7. DISTINGUISH WHAT YOU KNOW FROM WHAT YOU INFER

8. PRESERVE THE OPERATOR'S JUDGMENT
   Use "the data suggests", not "you must".

FORMATTING RULES:
- Numbers in bold
- Confidence inline
- Data gaps: ⚠
- Signal conflicts: ⚡ SIGNAL CONFLICT
- Active voice. Short sentences. No filler.
"""

SYSTEM_PROMPT_PROGRAM_INTELLIGENCE = (
    SYSTEM_PROMPT_MARKET_BRIEF
    + """
ADDITIONAL CONTEXT FOR PROGRAM INTELLIGENCE:
You are analysing a specific sourcing program.
Always show: cost estimate with layer breakdown, quote vs benchmark,
quantified risks, honest unknowns, and the one most important decision.
Never tell them which factory to use or make the sourcing decision for them.
"""
)


def _days_old(as_of: date) -> int:
    return (date.today() - as_of).days


def _quality_from_freshness(days: int, stale_threshold: int, base: float = 1.0) -> float:
    if days <= 1:
        return base
    if days <= stale_threshold:
        return max(0.5, base - (days / stale_threshold) * 0.25)
    return max(0.2, base - 0.5 - (days - stale_threshold) * 0.05)


def _su_signal(ratio_pct: float) -> tuple[str, str]:
    if ratio_pct < 40:
        return "SPIKE_RISK", "Stocks-to-use below 40% — historically associated with price spike risk."
    if ratio_pct > 60:
        return "BEARISH", "Stocks-to-use above 60% — ample supply relative to use; bearish price bias."
    if ratio_pct > 50:
        return "NEUTRAL", "Balanced stocks-to-use — no strong directional bias from fundamentals."
    return "BULLISH", "Stocks-to-use below 50% — tightening balance sheet; modest bullish bias."


def _curve_shape(spot: float, m3: float, m12: float) -> str:
    if m12 > spot * 1.02 and m3 > spot:
        return "contango"
    if m12 < spot * 0.98:
        return "backwardation"
    return "flat"


def get_cotton_snapshot(db: Session) -> dict:
    gaps: list[str] = []
    curve_row = (
        db.query(CommodityFutures)
        .filter(CommodityFutures.is_latest.is_(True))
        .order_by(desc(CommodityFutures.as_of_date))
        .first()
    )
    wasde = (
        db.query(CottonSupplyDemand)
        .order_by(desc(CottonSupplyDemand.report_month))
        .first()
    )

    if not curve_row:
        gaps.append("No cotton spot or futures curve in database")
        return {
            "spot_cents_per_lb": None,
            "gaps": gaps,
            "is_stale": True,
            "freshness_days": 999,
            "quality_score": 0.0,
            "source": "none",
        }

    spot = float(curve_row.ice_cotton_2_spot)
    m3 = float(curve_row.ice_cotton_2_3m)
    m6 = float(curve_row.ice_cotton_2_6m)
    m9 = float(curve_row.ice_cotton_2_9m)
    m12 = float(curve_row.ice_cotton_2_12m)
    premium = ((m12 - spot) / spot * 100) if spot else 0.0
    freshness = _days_old(curve_row.as_of_date)
    is_stale = freshness > 10
    is_real = (curve_row.status or "").upper() != "SYNTHETIC"

    prior_row = (
        db.query(CommodityFutures)
        .filter(
            CommodityFutures.is_latest.is_(True),
            CommodityFutures.as_of_date
            <= curve_row.as_of_date - timedelta(days=7),
        )
        .order_by(desc(CommodityFutures.as_of_date))
        .first()
    )
    wow_pct = wow_cents = None
    direction = "stable"
    if prior_row:
        prior_spot = float(prior_row.ice_cotton_2_spot)
        wow_cents = spot - prior_spot
        wow_pct = (wow_cents / prior_spot * 100) if prior_spot else None
        if wow_pct and wow_pct > 0.5:
            direction = "rising"
        elif wow_pct and wow_pct < -0.5:
            direction = "falling"
    else:
        gaps.append("No cotton row from 7 days prior for week-over-week comparison")

    su_ratio = su_signal = su_meaning = None
    prod = use = stocks = balance = None
    wasde_as_of = None
    if wasde:
        su_ratio = float(wasde.world_stocks_to_use_ratio_pct)
        su_signal, su_meaning = _su_signal(su_ratio)
        prod = float(wasde.world_production_million_bales)
        use = float(wasde.world_mill_use_million_bales)
        stocks = float(wasde.world_ending_stocks_million_bales)
        balance = prod - use
        wasde_as_of = wasde.report_month.isoformat()
    else:
        gaps.append("No USDA WASDE supply/demand data")

    month = date.today().month
    us_season = 3 <= month <= 10
    planted = (
        float(wasde.us_pct_planted) if wasde and wasde.us_pct_planted is not None else None
    )
    condition = (
        float(wasde.us_crop_condition_good_excellent_pct)
        if wasde and wasde.us_crop_condition_good_excellent_pct is not None
        else None
    )

    quality = _quality_from_freshness(freshness, 10, 0.95 if is_real else 0.65)
    if is_stale:
        quality *= 0.7

    return {
        "spot_cents_per_lb": spot,
        "curve_3m": m3,
        "curve_6m": m6,
        "curve_9m": m9,
        "curve_12m": m12,
        "curve_shape": _curve_shape(spot, m3, m12),
        "curve_premium_12m_pct": premium,
        "is_curve_real": is_real,
        "curve_caveat": (
            None
            if is_real
            else "Synthetic S/U-calibrated curve — real ICE individual contract data unavailable for this date"
        ),
        "su_ratio_pct": su_ratio,
        "su_signal": su_signal,
        "su_signal_meaning": su_meaning,
        "world_production_m_bales": prod,
        "world_mill_use_m_bales": use,
        "world_ending_stocks_m_bales": stocks,
        "supply_demand_balance": balance,
        "us_planted_pct": planted,
        "us_crop_condition_pct": condition,
        "us_crop_season_active": us_season,
        "week_over_week_change_pct": wow_pct,
        "week_over_week_change_cents": wow_cents,
        "direction": direction,
        "spot_data_as_of": curve_row.as_of_date.isoformat(),
        "wasde_data_as_of": wasde_as_of,
        "freshness_days": freshness,
        "is_stale": is_stale,
        "source": curve_row.source or "commodity_futures",
        "quality_score": round(quality, 3),
        "gaps": gaps,
    }


def _interpret_fx_rate_change(
    change_pct: Optional[float],
    currency_code: str,
    corridor: str,
) -> dict[str, Any]:
    """
    Rates are local currency per 1 USD (e.g. usd_bdt=122.73 → 122.73 BDT/USD).

    Rate UP   → USD strengthened, local currency weakened → corridor CHEAPER in USD.
    Rate DOWN → USD weakened, local currency strengthened → corridor MORE EXPENSIVE in USD.
    """
    if change_pct is None or abs(change_pct) <= 0.5:
        return {
            "local_currency_direction": "stable",
            "usd_direction": "stable",
            "corridor_cost_impact": "stable",
            "impact_statement": None,
        }

    if change_pct > 0:
        local_dir = "depreciated"
        usd_dir = "strengthened"
        corridor_impact = "cheaper"
        impact = (
            f"USD/{currency_code} rate rose {change_pct:.1f}% ({currency_code} {local_dir} vs USD) → "
            f"{corridor} {abs(change_pct):.1f}% {corridor_impact} in USD terms for importers"
        )
    else:
        local_dir = "appreciated"
        usd_dir = "weakened"
        corridor_impact = "more expensive"
        impact = (
            f"USD/{currency_code} rate fell {abs(change_pct):.1f}% ({currency_code} {local_dir} vs USD) → "
            f"{corridor} {abs(change_pct):.1f}% {corridor_impact} in USD terms for importers"
        )

    return {
        "local_currency_direction": local_dir,
        "usd_direction": usd_dir,
        "corridor_cost_impact": corridor_impact,
        "impact_statement": impact,
    }


def _fx_corridor_summary(
    corridor: str,
    currency_code: str,
    rate: float,
    change_pct: Optional[float],
    interp: dict[str, Any],
) -> str:
    """One-line corridor summary with correct importer cost direction."""
    if change_pct is None:
        return (
            f"{corridor} ({currency_code}): {rate:.4f} {currency_code}/USD — "
            f"no prior-period row for WoW comparison"
        )
    if interp["corridor_cost_impact"] == "stable":
        return (
            f"{corridor} ({currency_code}): {rate:.4f} {currency_code}/USD, "
            f"WoW {change_pct:+.2f}% — stable (|change| ≤ 0.5%), negligible FOB impact"
        )
    return interp["impact_statement"] or (
        f"{corridor} ({currency_code}): {rate:.4f} {currency_code}/USD, WoW {change_pct:+.2f}%"
    )


def get_fx_snapshot(db: Session) -> dict:
    latest = (
        db.query(FxRates)
        .filter(FxRates.is_latest.is_(True))
        .order_by(desc(FxRates.pulled_at))
        .first()
    )
    if not latest:
        return {
            "gaps": ["No FX data in database"],
            "quality_score": 0.0,
            "freshness_hours": 999,
        }

    anchor = latest.pulled_at or latest.created_at
    freshness_hours = int((datetime.utcnow() - anchor).total_seconds() / 3600)
    is_stale = freshness_hours > 48

    def _row_near(days: int) -> Optional[FxRates]:
        cutoff = anchor - timedelta(days=days)
        return (
            db.query(FxRates)
            .filter(
                FxRates.is_latest.is_(True),
                FxRates.pulled_at <= cutoff,
            )
            .order_by(desc(FxRates.pulled_at))
            .first()
        )

    week_ago = _row_near(7)
    month_ago = _row_near(30)

    currencies = {
        "INR": float(latest.usd_inr),
        "BDT": float(latest.usd_bdt),
        "VND": float(latest.usd_vnd),
        "CNY": float(latest.usd_cny),
        "TRY": float(latest.usd_try),
        "PKR": float(latest.usd_pkr),
    }

    corridors: dict[str, dict] = {}
    corridor_summaries: list[str] = []
    max_move = ("", 0.0, "")
    for corridor, field in CORRIDOR_FX.items():
        code = field.replace("usd_", "").upper()
        rate = currencies[code]
        wow = mom = None
        if week_ago:
            prev = float(getattr(week_ago, field))
            wow = (rate - prev) / prev * 100 if prev else None
        if month_ago:
            prev = float(getattr(month_ago, field))
            mom = (rate - prev) / prev * 100 if prev else None

        week_interp = _interpret_fx_rate_change(wow, code, corridor)
        month_interp = _interpret_fx_rate_change(mom, code, corridor)

        summary = _fx_corridor_summary(corridor, code, rate, wow, week_interp)
        corridor_summaries.append(summary)

        impact = week_interp["impact_statement"] or (
            f"{corridor}: no meaningful FX move this week (|change| ≤ 0.5%)"
        )
        if wow is not None and abs(wow) > abs(max_move[1]):
            max_move = (corridor, wow, impact)

        corridors[corridor.lower()] = {
            "currency_code": code,
            "rate": rate,
            "rate_convention": f"{code} per 1 USD (local currency per dollar)",
            "week_change_pct": wow,
            "month_change_pct": mom,
            "week_local_currency_direction": week_interp["local_currency_direction"],
            "week_usd_direction": week_interp["usd_direction"],
            "week_corridor_cost_impact": week_interp["corridor_cost_impact"],
            "month_local_currency_direction": month_interp["local_currency_direction"],
            "month_corridor_cost_impact": month_interp["corridor_cost_impact"],
            "corridor_summary": summary,
            "impact_statement": impact,
        }

    quality_tag = latest.status or "untagged"
    source_breakdown = {
        "INR": "FRED_historical" if "FRED" in (latest.source or "") else quality_tag,
        "BDT": "Alpha Vantage" if "ALPHA" in quality_tag else quality_tag,
        "VND": "Alpha Vantage" if "ALPHA" in quality_tag else quality_tag,
        "PKR": "Alpha Vantage" if "ALPHA" in quality_tag else quality_tag,
        "CNY": quality_tag,
        "TRY": quality_tag,
        "row_quality_tag": quality_tag,
        "row_source": latest.source,
    }

    return {
        "as_of": anchor.date().isoformat(),
        "freshness_hours": freshness_hours,
        "is_stale": is_stale,
        "rate_convention": (
            "All rates are local currency per 1 USD (e.g. usd_bdt=122.73 → 122.73 BDT per dollar). "
            "Rate UP week-over-week → USD strengthened, local currency weakened → corridor CHEAPER "
            "in USD for importers. Rate DOWN → local currency strengthened → corridor MORE EXPENSIVE "
            "in USD. Do not invert this logic."
        ),
        "corridors": corridors,
        "corridor_summaries": corridor_summaries,
        "most_significant_move": max_move[2] or "No corridor moved more than 0.5% this week",
        "quality_score": round(_quality_from_freshness(freshness_hours // 24, 2, 0.95), 3),
        "source_breakdown": source_breakdown,
        "gaps": [] if not is_stale else ["FX data older than 48 hours"],
    }


def get_crude_oil_snapshot(db: Session) -> dict:
    from database.constants import CRUDE_OIL_DYEING_PRESSURE_THRESHOLD, CRUDE_OIL_LEVEL_THRESHOLDS, STALENESS

    latest = (
        db.query(CrudeOil)
        .filter(CrudeOil.is_latest.is_(True))
        .order_by(desc(CrudeOil.as_of_date))
        .first()
    )
    if not latest:
        return {
            "gaps": ["No crude oil data — run: python -m data.ingestion.crude_oil_ingestion --backfill"],
            "quality_score": 0.0,
            "freshness_days": 999,
        }

    brent = float(latest.brent_spot)
    wti = float(latest.wti_spot)
    freshness = _days_old(latest.as_of_date)
    gaps: list[str] = []

    # Use materialized trend_30d_pct — avoids a DB round-trip that previously
    # re-queried 800 rows to find the prior value.
    ch30 = float(latest.trend_30d_pct) if latest.trend_30d_pct is not None else None
    if ch30 is None:
        gaps.append("trend_30d_pct NULL on latest row — run crude_oil_cleanup.py")
        ch30 = 0.0

    # 90d still requires a live query (not materialized)
    prior_90d = (
        db.query(CrudeOil)
        .filter(
            CrudeOil.is_latest.is_(True),
            CrudeOil.as_of_date <= latest.as_of_date - timedelta(days=90),
        )
        .order_by(desc(CrudeOil.as_of_date))
        .first()
    )
    ch90 = 0.0
    if prior_90d and prior_90d.brent_spot:
        p = float(prior_90d.brent_spot)
        ch90 = (brent - p) / p * 100 if p else 0.0

    trend = "stable"
    magnitude = "gradual"
    if ch30 > 3:
        trend = "rising"
        magnitude = "sharp" if ch30 > 8 else "moderate"
    elif ch30 < -3:
        trend = "falling"
        magnitude = "sharp" if ch30 < -8 else "moderate"

    # Spread: Brent - WTI (normally +$2-5; widening = tighter Asian/European crude supply)
    spread = (
        float(latest.brent_wti_spread_usd)
        if latest.brent_wti_spread_usd is not None
        else brent - wti
    )
    spread_signal = "normal"
    if spread > 10:
        spread_signal = "wide — Asian crude supply tighter than US, chemical import premiums elevated"
    elif spread < 0:
        spread_signal = "inverted — unusual market structure, monitor for supply disruption"

    # INR price — the anchor for polyester cost chain in Tirupur
    brent_inr = (
        float(latest.brent_inr_per_barrel)
        if latest.brent_inr_per_barrel is not None
        else None
    )
    if brent_inr is None:
        gaps.append("brent_inr_per_barrel NULL — run crude_oil_cleanup.py for FX materialization")

    # Dyeing chemical pressure uses the constant threshold (Brent-indexed)
    dye_threshold = float(CRUDE_OIL_DYEING_PRESSURE_THRESHOLD)
    if brent > dye_threshold:
        dye_pressure = "elevated"
        dye_note = (
            f"Dark colour dyeing costs under upward pressure. "
            f"Brent ${brent:.2f}/bbl exceeds ${dye_threshold:.0f} threshold. "
            f"Azo-free disperse dyes and reactive dye carriers are petroleum derivatives. "
            f"Estimated impact: +$0.08–0.15/dozen on dark-colour programs."
        )
    elif brent < 65:
        dye_pressure = "reduced"
        dye_note = (
            f"Brent ${brent:.2f}/bbl — dye chemical costs below normal range. "
            f"Minor cost relief on dark-colour programs."
        )
    else:
        dye_pressure = "normal"
        dye_note = f"Brent ${brent:.2f}/bbl — dye chemical costs within normal operating range."

    # Crude oil futures forward curve — contango vs backwardation signal
    crude_futures: dict = {}
    latest_futures = (
        db.query(CommodityFutures)
        .filter(
            CommodityFutures.is_latest.is_(True),
            CommodityFutures.brent_3m_fwd.isnot(None),
        )
        .order_by(desc(CommodityFutures.as_of_date))
        .first()
    )
    if latest_futures:
        def _f(v) -> float | None:
            return float(v) if v is not None else None
        crude_futures = {
            # Brent forwards (USD/bbl) — EIA STEO BREPUUS forecast
            "brent_3m_fwd": _f(latest_futures.brent_3m_fwd),
            "brent_6m_fwd": _f(latest_futures.brent_6m_fwd),
            "brent_9m_fwd": _f(latest_futures.brent_9m_fwd),
            "brent_12m_fwd": _f(latest_futures.brent_12m_fwd),
            # WTI forwards (USD/bbl) — EIA STEO WTIPUUS forecast
            "wti_3m_fwd": _f(latest_futures.wti_3m_fwd),
            "wti_6m_fwd": _f(latest_futures.wti_6m_fwd),
            "wti_9m_fwd": _f(latest_futures.wti_9m_fwd),
            "wti_12m_fwd": _f(latest_futures.wti_12m_fwd),
            # Contango signals (forward vs spot / spot × 100)
            "brent_3m_contango_pct": _f(latest_futures.brent_3m_contango_pct),
            "brent_9m_contango_pct": _f(latest_futures.brent_9m_contango_pct),
            "brent_12m_contango_pct": _f(latest_futures.brent_12m_contango_pct),
            "wti_3m_contango_pct": _f(latest_futures.wti_3m_contango_pct),
            "wti_12m_contango_pct": _f(latest_futures.wti_12m_contango_pct),
            # Summary signal and metadata
            "curve_signal": latest_futures.crude_curve_signal,
            "source": latest_futures.crude_source,
            "futures_as_of": latest_futures.as_of_date.isoformat() if latest_futures.as_of_date else None,
        }
        curve_note = {
            "contango": "EIA forecasts crude prices to remain elevated over next 12 months — polyester chain cost pressure persists.",
            "backwardation": "EIA forecasts crude decline over next 12 months — polyester cost pressure transient; no urgency to hedge input costs.",
            "flat": "EIA sees little crude price change over next 12 months — cost planning stable.",
        }.get(latest_futures.crude_curve_signal or "flat", "")
        crude_futures["curve_note"] = curve_note
    else:
        gaps.append(
            "Crude futures not available — run: python -m data.ingestion.crude_oil_futures_ingestion "
            "(requires EIA_API_KEY from https://www.eia.gov/opendata/)"
        )

    # Polyester chain proxy prices (crude-derived, is_proxy=True)
    polyester_chain: dict = {}
    latest_px = (
        db.query(PxParaxylene)
        .filter(PxParaxylene.is_latest.is_(True), PxParaxylene.spot_usd_tonne.isnot(None))
        .order_by(desc(PxParaxylene.as_of_date))
        .first()
    )
    latest_chip = (
        db.query(PolyesterPetChips)
        .filter(PolyesterPetChips.is_latest.is_(True), PolyesterPetChips.spot_usd_tonne.isnot(None))
        .order_by(desc(PolyesterPetChips.as_of_date))
        .first()
    )
    if latest_px:
        polyester_chain["px_spot_usd_tonne"] = float(latest_px.spot_usd_tonne)
        polyester_chain["crude_to_px_ratio"] = float(latest_px.crude_to_px_ratio) if latest_px.crude_to_px_ratio else None
        polyester_chain["px_is_proxy"] = bool(latest_px.is_proxy)
        polyester_chain["px_as_of"] = latest_px.as_of_date.isoformat() if latest_px.as_of_date else None
    if latest_chip:
        polyester_chain["chip_spot_usd_tonne"] = float(latest_chip.spot_usd_tonne)
        polyester_chain["chip_is_proxy"] = bool(latest_chip.is_proxy)

    # Hedge opportunity recommendation — driven by crude_market_structure (NYMEX spread)
    # from eia_petroleum_futures rows. Contango + polyester exposure → hedge input costs now.
    hedge_opportunity_recommendation: Optional[dict] = None
    try:
        crude_inputs = CrudeCostInputs(db)
        forward = crude_inputs.get_forward_input(
            delivery_date=date.today() + timedelta(days=90),
            as_of_date=date.today(),
        )
        market_structure = forward.get("market_structure")
        if market_structure == "contango" and polyester_chain:
            hedge_opportunity_recommendation = {
                "action": "hedge_polyester_input_costs",
                "rationale": (
                    "Crude oil in CONTANGO: forward prices exceed spot, indicating the market "
                    "expects crude to remain elevated. Polyester yarn and dye chemical costs will "
                    "build over the next 90 days. Operators with >20% polyester blend exposure "
                    "should consider locking in current input prices."
                ),
                "market_structure": market_structure,
                "brent_futures_3m": float(forward["brent_futures"]) if forward.get("brent_futures") else None,
                "tenor": forward.get("tenor_used"),
                "urgency": "high" if polyester_chain.get("px_spot_usd_tonne") else "medium",
                "source_row_id": forward.get("source_row_id"),
            }
        elif market_structure == "backwardation":
            hedge_opportunity_recommendation = {
                "action": "no_hedge_needed",
                "rationale": (
                    "Crude oil in BACKWARDATION: forward prices below spot. "
                    "Polyester cost pressure is transient — no urgency to lock in input costs."
                ),
                "market_structure": market_structure,
                "brent_futures_3m": float(forward["brent_futures"]) if forward.get("brent_futures") else None,
                "tenor": forward.get("tenor_used"),
            }
    except (CrudeDataStaleError, Exception) as exc:
        logger.debug(f"hedge_opportunity_recommendation: could not build ({exc})")
        hedge_opportunity_recommendation = None

    # Transmission lag coefficients (seeded in learned_coefficient)
    lag_coefficients: dict = {}
    for coef_name in ("crude_to_dye_chemical_lag_weeks", "crude_to_polyester_yarn_lag_weeks"):
        row = (
            db.query(LearnedCoefficient)
            .filter(
                LearnedCoefficient.coefficient_name == coef_name,
                LearnedCoefficient.is_active.is_(True),
            )
            .first()
        )
        if row:
            lag_coefficients[coef_name] = {
                "value": float(row.value),
                "unit": row.unit,
                "confidence_tier": row.confidence_tier,
            }

    # Build polyester chain pressure signal with real proxy prices.
    # Transmission lags (crude→dye_chemical, crude→polyester_yarn) are NOT included:
    # lag coefficients are marked uncalibrated pending RRK invoice data ingestion.
    # Once RRK dye chemical and polyester yarn invoices are ingested and correlated
    # against historical Brent prices, these lags will be measured and reactivated.
    proxy_note = " (crude-derived proxy ±20% accuracy)" if polyester_chain.get("px_is_proxy") else ""
    px_price = polyester_chain.get("px_spot_usd_tonne")
    chip_price = polyester_chain.get("chip_spot_usd_tonne")
    lag_pending_note = " Transmission lag to RRK costs uncalibrated — needs invoice corpus."

    if trend == "rising" and brent > float(CRUDE_OIL_LEVEL_THRESHOLDS["elevated"]):
        poly_signal = (
            f"BUILDING — Brent ${brent:.2f}/bbl rising ({ch30:+.1f}% in 30d). "
            + (f"PX proxy ${px_price:.0f}/tonne, chip proxy ${chip_price:.0f}/tonne{proxy_note}. " if px_price else "")
            + "Upward pressure on dye chemicals and polyester yarn expected."
            + lag_pending_note
        )
    elif trend == "falling":
        poly_signal = (
            f"EASING — Brent falling {abs(ch30):.1f}% in 30d. "
            + (f"PX proxy ${px_price:.0f}/tonne, chip proxy ${chip_price:.0f}/tonne{proxy_note}. " if px_price else "")
            + "Cost relief expected on dye chemicals and polyester yarn."
            + lag_pending_note
        )
    else:
        poly_signal = (
            f"STABLE — Brent ${brent:.2f}/bbl ({ch30:+.1f}% 30d). "
            + (f"PX proxy ${px_price:.0f}/tonne, chip proxy ${chip_price:.0f}/tonne{proxy_note}. " if px_price else "")
            + "Normal polyester chain pressure."
        )

    conflict_events = (
        db.query(GeopoliticalRiskEvent)
        .filter(
            GeopoliticalRiskEvent.is_active.is_(True),
            GeopoliticalRiskEvent.event_type.ilike("%conflict%"),
        )
        .all()
    )
    geo_premium = len(conflict_events) > 0
    geo_note = (
        "Active armed conflict events detected — chemical import routes and spot premiums may be affected."
        if geo_premium
        else ""
    )

    if freshness > STALENESS["crude_oil_error"]:
        gaps.append(
            f"Crude oil data {freshness} days old — exceeds {STALENESS['crude_oil_error']}d error threshold. "
            f"Run: python -m data.ingestion.crude_oil_ingestion --run-once"
        )
    elif freshness > STALENESS["crude_oil_warn"]:
        gaps.append(
            f"Crude oil data {freshness} days old — exceeds {STALENESS['crude_oil_warn']}d warn threshold."
        )

    return {
        "brent_usd": brent,
        "wti_usd": wti,
        "brent_wti_spread_usd": spread,
        "brent_wti_spread_signal": spread_signal,
        "brent_inr_per_barrel": brent_inr,
        "brent_30d_change_pct": ch30,
        "brent_90d_change_pct": ch90,
        "aggregation_period": latest.aggregation_period,
        "trend": trend,
        "trend_magnitude": magnitude,
        "crude_futures": crude_futures,
        "polyester_chain": polyester_chain,
        "transmission_lags": lag_coefficients,
        "dyeing_chemical_pressure": dye_pressure,
        "dyeing_chemical_note": dye_note,
        "polyester_chain_pressure": poly_signal,
        "local_freight_implication": (
            "Diesel-linked factory-to-port costs under upward pressure — "
            f"estimated +${brent * 0.0008:.2f}/dozen on India corridor"
            if trend == "rising"
            else "Local freight energy costs stable at current crude levels"
        ),
        "geopolitical_chemical_premium": geo_premium,
        "geopolitical_premium_note": geo_note,
        "data_as_of": latest.as_of_date.isoformat(),
        "freshness_days": freshness,
        "quality_score": round(
            _quality_from_freshness(freshness, STALENESS["crude_oil_error"], 0.9), 3
        ),
        "gaps": gaps,
        "hedge_opportunity_recommendation": hedge_opportunity_recommendation,
    }


# Crude pressure level thresholds (USD/bbl, brent_rolling_4w_avg)
_CRUDE_PRESSURE_BANDS = [(70.0, "low"), (90.0, "moderate"), (110.0, "elevated")]
_RETAILER_RECENCY_DAYS = 90
_CRUDE_RECENCY_DAYS = 7


def _crude_pressure_level(rolling_4w: float) -> str:
    for ceiling, label in _CRUDE_PRESSURE_BANDS:
        if rolling_4w < ceiling:
            return label
    return "high"


def _retailer_health_from_signal(buying: str, inventory: str, margin: str) -> str:
    """Map one retailer's demand_signals row to a health enum.
    strong / mixed / weak / critical."""
    buying = (buying or "").lower()
    margin = (margin or "").lower()
    negative = buying in ("reducing", "declining", "decreasing") or margin == "compressing"
    positive = buying in ("increasing", "expanding") and margin != "compressing"
    if negative and margin == "compressing" and buying in ("reducing", "declining", "decreasing"):
        return "critical"
    if negative:
        return "weak"
    if positive:
        return "strong"
    return "mixed"


# severity ordering for taking the worst across retailers
_HEALTH_RANK = {"strong": 0, "mixed": 1, "weak": 2, "critical": 3}
_RANK_HEALTH = {v: k for k, v in _HEALTH_RANK.items()}


def get_crude_retailer_demand_composite(db: Session, as_of_date: date) -> dict:
    """Composite of crude price environment AND retailer demand health.

    NOT a prediction and NOT a recommendation. It reports two real, recent data
    points (crude pressure + retailer demand health) and their historical
    co-occurrence with order-cancellation risk. The operator decides what to do.

    Returns None components (data_complete=False) when either source is stale:
      crude requires an eia_daily row < 7 days old,
      retailer requires demand_signals < 90 days old.
    Does NOT write to any output table.
    """
    result: dict = {
        "crude_brent_rolling_4w": None, "crude_pressure_level": None,
        "crude_data_as_of": None, "crude_data_age_days": None,
        "retailer_demand_health": None, "retailer_data_as_of": None,
        "retailer_data_age_days": None,
        "composite_risk_flag": None, "composite_note": "", "historical_analogs": [],
        "data_complete": False,
    }

    # ── Crude component (eia_daily primary, daily resolution) ──────────────────
    crude_row = db.execute(text("""
        SELECT as_of_date, CAST(brent_rolling_4w_avg AS REAL)
        FROM crude_oil
        WHERE source = 'eia_daily' AND brent_rolling_4w_avg IS NOT NULL
        ORDER BY as_of_date DESC LIMIT 1
    """)).fetchone()
    crude_ok = False
    if crude_row is not None and crude_row[1] is not None:
        c_date = crude_row[0] if isinstance(crude_row[0], date) else date.fromisoformat(str(crude_row[0]))
        c_age = (as_of_date - c_date).days
        result["crude_data_as_of"] = c_date.isoformat()
        result["crude_data_age_days"] = c_age
        if c_age <= _CRUDE_RECENCY_DAYS:
            result["crude_brent_rolling_4w"] = round(crude_row[1], 2)
            result["crude_pressure_level"] = _crude_pressure_level(crude_row[1])
            crude_ok = True
        else:
            logger.warning("crude_retailer_composite: eia_daily stale (%dd) — crude component None.", c_age)

    # ── Retailer component (worst demand health across tracked retailers) ──────
    cutoff = (as_of_date - timedelta(days=_RETAILER_RECENCY_DAYS)).isoformat()
    retailer_rows = db.execute(text("""
        SELECT retailer_id, buying_volume_signal, inventory_improving, margin_compression,
               COALESCE(period_end_date, date(created_at)) AS sig_date
        FROM demand_signals
        WHERE is_latest = 1 AND COALESCE(period_end_date, date(created_at)) >= :cutoff
    """), {"cutoff": cutoff}).fetchall()
    retailer_ok = False
    if retailer_rows:
        worst_rank, latest_sig = -1, None
        for rid, buying, inv, margin, sig_date in retailer_rows:
            h = _retailer_health_from_signal(buying, inv, margin)
            if _HEALTH_RANK[h] > worst_rank:
                worst_rank = _HEALTH_RANK[h]
            d = sig_date if isinstance(sig_date, date) else date.fromisoformat(str(sig_date))
            if latest_sig is None or d > latest_sig:
                latest_sig = d
        result["retailer_demand_health"] = _RANK_HEALTH[worst_rank]
        result["retailer_data_as_of"] = latest_sig.isoformat() if latest_sig else None
        result["retailer_data_age_days"] = (as_of_date - latest_sig).days if latest_sig else None
        retailer_ok = True

    # ── Composite (only when both components present) ──────────────────────────
    if crude_ok and retailer_ok:
        result["data_complete"] = True
        crude_elevated = result["crude_pressure_level"] in ("elevated", "high")
        retailer_weak = result["retailer_demand_health"] in ("weak", "critical")
        if crude_elevated and retailer_weak:
            result["composite_risk_flag"] = True
            result["composite_note"] = (
                f"Crude pressure {result['crude_pressure_level']} "
                f"(${result['crude_brent_rolling_4w']}/bbl 4w avg) coincides with "
                f"{result['retailer_demand_health']} retailer demand — historically associated "
                f"with discretionary-apparel spending compression and elevated order-cancellation risk."
            )
            result["historical_analogs"] = _crude_demand_analogs(db)
        else:
            result["composite_risk_flag"] = False
            result["composite_note"] = (
                f"Crude pressure {result['crude_pressure_level']}, retailer demand "
                f"{result['retailer_demand_health']} — combination not historically associated "
                f"with elevated cancellation risk."
            )
    else:
        missing = []
        if not crude_ok:
            missing.append(f"crude (eia_daily {'stale' if crude_row else 'absent'})")
        if not retailer_ok:
            missing.append("retailer demand_signals (<90d) absent")
        result["composite_note"] = f"Insufficient data to compute composite: {', '.join(missing)}."
    return result


def _crude_demand_analogs(db: Session) -> list[str]:
    """Historical periods (from eia_daily) where Brent ran elevated — context for the
    operator. Descriptive only; sourced from our own crude history."""
    rows = db.execute(text("""
        SELECT strftime('%Y', as_of_date) AS yr,
               AVG(CAST(brent_spot AS REAL)) AS avg_brent,
               MAX(CAST(brent_spot AS REAL)) AS max_brent
        FROM crude_oil
        WHERE source = 'eia_daily' AND brent_spot IS NOT NULL
        GROUP BY strftime('%Y', as_of_date)
        HAVING AVG(CAST(brent_spot AS REAL)) >= 100
        ORDER BY avg_brent DESC LIMIT 5
    """)).fetchall()
    return [
        f"{yr}: Brent avg ${avg:.0f}/bbl (peak ${mx:.0f}) — elevated crude environment."
        for yr, avg, mx in rows
    ]


def get_bunker_fuel_snapshot(db: Session) -> dict:
    """Marine fuel (crude→freight transmission variable) + the measured
    crude→bunker relationship. VLSFO is what links Brent to freight surcharges;
    until a real VLSFO feed is connected we use the EIA distillate proxy and the
    crude_to_bunker_fuel calibration fitted on decades of data."""
    grades = ("No2_heating_oil", "ULSD")
    latest: dict[str, dict] = {}
    for grade in grades:
        row = (
            db.query(BunkerFuelPrices)
            .filter(BunkerFuelPrices.grade == grade, BunkerFuelPrices.is_latest.is_(True))
            .order_by(desc(BunkerFuelPrices.as_of_date))
            .first()
        )
        if row:
            latest[grade] = {
                "price_usd": float(row.price_usd),
                "price_unit": row.price_unit,
                "port": row.port,
                "as_of_date": row.as_of_date.isoformat(),
                "is_proxy": bool(row.is_proxy),
                "proxy_basis": row.proxy_basis,
            }
    if not latest:
        return {
            "data_available": False,
            "gaps": ["No bunker fuel data — run: python -m data.ingestion.bunker_fuel_ingestion --backfill"],
        }

    # cost_component='freight_energy_surcharge' is the Brent→distillate fit
    # (dist_usd_bbl = 1.163×brent + 5.49, R²=0.88, n=1043, 2006-2026).
    cal = db.execute(text(
        "SELECT cost_component, lag_weeks_empirical, transmission_coeff, r_squared, "
        "obs_count, notes FROM crude_transmission_calibration "
        "WHERE cost_component = 'freight_energy_surcharge' AND is_active = 1 "
        "LIMIT 1"
    )).fetchone()

    brent_row = (
        db.query(CrudeOil)
        .filter(CrudeOil.brent_spot.isnot(None), CrudeOil.is_latest.is_(True))
        .order_by(desc(CrudeOil.as_of_date))
        .first()
    )
    brent = float(brent_row.brent_spot) if brent_row else None

    transmission = None
    if cal is not None and brent is not None:
        coeff = float(cal.transmission_coeff)      # $/bbl distillate per $/bbl Brent
        intercept = 5.49                           # fitted intercept (USD/bbl)
        implied_dist_bbl = coeff * brent + intercept
        implied_dist_gal = implied_dist_bbl / 42.0

        # VLSFO ≈ distillate proxy × 1.15 (IMO-2020 spread premium, order-of-magnitude)
        # Flagged as estimate — real VLSFO calibration requires Ship&Bunker/paid feed.
        vlsfo_est_gal = implied_dist_gal * 1.15

        delta10_bbl = coeff * 10.0                # $/bbl distillate shift per $10/bbl Brent
        delta10_gal = delta10_bbl / 42.0

        transmission = {
            "brent_to_distillate_coeff_bbl_per_bbl": round(coeff, 5),
            "lag_weeks": float(cal.lag_weeks_empirical),
            "r_squared": float(cal.r_squared),
            "obs_count": int(cal.obs_count),
            "implied_distillate_usd_bbl": round(implied_dist_bbl, 2),
            "implied_distillate_usd_gal": round(implied_dist_gal, 4),
            "vlsfo_estimate_usd_gal": round(vlsfo_est_gal, 4),
            "vlsfo_estimate_basis": "distillate_proxy × 1.15 (IMO-2020 spread premium estimate)",
            "interpretation": (
                f"At Brent ${brent:.2f}/bbl: implied distillate "
                f"${implied_dist_bbl:.2f}/bbl (${implied_dist_gal:.4f}/gal), "
                f"VLSFO proxy ~${vlsfo_est_gal:.4f}/gal. "
                f"A $10/bbl Brent move → ${delta10_bbl:.2f}/bbl (${delta10_gal:.4f}/gal) distillate shift. "
                f"R²={float(cal.r_squared):.2f}, n={int(cal.obs_count)} weekly obs 2006–2026. "
                "Bunker→container-rate pass-through deferred — awaiting paid WCI/FBX history."
            ),
        }

    return {
        "data_available": True,
        "latest_proxy_prices": latest,
        "current_brent_usd_bbl": brent,
        "crude_to_bunker_transmission": transmission,
        "note": (
            "EIA distillate proxy for VLSFO marine fuel (is_proxy=True). "
            "Real VLSFO assessments arrive with paid Ship&Bunker/Platts/Argus feed. "
            "Bunker→freight pass-through requires paid Drewry/FBX rate history."
        ),
        "gaps": [] if transmission else ["crude→bunker calibration not loaded — check freight_energy_surcharge row"],
    }


def get_freight_snapshot(db: Session) -> dict:
    count = (
        db.query(OceanFreightRates)
        .filter(OceanFreightRates.is_latest.is_(True))
        .count()
    )
    if count == 0:
        return {
            "data_available": False,
            "data_unavailable_reason": (
                "ocean_freight_rates table has zero is_latest rows. Landed cost cannot be computed "
                "with integrity — freight is excluded from all estimates until "
                "Drewry WCI rates are entered."
            ),
            "quality_score": 0.0,
            "active_disruptions": [],
            "war_risk_corridors": [],
        }

    # One is_latest row per (origin_port, destination_port) corridor.
    latest_rows = (
        db.query(OceanFreightRates)
        .filter(OceanFreightRates.is_latest.is_(True))
        .order_by(desc(OceanFreightRates.as_of_date))
        .all()
    )

    insurance_rows = (
        db.query(MarineInsurance).order_by(desc(MarineInsurance.as_of_date)).all()
    )
    ins_by_corridor = {r.corridor.lower(): r for r in insurance_rows}

    geo_events = (
        db.query(GeopoliticalRiskEvent).filter(GeopoliticalRiskEvent.is_active.is_(True)).all()
    )
    disruptions = [
        {
            "name": e.event_name,
            "corridors_affected": e.affected_corridors.split(","),
            "freight_impact_pct": float(e.freight_impact_pct),
            "lead_time_impact_days": e.lead_time_impact_days,
            "risk_level": e.risk_level,
            "started": e.start_date.isoformat(),
        }
        for e in geo_events
    ]

    war_corridors = [
        r.corridor
        for r in insurance_rows
        if r.active_war_risk_surcharge
    ]

    corridors: dict[str, dict] = {}
    skipped_no_rate: list[str] = []
    wci_composite_usd = None
    wci_composite_as_of = None
    for r in latest_rows:
        corridor_label = f"{r.origin_port} → {r.destination_port}"
        # 40ft high-cube is the WCI publication unit; fall back to 40ft standard.
        rate_dec = r.rate_40ft_hc_usd if r.rate_40ft_hc_usd is not None else r.rate_40ft_usd
        if rate_dec is None:
            # Honest gap — no usable container rate. Never fabricate one.
            skipped_no_rate.append(corridor_label)
            continue
        rate = float(rate_dec)
        # The WCI global composite is a benchmark index, not a shippable
        # port-pair — keep it out of per-corridor landed-cost math, expose
        # it separately as the headline freight series.
        if r.rate_source_tier == "drewry_wci_composite":
            wci_composite_usd = rate
            wci_composite_as_of = r.as_of_date.isoformat()
            continue
        key = f"{r.origin_port}_{r.destination_port}".lower().replace(" ", "_")
        per_doz = rate / DOZENS_PER_CONTAINER

        # Insurance and geo premium key off origin country / corridor names.
        ins = ins_by_corridor.get((r.origin_country or "").lower())
        ins_doz = 0.0
        geo_premium_doz = 0.0
        if ins:
            cif = CIF_ASSUMPTION_USD * DOZENS_PER_CONTAINER
            ins_doz = float(ins.total_effective_rate_pct_cif) / 100 * cif / DOZENS_PER_CONTAINER
        for d in disruptions:
            affected = d["corridors_affected"]
            if any(
                tok and (tok in (r.origin_country or "") or tok in r.origin_port
                         or tok in r.destination_port)
                for tok in (t.strip() for t in affected)
            ):
                geo_premium_doz += per_doz * d["freight_impact_pct"] / 100

        # Trend vs ~28 days prior for THIS corridor. History rows carry
        # is_latest=False, so do not filter on it here.
        prior = (
            db.query(OceanFreightRates)
            .filter(
                OceanFreightRates.origin_port == r.origin_port,
                OceanFreightRates.destination_port == r.destination_port,
                OceanFreightRates.as_of_date <= r.as_of_date - timedelta(days=28),
                OceanFreightRates.rate_40ft_hc_usd.isnot(None),
            )
            .order_by(desc(OceanFreightRates.as_of_date))
            .first()
        )
        trend_pct = None
        if prior and prior.rate_40ft_hc_usd:
            prev = float(prior.rate_40ft_hc_usd)
            trend_pct = (rate - prev) / prev * 100 if prev else None

        corridors[key] = {
            "origin_port": r.origin_port,
            "destination_port": r.destination_port,
            "origin_country": r.origin_country,
            "container_rate_usd": rate,
            "rate_unit": "40ft high-cube" if r.rate_40ft_hc_usd is not None else "40ft standard",
            "per_dozen_usd": round(per_doz, 4),
            "insurance_per_dozen_usd": round(ins_doz, 4),
            "geopolitical_premium_per_dozen": round(geo_premium_doz, 4),
            "total_freight_insurance_per_dozen": round(
                per_doz + ins_doz + geo_premium_doz, 4
            ),
            "transit_days": r.transit_days,
            "vessel_availability": r.vessel_availability,
            "source": r.source,
            "rate_source_tier": r.rate_source_tier,
            "as_of_date": r.as_of_date.isoformat(),
            "4wk_trend_pct": trend_pct,
        }

    if not corridors and wci_composite_usd is None:
        return {
            "data_available": False,
            "data_unavailable_reason": (
                "ocean_freight_rates has is_latest rows but none carry a usable 40ft "
                f"container rate (skipped: {', '.join(skipped_no_rate) or 'all'}). "
                "Freight excluded from estimates until real rates are present."
            ),
            "quality_score": 0.0,
            "active_disruptions": disruptions,
            "war_risk_corridors": war_corridors,
        }

    most_recent = max(r.as_of_date for r in latest_rows)
    freshness = _days_old(most_recent)
    gaps = []
    if skipped_no_rate:
        gaps.append(
            f"{len(skipped_no_rate)} corridor(s) had no usable rate and were excluded: "
            + ", ".join(skipped_no_rate)
        )
    return {
        "data_available": True,
        "data_unavailable_reason": None,
        "as_of_date": most_recent.isoformat(),
        "freshness_days": freshness,
        "corridor_count": len(corridors),
        "corridors": corridors,
        "wci_composite_usd": wci_composite_usd,
        "wci_composite_as_of": wci_composite_as_of,
        "bunker_fuel": get_bunker_fuel_snapshot(db),
        "active_disruptions": disruptions,
        "war_risk_corridors": war_corridors,
        "quality_score": round(_quality_from_freshness(freshness, 14, 0.85), 3),
        "gaps": gaps,
    }


def get_geopolitical_snapshot(db: Session) -> dict:
    events = (
        db.query(GeopoliticalRiskEvent)
        .filter(GeopoliticalRiskEvent.is_active.is_(True))
        .order_by(desc(GeopoliticalRiskEvent.risk_level))
        .all()
    )
    lanes = (
        db.query(ShippingLaneRisk)
        .filter(ShippingLaneRisk.is_currently_disrupted.is_(True))
        .all()
    )
    active = [
        {
            "name": e.event_name,
            "type": e.event_type,
            "region": e.affected_region,
            "corridors": e.affected_corridors,
            "risk_level": e.risk_level,
            "freight_impact_pct": float(e.freight_impact_pct),
        }
        for e in events
    ]
    disrupted = [
        {
            "lane": l.lane_name,
            "corridors": l.corridors_affected,
            "risk_level": l.current_risk_level,
            "extra_transit_days": l.additional_transit_days,
            "extra_cost_usd": float(l.additional_cost_per_40ft_usd or 0),
        }
        for l in lanes
    ]
    levels = [e.risk_level.lower() for e in events] + [
        l.current_risk_level.lower() for l in lanes
    ]
    overall = "low"
    if any("high" in lv for lv in levels):
        overall = "high"
    elif any("elevated" in lv for lv in levels):
        overall = "elevated"
    elif events or lanes:
        overall = "moderate"

    most_affected = events[0].affected_corridors if events else None
    return {
        "active_events": active,
        "disrupted_lanes": disrupted,
        "overall_risk_level": overall,
        "most_affected_corridor": most_affected,
    }


def get_cost_benchmark_snapshot(db: Session) -> dict:
    fin = (
        db.query(FactoryFinancingCost)
        .filter(FactoryFinancingCost.is_latest.is_(True))
        .order_by(desc(FactoryFinancingCost.pulled_at))
        .first()
    )
    duty = (
        db.query(UsImportDutyRate).order_by(desc(UsImportDutyRate.effective_date)).first()
    )
    incentives = (
        db.query(GovernmentExportIncentive)
        .filter(GovernmentExportIncentive.is_active.is_(True))
        .all()
    )
    layers = db.query(CostLayerPrior).all()
    variables = db.query(CostVariablePrior).all()

    rates = {}
    if fin:
        rates = {
            "bangladesh_pct": float(fin.bangladesh_rate_pct * 100),
            "india_pct": float(fin.india_rate_pct * 100),
            "vietnam_pct": float(fin.vietnam_rate_pct * 100),
            "china_pct": float(fin.china_rate_pct * 100),
            "turkey_pct": float(fin.turkey_rate_pct * 100),
            "pakistan_pct": float(fin.pakistan_rate_pct * 100),
        }
        bd = float(fin.bangladesh_rate_pct)
        cn = float(fin.china_rate_pct)
        diff_pp = (bd - cn) * 100
        financing_note = (
            f"Bangladesh factories pay {diff_pp:.1f}pp more to finance raw materials than China. "
            f"On a 90-day cycle at $13/doz FOB this adds ~${13 * diff_pp / 100 * 90 / 365:.2f}/doz."
        )
    else:
        financing_note = "Factory financing data not available."

    gov = [
        {
            "country": g.country,
            "program": g.program_name,
            "benefit_rate_pct": float(g.benefit_rate_pct_fob * 100),
            "per_dozen_usd": float(g.benefit_per_dozen_usd_estimate or 0),
            "note": g.benefit_recipient,
        }
        for g in incentives
    ]

    avg_conf = (
        sum(float(v.confidence) for v in variables) / len(variables) if variables else 0.65
    )
    live_layers = sum(1 for ly in layers if ly.update_count > 0)

    return {
        "financing_rates": rates,
        "financing_differential_note": financing_note,
        "government_incentives": gov,
        "effective_duty_6109_10_pct": float(duty.ntr_rate_6109_10_pct * 100) if duty else None,
        "section_301_china_additional_pct": (
            float(duty.section_301_china_6109_10_pct * 100) if duty else None
        ),
        "cost_layer_confidence": avg_conf,
        "layers_with_live_data": live_layers,
        "total_layers": len(layers),
    }


def detect_signal_conflicts(
    cotton: dict,
    crude: dict,
    fx: dict,
    freight: dict,
) -> list[dict]:
    conflicts: list[dict] = []

    su = cotton.get("su_signal")
    if su in ("BEARISH", "NEUTRAL") and cotton.get("direction") in ("rising", "stable"):
        if cotton.get("week_over_week_change_pct") is not None and cotton["week_over_week_change_pct"] > -1:
            conflicts.append(
                {
                    "conflict_name": "Global vs local cotton",
                    "signal_a": f"WASDE S/U {cotton.get('su_ratio_pct', 'N/A')}% → {su} (global ample supply)",
                    "signal_b": "Spot cotton stable/rising week-over-week despite bearish fundamentals",
                    "why_it_matters": (
                        "Tirupur yarn buyers may face local tightness not reflected in ICE/WASDE"
                    ),
                    "resolution": (
                        "For Tirupur-sourced programs, weight local yarn market signals over global S/U. "
                        "For Bangladesh yarn, weight global cotton."
                    ),
                    "dollar_impact_if_ignored": "$0.20–0.35/doz yarn cost if local tightness persists",
                }
            )

    if su == "BEARISH" and (cotton.get("curve_premium_12m_pct") or 0) > 5.0:
        conflicts.append(
            {
                "conflict_name": "Cotton fundamentals vs forward curve",
                "signal_a": f"WASDE bearish — S/U {cotton.get('su_ratio_pct')}%",
                "signal_b": f"ICE curve in contango — 12m premium {cotton['curve_premium_12m_pct']:.1f}%",
                "why_it_matters": "Market may be pricing weather, trade, or quality risks not yet in WASDE",
                "resolution": (
                    "Use spot for near-term costing; use curve for 6-12 month hedge decisions"
                ),
                "dollar_impact_if_ignored": "Hedging at spot when curve signals higher forward prices",
            }
        )

    if crude.get("trend") == "rising" and cotton.get("direction") == "falling":
        conflicts.append(
            {
                "conflict_name": "Crude rising vs cotton falling",
                "signal_a": f"Crude {crude.get('trend')} — Brent ${crude.get('brent_usd')}/bbl",
                "signal_b": f"Cotton falling WoW {cotton.get('week_over_week_change_pct', 0):+.1f}%",
                "why_it_matters": "Polyester chain costs rise as cotton falls — substitution dynamics shift",
                "resolution": (
                    "Cotton programs benefit; polyester-heavy blends face divergent cost pressure"
                ),
                "dollar_impact_if_ignored": "$0.15–0.30/doz on polyester-blend programs if crude trend continues",
            }
        )

    if not freight.get("data_available"):
        for corridor, data in fx.get("corridors", {}).items():
            wow = data.get("week_change_pct")
            # Rate up → local currency weakened → corridor cheaper (potential FOB advantage)
            if wow is not None and wow > 1:
                conflicts.append(
                    {
                        "conflict_name": "FX corridor advantage vs freight unknown",
                        "signal_a": data.get("impact_statement", ""),
                        "signal_b": "Ocean freight data not in system — cannot confirm landed cost advantage",
                        "why_it_matters": "FOB savings from FX may be offset by freight — cannot verify",
                        "resolution": "Treat FX advantage as FOB-only until freight data connected",
                        "dollar_impact_if_ignored": "Unknown — freight gap prevents landed cost integrity",
                    }
                )
                break

    return conflicts


def quantify_signal_impact(
    signal_type: str,
    signal_magnitude: float,
    corridor: str,
    program_dozens: int = 10000,
    gsm: int = 180,
    count: str = "30s",
    colour_tier: str = "medium",
) -> dict:
    yarn_factor = gsm / 180.0 * (1.1 if count == "40s" else 1.0)
    per_dozen = 0.0
    basis = ""

    if signal_type == "cotton_price_change":
        per_dozen = signal_magnitude / 100 * 0.55 * yarn_factor
        basis = f"{signal_magnitude}% cotton × ~$0.55/doz yarn sensitivity × GSM/count adj"
    elif signal_type == "freight_change":
        per_dozen = signal_magnitude / 100 * 0.40
        basis = f"{signal_magnitude}% freight on ~$0.40/doz baseline ({corridor})"
    elif signal_type == "fx_change":
        # Positive magnitude = USD/local rate rose → corridor cheaper for USD importer
        per_dozen = -signal_magnitude / 100 * 0.45
        basis = (
            f"{signal_magnitude:+.1f}% USD/local rate change × ~45% local-cost share "
            f"({corridor}; rate rise = cheaper FOB, rate fall = more expensive FOB)"
        )
    elif signal_type == "crude_dyeing":
        per_dozen = signal_magnitude / 100 * 0.12
        basis = f"{signal_magnitude}% dye chemical pass-through ({colour_tier} colours)"
    else:
        basis = "Unknown signal type"

    program_impact = per_dozen * program_dozens
    direction = "cost_increase" if per_dozen >= 0 else "cost_decrease"
    return {
        "signal_type": signal_type,
        "magnitude_description": f"{signal_magnitude:+.1f}%",
        "per_dozen_impact_usd": round(abs(per_dozen), 4),
        "program_impact_usd": round(abs(program_impact), 2),
        "direction": direction,
        "confidence": 0.75 if signal_type == "cotton_price_change" else 0.65,
        "calculation_basis": basis,
    }


def assess_overall_data_quality(
    cotton: dict,
    fx: dict,
    crude: dict,
    freight: dict,
    geo: dict,
    benchmarks: dict,
) -> dict:
    per_source = {
        "cotton_spot": {
            "score": cotton.get("quality_score", 0),
            "freshness_days": cotton.get("freshness_days", 999),
            "note": "STALE" if cotton.get("is_stale") else "Current",
        },
        "cotton_wasde": {
            "score": 0.85 if cotton.get("wasde_data_as_of") else 0.0,
            "freshness_days": cotton.get("freshness_days", 999),
            "note": cotton.get("wasde_data_as_of") or "WASDE missing",
        },
        "fx_rates": {
            "score": fx.get("quality_score", 0),
            "freshness_hours": fx.get("freshness_hours", 999),
            "note": fx.get("source_breakdown", {}).get("row_quality_tag", "untagged"),
        },
        "crude_oil": {
            "score": crude.get("quality_score", 0),
            "freshness_days": crude.get("freshness_days", 999),
            "note": crude.get("data_as_of", "missing"),
        },
        "ocean_freight": {
            "score": freight.get("quality_score", 0),
            "available": freight.get("data_available", False),
            "note": (
                "Not connected"
                if not freight.get("data_available")
                else f"As of {freight.get('as_of_date')}"
            ),
        },
        "cost_priors": {
            "score": benchmarks.get("cost_layer_confidence", 0.65),
            "real_data_layers": benchmarks.get("layers_with_live_data", 0),
            "note": (
                f"{benchmarks.get('layers_with_live_data', 0)}/"
                f"{benchmarks.get('total_layers', 12)} layers updated from real outcomes"
            ),
        },
    }

    scores = [v["score"] for v in per_source.values() if isinstance(v.get("score"), (int, float))]
    overall = sum(scores) / len(scores) if scores else 0.0
    if not freight.get("data_available"):
        overall *= 0.85

    if overall >= 0.8:
        label = "HIGH"
    elif overall >= 0.65:
        label = "MODERATE"
    elif overall >= 0.45:
        label = "REDUCED"
    else:
        label = "LOW"

    critical_gaps = []
    if not freight.get("data_available"):
        critical_gaps.append(
            "Ocean freight not connected — landed cost estimates are FOB-only"
        )
    if cotton.get("is_stale"):
        critical_gaps.append(f"Cotton spot data {cotton.get('freshness_days')} days stale")
    if not cotton.get("is_curve_real"):
        critical_gaps.append("Cotton forward curve not from real ICE contracts on latest date")

    confidence_statement = (
        f"Intelligence quality is {label} ({overall:.0%}). "
        + (
            "Freight data not yet connected — landed cost estimates incomplete. "
            if not freight.get("data_available")
            else ""
        )
        + "All other signals assessed from database sources with freshness metadata."
    )

    return {
        "overall_score": round(overall, 3),
        "overall_label": label,
        "per_source": per_source,
        "critical_gaps": critical_gaps,
        "confidence_statement": confidence_statement,
    }


def generate_market_brief(
    db: Session,
    company_id: Optional[int] = None,
    program_context: Optional[dict] = None,
) -> dict:
    cotton = get_cotton_snapshot(db)
    fx = get_fx_snapshot(db)
    crude = get_crude_oil_snapshot(db)
    freight = get_freight_snapshot(db)
    geo = get_geopolitical_snapshot(db)
    benchmarks = get_cost_benchmark_snapshot(db)

    conflicts = detect_signal_conflicts(cotton, crude, fx, freight)
    data_quality = assess_overall_data_quality(
        cotton, fx, crude, freight, geo, benchmarks
    )

    company_context = None
    if company_id:
        company = db.query(CompanyProfile).filter_by(id=company_id).first()
        if company:
            relationships = (
                db.query(CompanyFactoryRelationship)
                .filter_by(company_id=company_id)
                .all()
            )
            recent_pos = (
                db.query(PurchaseOrderHistory)
                .filter_by(company_id=company_id)
                .order_by(desc(PurchaseOrderHistory.committed_delivery_date))
                .limit(5)
                .all()
            )
            company_context = {
                "name": company.company_name,
                "primary_corridors": company.primary_corridors,
                "primary_retailers": company.primary_retail_relationships,
                "intelligence_confidence": float(company.intelligence_confidence),
                "factory_relationships": [
                    {
                        "factory": r.factory_name,
                        "corridor": r.factory_corridor,
                        "programs_completed": r.programs_completed,
                        "avg_price_vs_market_pct": float(r.avg_price_vs_market_pct or 0),
                        "avg_otd_rate": float(r.avg_otd_rate or 0),
                    }
                    for r in relationships
                ],
                "recent_programs": [
                    {
                        "product": p.product_category,
                        "corridor": p.corridor,
                        "quantity": float(p.quantity_dozens or 0),
                        "season": p.season,
                    }
                    for p in recent_pos
                ],
            }

    curve_caveat_line = ""
    if not cotton.get("is_curve_real") and cotton.get("curve_caveat"):
        curve_caveat_line = f"⚠ CURVE CAVEAT: {cotton['curve_caveat']}"

    wow_line = (
        f"{cotton['week_over_week_change_pct']:+.2f}% ({cotton['week_over_week_change_cents']:+.2f}¢/lb)"
        if cotton.get("week_over_week_change_pct") is not None
        else "Insufficient data for WoW comparison"
    )

    briefing = f"""
=== ARTEMIS DATA BRIEFING — {date.today().strftime('%d %B %Y')} ===
OVERALL DATA QUALITY: {data_quality['overall_label']} ({data_quality['overall_score']:.0%})
{data_quality['confidence_statement']}

--- COTTON ---
Spot price: {cotton.get('spot_cents_per_lb')}¢/lb
  Source: {cotton.get('source')} | As of: {cotton.get('spot_data_as_of')} | Freshness: {cotton.get('freshness_days')}d | Real curve: {cotton.get('is_curve_real')}
  {"⚠ STALE DATA: " + str(cotton.get('freshness_days')) + " days old" if cotton.get('is_stale') else ""}

Forward curve (ICE Cotton #2):
  3m: {cotton.get('curve_3m')}¢ | 6m: {cotton.get('curve_6m')}¢ | 9m: {cotton.get('curve_9m')}¢ | 12m: {cotton.get('curve_12m')}¢
  Shape: {cotton.get('curve_shape')} | 12m premium: {cotton.get('curve_premium_12m_pct', 0):.1f}%
  {curve_caveat_line or "Real ICE prices — individual contract tickers"}

Supply/Demand (USDA WASDE):
  Stocks-to-use: {cotton.get('su_ratio_pct')} → Signal: {cotton.get('su_signal')}
  World production: {cotton.get('world_production_m_bales')}M bales
  World mill use: {cotton.get('world_mill_use_m_bales')}M bales
  World ending stocks: {cotton.get('world_ending_stocks_m_bales')}M bales
  Supply/demand balance: {cotton.get('supply_demand_balance')}M bales
  WASDE as of: {cotton.get('wasde_data_as_of')}

US crop:
  Planted: {cotton.get('us_planted_pct') or 'Outside planting season'}%
  Condition good+excellent: {cotton.get('us_crop_condition_pct') or 'Not yet published'}%

Week-over-week: {wow_line}

--- FX RATES ---
Convention: {fx.get('rate_convention')}
As of: {fx.get('as_of')} | Freshness: {fx.get('freshness_hours')}h | Quality: {fx.get('quality_score', 0):.0%}

Pre-computed corridor summaries (authoritative — do NOT invert rate direction):
{chr(10).join(f"  • {s}" for s in fx.get('corridor_summaries', []))}

Most significant WoW move: {fx.get('most_significant_move')}

Full FX detail:
{json.dumps({k: v for k, v in fx.items() if k not in ('corridor_summaries', 'rate_convention')}, indent=2, default=str)}

--- CRUDE OIL ---
Brent: ${crude.get('brent_usd')}/bbl | WTI: ${crude.get('wti_usd')}/bbl
30-day change: {crude.get('brent_30d_change_pct', 0):+.1f}% | 90-day: {crude.get('brent_90d_change_pct', 0):+.1f}%
Trend: {crude.get('trend')} ({crude.get('trend_magnitude')})
Dyeing chemical pressure: {crude.get('dyeing_chemical_pressure')}
Note: {crude.get('dyeing_chemical_note')}
Geopolitical chemical premium active: {crude.get('geopolitical_chemical_premium')}

--- OCEAN FREIGHT ---
Data available: {freight.get('data_available')}
WCI global composite (40HQ): {("$" + format(freight['wci_composite_usd'], ',.0f') + " as of " + str(freight.get('wci_composite_as_of'))) if freight.get('wci_composite_usd') else "not parsed this week"}
{"⚠ " + freight.get('data_unavailable_reason', '') if not freight.get('data_available') else json.dumps(freight.get('corridors', {}), indent=2, default=str)}
Active disruptions: {json.dumps(freight.get('active_disruptions', []), indent=2)}
War risk surcharges active on: {freight.get('war_risk_corridors', [])}

Bunker fuel & crude→freight transmission:
{json.dumps(freight.get('bunker_fuel', {}), indent=2, default=str)}

--- GEOPOLITICAL ---
{json.dumps(geo, indent=2, default=str)}

--- STRUCTURAL COST CONTEXT ---
Factory financing rates (annual %):
{json.dumps(benchmarks.get('financing_rates', {}), indent=2)}
Financing differential note: {benchmarks.get('financing_differential_note')}

Government export incentives active:
{json.dumps(benchmarks.get('government_incentives', []), indent=2)}

NTR duty rate (HS 6109.10): {benchmarks.get('effective_duty_6109_10_pct')}%
Section 301 China additional: {benchmarks.get('section_301_china_additional_pct')}%

Cost reasoning confidence: {benchmarks.get('cost_layer_confidence', 0):.0%}
Layers with real market data: {benchmarks.get('layers_with_live_data', 0)}/{benchmarks.get('total_layers', 12)}

--- SIGNAL CONFLICTS DETECTED ---
{json.dumps(conflicts, indent=2) if conflicts else "None detected this week."}

--- DATA QUALITY SUMMARY ---
{json.dumps(data_quality, indent=2, default=str)}
"""

    if company_context:
        briefing += f"""
--- COMPANY CONTEXT ({company_context['name']}) ---
{json.dumps(company_context, indent=2, default=str)}
"""

    personalisation = ""
    if company_context:
        personalisation = f"""
This brief is for {company_context['name']}.
Personalise corridor implications to their primary corridors ({company_context['primary_corridors']})
and retail relationships ({company_context['primary_retailers']}).
Reference factory relationship data where relevant.
"""

    user_prompt = f"""
Using the data briefing below, produce a weekly apparel market intelligence brief.
Apply all principles in your system instructions with full rigor.

{briefing}

TOKEN PRIORITY RULE (non-negotiable):
Sections 6, 7, and 8 are the highest-value sections. They MUST be complete and MUST NOT
be truncated, abbreviated, or omitted under any circumstances — even if you are running
long on tokens. Sections 1–5 exist to provide context only; keep them brief so 6–8 always
fit within the output budget.

REQUIRED OUTPUT ORDER — write in this exact sequence:

**ARTEMIS MARKET BRIEF**
**{date.today().strftime('%d %B %Y')}** | Data quality: {data_quality['overall_label']}

---

**6. SIGNAL CONFLICTS** *(highest value — write this section FIRST and in full)*

[For each conflict in the briefing data:
⚡ CONFLICT: [Name]
Signal A says: [what it says and what it implies]
Signal B says: [what it says and what it implies]
Resolution: [which signal to weight for which decision type]
Dollar impact of following wrong signal: [quantified where possible]

If no conflicts: "All signals are consistent this week." — one sentence only.]

---

**7. 90-DAY OUTLOOK** *(write in full — do not truncate)*

[4–5 sentences. Synthesise all signals into a coherent directional view.
Which corridors face cost pressure vs advantage, single biggest risk, single biggest
opportunity. Quantify where possible. Distinguish high-confidence from moderate-confidence
elements. Do not be falsely precise about uncertain things.]

---

**8. ONE ACTION THIS WEEK** *(write in full — do not truncate)*

[1–2 sentences. The single most time-sensitive, highest-value action an operator
should consider. Must be specific, decision-relevant, and cite the signal that supports it.]

---

**1. COTTON MARKET** *[{cotton.get('su_signal')} — S/U {cotton.get('su_ratio_pct', 0):.1f}%]*

[Maximum 2 sentences. Spot + source/date, S/U implication, curve shape if relevant.
Quantify yarn cost in $/dozen for a standard 180gsm jersey program in one clause.]

**2. FX MOVEMENTS — CORRIDOR COST IMPACT**

[Maximum 2 sentences total. Only corridors with |move| > 0.5%. Direction must follow
USD/local rate convention: rate UP = local currency depreciated = corridor CHEAPER in USD.
Corridors with no move: one short clause.]

**3. CRUDE OIL & CHEMICAL COSTS**

[Maximum 2 sentences. Brent level + trend. Dyeing implication for dark colours.
Quantify $/dozen if possible.]

**4. OCEAN FREIGHT**

[Maximum 2 sentences if data available. If NOT available, use only:]
⚠ **FREIGHT DATA NOT YET CONNECTED** — Landed cost calculations are incomplete.
Estimates are FOB only until Drewry WCI rates are entered.

**5. GEOPOLITICAL & SUPPLY CHAIN RISKS**

[Maximum 2 sentences. Active events with corridor + quantified impact, or one sentence
if none active.]

---

**DATA QUALITY & SOURCES** *(mandatory — brief bullet list, max 6 lines)*

[Use the data quality summary. Each source: freshness + confidence. State critical gaps.]

{personalisation}
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        temperature=0.1,
        system=SYSTEM_PROMPT_MARKET_BRIEF,
        messages=[{"role": "user", "content": user_prompt}],
    )

    brief_text = response.content[0].text
    session_hash = hashlib.md5(briefing.encode()).hexdigest()[:8]
    logger.info(
        f"Market brief generated | company_id={company_id} | "
        f"data_quality={data_quality['overall_label']} | conflicts={len(conflicts)} | "
        f"tokens_in={response.usage.input_tokens} | tokens_out={response.usage.output_tokens} | "
        f"data_hash={session_hash}"
    )

    return {
        "brief": brief_text,
        "data_quality": data_quality,
        "signal_conflicts": conflicts,
        "generated_at": datetime.utcnow().isoformat(),
        "company_id": company_id,
        "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
        "estimated_cost_usd": round(
            (response.usage.input_tokens * 3 + response.usage.output_tokens * 15) / 1_000_000,
            4,
        ),
    }


def generate_program_intelligence(
    db: Session,
    spec_input: Any,
    company_id: Optional[int] = None,
    context_text: Optional[str] = None,
    factory_quote_usd: Optional[float] = None,
) -> dict:
    engine = CostReasoningEngine(db, company_id=company_id)
    cost_result = engine.reason(spec_input)
    parsed_spec = engine._parse_and_infer_spec(spec_input)

    cotton = get_cotton_snapshot(db)
    crude = get_crude_oil_snapshot(db)
    freight = get_freight_snapshot(db)
    benchmarks = get_cost_benchmark_snapshot(db)

    quote_analysis = None
    if factory_quote_usd and cost_result.fob_mid:
        gap = Decimal(str(factory_quote_usd)) - cost_result.fob_mid
        gap_pct = float(gap / cost_result.fob_mid * 100) if cost_result.fob_mid else 0.0
        factory_rel = None
        if company_id and parsed_spec.factory_name:
            factory_rel = (
                db.query(CompanyFactoryRelationship)
                .filter_by(
                    company_id=company_id,
                    factory_name=parsed_spec.factory_name,
                )
                .first()
            )

        quote_analysis = {
            "factory_quote": factory_quote_usd,
            "market_benchmark_mid": float(cost_result.fob_mid),
            "gap_usd": float(gap),
            "gap_pct": gap_pct,
            "quote_is_above_market": gap > 0,
            "relationship_adjustment_applied": factory_rel is not None,
            "historical_relationship_pct": (
                float(factory_rel.avg_price_vs_market_pct or 0) if factory_rel else None
            ),
            "negotiating_room_estimate": float(-gap * Decimal("0.7")) if gap > 0 else 0,
        }

    layer_lines = []
    for layer in cost_result.layer_estimates:
        factors = ", ".join(layer.factors_applied) if layer.factors_applied else "baseline prior"
        layer_lines.append(
            f"  {layer.layer_name}: ${layer.low:.2f} / ${layer.mid:.2f} / ${layer.high:.2f} "
            f"| {layer.confidence:.0%} | {factors}"
        )

    reasoning_summary = f"""
COST REASONING RESULT:
Mode: {cost_result.reasoning_mode}
Spec: {json.dumps(parsed_spec.__dict__, default=str)}
FOB estimate: ${cost_result.fob_low:.2f} – ${cost_result.fob_mid:.2f} – ${cost_result.fob_high:.2f}/dozen
Confidence: {cost_result.confidence_overall:.0%}
vs target: {cost_result.vs_target_fob}

Layer breakdown:
{chr(10).join(layer_lines)}

Active flags:
{chr(10).join(f'  • {f}' for f in cost_result.flags)}

Unknowns:
{chr(10).join(f'  • {u}' for u in cost_result.unknowns)}

Quote analysis:
{json.dumps(quote_analysis, indent=2, default=str) if quote_analysis else "No factory quote provided"}
"""

    user_prompt = f"""
Produce specific program intelligence for the following apparel program.
Decisions on this program may involve hundreds of thousands of dollars.

COST REASONING:
{reasoning_summary}

MARKET CONTEXT:
Cotton: {cotton.get('spot_cents_per_lb')}¢/lb | S/U: {cotton.get('su_ratio_pct')}% ({cotton.get('su_signal')}) | Data: {cotton.get('spot_data_as_of')}
Crude: ${crude.get('brent_usd')}/bbl | Dyeing pressure: {crude.get('dyeing_chemical_pressure')}
Freight: {'Available' if freight.get('data_available') else '⚠ NOT IN SYSTEM — landed cost is FOB only'}
Financing note: {benchmarks.get('financing_differential_note')}

ADDITIONAL CONTEXT FROM OPERATOR:
{context_text or "None provided"}

REQUIRED STRUCTURE:
**PROGRAM INTELLIGENCE**
**COST ASSESSMENT** *[Confidence: {cost_result.confidence_overall:.0%}]*
**ACTIVE RISKS ON THIS PROGRAM** *(each quantified)*
**WHAT WE DON'T KNOW**
**NEGOTIATING POSITION** *(if quote provided)*
**THE ONE DECISION**
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        temperature=0.1,
        system=SYSTEM_PROMPT_PROGRAM_INTELLIGENCE,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return {
        "intelligence": response.content[0].text,
        "cost_result": {
            "mode": cost_result.reasoning_mode,
            "fob_low": float(cost_result.fob_low),
            "fob_mid": float(cost_result.fob_mid),
            "fob_high": float(cost_result.fob_high),
            "confidence": float(cost_result.confidence_overall),
            "session_id": cost_result.session_id,
        },
        "quote_analysis": quote_analysis,
        "generated_at": datetime.utcnow().isoformat(),
        "tokens_used": response.usage.input_tokens + response.usage.output_tokens,
    }


def write_crude_hedge_recommendations(db: Session) -> dict:
    """
    Write hedge_opportunity_recommendation rows for open programs based on crude market structure.

    Logic:
    - If crude_market_structure == 'contango': create/update CONSIDER_HEDGE rows for all open
      programs with delivery_date > today + 30 days. Idempotent: skip if a row already exists
      for this program + commodity='crude_brent' + as_of_date=today.
    - If crude_market_structure == 'backwardation': note existing open rows (do not create new).

    Model version: 1.0.0 until empirical transmission coefficient is activated.
    Confidence: 0.55 — low, pending calibration from RRK invoice data.

    Returns summary dict with counts of inserted, skipped, and noted rows.
    """
    today = date.today()
    min_delivery = today + timedelta(days=30)
    MODEL_VERSION = "1.0.0"
    CONFIDENCE = Decimal("0.55")

    # Get market structure from CrudeCostInputs
    try:
        crude_inputs = CrudeCostInputs(db)
        forward = crude_inputs.get_forward_input(
            delivery_date=today + timedelta(days=90),
            as_of_date=today,
        )
        market_structure = forward.get("market_structure")
        brent_futures_3m = forward.get("brent_futures")
        spot = crude_inputs.get_spot_input(today)
        brent_t4w = spot.get("brent_t4w")
    except CrudeDataStaleError as exc:
        logger.warning(f"write_crude_hedge_recommendations: crude data stale: {exc}")
        return {"inserted": 0, "skipped": 0, "noted": 0, "error": str(exc)}

    if market_structure is None:
        return {"inserted": 0, "skipped": 0, "noted": 0, "error": "market_structure unavailable"}

    # Fetch open programs with upcoming delivery
    try:
        open_programs = (
            db.query(Program)
            .filter(
                Program.delivery_date_committed > min_delivery,
                Program.status.in_(["active", "in_production", "confirmed", "open"]),
            )
            .all()
        )
    except Exception as exc:
        logger.warning(f"write_crude_hedge_recommendations: program query failed: {exc}")
        open_programs = []

    inserted = skipped = noted = 0

    for program in open_programs:
        delivery = program.delivery_date_committed
        if delivery is None:
            continue

        days_to_delivery = (delivery - today).days
        if days_to_delivery <= 30:
            tenor_months = 1
        elif days_to_delivery <= 90:
            tenor_months = 3
        elif days_to_delivery <= 180:
            tenor_months = 6
        else:
            tenor_months = 12

        if market_structure == "contango":
            # Idempotency: skip if row already exists for today
            existing = (
                db.query(HedgeOpportunityRecommendation)
                .filter_by(
                    program_id=program.program_id,
                    commodity="crude_brent",
                    as_of_date=today,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            # potential_saving_doz: $0.003/dozen per $1 of crude contango
            # placeholder until empirical transmission coefficient is activated
            crude_spread = (
                float(brent_futures_3m) - float(brent_t4w)
                if brent_futures_3m is not None and brent_t4w is not None
                else 0.0
            )
            potential_saving_doz = Decimal(str(round(max(0.0, crude_spread * 0.003), 4)))

            qty_dozens = Decimal(str(program.quantity_units or 0)) / Decimal("12")
            risk_if_unhedged = potential_saving_doz * qty_dozens

            rec = HedgeOpportunityRecommendation(
                program_id=program.program_id,
                commodity="crude_brent",
                tenor_months=tenor_months,
                recommended_action=(
                    "CONSIDER_HEDGE — crude in contango, input costs rising. "
                    f"Brent futures 3m=${float(brent_futures_3m):.2f}/bbl vs "
                    f"current ${float(brent_t4w):.2f}/bbl."
                    if brent_futures_3m is not None and brent_t4w is not None
                    else "CONSIDER_HEDGE — crude in contango, forward curve elevated."
                ),
                potential_saving_doz=potential_saving_doz,
                risk_if_unhedged=risk_if_unhedged.quantize(Decimal("0.01")),
                confidence_score=CONFIDENCE,
                as_of_date=today,
                model_version=MODEL_VERSION,
            )
            db.add(rec)
            inserted += 1

        elif market_structure == "backwardation":
            # Note existing open crude hedge rows — backwardation = no urgency to hedge
            existing_rows = (
                db.query(HedgeOpportunityRecommendation)
                .filter_by(
                    program_id=program.program_id,
                    commodity="crude_brent",
                )
                .filter(HedgeOpportunityRecommendation.recommended_action.like("CONSIDER_HEDGE%"))
                .all()
            )
            for row in existing_rows:
                row.recommended_action = (
                    row.recommended_action
                    + f" | NOTE {today}: crude_cost_pressure_easing — "
                    "market in backwardation, monitor for opportunistic unhedge."
                )
                noted += 1

    try:
        db.commit()
        logger.info(
            f"write_crude_hedge_recommendations: structure={market_structure}, "
            f"inserted={inserted}, skipped={skipped}, noted={noted}"
        )
    except Exception as exc:
        db.rollback()
        logger.error(f"write_crude_hedge_recommendations: commit failed: {exc}")
        return {"inserted": 0, "skipped": 0, "noted": 0, "error": str(exc)}

    return {
        "market_structure": market_structure,
        "programs_evaluated": len(open_programs),
        "inserted": inserted,
        "skipped": skipped,
        "noted": noted,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Artemis synthesis engine — truth-seeking apparel intelligence"
    )
    parser.add_argument("command", choices=["brief", "program"])
    parser.add_argument("--company-id", type=int, default=None)
    parser.add_argument("--spec", type=str, default=None)
    parser.add_argument("--context", type=str, default=None)
    parser.add_argument("--quote", type=float, default=None)
    parser.add_argument("--output", choices=["text", "json"], default="text")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.command == "brief":
            result = generate_market_brief(db, company_id=args.company_id)
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                print(result["brief"])
                print(
                    f"\n[Generated at {result['generated_at']} | "
                    f"Tokens: {result['tokens_used']} | "
                    f"Cost: ~${result['estimated_cost_usd']}]"
                )
        elif args.command == "program":
            if not args.spec:
                print("Error: --spec required for program command")
                raise SystemExit(1)
            result = generate_program_intelligence(
                db,
                spec_input=args.spec,
                company_id=args.company_id,
                context_text=args.context,
                factory_quote_usd=args.quote,
            )
            if args.output == "json":
                print(json.dumps(result, indent=2, default=str))
            else:
                print(result["intelligence"])
    finally:
        db.close()

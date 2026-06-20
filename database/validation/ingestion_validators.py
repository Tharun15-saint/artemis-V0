"""Validation functions for ingestion pipelines. Each returns (is_valid, reason)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar

if TYPE_CHECKING:
    from database.ingestion_context import IngestionContext

T = TypeVar("T")


def validate_cotton_price(
    price_cents_per_lb,
    previous_spot=None,
) -> tuple[bool, str]:
    if price_cents_per_lb is None:
        return False, "cotton_price: None value"
    if price_cents_per_lb <= 0:
        return False, f"cotton_price: {price_cents_per_lb} is zero or negative"
    if price_cents_per_lb < 40:
        return False, f"cotton_price: {price_cents_per_lb} below minimum 40 cents/lb"
    if price_cents_per_lb > 250:
        return False, f"cotton_price: {price_cents_per_lb} above maximum 250 cents/lb"
    if previous_spot is not None and previous_spot > 0:
        pct_change = abs(price_cents_per_lb - previous_spot) / previous_spot * 100
        if pct_change > 15:
            return False, (
                f"cotton_price: {price_cents_per_lb:.2f} is {pct_change:.1f}% "
                f"different from previous {previous_spot:.2f} — exceeds 15% threshold"
            )
    return True, ""


def validate_crude_price(
    price_usd_per_barrel,
    previous_spot=None,
) -> tuple[bool, str]:
    """Hard structural bounds only — does NOT reject on week-over-week change.

    Crude oil routinely moves >20% in a single week during market stress events
    (COVID crash: -55% over 3 weeks; 1990 Gulf War: +54% in a month; 2022 Ukraine:
    +30% in 2 weeks). Rejecting on change guard silently loses real price data.
    Use check_crude_price_change_flag() to flag anomalies without rejecting.
    """
    if price_usd_per_barrel is None:
        return False, "crude_price: None value"
    if price_usd_per_barrel <= 0:
        return False, f"crude_price: {price_usd_per_barrel} zero or negative"
    if price_usd_per_barrel < 10:
        return False, f"crude_price: {price_usd_per_barrel} below minimum $10/bbl"
    if price_usd_per_barrel > 250:
        return False, f"crude_price: {price_usd_per_barrel} above maximum $250/bbl"
    return True, ""


def check_crude_price_change_flag(
    price_usd_per_barrel,
    previous_spot,
    label: str = "crude",
) -> Optional[str]:
    """Returns a warning string if week-over-week change exceeds 20%, else None.
    Caller should ctx.record_flag() and logger.warning() but MUST still write the row."""
    if previous_spot is None or previous_spot <= 0:
        return None
    pct_change = abs(price_usd_per_barrel - previous_spot) / previous_spot * 100
    if pct_change > 20:
        return (
            f"{label}: {float(price_usd_per_barrel):.2f} is {pct_change:.1f}% "
            f"from prior {float(previous_spot):.2f} — large move, row written"
        )
    return None


def validate_fx_rate(
    rate,
    currency_pair: str,
    previous_rate=None,
) -> tuple[bool, str]:
    RANGES = {
        "USD_INR": (38, 110),
        "USD_BDT": (55, 135),
        "USD_VND": (15000, 27000),
        "USD_CNY": (5.5, 9.0),
        "USD_TRY": (1, 50),
        "USD_MAD": (7, 12),
        "USD_PKR": (55, 340),
        "EUR_USD": (0.82, 1.65),   # USD per 1 EUR — range since 1999 launch
        "GBP_USD": (1.05, 2.15),   # USD per 1 GBP — range since early 2000s
        "USD_IDR": (2000, 21000),   # Indonesian Rupiah — 2004 low ~8900, 2026 high ~17,800+
        "USD_LKR": (30, 420),       # Sri Lanka Rupee — pre-crisis low ~60, 2022 peak ~370
        "USD_MXN": (3, 25),         # Mexican Peso — post-float low ~3.4, 2020 peak ~25
        "USD_THB": (20, 60),        # Thai Baht — 1997 crisis high ~56, stable ~30-35
        "USD_KHR": (3500, 4300),    # Cambodian Riel — NBC soft peg at ~4000/USD since 1994
    }
    if rate is None:
        return False, f"fx_rate {currency_pair}: None value"
    if rate <= 0:
        return False, f"fx_rate {currency_pair}: {rate} zero or negative"
    if currency_pair in RANGES:
        lo, hi = RANGES[currency_pair]
        if rate < lo or rate > hi:
            return False, f"fx_rate {currency_pair}: {rate} outside range [{lo}, {hi}]"
    if previous_rate is not None and previous_rate > 0:
        pct_change = abs(rate - previous_rate) / previous_rate * 100
        if pct_change > 5:
            return False, (
                f"fx_rate {currency_pair}: {rate:.4f} is {pct_change:.1f}% "
                f"different from previous {previous_rate:.4f} — exceeds 5% threshold"
            )
    return True, ""


def validate_freight_rate(rate_usd: float, route: str) -> tuple[bool, str]:
    if rate_usd is None:
        return False, f"freight_rate {route}: None value"
    if rate_usd <= 0:
        return False, f"freight_rate {route}: {rate_usd} zero or negative"
    if rate_usd < 500:
        return False, f"freight_rate {route}: {rate_usd} below minimum $500"
    if rate_usd > 30000:
        return False, f"freight_rate {route}: {rate_usd} above maximum $30,000"
    return True, ""


def validate_retailer_revenue(
    revenue_usd: float,
    retailer_name: str,
) -> tuple[bool, str]:
    # Gross-error tripwires only (catch a 10x/sign/parse error), NOT tight expectations:
    # the bounds must never reject a legitimate SEC-sourced quarter across the full history
    # we ingest. Walmart quarterly net sales span ~$93B (FY2009) → ~$181B (recent) and grow;
    # Target ~$15B → ~$31B. Nulling a verified value is worse than letting a tripwire be wide.
    QUARTERLY_RANGES = {
        "Walmart Inc": (80e9, 260e9),
        "Target Corporation": (12e9, 45e9),
        "TJX Companies": (5e9, 25e9),
        "Burlington": (0.5e9, 5e9),
        "Ross Stores": (2e9, 12e9),
    }
    if revenue_usd is None:
        return True, ""
    if revenue_usd <= 0:
        return False, f"retailer_revenue {retailer_name}: {revenue_usd} zero or negative"
    if retailer_name in QUARTERLY_RANGES:
        lo, hi = QUARTERLY_RANGES[retailer_name]
        if revenue_usd < lo or revenue_usd > hi:
            return False, (
                f"retailer_revenue {retailer_name}: ${revenue_usd:,.0f} "
                f"outside expected range ${lo:,.0f}–${hi:,.0f}"
            )
    return True, ""


def validate_gross_margin(
    margin_pct: float,
    retailer_name: str,
) -> tuple[bool, str]:
    RANGES = {
        "Walmart Inc": (20.0, 30.0),
        "Target Corporation": (22.0, 35.0),
        "TJX Companies": (25.0, 35.0),
        "Burlington": (35.0, 50.0),
        "Ross Stores": (25.0, 35.0),
    }
    if margin_pct is None:
        return True, ""
    if margin_pct <= 0 or margin_pct > 60:
        return False, f"gross_margin {retailer_name}: {margin_pct}% outside valid range"
    if retailer_name in RANGES:
        lo, hi = RANGES[retailer_name]
        if margin_pct < lo or margin_pct > hi:
            return False, (
                f"gross_margin {retailer_name}: {margin_pct}% "
                f"outside expected range {lo}%–{hi}%"
            )
    return True, ""


def validate_walmart_general_merch(revenue_usd: float) -> tuple[bool, str]:
    if revenue_usd is None:
        return True, ""
    if revenue_usd < 20e9 or revenue_usd > 40e9:
        return False, (
            f"walmart_general_merch: ${float(revenue_usd)/1e9:.1f}B "
            f"outside expected $20B–$40B"
        )
    return True, ""


def validate_sams_club_apparel(revenue_usd: float) -> tuple[bool, str]:
    if revenue_usd is None:
        return True, ""
    if revenue_usd < 1.5e9 or revenue_usd > 4e9:
        return False, (
            f"sams_club_home_apparel: ${float(revenue_usd)/1e9:.2f}B "
            f"outside expected $1.5B–$4B"
        )
    return True, ""


def validate_yarn_price_inr(
    price_inr_per_kg: float,
    yarn_type: str,
) -> tuple[bool, str]:
    RANGES = {
        "cotton_25s": (200, 300),
        "cotton_30s": (200, 400),
        "cotton_34s": (210, 320),
        "cotton_40s": (220, 340),
        "cotton_poly_blend": (150, 350),
        "modal": (300, 450),
        "recycled_poly_cotton": (120, 200),
    }
    if price_inr_per_kg is None:
        return False, f"yarn_price_inr {yarn_type}: None value"
    if price_inr_per_kg <= 0:
        return False, f"yarn_price_inr {yarn_type}: {price_inr_per_kg} zero or negative"
    if yarn_type in RANGES:
        lo, hi = RANGES[yarn_type]
        if price_inr_per_kg < lo or price_inr_per_kg > hi:
            return False, (
                f"yarn_price_inr {yarn_type}: {price_inr_per_kg} INR/kg "
                f"outside expected range {lo}–{hi}"
            )
    return True, ""


# Backward-compatible aliases
validate_general_merch_revenue = validate_walmart_general_merch
validate_sams_apparel = validate_sams_club_apparel


def validate_model_version(version: str) -> tuple[bool, str]:
    if not version:
        return False, "model_version: empty or None"
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        return False, f"model_version: {version} not in x.y.z format"
    return True, ""


def validate_and_log(
    value: T,
    validator_fn: Callable[..., tuple[bool, str]],
    ctx: IngestionContext,
    *validator_args: Any,
    **validator_kwargs: Any,
) -> Optional[T]:
    """
    Run validator. If valid, return value unchanged.
    If invalid, log rejection and return None.
    The field is stored as NULL rather than with bad data.
    """
    is_valid, reason = validator_fn(value, *validator_args, **validator_kwargs)
    if not is_valid:
        ctx.rejected(reason)
        return None
    return value

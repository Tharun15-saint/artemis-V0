"""
Compute yarn price_per_kg_usd from INR local price and grn_date-matched USD/INR FX.

Selects the fx_rates row whose effective date is on or before yarn.grn_date,
closest to receipt. Falls back to the most recent available rate when history
does not extend back far enough.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from database.models.market_data import FxRates
from database.models.yarn_fabric import Yarn

logger = logging.getLogger(__name__)

USD_PRICE_QUANTIZE = Decimal("0.0001")
USD_NOTE_PATTERN = re.compile(
    r"\s*\|\s*USD/kg computed using USD/INR rate of [^|]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FxRateLookup:
    usd_inr: Decimal
    rate_date: date
    used_fallback: bool


def get_usd_inr_rate_for_grn_date(db: Session, grn_date: Optional[date]) -> Optional[FxRateLookup]:
    """
    Find USD/INR closest to grn_date (on or before receipt).

    Equivalent intent:
      SELECT usd_inr FROM fx_rates
      WHERE as_of_date <= grn_date
      ORDER BY as_of_date DESC
      LIMIT 1

    Falls back to the most recent available rate when no row qualifies.
    """
    fallback_row = (
        db.query(FxRates)
        .filter(FxRates.usd_inr.isnot(None))
        .order_by(desc(FxRates.as_of_date))
        .first()
    )
    if fallback_row is None:
        return None

    if grn_date is None:
        return _row_to_lookup(fallback_row, used_fallback=True)

    historical_row = (
        db.query(FxRates)
        .filter(FxRates.usd_inr.isnot(None))
        .filter(FxRates.as_of_date <= grn_date)
        .order_by(desc(FxRates.as_of_date))
        .first()
    )
    if historical_row is not None:
        return _row_to_lookup(historical_row, used_fallback=False)

    return _row_to_lookup(fallback_row, used_fallback=True)


def _row_to_lookup(fx_row: FxRates, *, used_fallback: bool) -> Optional[FxRateLookup]:
    if fx_row.usd_inr is None:
        return None
    rate = Decimal(str(fx_row.usd_inr))
    if rate <= 0:
        return None
    if fx_row.as_of_date is not None:
        rate_date = fx_row.as_of_date
    elif fx_row.pulled_at is not None:
        rate_date = fx_row.pulled_at.date()
    else:
        rate_date = date.today()
    return FxRateLookup(usd_inr=rate, rate_date=rate_date, used_fallback=used_fallback)


def get_latest_usd_inr_rate(db: Session) -> Optional[Decimal]:
    """Return USD/INR from the most recent fx_rates row (fallback helper)."""
    lookup = get_usd_inr_rate_for_grn_date(db, None)
    return lookup.usd_inr if lookup else None


def compute_price_per_kg_usd(price_per_kg: Decimal, usd_inr_rate: Decimal) -> Decimal:
    """Convert INR/kg to USD/kg at four decimal places."""
    return (price_per_kg / usd_inr_rate).quantize(USD_PRICE_QUANTIZE)


def _usd_conversion_note(lookup: FxRateLookup) -> str:
    note = (
        f"USD/kg computed using USD/INR rate of {lookup.usd_inr} "
        f"dated {lookup.rate_date}"
    )
    if lookup.used_fallback:
        note += " (FX fallback — no rate on or before grn_date)"
    return note


def _merge_usd_note(existing_notes: Optional[str], usd_note: str) -> str:
    if not existing_notes:
        return usd_note
    cleaned = USD_NOTE_PATTERN.sub("", existing_notes).strip()
    if cleaned:
        return f"{cleaned} | {usd_note}"
    return usd_note


def _needs_usd_update(
    current_usd: Optional[Decimal],
    new_usd: Decimal,
    current_rate_date: Optional[date],
    new_rate_date: date,
    drift_threshold_pct: Optional[Decimal],
) -> bool:
    if current_usd is None or current_rate_date is None:
        return True
    if current_rate_date != new_rate_date:
        return True
    if drift_threshold_pct is None:
        return False
    if current_usd == 0:
        return True
    pct_diff = abs(new_usd - current_usd) / current_usd * Decimal("100")
    return pct_diff > drift_threshold_pct


def update_yarn_usd_prices(
    db: Session,
    *,
    only_null: bool = True,
    drift_threshold_pct: Optional[Decimal] = Decimal("2"),
) -> int:
    """
    Batch-update yarn.price_per_kg_usd using grn_date-matched historical FX.

    only_null=True: update rows where price_per_kg is set and price_per_kg_usd is NULL.
    only_null=False: also refresh when USD differs > drift_threshold_pct from
                     a fresh grn_date-matched calculation, or rate date changed.
    """
    if only_null:
        drift_threshold_pct = None

    candidates = (
        db.query(Yarn)
        .filter(Yarn.price_per_kg.isnot(None))
        .all()
    )

    updated = 0
    fallback_count = 0

    for yarn_row in candidates:
        lookup = get_usd_inr_rate_for_grn_date(db, yarn_row.grn_date)
        if lookup is None:
            continue

        price_per_kg = Decimal(str(yarn_row.price_per_kg))
        new_usd = compute_price_per_kg_usd(price_per_kg, lookup.usd_inr)
        current_usd = (
            Decimal(str(yarn_row.price_per_kg_usd))
            if yarn_row.price_per_kg_usd is not None
            else None
        )

        if not _needs_usd_update(
            current_usd,
            new_usd,
            yarn_row.price_per_kg_usd_rate_date,
            lookup.rate_date,
            drift_threshold_pct,
        ):
            continue

        yarn_row.price_per_kg_usd = new_usd
        yarn_row.price_per_kg_usd_rate_date = lookup.rate_date
        yarn_row.data_notes = _merge_usd_note(
            yarn_row.data_notes,
            _usd_conversion_note(lookup),
        )
        updated += 1
        if lookup.used_fallback:
            fallback_count += 1

    if updated:
        db.commit()
        logger.info(
            "Updated %s yarn row(s) with grn_date-matched USD/kg (%s used FX fallback)",
            updated,
            fallback_count,
        )

    return updated

"""
Tirupur local yarn market rate ingestion.

Primary source: TEXPROCIL weekly price bulletin
  Published every Friday at texprocil.org.
  Format: PDF bulletin with structured tables for each yarn count (Ne 20, 24, 30, 34, 40, 60).
  Covers: combed/semi-combed/open-end cotton yarn in INR per kg.

Manual upload path (primary operational method):
  Place a CSV file at data/uploads/tirupur_yarn_rates.csv
  Required columns:
    week_ending       — YYYY-MM-DD (Saturday of the TEXPROCIL week)
    yarn_count_ne     — integer (20, 24, 30, 34, 40, 60)
    spinning_method   — combed | semi_combed | open_end | compact | vortex
    fibre_type        — cotton | cotton_poly_blend | cotton_viscose_blend
    cotton_pct        — numeric (100.0 for pure cotton)
    price_per_kg_inr  — numeric, INR per kg
    data_quality      — verified_transaction | market_indicative | survey_based | manual_entry
    source            — e.g. TEXPROCIL_BULLETIN | trader_contact | market_survey
    source_url        — URL or blank
    notes             — free text or blank

After writing the market rate, this script queries the Cotton table to populate
the ICE cotton context fields:
  ice_cotton_near_cents_lb_at_obs  — ICE spot at the same week
  ice_cotton_near_inr_kg_at_obs    — converted to INR/kg using fx_rates
  ice_cotton_near_inr_kg_6w_prior  — ICE spot 6 weeks earlier (transmission lag baseline)
  implied_yarn_premium_over_cotton_inr — yarn_price - ice_cotton_near_inr_kg_at_obs

These fields enable the correlation analysis that quantifies the ICE→Tirupur
price transmission lag (seeded at 6 weeks in LearnedCoefficient table).

No synthetic data: if the CSV has no data for a week, no row is written.
"""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models.commodities import Cotton
from database.models.market_data import FxRates
from database.models.yarn_market import TirupurYarnMarketRate

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "1.0.0"
SOURCE_NAME = "tirupur_yarn_market_rate_manual"
DEFAULT_CSV_PATH = Path("data/uploads/tirupur_yarn_rates.csv")

VALID_SPINNING_METHODS = {"combed", "semi_combed", "open_end", "compact", "vortex"}
VALID_FIBRE_TYPES = {"cotton", "cotton_poly_blend", "cotton_viscose_blend", "modal"}
VALID_COUNTS = {20, 24, 30, 34, 40, 60}
VALID_QUALITY_TIERS = {
    "verified_transaction", "market_indicative", "survey_based", "manual_entry"
}

# lbs per kg — fixed physical constant
LBS_PER_KG = Decimal("2.20462")

# Seeded transmission lag (from LearnedCoefficient table)
DEFAULT_LAG_WEEKS = 6


# ---------------------------------------------------------------------------
# ICE cotton context lookup
# ---------------------------------------------------------------------------

def _ice_context_at_week(
    db: Session, week_ending: date
) -> dict[str, Optional[Decimal]]:
    """
    Find the ICE cotton spot price (USD ¢/lb) and the corresponding INR/kg
    price at the given week_ending and 6 weeks prior.

    Joins Cotton + FxRates using the closest available date within ±7 days.
    Returns a dict with all four context fields (all may be None).
    """
    def _nearest_cotton(target: date) -> Optional[Cotton]:
        rows = (
            db.query(Cotton)
            .filter(
                Cotton.origin_country == "ICE No.2 Global",  # only real world benchmark
                Cotton.as_of_date >= target - timedelta(days=14),
                Cotton.as_of_date <= target + timedelta(days=7),
                Cotton.spot_price.isnot(None),
                Cotton.is_latest == True,
            )
            .order_by(func.abs(func.julianday(Cotton.as_of_date) - func.julianday(target)))
            .limit(1)
            .all()
        )
        return rows[0] if rows else None

    def _nearest_fx(target: date) -> Optional[Decimal]:
        row = (
            db.query(FxRates)
            .filter(
                FxRates.usd_inr.isnot(None),
                FxRates.as_of_date <= target,
            )
            .order_by(desc(FxRates.as_of_date))
            .first()
        )
        return Decimal(str(row.usd_inr)) if row else None

    def _ice_to_inr_kg(cents_per_lb: Decimal, usd_inr: Decimal) -> Decimal:
        return (
            (cents_per_lb / Decimal("100")) * LBS_PER_KG * usd_inr
        ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    # Current week ICE context
    cotton_now = _nearest_cotton(week_ending)
    fx_now = _nearest_fx(week_ending)
    ice_spot_now: Optional[Decimal] = None
    ice_inr_now: Optional[Decimal] = None
    if cotton_now and cotton_now.spot_price:
        ice_spot_now = Decimal(str(cotton_now.spot_price))
        if fx_now:
            ice_inr_now = _ice_to_inr_kg(ice_spot_now, fx_now)

    # 6-week-prior ICE context (transmission lag baseline)
    lag_target = week_ending - timedelta(weeks=DEFAULT_LAG_WEEKS)
    cotton_6w = _nearest_cotton(lag_target)
    fx_6w = _nearest_fx(lag_target)
    ice_inr_6w: Optional[Decimal] = None
    if cotton_6w and cotton_6w.spot_price:
        ice_spot_6w = Decimal(str(cotton_6w.spot_price))
        if fx_6w:
            ice_inr_6w = _ice_to_inr_kg(ice_spot_6w, fx_6w)

    return {
        "ice_cotton_near_cents_lb_at_obs": ice_spot_now,
        "ice_cotton_near_inr_kg_at_obs": ice_inr_now,
        "ice_cotton_near_inr_kg_6w_prior": ice_inr_6w,
    }


def _implied_premium(
    yarn_inr: Decimal, ice_inr_at_obs: Optional[Decimal]
) -> Optional[Decimal]:
    if ice_inr_at_obs is None:
        return None
    return (yarn_inr - ice_inr_at_obs).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

def _parse_decimal(value: str, field: str) -> Optional[Decimal]:
    v = value.strip()
    if not v:
        return None
    try:
        return Decimal(v).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except Exception:
        raise ValueError(f"Invalid decimal for {field}: {v!r}")


def _parse_date(value: str, field: str) -> date:
    v = value.strip()
    try:
        return date.fromisoformat(v)
    except Exception:
        raise ValueError(f"Invalid date for {field}: {v!r}")


def _parse_int(value: str, field: str) -> int:
    v = value.strip()
    try:
        return int(v)
    except Exception:
        raise ValueError(f"Invalid integer for {field}: {v!r}")


def parse_csv(path: Path) -> list[dict]:
    if not path.exists():
        logger.error("CSV not found: %s", path)
        return []

    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for line_num, raw in enumerate(reader, start=2):
            try:
                we = _parse_date(raw["week_ending"], "week_ending")
                count = _parse_int(raw["yarn_count_ne"], "yarn_count_ne")
                spin = raw["spinning_method"].strip().lower()
                fibre = raw["fibre_type"].strip().lower()
                cotton_pct_str = raw.get("cotton_pct", "").strip()
                price = _parse_decimal(raw["price_per_kg_inr"], "price_per_kg_inr")
                quality = raw["data_quality"].strip().lower()
                source = raw["source"].strip()
                source_url = raw.get("source_url", "").strip() or None
                notes = raw.get("notes", "").strip() or None

                if price is None:
                    raise ValueError("price_per_kg_inr is required")
                if count not in VALID_COUNTS:
                    raise ValueError(f"yarn_count_ne {count} not in {VALID_COUNTS}")
                if spin not in VALID_SPINNING_METHODS:
                    raise ValueError(f"spinning_method {spin!r} invalid")
                if fibre not in VALID_FIBRE_TYPES:
                    raise ValueError(f"fibre_type {fibre!r} invalid")
                if quality not in VALID_QUALITY_TIERS:
                    raise ValueError(f"data_quality {quality!r} invalid")
                if price < Decimal("50") or price > Decimal("600"):
                    raise ValueError(
                        f"price_per_kg_inr {price} outside plausible range 50-600"
                    )

                cotton_pct = _parse_decimal(cotton_pct_str, "cotton_pct") if cotton_pct_str else None

                rows.append({
                    "week_ending": we,
                    "yarn_count_ne": count,
                    "spinning_method": spin,
                    "fibre_type": fibre,
                    "cotton_pct": cotton_pct,
                    "price_per_kg_inr": price,
                    "data_quality": quality,
                    "source": source,
                    "source_url": source_url,
                    "notes": notes,
                })

            except (KeyError, ValueError) as exc:
                logger.warning("Line %d skipped: %s", line_num, exc)

    return rows


# ---------------------------------------------------------------------------
# Prior week lookup for MoW change calculation
# ---------------------------------------------------------------------------

def _prior_week_price(
    db: Session,
    week_ending: date,
    yarn_count_ne: int,
    spinning_method: str,
    fibre_type: str,
) -> Optional[Decimal]:
    prior_week = week_ending - timedelta(weeks=1)
    row = (
        db.query(TirupurYarnMarketRate)
        .filter(
            TirupurYarnMarketRate.week_ending == prior_week,
            TirupurYarnMarketRate.yarn_count_ne == yarn_count_ne,
            TirupurYarnMarketRate.spinning_method == spinning_method,
            TirupurYarnMarketRate.fibre_type == fibre_type,
            TirupurYarnMarketRate.is_latest.is_(True),
        )
        .first()
    )
    return Decimal(str(row.price_per_kg_inr)) if row else None


def _four_week_avg_price(
    db: Session,
    week_ending: date,
    yarn_count_ne: int,
    spinning_method: str,
    fibre_type: str,
) -> Optional[Decimal]:
    cutoff = week_ending - timedelta(weeks=4)
    rows = (
        db.query(TirupurYarnMarketRate)
        .filter(
            TirupurYarnMarketRate.week_ending > cutoff,
            TirupurYarnMarketRate.week_ending < week_ending,
            TirupurYarnMarketRate.yarn_count_ne == yarn_count_ne,
            TirupurYarnMarketRate.spinning_method == spinning_method,
            TirupurYarnMarketRate.fibre_type == fibre_type,
            TirupurYarnMarketRate.is_latest.is_(True),
        )
        .all()
    )
    if not rows:
        return None
    prices = [Decimal(str(r.price_per_kg_inr)) for r in rows if r.price_per_kg_inr]
    if not prices:
        return None
    return (sum(prices) / len(prices)).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_yarn_rate_row(
    db: Session,
    ctx: IngestionContext,
    data: dict,
) -> None:
    we = data["week_ending"]
    count = data["yarn_count_ne"]
    spin = data["spinning_method"]
    fibre = data["fibre_type"]
    price = data["price_per_kg_inr"]

    dup_filter = {
        "week_ending": we,
        "yarn_count_ne": count,
        "spinning_method": spin,
        "fibre_type": fibre,
    }
    if is_duplicate_row(db, TirupurYarnMarketRate, dup_filter, {"price_per_kg_inr": price}):
        ctx.stale()
        return

    prior_price = _prior_week_price(db, we, count, spin, fibre)
    four_w_avg = _four_week_avg_price(db, we, count, spin, fibre)

    change_abs: Optional[Decimal] = None
    change_pct: Optional[Decimal] = None
    if prior_price is not None:
        change_abs = (price - prior_price).quantize(Decimal("0.0001"))
        if prior_price != 0:
            change_pct = (change_abs / prior_price * Decimal("100")).quantize(Decimal("0.0001"))

    change_vs_4w: Optional[Decimal] = None
    if four_w_avg is not None and four_w_avg != 0:
        change_vs_4w = (
            (price - four_w_avg) / four_w_avg * Decimal("100")
        ).quantize(Decimal("0.0001"))

    # ICE cotton context fields
    ice_ctx = _ice_context_at_week(db, we)
    implied_premium = _implied_premium(price, ice_ctx.get("ice_cotton_near_inr_kg_at_obs"))

    mark_latest(db, TirupurYarnMarketRate, dup_filter)
    db.add(TirupurYarnMarketRate(
        week_ending=we,
        yarn_count_ne=count,
        spinning_method=spin,
        fibre_type=fibre,
        cotton_pct=data.get("cotton_pct"),
        price_per_kg_inr=price,
        price_change_vs_prior_week_inr=change_abs,
        price_change_vs_prior_week_pct=change_pct,
        price_change_vs_4w_avg_pct=change_vs_4w,
        ice_cotton_near_cents_lb_at_obs=ice_ctx.get("ice_cotton_near_cents_lb_at_obs"),
        ice_cotton_near_inr_kg_at_obs=ice_ctx.get("ice_cotton_near_inr_kg_at_obs"),
        ice_cotton_near_inr_kg_6w_prior=ice_ctx.get("ice_cotton_near_inr_kg_6w_prior"),
        implied_yarn_premium_over_cotton_inr=implied_premium,
        observed_transmission_lag_weeks=None,   # set by calibration job after sufficient data
        data_quality=data["data_quality"],
        source=data["source"],
        source_url=data.get("source_url"),
        notes=data.get("notes"),
        as_of_date=we,
        pulled_at=datetime.now(timezone.utc),
        is_latest=True,
    ))
    db.commit()
    ctx.inserted()

    ice_inr_str = (
        f"{float(ice_ctx.get('ice_cotton_near_inr_kg_at_obs')):.2f}"
        if ice_ctx.get("ice_cotton_near_inr_kg_at_obs") else "N/A"
    )
    premium_str = (
        f"{float(implied_premium):.2f}"
        if implied_premium else "N/A"
    )
    logger.info(
        "  Ne%d %s %s | %s | INR/kg=%.2f | ICE@obs=%s | premium=%s",
        count, spin, fibre, we,
        float(price), ice_inr_str, premium_str,
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_once(csv_path: Optional[Path] = None) -> bool:
    if csv_path is None:
        csv_path = DEFAULT_CSV_PATH

    logger.info("Tirupur yarn rate ingestion from %s", csv_path)
    rows = parse_csv(csv_path)
    if not rows:
        logger.warning("No valid rows found in CSV — nothing to write.")
        return True   # not an error; file may legitimately be empty

    db = SessionLocal()
    try:
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=str(csv_path),
            db=db,
        ) as ctx:
            if rows:
                ctx.set_as_of_date(rows[-1]["week_ending"])

            for row in rows:
                try:
                    write_yarn_rate_row(db, ctx, row)
                except Exception as exc:
                    logger.error("Failed to write row %s: %s", row, exc)
                    ctx.increment_rejected(str(exc))

        logger.info(
            "Tirupur yarn rate ingestion complete: %d rows processed from CSV.",
            len(rows),
        )
        return True

    except Exception as exc:
        logger.critical("Tirupur yarn ingestion failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Write Tirupur yarn market rates from CSV. "
            "Populates ICE cotton context fields by joining the Cotton table."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help="Path to the CSV file (default: data/uploads/tirupur_yarn_rates.csv)",
    )
    args = parser.parse_args()
    raise SystemExit(0 if run_once(args.csv) else 1)

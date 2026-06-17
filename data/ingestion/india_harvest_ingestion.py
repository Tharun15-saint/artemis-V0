"""
India cotton harvest signal ingestion — USDA FAS PSD API.

Source: USDA Foreign Agricultural Service Production, Supply and Distribution (PSD) API
  https://apps.fas.usda.gov/psdonline/app/index.html#/app/downloads
  Same API infrastructure as WASDE.  Free, no auth required for public data.
  commodity_code = 2631000  (Cotton, Upland)
  country_code   = 'IN'     (India)

India-specific data extracted:
  - Production (million 480-lb bales)
  - Area harvested (thousand hectares)
  - MY ending stocks (million bales)
  - Season-average farm-gate price (not always available in PSD)

India marketing year: Oct 1 – Sep 30 (MY 2024 = Oct2024–Sep2025)

Why this matters:
  India produces ~25% of world cotton.  A downward revision in October–November
  (the first full-crop estimate after Kharif harvest) causes raw cotton prices in
  Gujarat and Vidarbha markets to spike within 4-8 weeks.  Tirupur yarn prices
  follow 6-10 weeks after that.  This signal is the earliest structural warning
  available in the data chain — earlier than ICE futures reaction.

  The vs_previous_estimate_lakh_bales field is the key actionable column:
  a revision of -5 lakh bales or more is a material supply shock.

No synthetic data: if PSD data is unavailable, the row is not written.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import os

import requests
from sqlalchemy.orm import Session

from database.base import SessionLocal, is_duplicate_row, mark_latest
from database.ingestion_context import IngestionContext
from database.models.weather import IndiaHarvestSignal

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

SCRIPT_VERSION = "1.0.0"
SOURCE_NAME = "usda_fas_psd_india_cotton"
PSD_BASE_URL = "https://api.fas.usda.gov/api/psd"
COMMODITY_CODE = "2631000"   # Cotton, Upland
USDA_FAS_API_KEY = os.getenv("USDA_FAS_API_KEY", "")
REQUEST_TIMEOUT = 60
SCHEDULE_INTERVAL_HOURS = 720   # monthly (PSD updates once a month with WASDE)

# PSD attribute names (matched by attributeName field in API response)
ATTR_PRODUCTION = "Production"
ATTR_AREA_HARVESTED = "Area Harvested"
ATTR_ENDING_STOCKS = "Ending Stocks"

# India cotton marketing year starts in October (month 10)
INDIA_MY_START_MONTH = 10


def _india_marketing_year(report_date: date) -> int:
    """
    Return the India marketing year that contains report_date.
    MY 2024 = Oct 2024 – Sep 2025.
    """
    if report_date.month >= INDIA_MY_START_MONTH:
        return report_date.year
    return report_date.year - 1


# ---------------------------------------------------------------------------
# USDA FAS PSD fetch
# ---------------------------------------------------------------------------

def _api_headers() -> dict[str, str]:
    if USDA_FAS_API_KEY:
        return {"X-Api-Key": USDA_FAS_API_KEY}
    return {}


def _get_json(url: str) -> list[dict[str, Any]]:
    resp = requests.get(url, headers=_api_headers(), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _india_country_code() -> Optional[str]:
    """Return India's numeric country code from the PSD /countries endpoint."""
    try:
        countries = _get_json(f"{PSD_BASE_URL}/countries")
        for c in countries:
            if "india" in c.get("countryName", "").lower():
                code = str(c["countryCode"])
                logger.info("India PSD country code: %s", code)
                return code
    except Exception as exc:
        logger.error("Countries lookup failed: %s", exc)
    return None


def _attributes_map() -> dict[int, str]:
    """Return attributeId → attributeName mapping."""
    try:
        attrs = _get_json(f"{PSD_BASE_URL}/commodityAttributes")
        return {int(a["attributeId"]): a["attributeName"] for a in attrs if "attributeId" in a}
    except Exception as exc:
        logger.error("Attributes lookup failed: %s", exc)
        return {}


def fetch_psd_india_cotton(marketing_years: list[int]) -> Optional[list[dict[str, Any]]]:
    """
    Fetch PSD records for India cotton for specified marketing years.
    Uses the correct URL pattern: /commodity/{code}/country/all/year/{year}
    Filters to India by country code, enriches with attribute names.
    """
    india_code = _india_country_code()
    if not india_code:
        logger.error("Cannot resolve India country code — aborting.")
        return None

    attrs = _attributes_map()
    all_records: list[dict[str, Any]] = []

    for my_year in marketing_years:
        url = f"{PSD_BASE_URL}/commodity/{COMMODITY_CODE}/country/all/year/{my_year}"
        try:
            records = _get_json(url)
            india_records = [
                r for r in records
                if str(r.get("countryCode", "")) == india_code
            ]
            for r in india_records:
                attr_id = r.get("attributeId")
                r["attributeName"] = attrs.get(int(attr_id), "") if attr_id else ""
            all_records.extend(india_records)
            logger.info("MY %d: %d India cotton PSD records", my_year, len(india_records))
            time.sleep(0.5)
        except requests.RequestException as exc:
            logger.warning("PSD fetch failed for MY %d: %s", my_year, exc)

    if not all_records:
        logger.error("No India cotton PSD records retrieved.")
        return None

    return all_records


def _parse_psd_into_monthly_rows(
    raw: list[dict[str, Any]],
) -> dict[tuple[int, int], dict[str, Any]]:
    """
    Group PSD records by (marketing_year, month).
    API v2 fields: marketYear, month (1-12 calendar), attributeName, value.
    """
    rows: dict[tuple[int, int], dict[str, Any]] = {}
    for record in raw:
        my_year = record.get("marketYear")
        month = record.get("month") or record.get("calendarMonth")
        attr_name = record.get("attributeName", "")
        value = record.get("value")

        if my_year is None or month is None or value is None:
            continue

        key = (int(my_year), int(month))
        if key not in rows:
            rows[key] = {"my_year": int(my_year), "calendar_month": int(month)}

        if attr_name == ATTR_PRODUCTION:
            rows[key]["production_thousand_bales"] = value
        elif attr_name == ATTR_AREA_HARVESTED:
            rows[key]["area_harvested_thousand_ha"] = value
        elif attr_name == ATTR_ENDING_STOCKS:
            rows[key]["ending_stocks_thousand_bales"] = value

    return rows


def _to_decimal(v: Any, places: int = 4) -> Optional[Decimal]:
    if v is None:
        return None
    try:
        return Decimal(str(round(float(v), places))).quantize(
            Decimal(f"0.{'0'*places}"), rounding=ROUND_HALF_UP
        )
    except (TypeError, ValueError):
        return None


def _thousand_bales_to_million(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    return _to_decimal(float(v) / 1000)


def _thousand_ha_to_lakh_ha(v: Any) -> Optional[Decimal]:
    """thousand hectares → lakh hectares (1 lakh = 100,000)."""
    if v is None:
        return None
    return _to_decimal(float(v) / 100)


def _million_bales_to_lakh_bales(v: Optional[Decimal]) -> Optional[Decimal]:
    """
    USDA million 480-lb bales → India lakh 170-kg bales.
    1 480-lb bale = 217.72 kg
    1 lakh bale   = 100,000 × 170 kg
    conversion: million_bales × 1,000,000 × 217.72 kg / (170 kg × 100,000) = × 12.8
    This is an approximation; USDA and CAI use different bale standards.
    """
    if v is None:
        return None
    factor = Decimal("12.8")   # million 480-lb bales → lakh 170-kg bales
    return (v * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _season_assessment(production_mb: Optional[Decimal], year: int) -> str:
    """Simple qualitative grade. Thresholds are approximate 5-year averages."""
    if production_mb is None:
        return "unknown"
    prod = float(production_mb)
    # India normal: 26-28 million bales; bumper ~30+; poor <22
    if prod >= 30:
        return "bumper"
    if prod >= 26:
        return "normal"
    if prod >= 22:
        return "below_average"
    return "crop_failure"


# ---------------------------------------------------------------------------
# Write to DB
# ---------------------------------------------------------------------------

def _write_harvest_row(
    db: Session,
    ctx: IngestionContext,
    my_year: int,
    cal_month: int,
    cal_year: int,
    prod_mb: Optional[Decimal],
    area_lakh_ha: Optional[Decimal],
    ending_stocks_mb: Optional[Decimal],
    prev_prod_mb: Optional[Decimal],
) -> None:
    report_month_date = date(cal_year, cal_month, 1)

    # Convert to lakh bales for the IndiaHarvestSignal schema
    prod_lakh = _million_bales_to_lakh_bales(prod_mb)
    stocks_lakh = _million_bales_to_lakh_bales(ending_stocks_mb)

    # Compute revision vs prior month
    vs_prev: Optional[Decimal] = None
    if prod_lakh is not None and prev_prod_mb is not None:
        prev_lakh = _million_bales_to_lakh_bales(prev_prod_mb)
        if prev_lakh is not None:
            vs_prev = (prod_lakh - prev_lakh).quantize(Decimal("0.01"))

    assessment = _season_assessment(prod_mb, my_year)

    dup_filter = {
        "marketing_year": my_year,
        "report_month": report_month_date,
        "source_agency": "USDA_FAS",
    }
    dup_values = {"estimated_production_lakh_bales": prod_lakh}

    if is_duplicate_row(db, IndiaHarvestSignal, dup_filter, dup_values):
        ctx.stale()
        return

    mark_latest(db, IndiaHarvestSignal, {"marketing_year": my_year, "report_month": report_month_date})

    db.add(IndiaHarvestSignal(
        marketing_year=my_year,
        report_month=report_month_date,
        estimated_production_lakh_bales=prod_lakh,
        acreage_lakh_hectares=area_lakh_ha,
        arrivals_lakh_bales=None,        # not in PSD; populated by CAI source in future
        closing_stock_lakh_bales=stocks_lakh,
        vs_previous_estimate_lakh_bales=vs_prev,
        vs_last_year_production_pct=None,   # requires joining to prior MY row
        season_assessment=assessment,
        source_agency="USDA_FAS",
        report_url=f"{PSD_BASE_URL}/commodity/{COMMODITY_CODE}/country/all",
        as_of_date=report_month_date,
        source=SOURCE_NAME,
        pulled_at=datetime.now(timezone.utc),
        is_latest=True,
    ))
    db.commit()
    ctx.inserted()

    prod_str = f"{float(prod_lakh):.1f}" if prod_lakh else "N/A"
    rev_str = f"{float(vs_prev):+.1f}" if vs_prev else "—"
    logger.info(
        "  MY%d/%02d: production=%s lakh bales | revision=%s | %s",
        my_year, cal_month, prod_str, rev_str, assessment,
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_once(backfill_years: int = 3) -> bool:
    """
    Fetch current and recent USDA PSD data for India cotton and write to DB.
    backfill_years: number of past marketing years to include (default 3).
    """
    logger.info("India harvest ingestion (USDA FAS PSD)...")
    today = date.today()
    current_my = _india_marketing_year(today)
    marketing_years = list(range(current_my - backfill_years, current_my + 1))
    raw = fetch_psd_india_cotton(marketing_years)
    if not raw:
        logger.error("PSD API returned no data — aborting. No rows written.")
        return False

    monthly_rows = _parse_psd_into_monthly_rows(raw)
    if not monthly_rows:
        logger.error("PSD: parsed zero rows from API response.")
        return False

    first_my = current_my - backfill_years

    db = SessionLocal()
    try:
        with IngestionContext(
            source_name=SOURCE_NAME,
            script_version=SCRIPT_VERSION,
            data_source_url=PSD_BASE_URL,
            db=db,
        ) as ctx:
            ctx.set_as_of_date(today)

            # Sort by (my_year, month) to enable revision computation
            sorted_keys = sorted(monthly_rows.keys())
            prev_prod_by_my: dict[int, Optional[Decimal]] = {}

            for (my_year, cal_month) in sorted_keys:
                if my_year < first_my:
                    continue

                row = monthly_rows[(my_year, cal_month)]
                prod_raw = row.get("production_thousand_bales")
                area_raw = row.get("area_harvested_thousand_ha")
                stocks_raw = row.get("ending_stocks_thousand_bales")

                prod_mb = _thousand_bales_to_million(prod_raw)
                area_lakh_ha = _thousand_ha_to_lakh_ha(area_raw)
                ending_stocks_mb = _thousand_bales_to_million(stocks_raw)

                # Calendar year: my_year if cal_month >= Oct, else my_year + 1
                cal_year = my_year if cal_month >= INDIA_MY_START_MONTH else my_year + 1

                prev_prod_mb = prev_prod_by_my.get(my_year)

                _write_harvest_row(
                    db, ctx, my_year, cal_month, cal_year,
                    prod_mb, area_lakh_ha, ending_stocks_mb, prev_prod_mb,
                )

                if prod_mb is not None:
                    prev_prod_by_my[my_year] = prod_mb

        logger.info("India harvest ingestion complete.")
        return True

    except Exception as exc:
        logger.critical("India harvest ingestion failed: %s", exc, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def run_scheduled() -> None:
    logger.info("India harvest scheduler: monthly cycle (%dh).", SCHEDULE_INTERVAL_HOURS)
    while True:
        run_once()
        time.sleep(SCHEDULE_INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="USDA FAS PSD India cotton harvest signal ingestion."
    )
    parser.add_argument("--backfill-years", type=int, default=3)
    parser.add_argument("--schedule", action="store_true")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled()
    else:
        raise SystemExit(0 if run_once(args.backfill_years) else 1)
